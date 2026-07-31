from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import Callable

from services.scan import scan_with_clamav
from services.validation import validate_file_size, validate_mime_type
from video_analyzer import VideoAnalysisError, analyze_video

_logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """Fallo en un paso del procesamiento; RQ reintenta el job."""


def process_video(temp_id: str, **kwargs: object) -> None:
    from app import app, queue

    with app.app_context():
        _process_with_queue(queue, temp_id)


def _process_with_queue(queue: object, temp_id: str) -> None:
    item = queue.get(temp_id)
    if not item:
        return
    tp = item["temp_path"]

    if not os.path.exists(tp):
        queue.log(temp_id, "size", "error", "Archivo temporal no encontrado")
        queue.update_status(temp_id, "error", error="Archivo temporal no encontrado")
        return

    queue.update_status(temp_id, "processing")

    try:
        ok, _ = _run_validation_step(queue, temp_id, tp, "size", "Validando tamaño...", validate_file_size)
        if not ok:
            return
        ok, _ = _run_validation_step(queue, temp_id, tp, "mime", "Detectando tipo MIME...", validate_mime_type)
        if not ok:
            return
        ok, clam_msg = _run_validation_step(queue, temp_id, tp, "clamav", "Escaneando con ClamAV...", scan_with_clamav)
        if not ok:
            return
        ok, analysis = _run_analysis_step(queue, temp_id, tp)
        if not ok:
            return
        _store_video(queue, temp_id, tp, item, analysis, clam_msg)
    except VideoAnalysisError as e:
        raise PipelineError(f"Error de análisis: {e}") from e
    except Exception as e:
        _logger.exception("Error procesando item %s", temp_id)
        raise PipelineError(f"Error interno: {e}") from e
    finally:
        item_now = queue.get(temp_id)
        if item_now and item_now["status"] in ("done", "error", "cancelled") and os.path.exists(item_now["temp_path"]):
            os.remove(item_now["temp_path"])


def _fail_step(queue: object, temp_id: str, step: str, msg: str) -> None:
    queue.log(temp_id, step, "error", msg)
    queue.update_status(temp_id, "queued", error=msg)
    raise PipelineError(msg)


def _run_validation_step(
    queue: object, temp_id: str, tp: str, step_name: str, label: str, validate_fn: Callable[[str], tuple[bool, str]]
) -> tuple[bool, str]:
    queue.log(temp_id, step_name, "checking", label)
    if queue.is_cancelled(temp_id):
        return False, None
    ok, result = validate_fn(tp)
    if not ok:
        _fail_step(queue, temp_id, step_name, result)
    queue.log(temp_id, step_name, "ok", result)
    return True, result


def _run_analysis_step(queue: object, temp_id: str, tp: str) -> tuple[bool, dict | None]:
    queue.log(temp_id, "analysis", "checking", "Analizando codecs y metadatos...")
    if queue.is_cancelled(temp_id):
        return False, None
    analysis = analyze_video(tp)
    if not analysis.get("valid", False):
        for err in analysis.get("errors", []):
            queue.log(temp_id, "analysis", "error", err)
        _fail_step(queue, temp_id, "analysis", "Análisis de video fallido")
    queue.log(temp_id, "analysis", "ok", f"Contenedor: {analysis.get('container')}")
    for s in analysis.get("streams", []):
        t = "Video" if s["type"] == "video" else "Audio"
        d = (
            f"{s['codec']} {s['resolution']} @ {s['fps']} fps"
            if s["type"] == "video"
            else f"{s['codec']} {s.get('channels', '?')}ch"
        )
        queue.log(temp_id, "stream", "info", f"{t}: {d}")
    return True, analysis


def _store_video(queue: object, temp_id: str, tp: str, item: dict, analysis: dict, clam_msg: str) -> None:
    from models import Video
    from services.validation import validate_mime_type

    queue.log(temp_id, "save", "checking", "Guardando archivo...")
    video_id = str(uuid.uuid4())
    filename = f"{video_id}{item['ext']}"
    session_dir = os.path.join(queue._upload_folder, item["session_code"])
    os.makedirs(session_dir, exist_ok=True)
    final_path = os.path.join(session_dir, filename)
    shutil.move(tp, final_path)

    mime_type = validate_mime_type(final_path)
    mime_val = mime_type[1] if mime_type[0] else "application/octet-stream"

    sha256 = _compute_sha256(final_path)

    has_thumbnail = _generate_thumbnail(
        queue, temp_id, final_path, video_id, item["session_code"], analysis.get("duration")
    )

    video = Video(
        id=video_id,
        filename=filename,
        original_name=item["original_name"],
        size=os.path.getsize(final_path),
        container=analysis.get("container", item["ext"].lstrip(".")),
        mime_type=mime_val,
        analysis_result=str(analysis.get("errors", [])),
        clamav_result=clam_msg,
        sha256=sha256,
        duration=analysis.get("duration"),
        bitrate=analysis.get("bitrate"),
        has_thumbnail=has_thumbnail,
        session_id=item["session_code"],
    )
    queue.db.session.add(video)
    queue.db.session.commit()

    queue.log(temp_id, "save", "ok", "Video almacenado correctamente")
    queue.log(temp_id, "complete", "ok", "Proceso finalizado")
    queue.update_status(temp_id, "done", result=video.to_dict())

    lines = [
        "Resultado del procesamiento",
        f"  Nombre      : {item['original_name']}",
        f"  Tamaño      : {os.path.getsize(final_path) / 1024 / 1024:.1f} MB",
        f"  Contenedor  : {analysis.get('container', '?')}",
        f"  MIME        : {mime_val}",
        f"  SHA-256     : {sha256}",
        f"  ClamAV      : {clam_msg}",
    ]
    if analysis.get("duration") is not None:
        lines.append(f"  Duración    : {_format_duration(analysis['duration'])}")
    if analysis.get("bitrate"):
        lines.append(f"  Bitrate     : {analysis['bitrate'] / 1000:.0f} kbps")
    for s in analysis.get("streams", []):
        if s["type"] == "video":
            lines.append(f"  Video       : {s['codec']} {s['resolution']} @ {s['fps']} fps")
        else:
            lines.append(f"  Audio       : {s['codec']} {s.get('channels', '?')}ch")
    for line in lines:
        queue.log(temp_id, "result", "info", line)


def _generate_thumbnail(
    queue: object, temp_id: str, filepath: str, video_id: str, session_code: str, duration: float | None
) -> bool:
    from services.thumbnail import generate_thumbnail, thumbnail_path

    try:
        out = thumbnail_path(queue._upload_folder, session_code, video_id)
        ok = generate_thumbnail(filepath, out, duration)
        if ok:
            queue.log(temp_id, "thumbnail", "ok", "Miniatura generada")
        else:
            queue.log(temp_id, "thumbnail", "info", "Miniatura omitida")
        return ok
    except Exception:
        _logger.exception("Error generando miniatura para %s", filepath)
        queue.log(temp_id, "thumbnail", "info", "Miniatura omitida")
        return False


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _compute_sha256(filepath: str) -> str:
    import hashlib

    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()
