import os
import base64
import httpx
from supabase import create_client
from supabase.lib.client_options import SyncClientOptions, SyncHttpxClient
from httpx import Timeout


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "videos")
SIGNED_URL_EXPIRY = 3600
TUS_CHUNK_SIZE = 10 * 1024 * 1024


_supabase = None
_http_client = None


def _get_http_client():
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(
            http1=True,
            http2=False,
            timeout=Timeout(300.0, connect=30.0, pool=None),
        )
    return _http_client


def _get_client():
    global _supabase
    if _supabase is None and SUPABASE_URL and SUPABASE_KEY:
        http_client = SyncHttpxClient(
            http1=True,
            http2=False,
            timeout=Timeout(300.0, connect=30.0),
        )
        options = SyncClientOptions(httpx_client=http_client)
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY, options=options)
    return _supabase


def is_available() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _get_bucket():
    client = _get_client()
    if client is None:
        raise RuntimeError("Supabase no está configurado (SUPABASE_URL y SUPABASE_KEY requeridos)")
    return client.storage.from_(SUPABASE_STORAGE_BUCKET)


MIME_MAP = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".mpeg": "video/mpeg",
    ".wmv": "video/x-ms-wmv",
}


def _tus_upload(filepath: str, storage_path: str) -> str:
    file_size = os.path.getsize(filepath)
    filename = os.path.basename(storage_path)
    filename_b64 = base64.b64encode(filename.encode()).decode()

    storage_url = f"{SUPABASE_URL}/storage/v1/upload/{SUPABASE_STORAGE_BUCKET}/{storage_path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Tus-Resumable": "1.0.0",
        "Upload-Length": str(file_size),
        "Upload-Metadata": f"filename {filename_b64}",
        "x-upsert": "true",
    }

    client = _get_http_client()

    response = client.post(storage_url, headers=headers)
    response.raise_for_status()

    location = response.headers.get("Location") or response.headers.get("location")
    if not location:
        upload_url = storage_url
    else:
        upload_url = location

    with open(filepath, "rb") as f:
        offset = 0
        while offset < file_size:
            chunk = f.read(TUS_CHUNK_SIZE)
            chunk_size = len(chunk)
            patch_headers = {
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Tus-Resumable": "1.0.0",
                "Upload-Offset": str(offset),
                "Content-Type": "application/offset+octet-stream",
            }
            resp = client.patch(upload_url, content=chunk, headers=patch_headers)
            resp.raise_for_status()
            offset += chunk_size

    return storage_path


def upload_file(filepath: str, storage_path: str) -> str:
    bucket = _get_bucket()
    ext = os.path.splitext(storage_path)[1].lower()
    content_type = MIME_MAP.get(ext, "application/octet-stream")
    with open(filepath, "rb") as f:
        bucket.upload(storage_path, f, file_options={"content-type": content_type})
    return storage_path


def upload_file_tus(filepath: str, storage_path: str) -> str:
    return _tus_upload(filepath, storage_path)


def download_file(storage_path: str) -> bytes:
    bucket = _get_bucket()
    return bucket.download(storage_path)


def delete_file(storage_path: str) -> None:
    bucket = _get_bucket()
    bucket.remove([storage_path])


def get_signed_url(storage_path: str) -> str:
    bucket = _get_bucket()
    result = bucket.create_signed_url(storage_path, expires_in=SIGNED_URL_EXPIRY)
    return result.get("signedURL", "")


def get_public_url(storage_path: str) -> str:
    bucket = _get_bucket()
    return bucket.get_public_url(storage_path)


def create_signed_upload_url(storage_path: str) -> dict:
    bucket = _get_bucket()
    result = bucket.create_signed_upload_url(storage_path)
    return result


def upload_via_signed_url(signed_url: str, token: str, storage_path: str, filepath: str) -> str:
    bucket = _get_bucket()
    with open(filepath, "rb") as f:
        bucket.upload_to_signed_url(storage_path, token, f)
    return storage_path


def download_to_temp(storage_path: str, temp_dir: str) -> str:
    data = download_file(storage_path)
    temp_path = os.path.join(temp_dir, storage_path.replace("/", "_"))
    with open(temp_path, "wb") as f:
        f.write(data)
    return temp_path
