from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import string
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from flask import (
    Flask,
    Response,
    g,
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
from services.config import ALLOWED_EXTENSIONS, MAX_CONTENT_LENGTH
from services.queue import QueueManager
from services.rate_limiter import RateLimiter
from services.sse import sse_complete, sse_error, sse_step
from services.validation import validate_mime_type

app = Flask(__name__)


# ---------------------------------------------------------------------------
# JSON logging
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            data["request_id"] = request_id
        return json.dumps(data, ensure_ascii=False)


def _request_id() -> str:
    try:
        return request.headers.get("X-Request-ID") or ""
    except Exception:
        return ""


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rid = getattr(request, "request_id", None) or _request_id()
        except RuntimeError:
            rid = ""
        record.request_id = rid
        return True


if os.environ.get("LOG_FORMAT", "json" if os.environ.get("RENDER") else "text").lower() in ("json", "true", "1"):
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(JsonFormatter())
    _handler.addFilter(_RequestIdFilter())
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
if database_url.startswith("sqlite:///") and not database_url.startswith("sqlite:////"):
    _db_path = database_url[len("sqlite:///") :]
    if not os.path.isabs(_db_path):
        _db_path = os.path.abspath(os.path.join(app.instance_path, _db_path))
        database_url = f"sqlite:///{_db_path}"
if database_url.startswith("postgres") and "sslmode" not in database_url and os.environ.get("RENDER"):
    database_url += "?sslmode=require" if "?" not in database_url else "&sslmode=require"
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
_engine_options: dict[str, Any] = {"pool_pre_ping": True, "pool_recycle": 300}
if database_url.startswith("postgres"):
    _engine_options.update({"pool_size": 8, "max_overflow": 12})
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = _engine_options
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

base_dir = os.environ.get("UPLOAD_DIR", "/data" if os.environ.get("RENDER") else app.instance_path)
app.config["UPLOAD_FOLDER"] = os.path.join(base_dir, "uploads")
app.config["TEMP_FOLDER"] = os.path.join(base_dir, "temp")
secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    app.logger.warning("SECRET_KEY no configurada — usando clave aleatoria. Las sesiones se invalidarán al reiniciar.")
    secret_key = os.urandom(24).hex()
app.secret_key = secret_key
app.config["DEBUG"] = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
app.config["SESSION_DAYS"] = int(os.environ.get("SESSION_DAYS", "7"))
app.config["ITEM_TIMEOUT"] = int(os.environ.get("ITEM_TIMEOUT", "600"))
app.config["MAX_RETRIES"] = int(os.environ.get("MAX_RETRIES", "3"))
app.config["MAX_QUEUE_ITEMS"] = int(os.environ.get("MAX_QUEUE_ITEMS", "20"))


@app.before_request
def _set_request_id() -> None:
    rid = request.headers.get("X-Request-ID") or _generate_request_id()
    request.request_id = rid  # type: ignore[attr-defined]
    g.request_id = rid  # type: ignore[attr-defined]


def _generate_request_id() -> str:
    return uuid.uuid4().hex[:12]


@app.after_request
def _apply_csp(response: Response) -> Response:
    response.headers.setdefault(
        "Content-Security-Policy",
        "; ".join(
            [
                "default-src 'self'",
                "img-src 'self' data:",
                "connect-src 'self' https://cdn.jsdelivr.net",
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
                "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com",
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            ]
        ),
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


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


SESSION_CODE_ALPHABET = string.ascii_uppercase + string.digits


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _touch_session(sess: Session) -> None:
    now = _utcnow()
    if sess.last_active is None or (now - sess.last_active).total_seconds() > 60:
        sess.last_active = now
        db.session.commit()


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
        _touch_session(sess)
        return code
    return None


_init_db_guard = False


def _init_db() -> None:
    global _init_db_guard
    if _init_db_guard:
        return
    _init_db_guard = True
    try:
        with app.app_context():
            alembic_cfg_path = os.path.join(os.path.dirname(__file__), "alembic.ini")
            from alembic.config import Config

            from alembic import command

            cfg = Config(alembic_cfg_path)
            command.upgrade(cfg, "head")
            app.logger.info("Migraciones Alembic ejecutadas correctamente")
    except Exception:
        app.logger.exception("No se pudieron aplicar migraciones Alembic")


_init_db()
_validate_config()


# ---------------------------------------------------------------------------
# Session routes
# ---------------------------------------------------------------------------
@app.route("/")
def index() -> Response:
    code = _get_session_code()
    if code:
        sess = db.session.get(Session, code)
        if sess:
            _touch_session(sess)
            resp = redirect(f"/s/{code}/")
            resp.set_cookie("session_code", code, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
            return resp
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
        new_code = _create_session()
        resp = redirect(f"/s/{new_code}/")
        resp.set_cookie("session_code", new_code, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
        return resp
    _touch_session(sess)
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


@app.route("/api/config", methods=["GET"])
def config_api() -> tuple[Response, int]:
    from services.config import (
        ALLOWED_EXTENSIONS,
        ALLOWED_MIMES,
        MAX_FILE_SIZE,
        MAX_QUEUE_ITEMS,
        MAX_RETRIES,
        MIN_FILE_SIZE,
    )

    return jsonify(
        {
            "min_file_size": MIN_FILE_SIZE,
            "max_file_size": MAX_FILE_SIZE,
            "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
            "allowed_mimes": sorted(ALLOWED_MIMES),
            "max_retries": MAX_RETRIES,
            "max_queue_items": MAX_QUEUE_ITEMS,
        }
    ), 200


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
    if ext not in ALLOWED_EXTENSIONS:
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
        last_ping = time.monotonic()
        while True:
            code = request.cookies.get("session_code")
            items = queue.list_items(session_code=code) if code else []
            serialized = json.dumps(items)
            if serialized != last_state:
                yield f"data: {serialized}\n\n"
                last_state = serialized
                last_ping = time.monotonic()
            elif time.monotonic() - last_ping >= 30:
                yield ": keep-alive\n\n"
                last_ping = time.monotonic()
            time.sleep(1)

    return Response(
        stream_with_context(generate()),
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
    queue.enqueue(temp_id)
    return jsonify({"message": "Item agregado a la cola de procesamiento"}), 200


@app.route("/api/queue/<temp_id>/stream")
def queue_stream(temp_id: str) -> Response:
    def generate():
        last_count = 0
        last_ping = time.monotonic()
        while True:
            item = queue.get(temp_id)
            if not item:
                yield sse_error("Item no encontrado en la cola")
                return
            while last_count < len(item["logs"]):
                log = item["logs"][last_count]
                yield sse_step(log["step"], log["status"], log["message"])
                last_count += 1
                last_ping = time.monotonic()
            if item["status"] == "done":
                yield sse_complete({"video": item["result"]})
                return
            if item["status"] in ("error", "cancelled"):
                yield sse_error(item["error"] or "Error desconocido")
                return
            if time.monotonic() - last_ping >= 30:
                yield ": keep-alive\n\n"
                last_ping = time.monotonic()
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


@app.route("/api/thumbnail/<video_id>", methods=["GET"])
def thumbnail(video_id: str) -> Response:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    video = db.session.get(Video, video_id)
    if not video:
        return jsonify({"error": "Video no encontrado"}), 404
    if video.session_id != code:
        return jsonify({"error": "Video no pertenece a esta sesión"}), 403
    if not video.has_thumbnail:
        return jsonify({"error": "Video no tiene miniatura"}), 404
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], video.session_id, f"{video_id}.jpg")
    if not os.path.exists(filepath):
        return jsonify({"error": "Miniatura no encontrada"}), 404
    return send_file(filepath, mimetype="image/jpeg", max_age=86400), 200


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


@app.route("/api/videos", methods=["DELETE"])
def delete_all_videos() -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    videos = Video.query.filter_by(session_id=code).all()
    for video in videos:
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], video.session_id, video.filename)
        if os.path.exists(filepath):
            os.remove(filepath)
        thumbpath = os.path.join(app.config["UPLOAD_FOLDER"], video.session_id, f"{video.id}.jpg")
        if os.path.exists(thumbpath):
            os.remove(thumbpath)
        db.session.delete(video)
    db.session.commit()
    return jsonify({"message": "Todos los videos eliminados"}), 200


@app.route("/api/queue", methods=["DELETE"])
def delete_all_queue() -> tuple[Response, int]:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    from models import QueueItem

    items = QueueItem.query.filter_by(session_id=code).all()
    for qi in items:
        if os.path.exists(qi.temp_path):
            os.remove(qi.temp_path)
        db.session.delete(qi)
    db.session.commit()
    return jsonify({"message": "Cola eliminada"}), 200


@app.route("/api/download", methods=["POST"])
def download_selected() -> tuple[Response, int] | Response:
    code = _session_required()
    if not code:
        return jsonify({"error": "Sesión no válida"}), 401
    data = request.get_json(silent=True)
    if not data or not isinstance(data.get("ids"), list):
        return jsonify({"error": "ids esperado como lista"}), 400
    ids = data["ids"]
    if not ids:
        return jsonify({"error": "No se especificaron IDs"}), 400

    videos = Video.query.filter(Video.id.in_(ids), Video.session_id == code).all()
    if not videos:
        return jsonify({"error": "Videos no encontrados"}), 404

    if len(videos) == 1:
        v = videos[0]
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], v.session_id, v.filename)
        if not os.path.exists(filepath):
            return jsonify({"error": "Archivo no encontrado"}), 404
        return send_file(
            filepath,
            mimetype=v.mime_type or "application/octet-stream",
            as_attachment=True,
            download_name=v.original_name,
        )

    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for v in videos:
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], v.session_id, v.filename)
            if os.path.exists(filepath):
                zf.write(filepath, v.original_name)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="vidscan_videos.zip",
    ), 200


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

if __name__ == "__main__":
    app.run(debug=True, port=5001)
