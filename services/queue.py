from __future__ import annotations

import json
import logging
import mimetypes
import os
import resource
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

_logger = logging.getLogger(__name__)

try:
    import magic
except ImportError:
    magic = None

try:
    import psutil
except ImportError:
    psutil = None

from video_analyzer import VideoAnalysisError, analyze_video  # noqa: E402


def _read_cgroup_mem(path: str) -> int | None:
    try:
        with open(path) as f:
            raw = f.read().strip()
            val = int(raw)
            if val > 0 and val < 2**62:
                return val
    except Exception:
        pass
    return None


def _get_container_memory_total() -> int | None:
    """Return total system memory in bytes, respecting cgroup limits when in a container."""  # noqa: E501
    # Try cgroup v2 via /proc/self/cgroup to find the actual container path
    try:
        with open("/proc/self/cgroup") as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) == 3 and "memory" in parts[1]:
                    cgroup_path = parts[2].lstrip("/")
                    for base in ("/sys/fs/cgroup",):
                        mem_max = _read_cgroup_mem(os.path.join(base, cgroup_path, "memory.max"))
                        if mem_max is not None:
                            return mem_max
    except Exception:
        pass
    # Try root cgroup v1 and v2 paths directly
    for path in ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory.max"):
        val = _read_cgroup_mem(path)
        if val is not None:
            return val
    if psutil is not None:
        try:
            return psutil.virtual_memory().total
        except Exception:
            pass
    return None


QueueDict = dict[str, Any]


