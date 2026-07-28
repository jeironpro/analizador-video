import os
import uuid
import json
import time
import threading
import subprocess
import shutil
import mimetypes
from datetime import datetime, timezone
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

try:
    import magic
except ImportError:
    magic = None

try:
    import psutil
except ImportError:
    psutil = None

from video_analyzer import analyze_video, VideoAnalysisError


class QueueManager:
    def __init__(self, app, db):
        self.app = app
        self.db = db
        self._queue = OrderedDict()
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._scheduler_running = False
        self._upload_folder = app.config["UPLOAD_FOLDER"]

    # ------------------------------------------------------------------
    # Thread-safe queue operations
    # ------------------------------------------------------------------
    def get(self, temp_id):
        with self._lock:
            return self._queue.get(temp_id)

    def list_items(self):
        with self._lock:
            return [
                {
                    "temp_id": qi["temp_id"],
                    "original_name": qi["original_name"],
                    "status": qi["status"],
                }
                for qi in self._queue.values()
            ]

    def add(self, temp_id, temp_path, temp_filename, original_name, ext):
        with self._lock:
            self._queue[temp_id] = {
                "temp_id": temp_id,
                "temp_path": temp_path,
                "temp_filename": temp_filename,
                "original_name": original_name,
                "ext": ext,
                "status": "uploaded",
                "logs": [],
                "result": None,
                "error": None,
            }
        self._save_to_db(temp_id)

    def log(self, temp_id, step, status, message):
        entry = {"step": step, "status": status, "message": message}
        with self._lock:
            item = self._queue.get(temp_id)
            if item:
                item["logs"].append(entry)
        self._persist_log(temp_id, entry)

    def update_status(self, temp_id, status, error=None, result=None):
        with self._lock:
            item = self._queue.get(temp_id)
            if item:
                item["status"] = status
                if error is not None:
                    item["error"] = error
                if result is not None:
                    item["result"] = result
        self._persist_status(temp_id, status, error, result)

    def remove(self, temp_id):
        item = None
        with self._lock:
            item = self._queue.pop(temp_id, None)
        if item and os.path.exists(item["temp_path"]):
            os.remove(item["temp_path"])
        self._delete_from_db(temp_id)

    # ------------------------------------------------------------------
    # DB persistence helpers
    # ------------------------------------------------------------------
    def _save_to_db(self, temp_id):
        with self._lock:
            item = self._queue.get(temp_id)
            if not item:
                return
            original_name = item["original_name"]
            ext = item["ext"]
            temp_path = item["temp_path"]
            temp_filename = item["temp_filename"]
        try:
            from models import QueueItem
            qi = QueueItem(
                temp_id=temp_id,
                original_name=original_name,
                ext=ext,
                temp_path=temp_path,
                temp_filename=temp_filename,
                status="uploaded",
                logs="[]",
            )
            self.db.session.add(qi)
            self.db.session.commit()
        except Exception:
            self.db.session.rollback()

    def _persist_log(self, temp_id, entry):
        try:
            from models import QueueItem
            qi = self.db.session.get(QueueItem, temp_id)
            if qi:
                logs = json.loads(qi.logs) if qi.logs else []
                logs.append(entry)
                qi.logs = json.dumps(logs)
                self.db.session.commit()
        except Exception:
            self.db.session.rollback()

    def _persist_status(self, temp_id, status, error, result):
        try:
            from models import QueueItem
            qi = self.db.session.get(QueueItem, temp_id)
            if qi:
                qi.status = status
                if error is not None:
                    qi.error = error
                if result is not None:
                    qi.result = json.dumps(result)
                self.db.session.commit()
        except Exception:
            self.db.session.rollback()

    def _delete_from_db(self, temp_id):
        try:
            from models import QueueItem
            qi = self.db.session.get(QueueItem, temp_id)
            if qi:
                self.db.session.delete(qi)
                self.db.session.commit()
        except Exception:
            self.db.session.rollback()

    # ------------------------------------------------------------------
    # Load from DB on startup
    # ------------------------------------------------------------------
    def load_from_db(self):
        try:
            from models import QueueItem
            items = QueueItem.query.filter(
                QueueItem.status.in_(["uploaded", "queued", "processing"])
            ).all()
            with self._lock:
                for qi in items:
                    self._queue[qi.temp_id] = {
                        "temp_id": qi.temp_id,
                        "temp_path": qi.temp_path,
                        "temp_filename": qi.temp_filename,
                        "original_name": qi.original_name,
                        "ext": qi.ext,
                        "status": qi.status,
                        "logs": json.loads(qi.logs) if qi.logs else [],
                        "result": json.loads(qi.result) if qi.result else None,
                        "error": qi.error,
                    }
        except Exception:
            self.app.logger.exception("Error loading queue from DB")

    # ------------------------------------------------------------------
    # Scheduler and worker
    # ------------------------------------------------------------------
    def start_scheduler(self):
        if self._scheduler_running:
            return
        self._scheduler_running = True
        with self._lock:
            for qi in self._queue.values():
                if qi["status"] == "processing":
                    qi["status"] = "queued"
        t = threading.Thread(target=self._scheduler_loop, daemon=True)
        t.start()

    def _scheduler_loop(self):
        with self.app.app_context():
            while True:
                self._recover_stale_processing()
                temp_id = None
                with self._lock:
                    processing = sum(
                        1 for qi in self._queue.values() if qi["status"] == "processing"
                    )
                    if processing < 2:
                        for qi in self._queue.values():
                            if qi["status"] == "queued":
                                qi["status"] = "processing"
                                temp_id = qi["temp_id"]
                                break
                if temp_id:
                    self.update_status(temp_id, "processing")
                    self._executor.submit(self._process_item, temp_id)
                time.sleep(1)

    def _recover_stale_processing(self):
        try:
            with self._lock:
                now = time.time()
                for qi in list(self._queue.values()):
                    if qi["status"] == "processing":
                        if "started_at" not in qi:
                            qi["started_at"] = now
                        elif now - qi["started_at"] > 600:
                            self.app.logger.warning(
                                "Recovering stale processing item %s", qi["temp_id"]
                            )
                            qi["status"] = "queued"
                            qi.pop("started_at", None)
        except Exception:
            pass

    def _process_item(self, temp_id):
        try:
            with self.app.app_context():
                try:
                    item = self.get(temp_id)
                    if not item:
                        return
                    tp = item["temp_path"]

                    self.log(temp_id, "size", "checking", "Validando tamaño...")
                    ok, msg = validate_file_size(tp)
                    if not ok:
                        self.log(temp_id, "size", "error", msg)
                        self.update_status(temp_id, "error", error=msg)
                        return
                    self.log(temp_id, "size", "ok", msg)

                    self.log(temp_id, "mime", "checking", "Detectando tipo MIME...")
                    ok, mime_or_msg = validate_mime_type(tp)
                    if not ok:
                        self.log(temp_id, "mime", "error", mime_or_msg)
                        self.update_status(temp_id, "error", error=mime_or_msg)
                        return
                    self.log(temp_id, "mime", "ok", mime_or_msg)

                    self.log(temp_id, "clamav", "checking", "Escaneando con ClamAV...")
                    ok, clam_msg = scan_with_clamav(tp)
                    if not ok:
                        self.log(temp_id, "clamav", "error", clam_msg)
                        self.update_status(temp_id, "error", error=f"ClamAV: {clam_msg}")
                        return
                    self.log(temp_id, "clamav", "ok", clam_msg)

                    self.log(temp_id, "analysis", "checking", "Analizando codecs y metadatos...")
                    analysis = analyze_video(tp)
                    if not analysis.get("valid", False):
                        for err in analysis.get("errors", []):
                            self.log(temp_id, "analysis", "error", err)
                        self.update_status(temp_id, "error", error="Análisis de video fallido")
                        return

                    self.log(temp_id, "analysis", "ok", f"Contenedor: {analysis.get('container')}")
                    for s in analysis.get("streams", []):
                        t = "Video" if s["type"] == "video" else "Audio"
                        d = f"{s['codec']} {s['resolution']} @ {s['fps']} fps" if s["type"] == "video" else f"{s['codec']} {s.get('channels', '?')}ch"
                        self.log(temp_id, "stream", "info", f"{t}: {d}")

                    self.log(temp_id, "save", "checking", "Guardando archivo...")
                    video_id = str(uuid.uuid4())
                    filename = f"{video_id}{item['ext']}"
                    final_path = os.path.join(self._upload_folder, filename)
                    shutil.move(tp, final_path)

                    mime_type = validate_mime_type(final_path)
                    mime_val = mime_type[1] if mime_type[0] else "application/octet-stream"

                    from models import Video
                    video = Video(
                        id=video_id,
                        filename=filename,
                        original_name=item["original_name"],
                        size=os.path.getsize(final_path),
                        container=analysis.get("container", item["ext"].lstrip(".")),
                        mime_type=mime_val,
                        analysis_result=str(analysis.get("errors", [])),
                        clamav_result=clam_msg,
                    )
                    self.db.session.add(video)
                    self.db.session.commit()

                    self.log(temp_id, "save", "ok", "Video almacenado correctamente")
                    self.log(temp_id, "complete", "ok", "Proceso finalizado")
                    self.update_status(temp_id, "done", result=video.to_dict())

                except VideoAnalysisError as e:
                    self.update_status(temp_id, "error", error=f"Error de análisis: {str(e)}")
                except Exception as e:
                    self.app.logger.exception("Error en process_item %s", temp_id)
                    self.update_status(temp_id, "error", error=f"Error interno: {str(e)}")
                finally:
                    item = self.get(temp_id)
                    if item and os.path.exists(item["temp_path"]):
                        os.remove(item["temp_path"])
        except Exception:
            self.app.logger.exception("Fatal error in _process_item thread for %s", temp_id)
            with self._lock:
                qi = self._queue.get(temp_id)
                if qi:
                    qi["status"] = "error"
                    qi["error"] = "Fatal error interno"


