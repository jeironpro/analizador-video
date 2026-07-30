from unittest.mock import MagicMock, patch

from services.queue import scan_with_clamav
from services.validation import validate_file_size, validate_mime_type


class TestValidateFileSize:
    @patch("services.queue.os.path.getsize")
    def test_too_small(self, mock_getsize, temp_file):
        mock_getsize.return_value = 1 * 1024 * 1024  # 1 MB
        ok, msg = validate_file_size(temp_file)
        assert not ok
        assert "Demasiado pequeño" in msg

    @patch("services.queue.os.path.getsize")
    def test_too_large(self, mock_getsize, temp_file):
        mock_getsize.return_value = 600 * 1024 * 1024  # 600 MB
        ok, msg = validate_file_size(temp_file)
        assert not ok
        assert "Demasiado grande" in msg

    @patch("services.queue.os.path.getsize")
    def test_valid(self, mock_getsize, temp_file):
        mock_getsize.return_value = 100 * 1024 * 1024  # 100 MB
        ok, msg = validate_file_size(temp_file)
        assert ok
        assert "100.0 MB" in msg

    @patch("services.queue.os.path.getsize")
    def test_boundary_minimum(self, mock_getsize, temp_file):
        mock_getsize.return_value = 50 * 1024 * 1024  # exact minimum
        ok, msg = validate_file_size(temp_file)
        assert ok

    @patch("services.queue.os.path.getsize")
    def test_boundary_maximum(self, mock_getsize, temp_file):
        mock_getsize.return_value = 500 * 1024 * 1024  # exact maximum
        ok, msg = validate_file_size(temp_file)
        assert ok


class TestValidateMimeType:
    @patch("services.validation.magic")
    def test_valid_mp4(self, mock_magic, temp_file):
        mock_magic.from_file.return_value = "video/mp4"
        ok, mime = validate_mime_type(temp_file)
        assert ok
        assert mime == "video/mp4"

    @patch("services.validation.magic")
    def test_valid_webm(self, mock_magic, temp_file):
        mock_magic.from_file.return_value = "video/webm"
        ok, mime = validate_mime_type(temp_file)
        assert ok
        assert mime == "video/webm"

    @patch("services.validation.magic")
    def test_valid_mkv(self, mock_magic, temp_file):
        mock_magic.from_file.return_value = "video/x-matroska"
        ok, mime = validate_mime_type(temp_file)
        assert ok

    @patch("services.validation.magic")
    def test_valid_mov(self, mock_magic, temp_file):
        mock_magic.from_file.return_value = "video/quicktime"
        ok, mime = validate_mime_type(temp_file)
        assert ok

    @patch("services.validation.magic")
    def test_invalid_mime(self, mock_magic, temp_file):
        mock_magic.from_file.return_value = "application/pdf"
        ok, msg = validate_mime_type(temp_file)
        assert not ok
        assert "no válido" in msg

    @patch("services.validation.magic", None)
    def test_fallback_when_magic_missing(self, temp_file):
        with patch("services.validation.mimetypes") as mock_mimetypes:
            mock_mimetypes.guess_type.return_value = ("video/mp4", None)
            ok, mime = validate_mime_type(temp_file)
            assert ok
            assert mime == "video/mp4"

    @patch("services.validation.magic", None)
    def test_invalid_when_magic_missing_and_unknown(self, temp_file):
        with patch("services.validation.mimetypes") as mock_mimetypes:
            mock_mimetypes.guess_type.return_value = (None, None)
            ok, msg = validate_mime_type(temp_file)
            assert not ok
            assert "application/octet-stream" in msg


class TestScanWithClamav:
    @patch("services.queue.subprocess.run")
    def test_clean(self, mock_run, temp_file):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        ok, msg = scan_with_clamav(temp_file)
        assert ok
        assert "limpio" in msg

    @patch("services.queue.subprocess.run")
    def test_virus_detected(self, mock_run, temp_file):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout.strip.return_value = "Win.Trojan.Test-1"
        mock_run.return_value = mock_result
        ok, msg = scan_with_clamav(temp_file)
        assert not ok
        assert "Virus detectado" in msg

    @patch("services.queue.subprocess.run")
    def test_clamav_not_found(self, mock_run, temp_file):
        mock_run.side_effect = FileNotFoundError
        ok, msg = scan_with_clamav(temp_file)
        assert not ok
        assert "no está instalado" in msg

    @patch("services.queue.subprocess.run")
    def test_timeout(self, mock_run, temp_file):
        from subprocess import TimeoutExpired

        mock_run.side_effect = TimeoutExpired("clamscan", 300)
        ok, msg = scan_with_clamav(temp_file)
        assert not ok
        assert "excedió" in msg

    @patch("services.queue.subprocess.run")
    def test_killed_by_rlimit_memory(self, mock_run, temp_file):
        mock_result = MagicMock()
        mock_result.returncode = -9
        mock_run.return_value = mock_result
        ok, msg = scan_with_clamav(temp_file)
        assert ok
        assert "límite de memoria" in msg

    @patch("services.queue.subprocess.run")
    def test_returncode_2_memory(self, mock_run, temp_file):
        mock_result = MagicMock()
        mock_result.returncode = 2
        mock_result.stderr.strip.return_value = ""
        mock_run.return_value = mock_result
        ok, msg = scan_with_clamav(temp_file)
        assert ok
        assert "límite de memoria" in msg
