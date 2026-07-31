from unittest.mock import patch


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


class TestQueueEnqueueFallback:
    @patch("services.queue.redis_available", return_value=False)
    @patch("services.queue.QueueManager._process_inline")
    def test_enqueue_processes_inline_without_redis(self, mock_inline, mock_available, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.enqueue("id-1")
        assert qm.get("id-1")["status"] == "queued"
        mock_inline.assert_called_once_with("id-1")

    @patch("services.queue.redis_available", return_value=True)
    @patch("services.queue.get_rq_queue")
    def test_enqueue_rq_with_redis(self, mock_queue, mock_available, qm):
        qm.add("id-1", "/tmp/a.mp4", "a.mp4", "v.mp4", ".mp4", "SESS001")
        qm.enqueue("id-1")
        assert qm.get("id-1")["status"] == "queued"
        mock_queue.return_value.enqueue.assert_called_once()


class TestQueueProcessItem:
    @patch("services.pipeline.analyze_video")
    @patch("services.pipeline.scan_with_clamav")
    @patch("services.pipeline.validate_mime_type")
    @patch("services.pipeline.validate_file_size")
    def test_process_item_success(self, mock_size, mock_mime, mock_clam, mock_analyze, qm, app):
        from services import pipeline

        mock_size.return_value = (True, "100.0 MB")
        mock_mime.return_value = (True, "video/mp4")
        mock_clam.return_value = (True, "Archivo limpio")
        mock_analyze.return_value = {
            "valid": True,
            "container": "mp4",
            "streams": [],
            "errors": [],
        }

        import os

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
        pipeline._process_with_queue(qm, "id-1")

        item = qm.get("id-1")
        assert item["status"] == "done"
        assert item["result"] is not None
        assert item["result"]["original_name"] == "test_video.mp4"

    @patch("services.pipeline.validate_file_size")
    def test_process_item_validation_failure(self, mock_size, qm, app):
        import os

        import pytest

        from services import pipeline
        from services.pipeline import PipelineError

        qm._max_retries = 1
        mock_size.return_value = (False, "Demasiado pequeño")

        temp_dir = app.config["TEMP_FOLDER"]
        filepath = os.path.join(temp_dir, "small.mp4")
        with open(filepath, "wb") as f:
            f.write(b"x" * 1024)

        qm.add("id-1", filepath, "small.mp4", "small.mp4", ".mp4", "SESS001")
        with pytest.raises(PipelineError):
            pipeline._process_with_queue(qm, "id-1")

        item = qm.get("id-1")
        assert item["status"] == "queued"
        assert "Demasiado pequeño" in item["error"]