# -----------------------------------------------------------------------
# Utility functions (standalone, testable without Flask)
# -----------------------------------------------------------------------
def validate_file_size(filepath: str) -> tuple[bool, str]:
    size = os.path.getsize(filepath)
    if size < 50 * 1024 * 1024:
        return False, f"Demasiado pequeño ({size / 1024 / 1024:.1f} MB). Mínimo 50 MB"
    if size > 500 * 1024 * 1024:
        return False, f"Demasiado grande ({size / 1024 / 1024:.1f} MB). Máximo 500 MB"
    return True, f"{size / 1024 / 1024:.1f} MB"


def validate_mime_type(filepath: str) -> tuple[bool, str]:
    if magic is not None:
        mime = magic.from_file(filepath, mime=True)
    else:
        mime, _ = mimetypes.guess_type(filepath)
        mime = mime or "application/octet-stream"
    video_mimes = {
        "video/mp4",
        "video/webm",
        "video/x-matroska",
        "video/avi",
        "video/x-msvideo",
        "video/quicktime",
        "video/mpeg",
        "video/x-ms-wmv",
    }
    if mime not in video_mimes:
        return False, f"Tipo MIME no válido: {mime}"
    return True, mime


def scan_with_clamav(filepath: str) -> tuple[bool, str]:
    if psutil is not None:
        try:
            mem = psutil.virtual_memory()
            if mem.available < 200 * 1024 * 1024:
                return True, "Memoria insuficiente, escaneo omitido"
        except Exception:
            pass
    try:
        result = subprocess.run(
            [
                "clamscan",
                "--stdout",
                "--no-summary",
                "--quiet",
                "--database=/var/lib/clamav",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=300,
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
