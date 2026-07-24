"""Orchestrator for the /extract-metadata-v2 (`analyze`) endpoint.

Composes the single-purpose extractors into the layered response envelope:

    exiftool (metadata_extractor)  -- metadata tags, ALL files
    PyMuPDF  (structure_analyzer)  -- PDF container structure + image pixels
    PIL      (dqt)                 -- JPEG encoder fingerprint

v1 emits RAW FACTS only -- no feature engineering / scoring (out of scope for
now). `schema_version` keeps the envelope forward-compatible: a later
feature-extraction / fraud-flag phase can add fields additively without a break.
See docs/metadata-extraction.md §8.
"""
from app import structure_analyzer
from app.dqt import jpeg_dqt
from app.metadata_extractor import MetadataError, get_file_metadata

SCHEMA_VERSION = "1"


def _safe_image_metadata(image_bytes: bytes) -> dict:
    """exiftool on an embedded/extracted image; failure recorded as data."""
    try:
        return get_file_metadata(image_bytes)
    except MetadataError as exc:
        return {"error": str(exc)}


def analyze_pdf(file_bytes: bytes) -> dict:
    """PyMuPDF container facts + exiftool/DQT on every embedded image. Returns
    the `pdf_structure` block, or {"error": ...} if the PDF won't open."""
    try:
        doc = structure_analyzer.open_pdf(file_bytes)
    except structure_analyzer.StructureAnalysisError as exc:
        return {"error": str(exc)}
    try:
        facts = structure_analyzer.container_facts(doc, file_bytes)
        images = []
        for rec, img_bytes, skip in structure_analyzer.iter_embedded_images(doc):
            if skip:
                rec["metadata"] = {"skipped": skip}
                rec["dqt"] = None
            else:
                rec["metadata"] = _safe_image_metadata(img_bytes)
                rec["dqt"] = jpeg_dqt(img_bytes)
            images.append(rec)
        facts["content"]["images"] = images
        return facts
    finally:
        doc.close()


def analyze_image(file_bytes: bytes) -> dict:
    """Ground-truth pixels + DQT fingerprint. Returns the `image_structure`
    block, or {"error": ...} if MuPDF can't decode the image."""
    try:
        structure = structure_analyzer.image_pixels(file_bytes)
    except structure_analyzer.StructureAnalysisError as exc:
        return {"error": str(exc)}
    structure["dqt"] = jpeg_dqt(file_bytes)  # None for non-JPEG
    return structure


def analyze(file_bytes: bytes, filename: str, content_type: str) -> dict:
    """Build the full layered response envelope for one file.

    Raises MetadataError if exiftool (the core signal) fails on the primary file
    -- the caller turns that into a 502. Structural/DQT failures are recorded as
    data inside their blocks and never raise.
    """
    metadata = get_file_metadata(file_bytes)  # exiftool, all files; may raise -> 502

    is_pdf = content_type == "application/pdf"
    return {
        "schema_version": SCHEMA_VERSION,
        "file": filename,
        "content_type": content_type,
        "kind": "pdf" if is_pdf else "image",
        "metadata": metadata,
        "pdf_structure": analyze_pdf(file_bytes) if is_pdf else None,
        "image_structure": None if is_pdf else analyze_image(file_bytes),
    }
