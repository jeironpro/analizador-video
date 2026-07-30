from __future__ import annotations

import os
import shutil
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any


class CleanupDaemon:
    def __init__(self, app: Any, db: Any, days: int = 7) -> None:
        self.app = app
        self.db = db
        self.days = days

    def start(self) -> None:
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self) -> None:
        while True:
            time.sleep(3600)
            try:
                self._cleanup()
            except Exception:
                pass

    def _cleanup(self) -> None:
        from models import Session

        cutoff = datetime.now(UTC) - timedelta(days=self.days)
        with self.app.app_context():
            expired = Session.query.filter(Session.last_active < cutoff).all()
            for sess in expired:
                folder = os.path.join(self.app.config["UPLOAD_FOLDER"], sess.code)
                if os.path.exists(folder):
                    shutil.rmtree(folder)
                self.db.session.delete(sess)
            self.db.session.commit()
