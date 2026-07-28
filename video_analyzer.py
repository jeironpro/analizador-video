import json
import subprocess
import struct
from datetime import datetime, timezone


ALLOWED_VIDEO_CODECS = {"h264", "hevc", "vp9", "av1", "mpeg4", "png", "prores", "dnxhd", "mpeg2video"}
ALLOWED_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis", "pcm_s16le"}
ALLOWED_CONTAINERS = {"mp4", "webm", "mkv", "avi", "mov"}
MAX_RESOLUTION = (7680, 4320)
MIN_RESOLUTION = (16, 16)
MAX_FRAME_RATE = 120
MIN_FRAME_RATE = 1
MAX_DURATION_SECONDS = 86400
CONTAINER_CODEC_MAP = {
    "mp4":  {"h264", "hevc", "aac", "mp3", "png", "prores", "mpeg2video"},
    "webm": {"vp9", "vp8", "opus", "vorbis"},
    "mkv":  {"h264", "hevc", "vp9", "av1", "aac", "opus", "vorbis", "mp3"},
    "avi":  {"mpeg4", "mp3", "pcm_s16le"},
    "mov":  {"h264", "hevc", "aac", "mp3", "pcm_s16le", "png", "prores"},
}


class VideoAnalysisError(Exception):
    pass


def _get_container_format(filepath: str) -> str:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        filepath
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise VideoAnalysisError("ffprobe no pudo leer el archivo")
    data = json.loads(result.stdout)
    format_name = data.get("format", {}).get("format_name", "")
    return format_name.split(",")[0].lower()


def _get_streams(filepath: str) -> list:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        filepath
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise VideoAnalysisError("ffprobe no pudo leer el archivo")
    data = json.loads(result.stdout)
    return data.get("streams", [])


def analyze_video(filepath: str) -> dict:
    streams = _get_streams(filepath)
    if not streams:
        raise VideoAnalysisError("El archivo no contiene streams de medios")

    container = _get_container_format(filepath)

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        raise VideoAnalysisError("El archivo no contiene un stream de video")

    errors = []
    stream_details = []
    all_codecs = set()

    for vs in video_streams:
        codec = vs.get("codec_name", "").lower()
        all_codecs.add(codec)
        width = vs.get("width", 0) or 0
        height = vs.get("height", 0) or 0
        r_frame_rate = vs.get("r_frame_rate", "0/1")
        try:
            num, den = r_frame_rate.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 0
        except (ValueError, ZeroDivisionError):
            fps = 0
        if fps > 1000:
            avg_frame_rate = vs.get("avg_frame_rate", "0/1")
            try:
                num, den = avg_frame_rate.split("/")
                fps = float(num) / float(den) if float(den) != 0 else 0
            except (ValueError, ZeroDivisionError):
                fps = 0

        if codec not in ALLOWED_VIDEO_CODECS:
            errors.append(f"Códec de video no permitido: {codec}")
        if width > MAX_RESOLUTION[0] or height > MAX_RESOLUTION[1]:
            errors.append(f"Resolución de video no válida: {width}x{height}")
        if width < MIN_RESOLUTION[0] or height < MIN_RESOLUTION[1]:
            errors.append(f"Resolución de video demasiado pequeña: {width}x{height}")
        if fps > MAX_FRAME_RATE:
            errors.append(f"FPS de video no válido: {fps}")
        if fps < MIN_FRAME_RATE:
            errors.append(f"FPS de video demasiado bajo: {fps}")

        stream_details.append({
            "type": "video",
            "codec": codec,
            "resolution": f"{width}x{height}",
            "fps": round(fps, 2),
            "bitrate": vs.get("bitrate", "N/A"),
        })

        container_allowed = CONTAINER_CODEC_MAP.get(container, set())
        if codec not in container_allowed:
            errors.append(
                f"Códec {codec} no es compatible con el contenedor {container}"
            )

    for a_stream in audio_streams:
        codec = a_stream.get("codec_name", "").lower()
        all_codecs.add(codec)
        if codec not in ALLOWED_AUDIO_CODECS:
            errors.append(f"Códec de audio no permitido: {codec}")

        container_allowed = CONTAINER_CODEC_MAP.get(container, set())
        if codec not in container_allowed:
            errors.append(
                f"Códec {codec} no es compatible con el contenedor {container}"
            )

        stream_details.append({
            "type": "audio",
            "codec": codec,
            "channels": a_stream.get("channels", "N/A"),
            "sample_rate": a_stream.get("sample_rate", "N/A"),
            "bitrate": a_stream.get("bitrate", "N/A"),
        })

    format_info = None
    for s in streams:
        if "TAG" in s:
            tags = s.get("TAG", {})
            creation_time = tags.get("creation_time")
            if creation_time:
                try:
                    parsed = datetime.fromisoformat(creation_time.replace("Z", "+00:00"))
                    if parsed > datetime.now(timezone.utc):
                        errors.append("Metadato 'creation_time' está en el futuro")
                except (ValueError, TypeError):
                    pass

    suspicious_keys = {"encoder", "encoder-version", "software"}
    for s in streams:
        tags = s.get("tags", {})
        for key in tags:
            key_lower = key.lower()
            if any(sus in key_lower for sus in suspicious_keys):
                val = tags[key].lower().strip()
                if "virus" in val or "malware" in val or "exploit" in val:
                    errors.append(f"Metadato sospechoso en '{key}': {tags[key]}")

    if container not in ALLOWED_CONTAINERS:
        errors.append(f"Formato de contenedor no permitido: {container}")

    is_valid = len(errors) == 0

    return {
        "valid": is_valid,
        "errors": errors,
        "container": container,
        "streams": stream_details,
    }
