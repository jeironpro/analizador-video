from __future__ import annotations

from datetime import UTC, datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Session(db.Model):
    __tablename__ = "sessions"
    code = db.Column(db.String(8), primary_key=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    last_active = db.Column(db.DateTime, default=lambda: datetime.now(UTC))


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
    sha256 = db.Column(db.String(64))
    duration = db.Column(db.Float)
    bitrate = db.Column(db.Integer)
    has_thumbnail = db.Column(db.Boolean, default=False)
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    session_id = db.Column(db.String(8), db.ForeignKey("sessions.code"), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "original_name": self.original_name,
            "size": self.size,
            "container": self.container,
            "mime_type": self.mime_type,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            "clamav_result": self.clamav_result,
            "sha256": self.sha256,
            "duration": self.duration,
            "bitrate": self.bitrate,
            "has_thumbnail": bool(self.has_thumbnail),
        }


class QueueItem(db.Model):
    __tablename__ = "queue_items"
    temp_id = db.Column(db.String(36), primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)
    ext = db.Column(db.String(10), nullable=False)
    temp_path = db.Column(db.String(500), nullable=False)
    temp_filename = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), default="uploaded")
    logs = db.Column(db.JSON, default=list)
    error = db.Column(db.Text)
    result = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    session_id = db.Column(db.String(8), db.ForeignKey("sessions.code"), nullable=False)
    retries = db.Column(db.Integer, default=0)
