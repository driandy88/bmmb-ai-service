from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app import forensics
from app.extraction import ALLOWED_MIME_TYPES, MAX_FILE_SIZE
from app.metadata_extractor import MetadataError, get_file_metadata
from app.schemas import ApiResponse

router = APIRouter(tags=["Metadata"])


async def _read_validated(file: UploadFile) -> tuple[bytes, str]:
    """Shared guardrails: MIME allowlist, non-empty, size cap. Returns
    (bytes, content_type) or raises the appropriate HTTPException."""
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type '{content_type}' for '{file.filename}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}."
            ),
        )
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail=f"'{file.filename}' is empty.")
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"'{file.filename}' exceeds the 20 MB size limit.")
    return file_bytes, content_type


# ── OpenAPI/Swagger examples (concrete envelope shapes for the docs UI) ───────

_EXAMPLE_PDF = {
    "schema_version": "1",
    "file": "tampered.pdf",
    "content_type": "application/pdf",
    "kind": "pdf",
    "metadata": {"File:FileType": "PDF", "PDF:Producer": "4-Heights PDF Library", "...": "…exiftool dump…"},
    "pdf_structure": {
        "document": {
            "page_count": 1, "xref_count": 14, "encrypted": False, "needs_pass": False,
            "permissions": -4, "is_repaired": False,
            "metadata": {"producer": "4-Heights PDF Library", "creator": "(unspecified)",
                         "creationDate": "D:20260709101608+00'00'", "modDate": "D:20260723101542Z"},
        },
        "revisions": {"eof_markers": 1, "incremental_updates": 0, "startxref_count": 1,
                      "trailer_id": ["93216A19…", "CEEF0E4C…"]},
        "provenance": {"xmp": None, "sigflags": -1, "javascript_markers": 0,
                       "embedded_files": [], "layers": {}},
        "content": {
            "fonts": [{"page": 1, "xref": 4, "type": "Type1", "basefont": "Helvetica", "embedded": False}],
            "images": [{
                "xref": 8, "pages": [1], "width": 1920, "height": 1080, "bpc": 8,
                "colorspace": "DeviceRGB", "filter": "DCTDecode", "extracted_ext": "jpeg",
                "metadata": {"File:FileType": "JPEG", "File:ImageWidth": 1920, "File:ImageHeight": 1080},
                "dqt": {"table_count": 2, "signature": "071f0329733acb1d", "quality_estimate": 75},
            }],
            "annotations": [], "link_count": 0, "form_field_count": 0,
        },
    },
    "image_structure": None,
}

_EXAMPLE_IMAGE = {
    "schema_version": "1",
    "file": "receipt.jpg",
    "content_type": "image/jpeg",
    "kind": "image",
    "metadata": {"EXIF:Make": "Apple", "EXIF:Software": "17.5.1",
                 "EXIF:ExifImageWidth": 4032, "EXIF:ExifImageHeight": 3024, "...": "…"},
    "pdf_structure": None,
    "image_structure": {"width": 620, "height": 45, "colorspace": "DeviceRGB", "n_components": 3,
                        "has_alpha": False, "xres_dpi": 72, "yres_dpi": 72,
                        "dqt": {"table_count": 2, "signature": "a1b2c3d4e5f60718", "quality_estimate": 78}},
}

_V2_RESPONSES = {
    200: {
        "description": "Forensic envelope. Always the same 7 `data` keys "
                       "(`null` where not applicable).",
        "content": {"application/json": {"examples": {
            "pdf": {"summary": "PDF — exiftool + PyMuPDF structure + per-image DQT",
                    "value": {"success": True, "data": _EXAMPLE_PDF, "error": None}},
            "image": {"summary": "Image — exiftool + ground-truth pixels + DQT",
                      "value": {"success": True, "data": _EXAMPLE_IMAGE, "error": None}},
        }}},
    },
    400: {"description": "Unsupported MIME type, or empty file."},
    413: {"description": "File exceeds the 20 MB size limit."},
    502: {"description": "exiftool (the core signal) failed on the primary file. "
                         "Note: PyMuPDF/DQT failures do NOT 502 — they are recorded "
                         "as `{\"error\": …}` inside their block."},
}

_V2_DESCRIPTION = """\
Combined forensic analysis for **fake-document / fraud detection** — the v2 of
metadata extraction.

**Tools run per file:**
- **exiftool** — metadata tags (EXIF/XMP/PDF `/Info`) on every file.
- **PyMuPDF** — for PDFs: container structure (revisions, trailer `/ID`, fonts,
  annotations) **plus each embedded image extracted and run through exiftool +
  DQT**; for images: ground-truth decoded pixels.
- **DQT (PIL)** — JPEG quantization-table fingerprint (survives EXIF stripping).

**Response contract (`schema_version` "1"):** the `data` object always carries
the same **7 keys** — `schema_version, file, content_type, kind, metadata,
pdf_structure, image_structure` — with `null` where not applicable. Raw facts
only; feature engineering / fraud scoring is out of scope for now. `schema_version`
keeps the envelope forward-compatible if such fields are added later.

**Open maps:** `metadata` blocks have **no fixed key set** — access with `.get()`;
a missing field is a *signal*, not an error. Do not validate them against a
closed schema.

**Errors as data:** any PyMuPDF/DQT failure (PDF won't open, image won't decode,
one bad embedded image) is recorded as `{"error": …}` / `{"skipped": …}` inside
its block and never sinks the response. Only an exiftool failure on the primary
file returns **502**.
"""


@router.post(
    "/extract-metadata",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse,
    summary="Raw file metadata (exiftool only)",
    responses={
        400: {"description": "Unsupported MIME type, or empty file."},
        413: {"description": "File exceeds the 20 MB size limit."},
        502: {"description": "exiftool failed."},
    },
)
async def extract_metadata(file: UploadFile = File(...)):
    """Dump the raw file-level metadata (EXIF/XMP/PDF/software tags) for a single
    uploaded file. Stripped files return a thin dict, not an error."""
    file_bytes, _ = await _read_validated(file)
    try:
        metadata = get_file_metadata(file_bytes)
    except MetadataError as exc:
        raise HTTPException(status_code=502, detail=f"Metadata extraction failed: {exc}")
    return ApiResponse(data={"filename": file.filename, "metadata": metadata})


@router.post(
    "/extract-metadata-v2",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse,
    summary="Combined forensic analysis (exiftool + PyMuPDF + DQT)",
    description=_V2_DESCRIPTION,
    responses=_V2_RESPONSES,
)
async def extract_metadata_v2(file: UploadFile = File(...)):
    file_bytes, content_type = await _read_validated(file)
    try:
        data = forensics.analyze(file_bytes, file.filename, content_type)
    except MetadataError as exc:  # exiftool is the core signal -> hard failure
        raise HTTPException(status_code=502, detail=f"Metadata extraction failed: {exc}")
    return ApiResponse(data=data)
