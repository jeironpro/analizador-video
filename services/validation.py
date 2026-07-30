from __future__ import annotations

import mimetypes
import os

from services.config import ALLOWED_MIMES, MAX_FILE_SIZE, MIN_FILE_SIZE

try:
    import magic
except ImportError:
    magic = None


def validate_file_size(filepath: str) -> tuple[bool, str]:
    size = os.path.getsize(filepath)
    if size < MIN_FILE_SIZE:
        return False, f"Demasiado pequeño ({size / 1024 / 1024:.1f} MB). Mínimo {MIN_FILE_SIZE // (1024 * 1024)} MB"
    if size > MAX_FILE_SIZE:
        return False, f"Demasiado grande ({size / 1024 / 1024:.1f} MB). Máximo {MAX_FILE_SIZE // (1024 * 1024)} MB"
    return True, f"{size / 1024 / 1024:.1f} MB"


def validate_mime_type(filepath: str) -> tuple[bool, str]:
    if magic is not None:
        mime = magic.from_file(filepath, mime=True)
    else:
        mime, _ = mimetypes.guess_type(filepath)
        mime = mime or "application/octet-stream"
    if mime not in ALLOWED_MIMES:
        return False, f"Tipo MIME no válido: {mime}"
    return True, mime
