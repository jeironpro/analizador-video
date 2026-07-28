import os
import uuid
import json
import time
from datetime import datetime, timezone

from flask import (
    Flask, request, render_template, jsonify, send_file, Response, stream_with_context
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect as sa_inspect
from werkzeug.utils import secure_filename

from models import db, Video
from services.queue import QueueManager, validate_file_size, validate_mime_type

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

db.init_app(app)

queue = QueueManager(app, db)

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
    if inspector.has_table("video"):
        cols = {c["name"] for c in inspector.get_columns("video")}
        video_cols = {
            "id": "VARCHAR(36)", "filename": "VARCHAR(255)", "original_name": "VARCHAR(255)",
            "size": "INTEGER", "container": "VARCHAR(20)", "mime_type": "VARCHAR(100)",
            "analysis_result": "TEXT", "clamav_result": "VARCHAR(50)", "uploaded_at": "TIMESTAMP",
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


_init_db()


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------
def _sse_step(step, status, message):
    return f"event: step\ndata: {json.dumps({'step': step, 'status': status, 'message': message})}\n\n"


def _sse_complete(data):
    return f"event: complete\ndata: {json.dumps(data)}\n\n"


def _sse_error(message):
    return f"event: error\ndata: {json.dumps({'message': message})}\n\n"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
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
    queue.add(temp_id, temp_path, temp_filename, safe_name, ext)
    return jsonify({"temp_id": temp_id, "original_name": safe_name, "temp_filename": temp_filename}), 201


@app.route("/api/queue")
def list_queue():
    return jsonify(queue.list_items())


@app.route("/api/queue/events")
def queue_events():
    def generate():
        last_state = None
        while True:
            items = queue.list_items()
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
def queue_process(temp_id):
    item = queue.get(temp_id)
    if not item:
        return jsonify({"error": "Item no encontrado"}), 404
    if item["status"] != "uploaded":
        return jsonify({"error": f"El item está en estado: {item['status']}"}), 400
    queue.update_status(temp_id, "queued")
    queue.start_scheduler()
    return jsonify({"message": "Item agregado a la cola de procesamiento"}), 200


@app.route("/api/queue/<temp_id>/stream")
def queue_stream(temp_id):
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
    item = queue.get(temp_id)
    if not item:
        return jsonify({"error": "Item no encontrado"}), 404
    if item["status"] == "processing":
        return jsonify({"error": f"No se puede eliminar un item en estado: {item['status']}"}), 400
    queue.remove(temp_id)
    return jsonify({"message": "Item eliminado de la cola"}), 200


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


# Load queue from DB on startup
with app.app_context():
    queue.load_from_db()

if __name__ == "__main__":
    app.run(debug=True, port=5001)
