from __future__ import annotations

import json
import subprocess
from typing import Any

ALLOWED_VIDEO_CODECS: set[str] = {"h264", "hevc", "vp9", "av1", "mpeg4", "png", "prores", "dnxhd", "mpeg2video"}
ALLOWED_AUDIO_CODECS: set[str] = {"aac", "mp3", "opus", "vorbis", "pcm_s16le"}
ALLOWED_CONTAINERS: set[str] = {"mp4", "webm", "mkv", "avi", "mov"}
MAX_RESOLUTION: tuple[int, int] = (7680, 4320)
MIN_RESOLUTION: tuple[int, int] = (16, 16)
MAX_FRAME_RATE: int = 120
MIN_FRAME_RATE: int = 1
MAX_DURATION_SECONDS: int = 86400
CONTAINER_CODEC_MAP: dict[str, set[str]] = {
    "mp4": {"h264", "hevc", "aac", "mp3", "png", "prores", "mpeg2video"},
    "webm": {"vp9", "vp8", "opus", "vorbis"},
    "mkv": {"h264", "hevc", "vp9", "av1", "aac", "opus", "vorbis", "mp3"},
    "avi": {"mpeg4", "mp3", "pcm_s16le"},
    "mov": {"h264", "hevc", "aac", "mp3", "pcm_s16le", "png", "prores"},
}


class VideoAnalysisError(Exception):
    pass


def _ffprobe(filepath: str, extra_args: list[str]) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        *extra_args,
        filepath,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise VideoAnalysisError("ffprobe no pudo leer el archivo")
    return json.loads(result.stdout)


def _get_container_format(filepath: str) -> str:
    data = _ffprobe(filepath, ["-show_format"])
    format_name = data.get("format", {}).get("format_name", "")
    return format_name.split(",")[0].lower()


def analyze_video(filepath: str) -> dict[str, Any]:
    data = _ffprobe(filepath, ["-show_streams", "-show_format"])
    streams = data.get("streams", [])
    if not streams:
        raise VideoAnalysisError("El archivo no contiene streams de medios")

    container = _get_container_format(filepath)

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        raise VideoAnalysisError("El archivo no contiene un stream de video")

    errors: list[str] = []
    stream_details: list[dict[str, Any]] = []

    for vs in video_streams:
        codec = vs.get("codec_name", "").lower()
        width = vs.get("width", 0) or 0
        height = vs.get("height", 0) or 0
        fps = _extract_fps(vs)

        if codec not in ALLOWED_VIDEO_CODECS:
            errors.append(f"Códec de video no permitido: {codec}")
        if width > MAX_RESOLUTION[0] or height > MAX_RESOLUTION[1]:
            errors.append(f"Resolución de video no válida: {width}x{height}")
        if width < MIN_RESOLUTION[0] or height < MIN_RESOLUTION[1]:
            errors.append(f"Resolución de video demasiado pequeña: {width}x{height}")
        if fps > 0:
            if fps > MAX_FRAME_RATE:
                errors.append(f"FPS de video no válido: {fps}")
            if fps < MIN_FRAME_RATE:
                errors.append(f"FPS de video demasiado bajo: {fps}")

        if codec not in CONTAINER_CODEC_MAP.get(container, set()):
            errors.append(f"Códec {codec} no es compatible con el contenedor {container}")

        stream_details.append(
            {
                "type": "video",
                "codec": codec,
                "resolution": f"{width}x{height}",
                "fps": round(fps, 2),
                "bitrate": vs.get("bitrate", "N/A"),
            }
        )

    for a_stream in audio_streams:
        codec = a_stream.get("codec_name", "").lower()
        if codec not in ALLOWED_AUDIO_CODECS:
            errors.append(f"Códec de audio no permitido: {codec}")

        if codec not in CONTAINER_CODEC_MAP.get(container, set()):
            errors.append(f"Códec {codec} no es compatible con el contenedor {container}")

        stream_details.append(
            {
                "type": "audio",
                "codec": codec,
                "channels": a_stream.get("channels", "N/A"),
                "sample_rate": a_stream.get("sample_rate", "N/A"),
                "bitrate": a_stream.get("bitrate", "N/A"),
            }
        )

    errors.extend(_check_suspicious_metadata(streams))

    if container not in ALLOWED_CONTAINERS:
        errors.append(f"Formato de contenedor no permitido: {container}")

    format_info = data.get("format", {}) or {}
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "container": container,
        "duration": _parse_duration(format_info.get("duration")),
        "bitrate": _parse_int(format_info.get("bit_rate")),
        "streams": stream_details,
    }


def _parse_duration(value: Any) -> float | None:
    try:
        return round(float(value), 2) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_fps(vs: dict) -> float:
    r_frame_rate = vs.get("r_frame_rate", "0/1")
    try:
        num, den = r_frame_rate.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 0
    except (ValueError, ZeroDivisionError):
        fps = 0
    if fps <= 0 or fps > 1000:
        avg_frame_rate = vs.get("avg_frame_rate", "0/1")
        try:
            num, den = avg_frame_rate.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 0
        except (ValueError, ZeroDivisionError):
            fps = 0
    return fps


def _check_suspicious_metadata(streams: list[dict]) -> list[str]:
    errors: list[str] = []
    for s in streams:
        tags = s.get("tags", {})
        suspicious_keys = {"encoder", "encoder-version", "software"}
        for key in tags:
            key_lower = key.lower()
            if any(sus in key_lower for sus in suspicious_keys):
                val = tags[key].lower().strip()
                if "virus" in val or "malware" in val or "exploit" in val:
                    errors.append(f"Metadato sospechoso en '{key}': {tags[key]}")
    return errors
