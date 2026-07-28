import os
import uuid
import mimetypes
from datetime import datetime, timezone
from flask import Flask, request, render_template, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect as sa_inspect
from werkzeug.utils import secure_filename
from video_analyzer import analyze_video, VideoAnalysisError

app = Flask(__name__)

database_url = os.environ.get("DATABASE_URL", "sqlite:///videos.db")
if database_url.startswith("postgres") and "sslmode" not in database_url:
    database_url += "?sslmode=require" if "?" not in database_url else "&sslmode=require"
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}
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
        "id": "VARCHAR(36)",
        "filename": "VARCHAR(255)",
        "original_name": "VARCHAR(255)",
        "size": "INTEGER",
        "container": "VARCHAR(20)",
        "mime_type": "VARCHAR(100)",
        "analysis_result": "TEXT",
        "clamav_result": "VARCHAR(50)",
        "uploaded_at": "TIMESTAMP",
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


def scan_with_clamav(filepath: str) -> tuple[bool, str]:
    try:
        import pyclamd
        try:
            cd = pyclamd.ClamdAgnostic()
            cd.ping()
        except Exception:
            return True, "ClamAV no está disponible en el sistema"
        result = cd.scan_file(filepath)
        if result is None:
            return True, "Archivo limpio"
        for r in result:
            if isinstance(r, tuple) and len(r) >= 2:
                return False, f"Virus detectado: {r[1]}"
        return True, "Archivo limpio"
    except ImportError:
        return True, "pyclamd no está instalado"
    except Exception as e:
        return True, f"Escaneo no disponible: {str(e)}"


def validate_file_size(filepath: str) -> tuple[bool, str]:
    size = os.path.getsize(filepath)
    if size < 50 * 1024 * 1024:
        return False, f"El archivo es demasiado pequeño ({size / 1024 / 1024:.1f} MB). Mínimo 50 MB"
    if size > 500 * 1024 * 1024:
        return False, f"El archivo es demasiado grande ({size / 1024 / 1024:.1f} MB). Máximo 200 MB"
    return True, "OK"


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

    try:
        valid_size, msg = validate_file_size(temp_path)
        if not valid_size:
            return jsonify({"error": msg}), 400

        valid_mime, mime_or_msg = validate_mime_type(temp_path)
        if not valid_mime:
            return jsonify({"error": mime_or_msg}), 400
        mime_type = mime_or_msg

        clam_ok, clam_msg = scan_with_clamav(temp_path)
        if not clam_ok:
            return jsonify({"error": f"ClamAV: {clam_msg}"}), 400

        analysis = analyze_video(temp_path)

        if not analysis.get("valid", False):
            errors = analysis.get("errors", [])
            return jsonify({
                "error": "Análisis de video fallido",
                "details": errors
            }), 400

        video_id = str(uuid.uuid4())
        filename = f"{video_id}{ext}"
        final_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

        import shutil
        shutil.move(temp_path, final_path)

        video = Video(
            id=video_id,
            filename=filename,
            original_name=safe_name,
            size=os.path.getsize(final_path),
            container=analysis.get("container", ext.lstrip(".")),
            mime_type=mime_type,
            analysis_result=str(analysis.get("errors", [])),
            clamav_result=clam_msg,
        )

        db.session.add(video)
        db.session.commit()

        return jsonify({
            "message": "Video subido y analizado correctamente",
            "video": video.to_dict(),
            "analysis": {
                "container": analysis.get("container"),
                "streams": analysis.get("streams", []),
            },
            "clamav": clam_msg,
        }), 201

    except VideoAnalysisError as e:
        return jsonify({"error": f"Error de análisis: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"Error interno: {str(e)}"}), 500
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


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
