from __future__ import annotations

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

CLAMAV_MAX_SIZE = 200 * 1024 * 1024

MAX_RETRIES = 3
MAX_QUEUE_ITEMS = 20
SESSION_DAYS = 7
ITEM_TIMEOUT = 600
