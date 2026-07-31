from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

_logger = logging.getLogger(__name__)

THUMBNAIL_WIDTH = 640
THUMBNAIL_QUALITY = 6
THUMBNAIL_EXT = "jpg"


def thumbnail_path(upload_folder: str, session_code: str, video_id: str) -> str:
    return os.path.join(upload_folder, session_code, f"{video_id}.{THUMBNAIL_EXT}")


def generate_thumbnail(filepath: str, output_path: str, duration: float | None = None) -> bool:
    """Extrae un frame del video y lo guarda como JPEG. Devuelve True si tuvo éxito."""
    if not os.path.exists(filepath):
        return False
    timestamp = _pick_timestamp(duration)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp_path = output_path + ".tmp"
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        filepath,
        "-frames:v",
        "1",
        "-vf",
        f"scale={THUMBNAIL_WIDTH}:-2",
        "-q:v",
        str(THUMBNAIL_QUALITY),
        "-f",
        "image2",
        tmp_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            _logger.warning("ffmpeg thumbnail falló para %s: %s", filepath, result.stderr.strip()[-200:])
            return False
        os.replace(tmp_path, output_path)
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        _logger.warning("ffmpeg thumbnail error para %s: %s", filepath, e)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False
    except Exception:
        _logger.exception("Error generando thumbnail de %s", filepath)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False


def _pick_timestamp(duration: float | None) -> float:
    if not duration or duration <= 0:
        return 1.0
    return round(min(1.0, max(0.0, duration * 0.1)), 2)


# ---------------------------------------------------------------------------
# ffmpeg inspection helpers
# ---------------------------------------------------------------------------
def ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def video_dimensions(filepath: str) -> tuple[int, int] | None:
    """Devuelve (ancho, alto) del primer stream de video usando ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        import json

        stream = json.loads(result.stdout).get("streams", [{}])[0]
        width = int(stream.get("width", 0))
        height = int(stream.get("height", 0))
        if width and height:
            return width, height
    except Exception:
        pass
    return None
