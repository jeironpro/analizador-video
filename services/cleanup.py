import os
import shutil
import time
import threading
from datetime import datetime, timezone, timedelta


class CleanupDaemon:
    def __init__(self, app, db, days=7):
        self.app = app
        self.db = db
        self.days = days

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while True:
            time.sleep(3600)
            try:
                self._cleanup()
            except Exception:
                pass

    def _cleanup(self):
        from models import Session

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days)
        with self.app.app_context():
            expired = Session.query.filter(Session.last_active < cutoff).all()
            for sess in expired:
                folder = os.path.join(self.app.config["UPLOAD_FOLDER"], sess.code)
                if os.path.exists(folder):
                    shutil.rmtree(folder)
                self.db.session.delete(sess)
            self.db.session.commit()
