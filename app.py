import os
import uuid
import json
import mimetypes
import time
import threading
import subprocess
from datetime import datetime, timezone
from collections import OrderedDict
from flask import (
    Flask, request, render_template, jsonify, send_file, Response, stream_with_context
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect as sa_inspect
from werkzeug.utils import secure_filename
from video_analyzer import analyze_video, VideoAnalysisError

app = Flask(__name__)

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

try:
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["TEMP_FOLDER"], exist_ok=True)
except PermissionError:
    base_dir = app.instance_path
    app.config["UPLOAD_FOLDER"] = os.path.join(base_dir, "uploads")
    app.config["TEMP_FOLDER"] = os.path.join(base_dir, "temp")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["TEMP_FOLDER"], exist_ok=True)

db = SQLAlchemy(app)


class Video(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    container = db.Column(db.String(20))
    mime_type = db.Column(db.String(100))
    analysis_result = db.Column(db.Text)
    clamav_result = db.Column(db.String(50))
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "original_name": self.original_name,
            "size": self.size,
            "container": self.container,
            "mime_type": self.mime_type,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
        }


_db_initialized = False


def _init_db():
    global _db_initialized
    if _db_initialized:
        return
    try:
        with app.app_context():
            db.create_all()
            _migrate_schema()
        _db_initialized = True
    except Exception as e:
        app.logger.warning("No se pudo conectar a la base de datos: %s", e)


def _migrate_schema():
    engine = db.engine
    inspector = sa_inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("video")}
    type_map = {
        "id": "VARCHAR(36)", "filename": "VARCHAR(255)", "original_name": "VARCHAR(255)",
        "size": "INTEGER", "container": "VARCHAR(20)", "mime_type": "VARCHAR(100)",
        "analysis_result": "TEXT", "clamav_result": "VARCHAR(50)", "uploaded_at": "TIMESTAMP",
    }
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            for name, raw_type in type_map.items():
                if name not in cols:
                    col = Video.__table__.columns.get(name)
                    nullable = "NULL" if col is None or col.nullable else "NOT NULL"
                    conn.execute(db.text(f"ALTER TABLE video ADD COLUMN {name} {raw_type} {nullable}"))
            trans.commit()
        except Exception:
            trans.rollback()
            raise


_init_db()

# Queue system
_queue = OrderedDict()
_queue_lock = threading.Lock()
_queue_worker_thread = None

def _queue_add(temp_id, temp_path, temp_filename, original_name, ext):
    with _queue_lock:
        _queue[temp_id] = {
            "temp_id": temp_id,
            "temp_path": temp_path,
            "temp_filename": temp_filename,
            "original_name": original_name,
            "ext": ext,
            "status": "queued",
            "logs": [],
            "result": None,
            "error": None,
            "position": len(_queue),
        }
    _start_queue_worker()

def _queue_log(temp_id, step, status, message):
    with _queue_lock:
        item = _queue.get(temp_id)
        if item:
            item["logs"].append({"step": step, "status": status, "message": message})

def _queue_remove_temp(temp_id):
    with _queue_lock:
        item = _queue.pop(temp_id, None)
        if item and os.path.exists(item["temp_path"]):
            os.remove(item["temp_path"])

def _start_queue_worker():
    global _queue_worker_thread
    if _queue_worker_thread is None or not _queue_worker_thread.is_alive():
        _queue_worker_thread = threading.Thread(target=_queue_worker_loop, daemon=True)
        _queue_worker_thread.start()

def _queue_worker_loop():
    with app.app_context():
        while True:
            item = None
            with _queue_lock:
                for qi in _queue.values():
                    if qi["status"] == "queued":
                        item = qi
                        item["status"] = "processing"
                        break
            if not item:
                time.sleep(1)
                continue

            tp = item["temp_path"]
            try:
                _queue_log(item["temp_id"], "size", "checking", "Validando tamaño...")
                ok, msg = validate_file_size(tp)
                if not ok:
                    _queue_log(item["temp_id"], "size", "error", msg)
                    item["status"] = "error"; item["error"] = msg; continue
                _queue_log(item["temp_id"], "size", "ok", msg)

                _queue_log(item["temp_id"], "mime", "checking", "Detectando tipo MIME...")
                ok, mime_or_msg = validate_mime_type(tp)
                if not ok:
                    _queue_log(item["temp_id"], "mime", "error", mime_or_msg)
                    item["status"] = "error"; item["error"] = mime_or_msg; continue
                _queue_log(item["temp_id"], "mime", "ok", mime_or_msg)

                _queue_log(item["temp_id"], "clamav", "checking", "Escaneando con ClamAV...")
                ok, clam_msg = scan_with_clamav(tp)
                if not ok:
                    _queue_log(item["temp_id"], "clamav", "error", clam_msg)
                    item["status"] = "error"; item["error"] = f"ClamAV: {clam_msg}"; continue
                _queue_log(item["temp_id"], "clamav", "ok", clam_msg)

                _queue_log(item["temp_id"], "analysis", "checking", "Analizando codecs y metadatos...")
                analysis = analyze_video(tp)
                if not analysis.get("valid", False):
                    for err in analysis.get("errors", []):
                        _queue_log(item["temp_id"], "analysis", "error", err)
                    item["status"] = "error"; item["error"] = "Análisis de video fallido"; continue
                _queue_log(item["temp_id"], "analysis", "ok", f"Contenedor: {analysis.get('container')}")
                for s in analysis.get("streams", []):
                    t = "Video" if s["type"] == "video" else "Audio"
                    d = f"{s['codec']} {s['resolution']} @ {s['fps']} fps" if s["type"] == "video" else f"{s['codec']} {s.get('channels', '?')}ch"
                    _queue_log(item["temp_id"], "stream", "info", f"{t}: {d}")

                _queue_log(item["temp_id"], "save", "checking", "Guardando archivo...")
                import shutil
                video_id = str(uuid.uuid4())
                filename = f"{video_id}{item['ext']}"
                final_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                shutil.move(tp, final_path)

                mime_type = validate_mime_type(final_path)
                mime_val = mime_type[1] if mime_type[0] else "application/octet-stream"

                video = Video(
                    id=video_id, filename=filename, original_name=item["original_name"],
                    size=os.path.getsize(final_path),
                    container=analysis.get("container", item["ext"].lstrip(".")),
                    mime_type=mime_val,
                    analysis_result=str(analysis.get("errors", [])),
                    clamav_result=clam_msg,
                )
                db.session.add(video)
                db.session.commit()

                item["result"] = video.to_dict()
                item["status"] = "done"
                _queue_log(item["temp_id"], "save", "ok", "Video almacenado correctamente")
                _queue_log(item["temp_id"], "complete", "ok", "Proceso finalizado")

            except VideoAnalysisError as e:
                item["status"] = "error"
                item["error"] = f"Error de análisis: {str(e)}"
            except Exception as e:
                app.logger.exception("Error en queue worker")
                item["status"] = "error"
                item["error"] = f"Error interno: {str(e)}"
            finally:
                if os.path.exists(tp):
                    os.remove(tp)


