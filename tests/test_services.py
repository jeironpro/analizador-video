from __future__ import annotations

from services.rate_limiter import RateLimiter
from services.sse import sse_complete, sse_error, sse_step
from video_analyzer import _check_suspicious_metadata, _extract_fps


class TestRateLimiter:
    def test_allows_within_limit(self):
        rl = RateLimiter(limit=3, window=60)
        assert rl.is_allowed("a")
        assert rl.is_allowed("a")
        assert rl.is_allowed("a")

    def test_blocks_when_exceeded(self):
        rl = RateLimiter(limit=2, window=60)
        assert rl.is_allowed("a")
        assert rl.is_allowed("a")
        assert not rl.is_allowed("a")

    def test_independent_keys(self):
        rl = RateLimiter(limit=2, window=60)
        assert rl.is_allowed("a")
        assert rl.is_allowed("a")
        assert rl.is_allowed("b")

    def test_window_expires(self):
        rl = RateLimiter(limit=2, window=0)
        assert rl.is_allowed("a")
        assert rl.is_allowed("a")
        assert rl.is_allowed("a")

    def test_cleanup_removes_stale(self):
        rl = RateLimiter(limit=2, window=0)
        rl.is_allowed("a")
        rl.cleanup()
        assert not rl._buckets.get("a")


class TestSSE:
    def test_sse_step_format(self):
        result = sse_step("clamav", "ok", "Archivo limpio")
        assert "event: step" in result
        assert "Archivo limpio" in result
        assert result.endswith("\n\n")

    def test_sse_complete_format(self):
        result = sse_complete({"id": "abc"})
        assert "event: complete" in result
        assert "abc" in result
        assert result.endswith("\n\n")

    def test_sse_error_format(self):
        result = sse_error("Algo salio mal")
        assert "event: error" in result
        assert "Algo salio mal" in result
        assert result.endswith("\n\n")


class TestExtractFps:
    def test_normal_fps(self):
        vs = {"r_frame_rate": "30000/1001"}
        assert abs(_extract_fps(vs) - 29.97) < 0.1

    def test_zero_denominator_fallsback_to_avg(self):
        vs = {"r_frame_rate": "0/0", "avg_frame_rate": "24000/1001"}
        assert abs(_extract_fps(vs) - 23.97) < 0.1

    def test_both_zero(self):
        vs = {"r_frame_rate": "0/0", "avg_frame_rate": "0/0"}
        assert _extract_fps(vs) == 0

    def test_unparsable_fallsback(self):
        vs = {"r_frame_rate": "abc", "avg_frame_rate": "24/1"}
        assert _extract_fps(vs) == 24

    def test_high_fps_fallsback_to_avg(self):
        vs = {"r_frame_rate": "100000/1", "avg_frame_rate": "60/1"}
        assert abs(_extract_fps(vs) - 60) < 0.1


class TestCheckSuspiciousMetadata:
    def test_clean(self):
        streams = [{"tags": {"encoder": "libx264"}}]
        assert _check_suspicious_metadata(streams) == []

    def test_suspicious_virus_tag(self):
        streams = [{"tags": {"encoder": "libx264 virus"}}]
        errors = _check_suspicious_metadata(streams)
        assert len(errors) == 1
        assert "sospechoso" in errors[0]

    def test_suspicious_malware_tag(self):
        streams = [{"tags": {"encoder": "something malware"}}]
        errors = _check_suspicious_metadata(streams)
        assert len(errors) == 1

    def test_multiple_suspicious(self):
        streams = [
            {"tags": {"encoder": "libx264 virus"}},
            {"tags": {"Software": "Exploit kit"}},
        ]
        errors = _check_suspicious_metadata(streams)
        assert len(errors) == 2

    def test_no_tags(self):
        streams = [{}]
        assert _check_suspicious_metadata(streams) == []

    def test_empty_tags(self):
        streams = [{"tags": {}}]
        assert _check_suspicious_metadata(streams) == []
