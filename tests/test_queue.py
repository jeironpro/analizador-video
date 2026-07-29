import json
import os
import time
from unittest.mock import MagicMock, PropertyMock, patch

import pytest


class TestQueueAddGetList:
    def test_add_and_get(self, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "video.mp4", ".mp4", "SESS001")
        item = qm.get("id-1")
        assert item is not None
        assert item["original_name"] == "video.mp4"
        assert item["status"] == "uploaded"
        assert item["session_code"] == "SESS001"

    def test_list_filters_by_session(self, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v1.mp4", ".mp4", "SESS001")
        qm.add("id-2", "/tmp/b.mp4", "b.mp4", "v2.mp4", ".mp4", "SESS002")
        items = qm.list_items(session_code="SESS001")
        assert len(items) == 1
        assert items[0]["temp_id"] == "id-1"

    def test_count_items(self, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v1.mp4", ".mp4", "SESS001")
        qm.add("id-2", "/tmp/b.mp4", "b.mp4", "v2.mp4", ".mp4", "SESS001")
        assert qm.count_items("SESS001") == 2
        assert qm.count_items("OTHER") == 0


class TestQueueStatus:
    def test_update_status(self, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.update_status("id-1", "processing")
        assert qm.get("id-1")["status"] == "processing"

    def test_update_status_with_error(self, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.update_status("id-1", "error", error="something broke")
        item = qm.get("id-1")
        assert item["status"] == "error"
        assert item["error"] == "something broke"

    def test_update_status_with_result(self, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.update_status("id-1", "done", result={"id": "vid-1"})
        item = qm.get("id-1")
        assert item["status"] == "done"
        assert item["result"] == {"id": "vid-1"}

    def test_remove_item(self, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.remove("id-1")
        assert qm.get("id-1") is None

    def test_cancel_item(self, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.update_status("id-1", "processing")
        qm.cancel("id-1")
        item = qm.get("id-1")
        assert item["status"] == "cancelled"
        assert item["error"] == "Cancelado por el usuario"


class TestQueueLog:
    def test_log_entry(self, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.log("id-1", "size", "checking", "Validando...")
        item = qm.get("id-1")
        assert len(item["logs"]) == 1
        assert item["logs"][0]["step"] == "size"
        assert item["logs"][0]["message"] == "Validando..."

    def test_log_multiple_entries(self, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.log("id-1", "a", "ok", "msg1")
        qm.log("id-1", "b", "ok", "msg2")
        assert len(qm.get("id-1")["logs"]) == 2


class TestQueueFailRetry:
    def test_fail_triggers_retry(self, qm):
        qm._max_retries = 3
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm._fail_or_retry("id-1", "error msg")
        item = qm.get("id-1")
        assert item["retries"] == 1
        assert item["status"] == "queued"

    def test_fail_exhausts_retries(self, qm):
        qm._max_retries = 2
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm._fail_or_retry("id-1", "err1")
        qm._fail_or_retry("id-1", "err2")
        item = qm.get("id-1")
        assert item["retries"] == 2
        assert item["status"] == "error"
        assert "err2" in item["error"]

    def test_fail_non_existent_item(self, qm):
        qm._fail_or_retry("no-exist", "error")
        # Should not raise


class TestQueueStaleProcessing:
    def test_recover_stale(self, qm):
        qm._item_timeout = 1
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.update_status("id-1", "processing")
        # First call sets started_at; second call recovers it
        qi = qm._queue["id-1"]
        qi["started_at"] = time.time() - 5  # artificially age it
        qm._recover_stale_processing()
        item = qm.get("id-1")
        assert item["status"] == "queued", "stale processing item should be recovered"

    def test_no_recover_within_timeout(self, qm):
        qm._item_timeout = 60
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.update_status("id-1", "processing")
        qm._recover_stale_processing()
        item = qm.get("id-1")
        assert item["status"] == "processing", "item within timeout should not recover"


class TestQueueProcessItem:
    @patch("services.queue.analyze_video")
    @patch("services.queue.scan_with_clamav")
    @patch("services.queue.validate_mime_type")
    @patch("services.queue.validate_file_size")
    def test_process_item_success(self, mock_size, mock_mime, mock_clam, mock_analyze, qm, app):
        mock_size.return_value = (True, "100.0 MB")
        mock_mime.return_value = (True, "video/mp4")
        mock_clam.return_value = (True, "Archivo limpio")
        mock_analyze.return_value = {
            "valid": True,
            "container": "mp4",
            "streams": [],
            "errors": [],
        }

        temp_dir = app.config["TEMP_FOLDER"]
        filepath = os.path.join(temp_dir, "test_video.mp4")
        with open(filepath, "wb") as f:
            f.write(b"x" * 1024)

        qm.add(
            "id-1",
            filepath,
            "test_video.mp4",
            "test_video.mp4",
            ".mp4",
            "SESS001",
        )
        qm.update_status("id-1", "processing")
        qm._process_item("id-1")

        item = qm.get("id-1")
        assert item["status"] == "done"
        assert item["result"] is not None
        assert item["result"]["original_name"] == "test_video.mp4"

    @patch("services.queue.analyze_video")
    @patch("services.queue.scan_with_clamav")
    @patch("services.queue.validate_mime_type")
    @patch("services.queue.validate_file_size")
    def test_process_item_retry_then_error(self, mock_size, mock_mime, mock_clam, mock_analyze, qm, app):
        qm._max_retries = 1
        mock_size.return_value = (False, "Demasiado pequeño")

        temp_dir = app.config["TEMP_FOLDER"]
        filepath = os.path.join(temp_dir, "small.mp4")
        with open(filepath, "wb") as f:
            f.write(b"x" * 1024)

        qm.add("id-1", filepath, "small.mp4", "small.mp4", ".mp4", "SESS001")
        qm.update_status("id-1", "processing")
        qm._process_item("id-1")

        item = qm.get("id-1")
        assert item["status"] == "error"
        assert "Demasiado pequeño" in item["error"]


class TestQueueShutdown:
    def test_shutdown_resets_processing_items(self, qm, app):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.add("id-2", "/tmp/b.mp4", "b.mp4", "v2.mp4", ".mp4", "SESS001")
        qm._queue["id-1"]["status"] = "processing"
        qm._queue["id-2"]["status"] = "processing"

        qm._shutdown = True
        qm._shutdown_cleanup()

        assert qm.get("id-1")["status"] == "queued"
        assert qm.get("id-2")["status"] == "queued"
