"""
Tests for the /extract-metadata endpoint and its exiftool wrapper.

The exiftool *subprocess* is monkeypatched in every test except the ones under
`TestRealExifTool`, so this suite runs in CI with no exiftool binary, no
credentials, and no network. `TestRealExifTool` runs a genuine extraction and is
skipped automatically when the binary isn't installed.
"""
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.metadata_extractor import MetadataError, get_file_metadata

client = TestClient(app)

# A minimal, valid one-element exiftool -j -G dump.
FAKE_DUMP = {
    "SourceFile": "-",
    "File:FileType": "JPEG",
    "File:MIMEType": "image/jpeg",
    "EXIF:Make": "Apple",
    "EXIF:Software": "17.5.1",
}
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "3df40000000a4944415478da6360000002000155a2d0870000000049454e44ae426082"
)


class _FakeProc:
    """Stand-in for subprocess.CompletedProcess."""
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── Extractor unit tests (monkeypatch subprocess.run) ────────────────────────

class TestGetFileMetadata:
    def test_returns_parsed_dump(self, monkeypatch):
        def fake_run(*args, **kwargs):
            return _FakeProc(stdout=json.dumps([FAKE_DUMP]).encode())
        monkeypatch.setattr(subprocess, "run", fake_run)

        assert get_file_metadata(b"\xff\xd8\xffdata") == FAKE_DUMP

    def test_passes_bytes_on_stdin_with_expected_args(self, monkeypatch):
        """The extractor must feed bytes via stdin (`-`), never a temp path."""
        captured = {}

        def fake_run(cmd, input=None, **kwargs):
            captured["cmd"] = cmd
            captured["input"] = input
            return _FakeProc(stdout=json.dumps([FAKE_DUMP]).encode())
        monkeypatch.setattr(subprocess, "run", fake_run)

        get_file_metadata(b"the-bytes")
        assert captured["cmd"] == ["exiftool", "-j", "-G", "-"]
        assert captured["input"] == b"the-bytes"
        assert "shell" not in captured  # never invoked via a shell

    def test_thin_dump_is_not_an_error(self, monkeypatch):
        """A stripped file (almost no metadata) returns a thin dict, not a raise."""
        thin = {"SourceFile": "-", "File:FileType": "JPEG"}
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: _FakeProc(stdout=json.dumps([thin]).encode()),
        )
        assert get_file_metadata(b"x") == thin

    def test_missing_binary_raises_metadata_error(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "exiftool")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(MetadataError, match="exiftool not found"):
            get_file_metadata(b"x")

    def test_timeout_raises_metadata_error(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="exiftool", timeout=30)
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(MetadataError, match="timed out"):
            get_file_metadata(b"x")

    def test_nonzero_exit_raises_with_stderr(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: _FakeProc(returncode=1, stderr=b"Error: bad magic"),
        )
        with pytest.raises(MetadataError, match="bad magic"):
            get_file_metadata(b"x")

    def test_unparseable_output_raises(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: _FakeProc(stdout=b"not json at all"),
        )
        with pytest.raises(MetadataError, match="could not parse"):
            get_file_metadata(b"x")

    def test_empty_array_output_raises(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: _FakeProc(stdout=b"[]"),
        )
        with pytest.raises(MetadataError, match="could not parse"):
            get_file_metadata(b"x")


# ── Endpoint tests (monkeypatch the extractor) ───────────────────────────────

class TestExtractMetadataValidation:
    def test_unsupported_mime_type_400(self):
        r = client.post(
            "/extract-metadata",
            files={"file": ("doc.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert r.status_code == 400

    def test_empty_file_400(self):
        r = client.post(
            "/extract-metadata",
            files={"file": ("doc.jpg", io.BytesIO(b""), "image/jpeg")},
        )
        assert r.status_code == 400

    def test_missing_file_422(self):
        r = client.post("/extract-metadata", data={})
        assert r.status_code == 422  # FastAPI form validation

    def test_oversized_file_413(self, monkeypatch):
        from app import metadata
        monkeypatch.setattr(metadata, "MAX_FILE_SIZE", 8)  # tiny cap for the test
        r = client.post(
            "/extract-metadata",
            files={"file": ("big.jpg", io.BytesIO(b"123456789"), "image/jpeg")},
        )
        assert r.status_code == 413


class TestExtractMetadataSuccess:
    @pytest.fixture(autouse=True)
    def mock_extractor(self, monkeypatch):
        monkeypatch.setattr("app.metadata.get_file_metadata", lambda b: FAKE_DUMP)

    def test_returns_raw_dump(self):
        r = client.post(
            "/extract-metadata",
            files={"file": ("photo.jpg", io.BytesIO(b"\xff\xd8\xffdata"), "image/jpeg")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["data"]["filename"] == "photo.jpg"
        assert body["data"]["metadata"] == FAKE_DUMP


class TestExtractMetadataFailure:
    def test_extractor_failure_returns_502(self, monkeypatch):
        def _boom(_bytes):
            raise MetadataError("exiftool not found")
        monkeypatch.setattr("app.metadata.get_file_metadata", _boom)

        r = client.post(
            "/extract-metadata",
            files={"file": ("photo.jpg", io.BytesIO(b"\xff\xd8\xffdata"), "image/jpeg")},
        )
        assert r.status_code == 502
        assert "metadata extraction failed" in r.json()["detail"].lower()


# ── Real exiftool (skipped when the binary is absent) ────────────────────────

_SAMPLE = Path(__file__).parent.parent / "sample_docs" / "sample_ic_photocopy.png"


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
class TestRealExifTool:
    def test_extractor_on_real_png_bytes(self):
        data = _SAMPLE.read_bytes()
        result = get_file_metadata(data)
        assert isinstance(result, dict)
        assert result.get("File:FileType") == "PNG"

    def test_endpoint_on_real_png(self):
        with _SAMPLE.open("rb") as fh:
            r = client.post(
                "/extract-metadata",
                files={"file": (_SAMPLE.name, fh, "image/png")},
            )
        assert r.status_code == 200
        assert r.json()["data"]["metadata"]["File:FileType"] == "PNG"
