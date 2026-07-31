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

CLAMAV_MAX_SIZE = 200 * 1024 * 1024

# Si hay menos RAM disponible que este umbral se omite el escaneo para evitar
# un OOM-kill. Configurable via CLAMAV_MIN_MEM_MB (en MiB).
CLAMAV_MIN_MEM_MB = int(os.environ.get("CLAMAV_MIN_MEM_MB", "200"))
CLAMAV_MIN_MEM_BYTES = CLAMAV_MIN_MEM_MB * 1024 * 1024

MAX_RETRIES = 3
MAX_QUEUE_ITEMS = 20
SESSION_DAYS = 7
ITEM_TIMEOUT = 600
