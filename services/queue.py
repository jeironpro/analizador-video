from __future__ import annotations

import logging
import os
from typing import Any

from models import QueueItem
from services.redis_queue import get_rq_queue, redis_available

_logger = logging.getLogger(__name__)

QueueDict = dict[str, Any]


def _item_to_dict(qi: QueueItem) -> QueueDict:
    return {
        "temp_id": qi.temp_id,
        "temp_path": qi.temp_path,
        "temp_filename": qi.temp_filename,
        "original_name": qi.original_name,
        "ext": qi.ext,
        "session_code": qi.session_id,
        "status": qi.status,
        "logs": list(qi.logs) if qi.logs else [],
        "result": dict(qi.result) if qi.result else None,
        "error": qi.error,
        "retries": qi.retries or 0,
    }


class QueueManager:
    """Cola respaldada por PostgreSQL + jobs RQ en Redis.

    El estado y los logs viven en la tabla queue_items (fuente de verdad),
    compartida entre los web workers y los workers RQ.
    """

    def __init__(self, app: Any, db: Any) -> None:
        self.app = app
        self.db = db
        self._upload_folder: str = app.config["UPLOAD_FOLDER"]
        self._item_timeout: int = app.config.get("ITEM_TIMEOUT", 1200)
        self._max_retries: int = app.config.get("MAX_RETRIES", 3)

    # ------------------------------------------------------------------
    # Reads (Postgres)
    # ------------------------------------------------------------------
    def get(self, temp_id: str) -> QueueDict | None:
        qi = self.db.session.get(QueueItem, temp_id)
        return _item_to_dict(qi) if qi else None

    def list_items(self, session_code: str | None = None) -> list[dict[str, str]]:
        q = QueueItem.query
        if session_code is not None:
            q = q.filter(QueueItem.session_id == session_code)
        return [
            {"temp_id": qi.temp_id, "original_name": qi.original_name, "status": qi.status}
            for qi in q.order_by(QueueItem.created_at.desc()).all()
        ]

    def count_items(self, session_code: str) -> int:
        return QueueItem.query.filter(QueueItem.session_id == session_code).count()

    # ------------------------------------------------------------------
    # Writes (Postgres)
    # ------------------------------------------------------------------
    def add(
        self, temp_id: str, temp_path: str, temp_filename: str, original_name: str, ext: str, session_code: str
    ) -> None:
        qi = QueueItem(
            temp_id=temp_id,
            original_name=original_name,
            ext=ext,
            temp_path=temp_path,
            temp_filename=temp_filename,
            session_id=session_code,
            status="uploaded",
            logs=[],
            retries=0,
        )
        self.db.session.add(qi)
        self.db.session.commit()

    def log(self, temp_id: str, step: str, status: str, message: str) -> None:
        entry = {"step": step, "status": status, "message": message}
        try:
            qi = self.db.session.get(QueueItem, temp_id)
            if qi:
                logs = list(qi.logs) if qi.logs else []
                logs.append(entry)
                qi.logs = logs
                self.db.session.commit()
        except Exception:
            self.db.session.rollback()

    def update_status(self, temp_id: str, status: str, error: str | None = None, result: dict | None = None) -> None:
        try:
            qi = self.db.session.get(QueueItem, temp_id)
            if qi:
                qi.status = status
                if error is not None:
                    qi.error = error
                if result is not None:
                    qi.result = result
                self.db.session.commit()
        except Exception:
            self.db.session.rollback()

    def remove(self, temp_id: str) -> None:
        self._cancel_rq_job(temp_id)
        qi = self.db.session.get(QueueItem, temp_id)
        if qi and os.path.exists(qi.temp_path):
            os.remove(qi.temp_path)
        if qi:
            self.db.session.delete(qi)
            self.db.session.commit()

    def cancel(self, temp_id: str) -> None:
        entry = {"step": "cancel", "status": "error", "message": "Procesamiento cancelado por el usuario"}
        qi = self.db.session.get(QueueItem, temp_id)
        if qi and qi.status == "processing":
            qi.status = "cancelled"
            qi.error = "Cancelado por el usuario"
            logs = list(qi.logs) if qi.logs else []
            logs.append(entry)
            qi.logs = logs
            self.db.session.commit()
        self._cancel_rq_job(temp_id)

    def is_cancelled(self, temp_id: str) -> bool:
        qi = self.db.session.get(QueueItem, temp_id)
        return qi is None or qi.status != "processing"

    # ------------------------------------------------------------------
    # RQ orchestration
    # ------------------------------------------------------------------
    def enqueue(self, temp_id: str) -> None:
        from rq import Retry

        from services.pipeline import process_video

        try:
            qi = self.db.session.get(QueueItem, temp_id)
            if qi:
                qi.status = "queued"
                self.db.session.commit()
        except Exception:
            self.db.session.rollback()
        if not redis_available():
            _logger.warning("Redis no disponible; procesando %s en línea", temp_id)
            self._process_inline(temp_id)
            return
        retry = Retry(max=max(0, self._max_retries - 1), interval=[60, 120, 240])
        try:
            get_rq_queue().enqueue(
                process_video,
                temp_id,
                job_id=temp_id,
                timeout=self._item_timeout,
                retry=retry,
            )
        except Exception:
            _logger.exception("No se pudo encolar %s en RQ; procesando en línea", temp_id)
            self._process_inline(temp_id)

    def _process_inline(self, temp_id: str) -> None:
        from app import app
        from services.pipeline import process_video

        with app.app_context():
            process_video(temp_id)

    def _cancel_rq_job(self, temp_id: str) -> None:
        if not redis_available():
            return
        try:
            get_rq_queue().cancel_job(temp_id)
        except Exception:
            _logger.warning("No se pudo cancelar el job RQ %s", temp_id)

    # ------------------------------------------------------------------
    # Compatibility no-ops (la cola vive en Postgres/Redis)
    # ------------------------------------------------------------------
    def load_from_db(self) -> None:
        pass

    def start_scheduler(self) -> None:
        pass

    def shutdown(self) -> None:
        pass
