"""
Tests for /extract-metadata-v2 (the combined forensic `analyze` endpoint) and
its orchestrator. Wiring/validation tests monkeypatch the orchestrator so they
run with no tools. `TestRealTools` runs the genuine exiftool+PyMuPDF+PIL
pipeline against the sample docs and is skipped when exiftool isn't installed.
"""
import io
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import forensics
from app.dqt import jpeg_dqt
from app.main import app
from app.metadata_extractor import MetadataError

client = TestClient(app)

SAMPLES = Path(__file__).resolve().parent.parent / "sample_docs"
SSM_PDF = SAMPLES / "sample_ssm_certificate.pdf"       # clean
TAMPERED_PDF = SAMPLES / "Tampered_Image.pdf"          # trailer-id mismatch + embedded img
IC_PNG = SAMPLES / "sample_ic_photocopy.png"

ENVELOPE_KEYS = {"schema_version", "file", "content_type", "kind",
                 "metadata", "pdf_structure", "image_structure"}

_HAVE_EXIFTOOL = shutil.which("exiftool") is not None


# ── Validation (no tools invoked) ────────────────────────────────────────────

class TestValidation:
    def test_unsupported_mime_400(self):
        r = client.post("/extract-metadata-v2",
                        files={"file": ("x.txt", io.BytesIO(b"hi"), "text/plain")})
        assert r.status_code == 400

    def test_empty_file_400(self):
        r = client.post("/extract-metadata-v2",
                        files={"file": ("x.pdf", io.BytesIO(b""), "application/pdf")})
        assert r.status_code == 400

    def test_missing_file_422(self):
        assert client.post("/extract-metadata-v2", data={}).status_code == 422

    def test_oversized_413(self, monkeypatch):
        from app import metadata
        monkeypatch.setattr(metadata, "MAX_FILE_SIZE", 4)
        r = client.post("/extract-metadata-v2",
                        files={"file": ("x.pdf", io.BytesIO(b"123456789"), "application/pdf")})
        assert r.status_code == 413


# ── Endpoint wiring (orchestrator monkeypatched) ─────────────────────────────

class TestWiring:
    def test_success_passes_envelope_through(self, monkeypatch):
        canned = {"schema_version": "1", "file": "x.pdf", "kind": "pdf"}
        monkeypatch.setattr(forensics, "analyze", lambda *a, **k: canned)
        r = client.post("/extract-metadata-v2",
                        files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")})
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert r.json()["data"] == canned

    def test_exiftool_failure_returns_502(self, monkeypatch):
        def _boom(*a, **k):
            raise MetadataError("exiftool not found")
        monkeypatch.setattr(forensics, "analyze", _boom)
        r = client.post("/extract-metadata-v2",
                        files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")})
        assert r.status_code == 502
        assert "metadata extraction failed" in r.json()["detail"].lower()


# ── DQT unit tests (need PIL only) ───────────────────────────────────────────

class TestDqt:
    def test_non_jpeg_returns_none(self):
        # A valid, decodable PNG has no quantization tables -> None (distinct from
        # an undecodable blob, which returns {"error": ...}).
        pytest.importorskip("PIL")
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, "PNG")
        assert jpeg_dqt(buf.getvalue()) is None

    def test_undecodable_blob_returns_error(self):
        assert "error" in jpeg_dqt(b"not an image at all")

    def test_jpeg_returns_fingerprint(self):
        pytest.importorskip("PIL")
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, "JPEG", quality=75)
        dqt = jpeg_dqt(buf.getvalue())
        assert dqt["table_count"] >= 1
        assert len(dqt["signature"]) == 16
        assert isinstance(dqt["quality_estimate"], int)
        assert "tables" not in dqt          # compact by default
        assert "tables" in jpeg_dqt(buf.getvalue(), include_tables=True)


# ── Orchestrator error handling (no exiftool needed) ─────────────────────────

class TestOrchestrator:
    def test_analyze_pdf_on_garbage_records_error(self):
        # PyMuPDF can't open garbage as a PDF -> {"error": ...}, never raises.
        assert "error" in forensics.analyze_pdf(b"not a real pdf at all")


# ── Real pipeline against the sample docs ────────────────────────────────────

@pytest.mark.skipif(not _HAVE_EXIFTOOL, reason="exiftool not installed")
class TestRealTools:
    def _post(self, path: Path, mime: str):
        with path.open("rb") as fh:
            return client.post("/extract-metadata-v2", files={"file": (path.name, fh, mime)})

    def test_clean_pdf_envelope(self):
        r = self._post(SSM_PDF, "application/pdf")
        assert r.status_code == 200
        d = r.json()["data"]
        assert set(d.keys()) == ENVELOPE_KEYS
        assert d["schema_version"] == "1"
        assert d["kind"] == "pdf"
        assert d["image_structure"] is None
        rev = d["pdf_structure"]["revisions"]
        assert rev["incremental_updates"] == 0
        assert rev["trailer_id"][0] == rev["trailer_id"][1]   # untouched

    def test_tampered_pdf_signals(self):
        d = self._post(TAMPERED_PDF, "application/pdf").json()["data"]
        rev = d["pdf_structure"]["revisions"]
        assert rev["trailer_id"][0] != rev["trailer_id"][1]   # modified after write
        imgs = d["pdf_structure"]["content"]["images"]
        assert imgs, "tampered pdf should expose an embedded image"
        dqt = imgs[0]["dqt"]
        assert isinstance(dqt["quality_estimate"], int)       # fingerprint survives
        assert "metadata" in imgs[0]                          # exiftool ran on it

    def test_image_envelope(self):
        d = self._post(IC_PNG, "image/png").json()["data"]
        assert d["kind"] == "image"
        assert d["pdf_structure"] is None
        assert d["image_structure"]["width"] == 900
        assert d["image_structure"]["height"] == 560
        assert d["image_structure"]["dqt"] is None            # PNG has no DQT

    def test_corrupt_pdf_still_returns_metadata(self):
        # garbage bytes but declared application/pdf: exiftool still runs (200),
        # PyMuPDF records a structural error rather than 502-ing.
        r = client.post("/extract-metadata-v2",
                        files={"file": ("broken.pdf", io.BytesIO(b"%PDF-1.4 broken \xff\xfe"), "application/pdf")})
        assert r.status_code == 200
        d = r.json()["data"]
        assert "error" in d["pdf_structure"]
        assert d["metadata"] is not None
