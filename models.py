from __future__ import annotations

from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Session(db.Model):
    __tablename__ = "sessions"
    code = db.Column(db.String(8), primary_key=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_active = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Video(db.Model):
    __tablename__ = "video"
    id = db.Column(db.String(36), primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    container = db.Column(db.String(20))
    mime_type = db.Column(db.String(100))
    analysis_result = db.Column(db.Text)
    clamav_result = db.Column(db.String(50))
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    session_id = db.Column(db.String(8), nullable=False, default='LEGACY01')

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "original_name": self.original_name,
            "size": self.size,
            "container": self.container,
            "mime_type": self.mime_type,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
        }


class QueueItem(db.Model):
    __tablename__ = "queue_items"
    temp_id = db.Column(db.String(36), primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)
    ext = db.Column(db.String(10), nullable=False)
    temp_path = db.Column(db.String(500), nullable=False)
    temp_filename = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), default="uploaded")
    logs = db.Column(db.Text, default="[]")
    error = db.Column(db.Text)
    result = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    session_id = db.Column(db.String(8), nullable=False, default='LEGACY01')
    retries = db.Column(db.Integer, default=0)
