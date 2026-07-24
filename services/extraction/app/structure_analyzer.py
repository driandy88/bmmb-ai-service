"""Structural analysis via PyMuPDF (fitz) -- PyMuPDF-only, no exiftool/PIL here.

PDFs -> container structure (revisions, trailer, fonts, embedded-image streams).
Images -> decoded ground-truth pixels. The orchestrator (app.forensics) composes
these with exiftool + DQT. v1 collects raw facts only; no scoring.
"""
import re

import pymupdf  # PyMuPDF -- use the 'pymupdf' name to avoid the unrelated 'fitz' PyPI package

# Bound embedded-image work: a PDF could hold many/huge images.
MAX_EMBEDDED_IMAGES = 25
MAX_EMBEDDED_IMAGE_BYTES = 20 * 1024 * 1024


class StructureAnalysisError(Exception):
    """PyMuPDF could not open/parse the bytes (bad PDF, or an image format MuPDF
    cannot decode, e.g. some HEIC builds)."""


# ── PDF ──────────────────────────────────────────────────────────────────────

def open_pdf(file_bytes: bytes):
    try:
        return pymupdf.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise StructureAnalysisError(str(exc)) from exc


def _trailer_id_pair(doc):
    """[original_id, current_id] from the trailer /ID array; differ => modified."""
    try:
        ids = re.findall(r"<([0-9A-Fa-f]+)>", doc.pdf_trailer() or "")
    except Exception:  # noqa: BLE001
        return None
    return ids[:2] or None


def _fonts(doc):
    out = []
    for pno in range(doc.page_count):
        for f in doc.get_page_fonts(pno, full=True):  # (xref, ext, type, basefont, name, enc, ...)
            out.append({
                "page": pno + 1, "xref": f[0], "type": f[2], "basefont": f[3],
                "embedded": f[1] not in ("", "n/a"),
            })
    return out


def _annotations(doc):
    out = []
    for page in doc:
        for a in page.annots():
            out.append({"page": page.number + 1, "type": a.type[1],
                        "rect": [round(v, 1) for v in a.rect]})
    return out


def container_facts(doc, raw_bytes: bytes) -> dict:
    """Everything structural EXCEPT the embedded-image records, which the
    orchestrator fills in (they need exiftool + DQT enrichment)."""
    eof = raw_bytes.count(b"%%EOF")
    return {
        "document": {
            "page_count": doc.page_count,
            "xref_count": doc.xref_length(),
            "encrypted": doc.is_encrypted,
            "needs_pass": bool(doc.needs_pass),
            "permissions": doc.permissions,
            "is_repaired": bool(getattr(doc, "is_repaired", False)),
            "metadata": doc.metadata,  # producer/creator + dates (PDF /Info)
        },
        "revisions": {
            "eof_markers": eof,
            "incremental_updates": max(eof - 1, 0),
            "startxref_count": raw_bytes.count(b"startxref"),
            "trailer_id": _trailer_id_pair(doc),
        },
        "provenance": {
            "xmp": doc.xref_xml_metadata() or None,
            "sigflags": doc.get_sigflags(),
            "javascript_markers": raw_bytes.count(b"/JavaScript") + raw_bytes.count(b"/JS"),
            "embedded_files": doc.embfile_names(),
            "layers": doc.get_ocgs(),
        },
        "content": {
            "fonts": _fonts(doc),
            "images": [],  # <- filled by the orchestrator
            "annotations": _annotations(doc),
            "link_count": sum(len(p.get_links()) for p in doc),
            "form_field_count": sum(len(list(p.widgets())) for p in doc),
        },
    }


def iter_embedded_images(doc):
    """Yield (record, image_bytes, skip_reason) for each UNIQUE embedded image
    (deduped by xref, capped). image_bytes is None when skipped. The record
    carries structural facts; the orchestrator adds metadata + dqt."""
    seen = {}  # xref -> pages it appears on
    order = []  # preserve first-seen order
    for pno in range(doc.page_count):
        for im in doc.get_page_images(pno, full=True):  # (xref, smask, w, h, bpc, cs, altcs, name, filter)
            xref = im[0]
            if xref in seen:
                seen[xref].append(pno + 1)
                continue
            seen[xref] = [pno + 1]
            order.append(im)

    for idx, im in enumerate(order):
        xref = im[0]
        rec = {
            "xref": xref, "pages": seen[xref], "width": im[2], "height": im[3],
            "bpc": im[4], "colorspace": im[5], "filter": im[8],
            "extracted_ext": None,
        }
        if idx >= MAX_EMBEDDED_IMAGES:
            yield rec, None, "image cap reached"
            continue
        try:
            extracted = doc.extract_image(xref)
        except Exception as exc:  # noqa: BLE001
            yield rec, None, f"extract failed: {exc}"
            continue
        img_bytes = extracted.get("image", b"")
        rec["extracted_ext"] = extracted.get("ext")
        if not img_bytes:
            yield rec, None, "no extractable stream"
        elif len(img_bytes) > MAX_EMBEDDED_IMAGE_BYTES:
            yield rec, None, "image exceeds size cap"
        else:
            yield rec, img_bytes, None


# ── Image ────────────────────────────────────────────────────────────────────

def image_pixels(file_bytes: bytes) -> dict:
    """Ground-truth decoded pixels -- cross-check against exiftool's claimed EXIF
    dimensions. Raises StructureAnalysisError if MuPDF can't decode it."""
    try:
        pix = pymupdf.Pixmap(file_bytes)
    except Exception as exc:  # noqa: BLE001
        raise StructureAnalysisError(str(exc)) from exc
    return {
        "width": pix.width,
        "height": pix.height,
        "colorspace": pix.colorspace.name if pix.colorspace else None,
        "n_components": pix.n,
        "has_alpha": bool(pix.alpha),
        "xres_dpi": pix.xres,
        "yres_dpi": pix.yres,
        # NB: Pixmap is 8bpc post-decode -- original bit depth is exiftool's File:BitsPerSample.
    }
