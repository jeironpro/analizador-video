from __future__ import annotations

import os

MIN_FILE_SIZE = 50 * 1024 * 1024
MAX_FILE_SIZE = 500 * 1024 * 1024

MAX_CONTENT_LENGTH = MAX_FILE_SIZE

ALLOWED_EXTENSIONS: set[str] = {
    ".mp4",
    ".webm",
    ".mkv",
    ".avi",
    ".mov",
    ".mpeg",
    ".wmv",
}

ALLOWED_MIMES: set[str] = {
    "video/mp4",
    "video/webm",
    "video/x-matroska",
    "video/avi",
    "video/x-msvideo",
    "video/quicktime",
    "video/mpeg",
    "video/x-ms-wmv",
}

CLAMAV_MAX_MB = int(os.environ.get("CLAMAV_MAX_MB", "500"))

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
RQ_QUEUE = os.environ.get("RQ_QUEUE", "vidscan")

MAX_RETRIES = 3
MAX_QUEUE_ITEMS = 20
SESSION_DAYS = 7
