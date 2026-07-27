import os
from supabase import create_client


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "videos")
SIGNED_URL_EXPIRY = 3600


_supabase = None


def _get_client():
    global _supabase
    if _supabase is None and SUPABASE_URL and SUPABASE_KEY:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
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


def upload_file(filepath: str, storage_path: str) -> str:
    bucket = _get_bucket()
    ext = os.path.splitext(storage_path)[1].lower()
    content_type = MIME_MAP.get(ext, "application/octet-stream")
    with open(filepath, "rb") as f:
        bucket.upload(storage_path, f, file_options={"content-type": content_type})
    return storage_path


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