def _sse_step(step, status, message):
    return f"event: step\ndata: {json.dumps({'step': step, 'status': status, 'message': message})}\n\n"

def _sse_complete(data):
    return f"event: complete\ndata: {json.dumps(data)}\n\n"

def _sse_error(message):
    return f"event: error\ndata: {json.dumps({'message': message})}\n\n"


def scan_with_clamav(filepath: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["clamscan", "--stdout", "--no-summary", "--quiet",
             "--database=/var/lib/clamav", filepath],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            return True, "Archivo limpio"
        if result.returncode == 1:
            return False, f"Virus detectado: {result.stdout.strip()}"
        return True, f"ClamAV: error ({result.returncode})"
    except FileNotFoundError:
        return True, "ClamAV no está instalado en el servidor"
    except subprocess.TimeoutExpired:
        return True, "Escaneo excedió el tiempo límite"
    except Exception:
        return True, "Escaneo no disponible"


def validate_file_size(filepath: str) -> tuple[bool, str]:
    size = os.path.getsize(filepath)
    if size < 50 * 1024 * 1024:
        return False, f"Demasiado pequeño ({size / 1024 / 1024:.1f} MB). Mínimo 50 MB"
    if size > 500 * 1024 * 1024:
        return False, f"Demasiado grande ({size / 1024 / 1024:.1f} MB). Máximo 200 MB"
    return True, f"{size / 1024 / 1024:.1f} MB"


def validate_mime_type(filepath: str) -> tuple[bool, str]:
    try:
        import magic
        mime = magic.from_file(filepath, mime=True)
    except ImportError:
        mime, _ = mimetypes.guess_type(filepath)
        mime = mime or "application/octet-stream"
    video_mimes = {
        "video/mp4", "video/webm", "video/x-matroska",
        "video/avi", "video/x-msvideo", "video/quicktime",
        "video/mpeg", "video/x-ms-wmv",
    }
    if mime not in video_mimes:
        return False, f"Tipo MIME no válido: {mime}"
    return True, mime


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/videos", methods=["GET"])
def list_videos():
    videos = Video.query.order_by(Video.uploaded_at.desc()).all()
    return jsonify([v.to_dict() for v in videos])


@app.route("/api/upload", methods=["POST"])
def upload():
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
    _queue_add(temp_id, temp_path, temp_filename, safe_name, ext)
    return jsonify({"temp_id": temp_id, "original_name": safe_name, "temp_filename": temp_filename}), 201


@app.route("/api/queue")
def list_queue():
    with _queue_lock:
        items = []
        for qi in _queue.values():
            items.append({
                "temp_id": qi["temp_id"],
                "original_name": qi["original_name"],
                "status": qi["status"],
                "position": qi.get("position", 0),
            })
    return jsonify(items)


@app.route("/api/queue/<temp_id>/stream")
def queue_stream(temp_id):
    def generate():
        last_count = 0
        while True:
            with _queue_lock:
                item = _queue.get(temp_id)
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
            if item["status"] == "error":
                yield _sse_error(item["error"] or "Error desconocido")
                return
            time.sleep(0.5)
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/queue/<temp_id>", methods=["DELETE"])
def queue_remove(temp_id):
    with _queue_lock:
        item = _queue.pop(temp_id, None)
        if item and item["status"] == "queued":
            if os.path.exists(item["temp_path"]):
                os.remove(item["temp_path"])
            return jsonify({"message": "Item eliminado de la cola"}), 200
    return jsonify({"error": "Item no encontrado o ya procesándose"}), 404


@app.route("/api/download/<video_id>", methods=["GET"])
def download(video_id):
    video = db.session.get(Video, video_id)
    if not video:
        return jsonify({"error": "Video no encontrado"}), 404
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], video.filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Archivo no encontrado en el servidor"}), 404
    return send_file(
        filepath,
        mimetype=video.mime_type or "application/octet-stream",
        as_attachment=True,
        download_name=video.original_name,
    )


@app.route("/api/delete/<video_id>", methods=["DELETE"])
def delete(video_id):
    video = db.session.get(Video, video_id)
    if not video:
        return jsonify({"error": "Video no encontrado"}), 404
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], video.filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    db.session.delete(video)
    db.session.commit()
    return jsonify({"message": "Video eliminado correctamente"}), 200


if __name__ == "__main__":
    app.run(debug=True, port=5001)
