"""
Exploration script: EVERYTHING PyMuPDF (fitz) can pull from a PDF or an image,
emitted as ONE JSON object per file, grouped for fake-document / fraud detection.

The JSON shape here == the `pdf_structure` / `image_structure` blocks the real
`/analyze` endpoint will return (see docs/metadata-extraction.md §8), so this
doubles as a contract preview. Raw facts only; no scoring / flags. The inline
comments explain WHY each field matters for fraud.

NOTE: PyMuPDF has no single "dump everything as JSON" API -- only sub-part
exporters (e.g. page.get_text("json")). We assemble the dict ourselves.

Run:  ./.venv/bin/python notebooks/explore_pymupdf.py
      ./.venv/bin/python notebooks/explore_pymupdf.py /path/to/file.pdf ...
      ./.venv/bin/python notebooks/explore_pymupdf.py --full file.pdf   # + raw DQT matrices
Requires: PyMuPDF, pillow  (both runtime deps in requirements.txt)
"""
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import pymupdf as fitz  # PyMuPDF — use the 'pymupdf' name to dodge the unrelated
                        # 'fitz' PyPI package (its "No module named 'frontend'" error)

# Make the service root importable so this runs from the notebooks/ dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.metadata_extractor import MetadataError, get_file_metadata  # noqa: E402  exiftool wrapper

try:
    from PIL import Image  # for DQT (JPEG quantization tables) — exiftool can't expose these
    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False

# Standard IJG (Annex K) luminance quantization table @ quality 50 -- the baseline
# we invert against to estimate a JPEG's quality from its own tables.
_STD_LUM = [
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
]


def _jpeg_quality(qt) -> int | None:
    """Estimate the save quality (1-100) by inverting the IJG scaling formula
    against the luminance table. An approximation, but a good encoder tell."""
    lum = qt.get(0)
    if not lum:
        return None
    scales = [(q * 100 - 50) / s for q, s in zip(lum, _STD_LUM) if s]
    if not scales:
        return None
    S = sum(scales) / len(scales)
    if S <= 0:
        return 100
    q = (200 - S) / 2 if S < 100 else 5000 / S
    return round(max(1, min(100, q)))


def _dqt(img_bytes: bytes):
    """JPEG Define-Quantization-Table data -- the STRONGEST encoder fingerprint,
    living in the compressed stream so it survives EXIF stripping. Returns None
    for non-JPEG images (PNG/Flate-reconstructed streams carry no DQT)."""
    if not _HAVE_PIL:
        return {"skipped": "pillow not installed"}
    try:
        im = Image.open(io.BytesIO(img_bytes))
        qt = getattr(im, "quantization", None)  # {table_id: [64 ints]} for JPEGs
    except Exception as exc:
        return {"error": str(exc)}
    if not qt:
        return None  # not a JPEG / no quantization tables
    tables = {idx: list(vals) for idx, vals in qt.items()}
    # Compact signature: identical signature across two images => identical
    # quantization => same encoder+settings (a splice/consistency check).
    sig = hashlib.sha256(
        repr(sorted((k, list(v)) for k, v in qt.items())).encode()
    ).hexdigest()[:16]
    result = {
        "table_count": len(tables),
        "signature": sig,
        "quality_estimate": _jpeg_quality(qt),
    }
    if SHOW_FULL:  # raw 64-value luminance/chroma matrices (verbose)
        result["tables"] = tables
    return result

SAMPLES = Path(__file__).resolve().parent.parent / "sample_docs"
DEFAULTS = [SAMPLES / "sample_ssm_certificate.pdf", SAMPLES / "sample_ic_photocopy.png"]

# Guardrails for embedded-image extraction (a PDF could hold many/huge images).
MAX_EMBEDDED_IMAGES = 25            # cap how many we extract + run exiftool on
MAX_EMBEDDED_IMAGE_BYTES = 20 * 1024 * 1024

# Set by --full: include the raw 64-value DQT matrices (verbose) vs just the
# signature + quality estimate.
SHOW_FULL = False


def _trailer_id_pair(doc):
    """[original_id, current_id] from the trailer /ID array. Differ => the file
    was modified after it was first written."""
    try:
        ids = re.findall(r"<([0-9A-Fa-f]+)>", doc.pdf_trailer() or "")
    except Exception:
        return None
    return ids[:2] or None


def _fonts(doc):
    """Non-embedded or mismatched fonts across a page => spliced/edited text."""
    out = []
    for pno in range(doc.page_count):
        for f in doc.get_page_fonts(pno, full=True):  # (xref, ext, type, basefont, name, enc, ...)
            out.append({
                "page": pno + 1, "xref": f[0], "type": f[2], "basefont": f[3],
                "embedded": f[1] not in ("", "n/a"),
            })
    return out


