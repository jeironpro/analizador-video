from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import signal
import string
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timezone
from typing import Any, Optional

from flask import (
    Flask,
    Response,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    stream_with_context,
)
from werkzeug.utils import secure_filename

from models import Session, Video, db
from services.cleanup import CleanupDaemon
from services.queue import QueueManager, validate_file_size, validate_mime_type

app = Flask(__name__)


# ---------------------------------------------------------------------------
# JSON logging
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "module": record.module,
                "func": record.funcName,
                "line": record.lineno,
            },
            ensure_ascii=False,
        )


if os.environ.get("LOG_FORMAT", "json" if os.environ.get("RENDER") else "text").lower() in ("json", "true", "1"):
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(JsonFormatter())
    _handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(_handler)
    root.setLevel(logging.INFO)
    app.logger.handlers.clear()
    app.logger.propagate = False
    app.logger.addHandler(_handler)
    app.logger.setLevel(logging.INFO)
    logging.getLogger("werkzeug").handlers.clear()
    logging.getLogger("werkzeug").propagate = True

database_url = os.environ.get("DATABASE_URL", "sqlite:///videos.db")
if database_url.startswith("postgres") and "sslmode" not in database_url:
    database_url += "?sslmode=require" if "?" not in database_url else "&sslmode=require"
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 300}
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

base_dir = os.environ.get("UPLOAD_DIR", "/data" if os.environ.get("RENDER") else app.instance_path)
app.config["UPLOAD_FOLDER"] = os.path.join(base_dir, "uploads")
app.config["TEMP_FOLDER"] = os.path.join(base_dir, "temp")
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())
app.config["DEBUG"] = os.environ.get("RENDER") != "true"
app.config["SESSION_DAYS"] = int(os.environ.get("SESSION_DAYS", "7"))
app.config["ITEM_TIMEOUT"] = int(os.environ.get("ITEM_TIMEOUT", "600"))
app.config["MAX_RETRIES"] = int(os.environ.get("MAX_RETRIES", "3"))
app.config["MAX_QUEUE_ITEMS"] = int(os.environ.get("MAX_QUEUE_ITEMS", "20"))

try:
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["TEMP_FOLDER"], exist_ok=True)
except PermissionError:
    base_dir = app.instance_path
    app.config["UPLOAD_FOLDER"] = os.path.join(base_dir, "uploads")
    app.config["TEMP_FOLDER"] = os.path.join(base_dir, "temp")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["TEMP_FOLDER"], exist_ok=True)

db.init_app(app)

queue = QueueManager(app, db)
cleanup = CleanupDaemon(app, db, days=app.config["SESSION_DAYS"])


def _handle_sigterm(signum: int, frame: Any) -> None:
    app.logger.info("Received SIGTERM, shutting down gracefully...")
    queue.shutdown()
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


# ---------------------------------------------------------------------------
# Rate limiter (per-session sliding window)
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, limit: int = 10, window: int = 60) -> None:
        self.limit = limit
        self.window = window
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            timestamps = self._buckets.get(key, [])
            timestamps = [t for t in timestamps if now - t < self.window]
            if len(timestamps) >= self.limit:
                return False
            timestamps.append(now)
            self._buckets[key] = timestamps
            return True

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            self._buckets = {k: [t for t in ts if now - t < self.window] for k, ts in self._buckets.items()}


_upload_limiter = RateLimiter(
    limit=int(os.environ.get("RATE_LIMIT_UPLOAD", "20")),
    window=int(os.environ.get("RATE_LIMIT_WINDOW", "60")),
)


