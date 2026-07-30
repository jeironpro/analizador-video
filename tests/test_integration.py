import tempfile
from unittest.mock import patch

import pytest

# Patch targets:
#   app.XX → upload endpoint (which imports validate_mime_type locally)
#   services.queue.XX → _process_item (calls them from the same module)
UPLOAD_PATCHES = [
    patch("app.validate_mime_type", return_value=(True, "video/mp4")),
    patch("services.queue.validate_mime_type", return_value=(True, "video/mp4")),
    patch("services.queue.validate_file_size", return_value=(True, "100.0 MB")),
    patch("services.queue.scan_with_clamav", return_value=(True, "Archivo limpio")),
    patch(
        "services.queue.analyze_video",
        return_value={
            "valid": True,
            "container": "mp4",
            "streams": [{"type": "video", "codec": "h264", "resolution": "1920x1080", "fps": 30.0}],
            "errors": [],
        },
    ),
]


def _apply_patches():
    for p in UPLOAD_PATCHES:
        p.start()


def _remove_patches():
    for p in UPLOAD_PATCHES:
        p.stop()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _reset_db():
    from app import app as _app
    from models import db

    with _app.app_context():
        db.drop_all()
        db.create_all()


@pytest.fixture(scope="class")
def _with_patches(request):
    """Apply patches for the duration of each test class."""
    _reset_db()
    _apply_patches()
    request.addfinalizer(_remove_patches)


@pytest.fixture
def client(_with_patches):
    from app import app as _app

    return _app.test_client()


@pytest.fixture
def session_code(client):
    r = client.get("/", follow_redirects=False)
    cookie = r.headers["Set-Cookie"].split(";")[0].split("=")[1]
    client.set_cookie("session_code", cookie)
    return cookie


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json["status"] == "ok"


class TestSession:
    def test_index_redirects_to_session(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert "/s/" in r.headers["Location"]

    def test_session_page_renders(self, client, session_code):
        r = client.get(f"/s/{session_code}/")
        assert r.status_code == 200
        assert "VidScan" in r.get_data(as_text=True)

    def test_session_api(self, client, session_code):
        r = client.get("/api/session")
        assert r.status_code == 200
        assert r.json["code"] == session_code

    def test_session_api_unauthorized(self, client):
        client.set_cookie("session_code", "")
        r = client.get("/api/session")
        assert r.status_code == 401

    def test_invalid_session_code_redirects(self, client):
        r = client.get("/s/BADCODE/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "/"


class TestUpload:
    def test_upload_file(self, client, session_code):
        data = {"video": (tempfile.NamedTemporaryFile(suffix=".mp4"), "test.mp4")}
        r = client.post("/api/upload", data=data, content_type="multipart/form-data")
        assert r.status_code == 201, r.get_data(as_text=True)
        assert "temp_id" in r.json
        assert r.json["original_name"] == "test.mp4"

    def test_upload_wrong_extension(self, client, session_code):
        data = {"video": (tempfile.NamedTemporaryFile(suffix=".exe"), "virus.exe")}
        r = client.post("/api/upload", data=data, content_type="multipart/form-data")
        assert r.status_code == 400
        assert "Extensi" in r.json["error"]

    def test_upload_no_file(self, client, session_code):
        r = client.post("/api/upload", data={}, content_type="multipart/form-data")
        assert r.status_code == 400
        assert "archivo" in r.json["error"]

    def test_upload_empty_filename(self, client, session_code):
        data = {"video": (tempfile.NamedTemporaryFile(suffix=".mp4"), "")}
        r = client.post("/api/upload", data=data, content_type="multipart/form-data")
        assert r.status_code == 400


class TestQueue:
    def test_list_queue_empty(self, client, session_code):
        r = client.get("/api/queue")
        assert r.status_code == 200
        assert r.json == []

    def test_list_queue_with_items(self, client, session_code):
        data = {"video": (tempfile.NamedTemporaryFile(suffix=".mp4"), "v1.mp4")}
        client.post("/api/upload", data=data, content_type="multipart/form-data")
        r = client.get("/api/queue")
        assert len(r.json) == 1
        assert r.json[0]["original_name"] == "v1.mp4"

    def test_process_and_complete(self, client, session_code):
        data = {"video": (tempfile.NamedTemporaryFile(suffix=".mp4"), "v1.mp4")}
        r = client.post("/api/upload", data=data, content_type="multipart/form-data")
        temp_id = r.json["temp_id"]

        r = client.post(f"/api/queue/{temp_id}/process")
        assert r.status_code == 200

        import time

        for _ in range(20):
            items = client.get("/api/queue").json
            if items and items[0]["status"] == "done":
                break
            time.sleep(0.2)

        items = client.get("/api/queue").json
        assert items[0]["status"] == "done"

    def test_remove_queue_item(self, client, session_code):
        data = {"video": (tempfile.NamedTemporaryFile(suffix=".mp4"), "v1.mp4")}
        r = client.post("/api/upload", data=data, content_type="multipart/form-data")
        temp_id = r.json["temp_id"]

        r = client.delete(f"/api/queue/{temp_id}")
        assert r.status_code == 200

        r = client.get("/api/queue")
        assert r.json == []


class TestVideos:
    def test_list_videos(self, client, session_code):
        r = client.get("/api/videos")
        assert r.status_code == 200

    def test_download_nonexistent(self, client, session_code):
        r = client.get("/api/download/no-exist")
        assert r.status_code == 404

    def test_delete_nonexistent(self, client, session_code):
        r = client.delete("/api/delete/no-exist")
        assert r.status_code == 404


class TestSessionDelete:
    def test_delete_session(self, client, session_code):
        r = client.post("/api/session/delete")
        assert r.status_code == 200

        # After deletion the cookie is cleared → API returns 401
        r = client.get("/api/videos")
        assert r.status_code == 401


class TestErrorPages:
    def test_404(self, client):
        r = client.get("/no-existe")
        assert r.status_code == 404
        assert "Página no encontrada" in r.get_data(as_text=True)

    def test_404_not_redirected(self, client, session_code):
        r = client.get("/ruta-inexistente")
        assert r.status_code == 404
        assert "404" in r.get_data(as_text=True)
