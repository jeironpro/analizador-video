from __future__ import annotations

import logging
import os

from rq import Worker

from services.config import RQ_QUEUE
from services.redis_queue import get_redis

_logger = logging.getLogger(__name__)


def _start_cleanup_once() -> None:
    from app import app, cleanup

    if os.environ.get("RUN_CLEANUP", "1") != "1":
        return
    try:
        cleanup.start()
        _logger.info("CleanupDaemon iniciado en el worker")
    except Exception:
        _logger.exception("No se pudo iniciar CleanupDaemon")


def _handle_failure(job: object, exc_type: type, exc_value: BaseException, traceback: object) -> None:
    try:
        from app import app, queue

        with app.app_context():
            msg = str(exc_value) or "Error de procesamiento"
            queue.update_status(job.id, "error", error=msg)
            item = queue.get(job.id)
            if item and item.get("temp_path") and os.path.exists(item["temp_path"]):
                os.remove(item["temp_path"])
            _logger.warning("Job %s falló: %s", job.id, msg)
    except Exception:
        _logger.exception("Error marcando job %s como error", job.id)


def main() -> None:
    _start_cleanup_once()
    worker = Worker([RQ_QUEUE], connection=get_redis())
    worker.push_exc_handler(_handle_failure)
    worker.work()


if __name__ == "__main__":
    main()