def _images(doc):
    """Embedded images, each with exiftool run on the EXTRACTED bytes.

    A JPEG (DCTDecode) pasted into an otherwise-text PDF is a red flag; and an
    embedded image often still carries its ORIGINAL EXIF (editor Software, device
    Make, GPS) even when the PDF wrapper looks clean -- the strongest tell.
    Dedupe by xref (one image is often reused across pages) and cap count/size."""
    out = []
    seen = {}  # xref -> the pages it appears on
    for pno in range(doc.page_count):
        for im in doc.get_page_images(pno, full=True):  # (xref, smask, w, h, bpc, cs, altcs, name, filter)
            xref = im[0]
            if xref in seen:
                seen[xref].append(pno + 1)
                continue
            seen[xref] = [pno + 1]
            entry = {
                "xref": xref, "width": im[2], "height": im[3],
                "bpc": im[4], "colorspace": im[5], "filter": im[8],
                "metadata": None,  # filled below (exiftool), or {"error"/"skipped": ...}
                "dqt": None,       # JPEG quantization tables (encoder fingerprint)
            }
            if len(out) >= MAX_EMBEDDED_IMAGES:
                entry["metadata"] = {"skipped": "image cap reached"}
                out.append(entry)
                continue
            try:
                extracted = doc.extract_image(xref)
                img_bytes = extracted.get("image", b"")
                entry["extracted_ext"] = extracted.get("ext")
                if not img_bytes:
                    entry["metadata"] = {"skipped": "no extractable stream"}
                elif len(img_bytes) > MAX_EMBEDDED_IMAGE_BYTES:
                    entry["metadata"] = {"skipped": "image exceeds size cap"}
                else:
                    # exiftool on the extracted image -- open map, may be thin/absent
                    entry["metadata"] = get_file_metadata(img_bytes)
                    entry["dqt"] = _dqt(img_bytes)  # strongest encoder fingerprint
            except MetadataError as exc:
                entry["metadata"] = {"error": str(exc)}
            except Exception as exc:  # extract_image can fail on odd/broken streams
                entry["metadata"] = {"error": f"extract failed: {exc}"}
            out.append(entry)
    # attach the page list now that we've seen all occurrences
    for entry in out:
        entry["pages"] = seen[entry["xref"]]
    return out


def _annotations(doc):
    """FreeText / Stamp / Redact annotations => manual editing / overlays."""
    out = []
    for page in doc:
        for a in page.annots():
            out.append({"page": page.number + 1, "type": a.type[1], "rect": [round(v, 1) for v in a.rect]})
    return out


def pdf_report(path: Path) -> dict:
    raw = path.read_bytes()
    doc = fitz.open(stream=raw, filetype="pdf")
    try:
        eof = raw.count(b"%%EOF")
        report = {
            "document": {
                "page_count": doc.page_count,
                "xref_count": doc.xref_length(),          # total PDF objects
                "encrypted": doc.is_encrypted,
                "needs_pass": bool(doc.needs_pass),
                "permissions": doc.permissions,
                "is_repaired": bool(getattr(doc, "is_repaired", False)),  # repair => often tampered
                "metadata": doc.metadata,                 # producer/creator + dates
            },
            "revisions": {                                # STRONGEST tamper signal
                "eof_markers": eof,                       # one-shot export = 1
                "incremental_updates": max(eof - 1, 0),   # each extra = a save after original
                "startxref_count": raw.count(b"startxref"),
                "trailer_id": _trailer_id_pair(doc),      # [original, current]; differ => modified
            },
            "provenance": {
                "xmp": doc.xref_xml_metadata() or None,   # may carry xmpMM:History edit trail
                "sigflags": doc.get_sigflags(),           # -1 none, 1 signed, 3 signed+append-only
                "javascript_markers": raw.count(b"/JavaScript") + raw.count(b"/JS"),
                "embedded_files": doc.embfile_names(),
                "layers": doc.get_ocgs(),                 # OCGs => layered editing tools
            },
            "content": {
                "fonts": _fonts(doc),
                "images": _images(doc),
                "annotations": _annotations(doc),
                "link_count": sum(len(p.get_links()) for p in doc),
                "form_field_count": sum(len(list(p.widgets())) for p in doc),
            },
        }
        return report
    finally:
        doc.close()


def image_report(path: Path) -> dict:
    """GROUND-TRUTH decoded pixels -- cross-check against exiftool's *claimed*
    EXIF dimensions. A mismatch => cropped/replaced without updating the EXIF.
    Flat images have no container history, unlike PDFs."""
    raw = path.read_bytes()
    try:
        pix = fitz.Pixmap(raw)
    except Exception as exc:  # e.g. some HEIC builds MuPDF can't decode
        return {"error": str(exc)}
    return {
        "width": pix.width,                # ACTUAL decoded pixels
        "height": pix.height,
        "colorspace": pix.colorspace.name if pix.colorspace else None,
        "n_components": pix.n,
        "has_alpha": bool(pix.alpha),
        "xres_dpi": pix.xres,
        "yres_dpi": pix.yres,
        # NB: Pixmap is 8bpc post-decode -- NOT original bit depth (use exiftool
        # File:BitsPerSample for that).
    }


def analyze(path: Path) -> dict:
    is_pdf = path.suffix.lower() == ".pdf"
    return {
        "file": path.name,
        "kind": "pdf" if is_pdf else "image",
        # mirrors the /analyze contract; exiftool metadata is added separately there
        "pdf_structure": pdf_report(path) if is_pdf else None,
        "image_structure": None if is_pdf else image_report(path),
    }


def main():
    global SHOW_FULL
    args = sys.argv[1:]
    SHOW_FULL = "--full" in args  # include raw DQT matrices
    paths = [Path(a) for a in args if a != "--full"] or DEFAULTS
    reports = [analyze(p) for p in paths]
    print(json.dumps(reports if len(reports) > 1 else reports[0], indent=2, default=str))


if __name__ == "__main__":
    main()