def _validate_config() -> None:
    cfg = {
        "DATABASE_URL": (
            database_url.replace(
                database_url.split("@")[-1].split(":")[0] if "@" in database_url else "",
                "****",
            )
            if "postgres" in database_url
            else database_url
        ),
        "UPLOAD_DIR": base_dir,
        "SESSION_DAYS": app.config["SESSION_DAYS"],
        "ITEM_TIMEOUT": app.config["ITEM_TIMEOUT"],
        "MAX_RETRIES": app.config["MAX_RETRIES"],
        "MAX_QUEUE_ITEMS": app.config.get("MAX_QUEUE_ITEMS", 20),
        "RATE_LIMIT_UPLOAD": f"{_upload_limiter.limit}/{_upload_limiter.window}s",
        "MAX_CONTENT_LENGTH_MB": app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
        "RENDER": os.environ.get("RENDER", "false"),
        "LOG_FORMAT": os.environ.get("LOG_FORMAT", "text"),
        "DEBUG": app.config.get("DEBUG", False),
    }
    app.logger.info("Configuration: %s", json.dumps(cfg, ensure_ascii=False))
    required = ["DATABASE_URL"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        app.logger.warning("Variables de entorno faltantes: %s", missing)


_db_initialized = False

SESSION_CODE_ALPHABET = string.ascii_uppercase + string.digits


def _generate_session_code(length: int = 8) -> str:
    return "".join(secrets.choice(SESSION_CODE_ALPHABET) for _ in range(length))


def _create_session() -> str:
    while True:
        code = _generate_session_code()
        if not db.session.get(Session, code):
            break
    sess = Session(code=code)
    db.session.add(sess)
    db.session.commit()
    return code


def _get_session_code() -> str | None:
    return request.cookies.get("session_code")


def _session_required() -> str | None:
    code = _get_session_code()
    if not code:
        return None
    sess = db.session.get(Session, code)
    if sess:
        sess.last_active = datetime.now(UTC)
        db.session.commit()
        return code
    return None


def _init_db() -> None:
    global _db_initialized
    if _db_initialized:
        return
    try:
        with app.app_context():
            _run_alembic_migrations()
            _migrate_sessions()
        _db_initialized = True
    except Exception as e:
        app.logger.warning("No se pudo conectar a la base de datos: %s", e)
        try:
            with app.app_context():
                db.create_all()
                _migrate_schema()
                _migrate_sessions()
            _db_initialized = True
        except Exception as e2:
            app.logger.warning("Fallback db.create_all también falló: %s", e2)


def _run_alembic_migrations() -> None:
    alembic_cfg_path = os.path.join(os.path.dirname(__file__), "alembic.ini")
    if not os.path.exists(alembic_cfg_path):
        app.logger.info("alembic.ini no encontrado, usando db.create_all")
        raise FileNotFoundError("alembic.ini not found")
    try:
        from alembic.config import Config

        from alembic import command

        cfg = Config(alembic_cfg_path)
        command.upgrade(cfg, "head")
        app.logger.info("Migraciones Alembic ejecutadas correctamente")
    except Exception as e:
        app.logger.warning("Error ejecutando migraciones Alembic: %s", e)
        raise


def _migrate_schema() -> None:
    engine = db.engine
    inspector = db.inspect(engine)
    if inspector.has_table("video"):
        cols = {c["name"] for c in inspector.get_columns("video")}
        video_cols = {
            "id": "VARCHAR(36)",
            "filename": "VARCHAR(255)",
            "original_name": "VARCHAR(255)",
            "size": "INTEGER",
            "container": "VARCHAR(20)",
            "mime_type": "VARCHAR(100)",
            "analysis_result": "TEXT",
            "clamav_result": "VARCHAR(50)",
            "uploaded_at": "TIMESTAMP",
            "session_id": "VARCHAR(8)",
        }
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                for name, raw_type in video_cols.items():
                    if name not in cols:
                        col = Video.__table__.columns.get(name)
                        nullable = "NULL" if col is None or col.nullable else "NOT NULL"
                        conn.execute(db.text(f"ALTER TABLE video ADD COLUMN {name} {raw_type} {nullable}"))
                trans.commit()
            except Exception:
                trans.rollback()
    if inspector.has_table("queue_items"):
        qcols = {c["name"] for c in inspector.get_columns("queue_items")}
        if "session_id" not in qcols:
            try:
                with engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE queue_items ADD COLUMN session_id VARCHAR(8)"))
                    conn.commit()
            except Exception:
                pass


def _migrate_sessions() -> None:
    from models import QueueItem

    has_legacy = db.session.query(Video).filter(Video.session_id.is_(None)).count() > 0
    if not has_legacy:
        return
    legacy = Session(code="LEGACY01")
    db.session.add(legacy)
    Video.query.filter(Video.session_id.is_(None)).update({"session_id": "LEGACY01"})
    QueueItem.query.filter(QueueItem.session_id.is_(None)).update({"session_id": "LEGACY01"})
    legacy_dir = os.path.join(app.config["UPLOAD_FOLDER"], "LEGACY01")
    os.makedirs(legacy_dir, exist_ok=True)
    for f in os.listdir(app.config["UPLOAD_FOLDER"]):
        fpath = os.path.join(app.config["UPLOAD_FOLDER"], f)
        if os.path.isfile(fpath):
            shutil.move(fpath, os.path.join(legacy_dir, f))
    db.session.commit()


_init_db()
_validate_config()


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------
def _sse_step(step: str, status: str, message: str) -> str:
    return f"event: step\ndata: {json.dumps({'step': step, 'status': status, 'message': message})}\n\n"


def _sse_complete(data: Any) -> str:
    return f"event: complete\ndata: {json.dumps(data)}\n\n"


def _sse_error(message: str) -> str:
    return f"event: error\ndata: {json.dumps({'message': message})}\n\n"


# ---------------------------------------------------------------------------
# Session routes
# ---------------------------------------------------------------------------
@app.route("/")
def index() -> Response:
    code = _get_session_code()
    if code:
        sess = db.session.get(Session, code)
        if sess:
            sess.last_active = datetime.now(UTC)
            db.session.commit()
            return redirect(f"/s/{code}/")
    code = _create_session()
    resp = redirect(f"/s/{code}/")
    resp.set_cookie("session_code", code, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
    return resp


@app.route("/s/<code>/")
def session_view(code: str) -> Response:
    if len(code) != 8 or not all(c in SESSION_CODE_ALPHABET for c in code):
        return redirect("/")
    sess = db.session.get(Session, code)
    if not sess:
        app.logger.info("Session code %s not found, creating new session", code)
        return redirect("/")
    sess.last_active = datetime.now(UTC)
    db.session.commit()
    resp = make_response(render_template("index.html"))
    resp.set_cookie("session_code", code, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
    return resp


@app.route("/api/session")
def session_info() -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    return jsonify({"code": code}), 200


@app.route("/api/session/delete", methods=["POST"])
def session_delete() -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    from models import QueueItem

    videos = Video.query.filter_by(session_id=code).all()
    for v in videos:
        fpath = os.path.join(app.config["UPLOAD_FOLDER"], code, v.filename)
        if os.path.exists(fpath):
            os.remove(fpath)
        db.session.delete(v)
    QueueItem.query.filter_by(session_id=code).delete()
    sess = db.session.get(Session, code)
    if sess:
        db.session.delete(sess)
    db.session.commit()
    sdir = os.path.join(app.config["UPLOAD_FOLDER"], code)
    if os.path.exists(sdir):
        try:
            os.rmdir(sdir)
        except OSError:
            pass
    resp = jsonify({"message": "Sesión eliminada"})
    resp.set_cookie("session_code", "", expires=0)
    return resp, 200


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.route("/health")
def health() -> tuple[Response, int]:
    ok = False
    try:
        db.session.execute(db.text("SELECT 1"))
        ok = True
    except Exception:
        pass
    return jsonify({"status": "ok" if ok else "db_error"}), 200 if ok else 503


# ---------------------------------------------------------------------------
# API routes (all require valid session)
# ---------------------------------------------------------------------------
@app.route("/api/videos", methods=["GET"])
def list_videos() -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    videos = Video.query.filter_by(session_id=code).order_by(Video.uploaded_at.desc()).all()
    return jsonify([v.to_dict() for v in videos]), 200


@app.route("/api/upload", methods=["POST"])
def upload() -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401

    if not _upload_limiter.is_allowed(code):
        return jsonify({"error": "Demasiadas solicitudes. Espera un momento antes de subir más archivos."}), 429

    max_items = app.config["MAX_QUEUE_ITEMS"]
    current = queue.count_items(code)
    if current >= max_items:
        return jsonify(
            {
                "error": f"Límite de {max_items} archivos en cola alcanzado. "
                f"Procesá o eliminá algunos antes de subir más."
            }
        ), 429

    if "video" not in request.files:
        return jsonify({"error": "No se envió ningún archivo"}), 400
    file = request.files["video"]
    if file.filename == "":
        return jsonify({"error": "Nombre de archivo vacío"}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    video_exts = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".mpeg", ".wmv"}
    if ext not in video_exts:
        return jsonify({"error": f"Extensión no permitida: {ext}"}), 400

    temp_id = str(uuid.uuid4())
    safe_name = secure_filename(file.filename) or f"video{ext}"
    temp_filename = f"{temp_id}_{safe_name}"
    temp_path = os.path.join(app.config["TEMP_FOLDER"], temp_filename)
    file.save(temp_path)

    ok, mime_or_msg = validate_mime_type(temp_path)
    if not ok:
        os.remove(temp_path)
        return jsonify({"error": mime_or_msg}), 400

    queue.add(temp_id, temp_path, temp_filename, safe_name, ext, code)
    return jsonify({"temp_id": temp_id, "original_name": safe_name, "temp_filename": temp_filename}), 201


@app.route("/api/queue")
def list_queue() -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    return jsonify(queue.list_items(session_code=code)), 200


@app.route("/api/queue/events")
def queue_events() -> Response:
    def generate():
        last_state = None
        while True:
            code = request.cookies.get("session_code")
            if not code:
                yield f"data: []\n\n"
                last_state = "[]"
                time.sleep(1)
                continue
            items = queue.list_items(session_code=code)
            serialized = json.dumps(items)
            if serialized != last_state:
                yield f"data: {serialized}\n\n"
                last_state = serialized
            time.sleep(1)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/queue/<temp_id>/process", methods=["POST"])
def queue_process(temp_id: str) -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    item = queue.get(temp_id)
    if not item:
        return jsonify({"error": "Item no encontrado"}), 404
    if item.get("session_code") != code:
        return jsonify({"error": "Item no pertenece a esta sesión"}), 403
    if item["status"] != "uploaded":
        return jsonify({"error": f"El item está en estado: {item['status']}"}), 400
    queue.update_status(temp_id, "queued")
    queue.start_scheduler()
    return jsonify({"message": "Item agregado a la cola de procesamiento"}), 200


@app.route("/api/queue/<temp_id>/stream")
def queue_stream(temp_id: str) -> Response:
    def generate():
        last_count = 0
        while True:
            item = queue.get(temp_id)
            if not item:
                yield _sse_error("Item no encontrado en la cola")
                return
            while last_count < len(item["logs"]):
                log = item["logs"][last_count]
                yield _sse_step(log["step"], log["status"], log["message"])
                last_count += 1
            if item["status"] == "done":
                yield _sse_complete({"video": item["result"]})
                return
            if item["status"] in ("error", "cancelled"):
                yield _sse_error(item["error"] or "Error desconocido")
                return
            time.sleep(0.5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/queue/<temp_id>", methods=["DELETE"])
def queue_remove(temp_id: str) -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    item = queue.get(temp_id)
    if not item:
        return jsonify({"error": "Item no encontrado"}), 404
    if item.get("session_code") != code:
        return jsonify({"error": "Item no pertenece a esta sesión"}), 403
    if item["status"] == "processing":
        return jsonify({"error": f"No se puede eliminar un item en estado: {item['status']}"}), 400
    queue.remove(temp_id)
    return jsonify({"message": "Item eliminado de la cola"}), 200


@app.route("/api/queue/<temp_id>/cancel", methods=["POST"])
def queue_cancel(temp_id: str) -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    item = queue.get(temp_id)
    if not item:
        return jsonify({"error": "Item no encontrado"}), 404
    if item.get("session_code") != code:
        return jsonify({"error": "Item no pertenece a esta sesión"}), 403
    if item["status"] != "processing":
        return jsonify({"error": "Solo se puede cancelar un item en procesamiento"}), 400
    queue.cancel(temp_id)
    return jsonify({"message": "Procesamiento cancelado"}), 200


@app.route("/api/download/<video_id>", methods=["GET"])
def download(video_id: str) -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    video = db.session.get(Video, video_id)
    if not video:
        return jsonify({"error": "Video no encontrado"}), 404
    if video.session_id != code:
        return jsonify({"error": "Video no pertenece a esta sesión"}), 403
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], video.session_id, video.filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Archivo no encontrado en el servidor"}), 404
    return send_file(
        filepath,
        mimetype=video.mime_type or "application/octet-stream",
        as_attachment=True,
        download_name=video.original_name,
    ), 200


@app.route("/api/delete/<video_id>", methods=["DELETE"])
def delete(video_id: str) -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    video = db.session.get(Video, video_id)
    if not video:
        return jsonify({"error": "Video no encontrado"}), 404
    if video.session_id != code:
        return jsonify({"error": "Video no pertenece a esta sesión"}), 403
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], video.session_id, video.filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    db.session.delete(video)
    db.session.commit()
    return jsonify({"message": "Video eliminado correctamente"}), 200


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e: Any) -> tuple[str, int]:
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e: Any) -> tuple[str, int]:
    return render_template("500.html"), 500


# Load queue from DB on startup
with app.app_context():
    queue.load_from_db()
    cleanup.start()

if __name__ == "__main__":
    app.run(debug=True, port=5001)