class QueueManager:
    def __init__(self, app: Any, db: Any) -> None:
        self.app = app
        self.db = db
        self._queue: OrderedDict[str, QueueDict] = OrderedDict()
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1)

        self._scheduler_running = False
        self._upload_folder: str = app.config["UPLOAD_FOLDER"]
        self._item_timeout: int = app.config.get("ITEM_TIMEOUT", 600)
        self._max_retries: int = app.config.get("MAX_RETRIES", 3)
        self._shutdown = False

    # ------------------------------------------------------------------
    # Thread-safe queue operations
    # ------------------------------------------------------------------
    def get(self, temp_id: str) -> QueueDict | None:
        with self._lock:
            return self._queue.get(temp_id)

    def list_items(self, session_code: str | None = None) -> list[dict[str, str]]:
        with self._lock:
            return [
                {
                    "temp_id": qi["temp_id"],
                    "original_name": qi["original_name"],
                    "status": qi["status"],
                }
                for qi in self._queue.values()
                if session_code is None or qi.get("session_code") == session_code
            ]

    def count_items(self, session_code: str) -> int:
        with self._lock:
            return sum(1 for qi in self._queue.values() if qi.get("session_code") == session_code)

    def add(
        self, temp_id: str, temp_path: str, temp_filename: str, original_name: str, ext: str, session_code: str
    ) -> None:
        with self._lock:
            self._queue[temp_id] = {
                "temp_id": temp_id,
                "temp_path": temp_path,
                "temp_filename": temp_filename,
                "original_name": original_name,
                "ext": ext,
                "session_code": session_code,
                "status": "uploaded",
                "logs": [],
                "result": None,
                "error": None,
                "retries": 0,
            }
        self._save_to_db(temp_id)

    def log(self, temp_id: str, step: str, status: str, message: str) -> None:
        entry = {"step": step, "status": status, "message": message}
        with self._lock:
            item = self._queue.get(temp_id)
            if item:
                item["logs"].append(entry)
        self._persist_log(temp_id, entry)

    def update_status(self, temp_id: str, status: str, error: str | None = None, result: dict | None = None) -> None:
        with self._lock:
            item = self._queue.get(temp_id)
            if item:
                item["status"] = status
                if error is not None:
                    item["error"] = error
                if result is not None:
                    item["result"] = result
        self._persist_status(temp_id, status, error, result)

    def remove(self, temp_id: str) -> None:
        item = None
        with self._lock:
            item = self._queue.pop(temp_id, None)
        if item and os.path.exists(item["temp_path"]):
            os.remove(item["temp_path"])
        self._delete_from_db(temp_id)

    def cancel(self, temp_id: str) -> None:
        entry = {"step": "cancel", "status": "error", "message": "Procesamiento cancelado por el usuario"}
        with self._lock:
            item = self._queue.get(temp_id)
            if item and item["status"] == "processing":
                item["status"] = "cancelled"
                item["error"] = "Cancelado por el usuario"
                item["logs"].append(entry)
        self._persist_status(temp_id, "cancelled", "Cancelado por el usuario", None)
        self._persist_log(temp_id, entry)

    def _is_cancelled(self, temp_id: str) -> bool:
        with self._lock:
            item = self._queue.get(temp_id)
            return item is None or item["status"] != "processing"

    # ------------------------------------------------------------------
    # DB persistence helpers
    # ------------------------------------------------------------------
    def _save_to_db(self, temp_id: str) -> None:
        with self._lock:
            item = self._queue.get(temp_id)
            if not item:
                return
            original_name = item["original_name"]
            ext = item["ext"]
            temp_path = item["temp_path"]
            temp_filename = item["temp_filename"]
            session_code = item.get("session_code", "LEGACY01")
        try:
            from models import QueueItem

            qi = QueueItem(
                temp_id=temp_id,
                original_name=original_name,
                ext=ext,
                temp_path=temp_path,
                temp_filename=temp_filename,
                session_id=session_code,
                status="uploaded",
                logs="[]",
                retries=0,
            )
            self.db.session.add(qi)
            self.db.session.commit()
        except Exception:
            self.db.session.rollback()

    def _persist_log(self, temp_id: str, entry: dict) -> None:
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

    def _persist_status(self, temp_id: str, status: str, error: str | None, result: dict | None) -> None:
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

    def _delete_from_db(self, temp_id: str) -> None:
        try:
            from models import QueueItem

            qi = self.db.session.get(QueueItem, temp_id)
            if qi:
                self.db.session.delete(qi)
                self.db.session.commit()
        except Exception:
            self.db.session.rollback()

    def _persist_retries(self, temp_id: str, retries: int) -> None:
        try:
            from models import QueueItem

            qi = self.db.session.get(QueueItem, temp_id)
            if qi:
                qi.retries = retries
                self.db.session.commit()
        except Exception:
            self.db.session.rollback()

    def _fail_or_retry(self, temp_id: str, error_msg: str) -> None:
        retries = 0
        with self._lock:
            item = self._queue.get(temp_id)
            if not item:
                return
            item["retries"] = item.get("retries", 0) + 1
            retries = item["retries"]
            if retries < self._max_retries:
                item["error"] = error_msg
                item["status"] = "queued"
                self.app.logger.warning(
                    "Item %s failed (attempt %d/%d), retrying: %s",
                    temp_id,
                    retries,
                    self._max_retries,
                    error_msg,
                )
            else:
                item["status"] = "error"
                item["error"] = error_msg
        if retries < self._max_retries:
            self._persist_status(temp_id, "queued", error=error_msg, result=None)
        else:
            self._persist_status(temp_id, "error", error=error_msg, result=None)
        self._persist_retries(temp_id, retries)

    # ------------------------------------------------------------------
    # Load from DB on startup
    # ------------------------------------------------------------------
    def load_from_db(self) -> None:
        try:
            from models import QueueItem

            items = QueueItem.query.filter(QueueItem.status.in_(["uploaded", "queued", "processing"])).all()
            with self._lock:
                for qi in items:
                    self._queue[qi.temp_id] = {
                        "temp_id": qi.temp_id,
                        "temp_path": qi.temp_path,
                        "temp_filename": qi.temp_filename,
                        "original_name": qi.original_name,
                        "ext": qi.ext,
                        "session_code": qi.session_id,
                        "status": qi.status,
                        "logs": json.loads(qi.logs) if qi.logs else [],
                        "result": json.loads(qi.result) if qi.result else None,
                        "error": qi.error,
                        "retries": qi.retries or 0,
                    }
        except Exception:
            self.app.logger.exception("Error loading queue from DB")

    # ------------------------------------------------------------------
    # Scheduler and worker
    # ------------------------------------------------------------------
    def start_scheduler(self) -> None:
        if self._scheduler_running:
            return
        self._scheduler_running = True
        with self._lock:
            for qi in self._queue.values():
                if qi["status"] == "processing":
                    qi["status"] = "queued"
        t = threading.Thread(target=self._scheduler_loop, daemon=True)
        t.start()

    def _scheduler_loop(self) -> None:
        with self.app.app_context():
            while not self._shutdown:
                self._recover_stale_processing()
                temp_id = None
                with self._lock:
                    processing = sum(1 for qi in self._queue.values() if qi["status"] == "processing")
                    if processing < 1:
                        for qi in self._queue.values():
                            if qi["status"] == "queued":
                                qi["status"] = "processing"
                                temp_id = qi["temp_id"]
                                break
                if temp_id:
                    self.update_status(temp_id, "processing")
                    self._executor.submit(self._process_item, temp_id)
                time.sleep(1)
        self._shutdown_cleanup()

    def _shutdown_cleanup(self) -> None:
        self._executor.shutdown(wait=True)
        self.app.logger.info("Graceful shutdown: resetting processing items to queued")
        with self._lock:
            for qi in self._queue.values():
                if qi["status"] == "processing":
                    qi["status"] = "queued"
                    qi.pop("started_at", None)
        for qi in list(self._queue.values()):
            if qi["status"] in ("processing",):
                self._persist_status(qi["temp_id"], "queued")

    def shutdown(self) -> None:
        self._shutdown = True

    def _recover_stale_processing(self) -> None:
        try:
            with self._lock:
                now = time.time()
                for qi in list(self._queue.values()):
                    if qi["status"] == "processing":
                        if "started_at" not in qi:
                            qi["started_at"] = now
                        elif now - qi["started_at"] > self._item_timeout:
                            self.app.logger.warning("Recovering stale processing item %s", qi["temp_id"])
                            qi["status"] = "queued"
                            qi.pop("started_at", None)
        except Exception:
            pass

    def _process_item(self, temp_id: str) -> None:
        try:
            with self.app.app_context():
                try:
                    item = self.get(temp_id)
                    if not item:
                        return
                    tp = item["temp_path"]

                    if not os.path.exists(tp):
                        self.log(temp_id, "size", "error", "Archivo temporal no encontrado")
                        self.update_status(temp_id, "error", error="Archivo temporal no encontrado")
                        return

                    self.log(temp_id, "size", "checking", "Validando tamaño...")
                    if self._is_cancelled(temp_id):
                        return
                    ok, msg = validate_file_size(tp)
                    if not ok:
                        self.log(temp_id, "size", "error", msg)
                        self._fail_or_retry(temp_id, msg)
                        return
                    self.log(temp_id, "size", "ok", msg)

                    self.log(temp_id, "mime", "checking", "Detectando tipo MIME...")
                    if self._is_cancelled(temp_id):
                        return
                    ok, mime_or_msg = validate_mime_type(tp)
                    if not ok:
                        self.log(temp_id, "mime", "error", mime_or_msg)
                        self._fail_or_retry(temp_id, mime_or_msg)
                        return
                    self.log(temp_id, "mime", "ok", mime_or_msg)

                    self.log(temp_id, "clamav", "checking", "Escaneando con ClamAV...")
                    if self._is_cancelled(temp_id):
                        return
                    ok, clam_msg = scan_with_clamav(tp)
                    if not ok:
                        self.log(temp_id, "clamav", "error", clam_msg)
                        self._fail_or_retry(temp_id, clam_msg)
                        return
                    self.log(temp_id, "clamav", "ok", clam_msg)

                    self.log(temp_id, "analysis", "checking", "Analizando codecs y metadatos...")
                    if self._is_cancelled(temp_id):
                        return
                    analysis = analyze_video(tp)
                    if not analysis.get("valid", False):
                        for err in analysis.get("errors", []):
                            self.log(temp_id, "analysis", "error", err)
                        self.update_status(temp_id, "error", error="Análisis de video fallido")
                        self._fail_or_retry(temp_id, "Análisis de video fallido")
                        return

                    self.log(temp_id, "analysis", "ok", f"Contenedor: {analysis.get('container')}")
                    for s in analysis.get("streams", []):
                        t = "Video" if s["type"] == "video" else "Audio"
                        d = (
                            f"{s['codec']} {s['resolution']} @ {s['fps']} fps"
                            if s["type"] == "video"
                            else f"{s['codec']} {s.get('channels', '?')}ch"
                        )
                        self.log(temp_id, "stream", "info", f"{t}: {d}")

                    self.log(temp_id, "save", "checking", "Guardando archivo...")
                    video_id = str(uuid.uuid4())
                    filename = f"{video_id}{item['ext']}"
                    session_dir = os.path.join(self._upload_folder, item["session_code"])
                    os.makedirs(session_dir, exist_ok=True)
                    final_path = os.path.join(session_dir, filename)
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
                        session_id=item["session_code"],
                    )
                    self.db.session.add(video)
                    self.db.session.commit()

                    self.log(temp_id, "save", "ok", "Video almacenado correctamente")
                    self.log(temp_id, "complete", "ok", "Proceso finalizado")
                    self.update_status(temp_id, "done", result=video.to_dict())

                except VideoAnalysisError as e:
                    self._fail_or_retry(temp_id, f"Error de análisis: {str(e)}")
                except Exception as e:
                    self.app.logger.exception("Error en process_item %s", temp_id)
                    self._fail_or_retry(temp_id, f"Error interno: {str(e)}")
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


def _limit_clamav_memory() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (380 * 1024 * 1024, 380 * 1024 * 1024))


def scan_with_clamav(filepath: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [
                "clamscan",
                "--stdout",
                "--no-summary",
                "--quiet",
                "--database=/var/lib/clamav",
                "--max-filesize=200M",
                "--max-scansize=200M",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=300,
            preexec_fn=_limit_clamav_memory,
        )
        if result.returncode == 0:
            return True, "Archivo limpio"
        if result.returncode == 1:
            return False, f"Virus detectado: {result.stdout.strip()}"
        if result.returncode < 0:
            return True, "Escaneo omitido por límite de memoria"
        stderr = result.stderr.strip()
        if stderr:
            _logger.error("ClamAV error en %s (código %s): %s", filepath, result.returncode, stderr)
        return True, f"ClamAV no disponible (código {result.returncode})"
    except FileNotFoundError:
        return True, "ClamAV no está instalado en el servidor"
    except subprocess.TimeoutExpired:
        return True, "Escaneo excedió el tiempo límite"
    except Exception as e:
        _logger.exception("ClamAV exception al escanear %s", filepath)
        return True, f"ClamAV: error inesperado ({e})"
