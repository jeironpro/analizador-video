from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from services.config import CLAMAV_MAX_MB

_logger = logging.getLogger(__name__)

try:
    import pyclamd
except ImportError:
    pyclamd = None

CLAMAV_HOST = os.environ.get("CLAMAV_HOST", "").strip()
CLAMAV_PORT = int(os.environ.get("CLAMAV_PORT", "3310"))
CLAMAV_TIMEOUT = int(os.environ.get("CLAMAV_TIMEOUT", "60"))


def _clamd_client() -> Any | None:
    if pyclamd is None or not CLAMAV_HOST:
        return None
    try:
        client = pyclamd.ClamdNetworkSocket(CLAMAV_HOST, CLAMAV_PORT, timeout=CLAMAV_TIMEOUT)
        if client.ping():
            return client
    except Exception:
        _logger.warning("ClamAV daemon no disponible en %s:%s", CLAMAV_HOST, CLAMAV_PORT)
    return None


def clamd_available() -> bool:
    return _clamd_client() is not None


def scan_with_clamav(filepath: str) -> tuple[bool, str]:
    """Escanea el archivo con clamd (daemon) o, si no está disponible, con clamscan local.

    Nunca bloquea el procesamiento salvo que se detecte un virus (returncode 1 o FOUND).
    """
    client = _clamd_client()
    if client is not None:
        result = _scan_with_clamd(client, filepath)
        if result is not None:
            return result
        _logger.warning("clamd falló al escanear %s, usando clamscan local", filepath)
    return _scan_with_clamscan(filepath)


def _scan_with_clamd(client: Any, filepath: str) -> tuple[bool, str] | None:
    try:
        with open(filepath, "rb") as fh:
            result = client.scan_stream(fh)
    except (ConnectionError, OSError, TimeoutError):
        return None
    except Exception as e:
        if pyclamd is not None and isinstance(e, pyclamd.BufferTooLongError):
            return True, "Escaneo omitido por límite de tamaño"
        _logger.exception("clamd exception al escanear %s", filepath)
        return True, "Escaneo omitido por error interno"
    if not result:
        return True, "Archivo limpio"
    for _filename, info in result.items():
        status, reason = info if isinstance(info, tuple) else ("FOUND", info)
        if status == "FOUND":
            return False, f"Virus detectado: {reason}"
        if status != "OK":
            return True, "Escaneo omitido por error interno"
    return True, "Archivo limpio"


def _scan_with_clamscan(filepath: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [
                "clamscan",
                "--stdout",
                "--no-summary",
                "--quiet",
                "--database=/var/lib/clamav",
                f"--max-filesize={CLAMAV_MAX_MB}M",
                f"--max-scansize={CLAMAV_MAX_MB}M",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=CLAMAV_TIMEOUT,
        )
    except FileNotFoundError:
        return True, "ClamAV no disponible, escaneo omitido"
    except subprocess.TimeoutExpired:
        return True, "Escaneo excedió el tiempo límite, omitido"
    except Exception:
        _logger.exception("ClamAV exception al escanear %s", filepath)
        return True, "Escaneo omitido por error interno"
    if result.returncode == 0:
        return True, "Archivo limpio"
    if result.returncode == 1:
        return False, f"Virus detectado: {result.stdout.strip()}"
    if result.returncode == 2:
        return True, "Escaneo omitido por límite de tamaño"
    stderr = result.stderr.strip()
    if stderr:
        _logger.error("ClamAV error en %s (código %s): %s", filepath, result.returncode, stderr)
    return True, "Escaneo omitido por error interno"
