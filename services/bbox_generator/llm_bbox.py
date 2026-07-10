"""
llm_bbox.py — LLM-based bounding-box localization (Gemini), with verification.

Design: for each extracted value, prompt Gemini with (value, template field
description, page image) -> box_2d coordinates. Gemini natively outputs
box_2d as [ymin, xmin, ymax, xmax] normalized 0-1000.

IMPORTANT — this ships with verify_llm_box() always applied. Measured on
real docs, raw LLM boxes score IoU ~0.4-0.65 on simple cards and ~0.1-0.35 on
dense tables (sometimes the wrong cell entirely). The verifier turns a
confidently-wrong box into an explicit "unverified" flag instead of a
silently-wrong highlight.

This is the second alignment strategy alongside bbox_aligner.py's OCR/text-
layer approach (never removed — api.py's `method` param picks between them).
Both produce the same {field: {value, bbox, match_quality}} output shape so
callers don't care which one ran.
"""
import io
import json
import os

from google import genai
from google.genai import types

from .bbox_aligner import _load_words, _norm_token, _try_parse_date, _try_parse_number

MODEL = "gemini-2.5-flash"  # pin exact version here, same convention as gemini_client.py

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "asia-southeast1")


class LlmConfigError(RuntimeError):
    """Raised when required config (e.g. GCP_PROJECT_ID) is missing."""


def get_client() -> genai.Client:
    if not GCP_PROJECT_ID:
        raise LlmConfigError(
            "GCP_PROJECT_ID is not set. Set it as an environment variable "
            "(Cloud Run: --set-env-vars=GCP_PROJECT_ID=...; local: in .env)."
        )
    return genai.Client(vertexai=True, project=GCP_PROJECT_ID, location=VERTEX_LOCATION)

# ── the prompt: value-as-param + template description + page image ──────────

LOCALIZE_PROMPT = """You are a document localization engine. You are given a
page image from a document and a list of values that were already extracted
from it by a separate process. Your only job is to find WHERE each value
appears on THIS page image — you are not extracting or verifying the values
themselves, only locating them.

Document type: {template_name}
{template_description}

Values to locate:
{field_blocks}

Rules:
1. Return box_2d as [ymin, xmin, ymax, xmax], each coordinate normalized to
   0-1000 relative to this page image's full width/height.
2. The box must tightly enclose ONLY the value text itself, not its label
   (e.g. for "Revenue: RM 4,820,500.00", box just "RM 4,820,500.00").
3. If the value appears more than once on this page, use the field's
   description to disambiguate (e.g. a summary-table total, not a line item;
   the current year's column, not a prior-year comparison).
4. Numbers may be formatted differently on the page than the value given
   (thousand separators, currency prefixes, parentheses for negatives) —
   match the underlying number, not the exact string.
5. Dates may appear in a different format or language on the page (including
   Malay month names) — match the underlying date, not the exact string.
6. If you cannot find the value anywhere on THIS page, set found=false.
   Never guess or invent a box — a missing box is far better than a wrong one.
Return JSON matching the schema exactly, one entry per value listed above."""

FIELD_BLOCK = """- field: {field}
  value: {value}
  description: {description}"""


def build_prompt(template: dict, fields: dict) -> str:
    """template: {key, name?, description?, fields: {field_name: {description}}}.
    fields: {field_name: extracted_value} for this call (one page's worth)."""
    template_fields = template.get("fields", {})
    blocks = []
    for name, value in fields.items():
        meta = template_fields.get(name, {})
        blocks.append(FIELD_BLOCK.format(
            field=name, value=value,
            description=meta.get("description") or "(no description)"))
    return LOCALIZE_PROMPT.format(
        template_name=template.get("name") or template.get("key", "document"),
        template_description=template.get("description", ""),
        field_blocks="\n".join(blocks))


# structured output schema: one box per field
LOCALIZE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "field": {"type": "STRING"},
            "found": {"type": "BOOLEAN"},
            "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"},
                        "description": "[ymin, xmin, ymax, xmax] normalized 0-1000"},
        },
        "required": ["field", "found"],
    },
}


def localize_page(client: genai.Client, page_image_bytes: bytes,
                  template: dict, fields: dict, page_no: int) -> dict:
    """One Gemini vision call per page: locate all of `fields` on this page.
    Returns {field: {bbox: {page,x0,y0,x1,y1} | None}}."""
    resp = client.models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=page_image_bytes, mime_type="image/png"),
                  build_prompt(template, fields)],
        config={"response_mime_type": "application/json",
                "response_schema": LOCALIZE_SCHEMA, "temperature": 0},
    )
    out = {}
    for item in json.loads(resp.text):
        if not item.get("found") or not item.get("box_2d"):
            out[item["field"]] = {"bbox": None}
            continue
        y0, x0, y1, x1 = [v / 1000 for v in item["box_2d"]]
        out[item["field"]] = {"bbox": {"page": page_no, "x0": x0, "y0": y0, "x1": x1, "y1": y1}}
    return out

# ── the safety layer: verify the box actually contains the value ─────────────

def verify_llm_box(bbox: dict, value, words: list, pad: float = 0.01) -> str:
    """Deterministic check: do the document words inside the box actually
    contain the value? Uses the same word geometry the OCR aligner uses.
    Returns 'verified' | 'unverified' | 'no_box'."""
    if not bbox:
        return "no_box"
    inside = [w for w in words
              if w["page"] == bbox["page"]
              and w["x0"] >= bbox["x0"] - pad and w["x1"] <= bbox["x1"] + pad
              and w["y0"] >= bbox["y0"] - pad and w["y1"] <= bbox["y1"] + pad]
    if not inside:
        return "unverified"
    joined = _norm_token("".join(w["text"] for w in inside))

    if isinstance(value, (int, float)):
        for w in inside:
            n = _try_parse_number(w["text"])
            if n is not None and abs(n - float(value)) < 1e-6:
                return "verified"
        n = _try_parse_number(" ".join(w["text"] for w in inside))
        return "verified" if n is not None and abs(n - float(value)) < 1e-6 else "unverified"
    d_target = _try_parse_date(str(value))
    if d_target:
        d_found = _try_parse_date(" ".join(w["text"] for w in inside))
        if d_found and (d_found == d_target or
                        (d_found.year, d_found.month) == (d_target.year, d_target.month)):
            return "verified"
    return "verified" if _norm_token(str(value)) in joined else "unverified"

# ── page rendering (PDF pages -> PNG bytes; images pass through) ────────────

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff")


def render_pages(doc_path: str, resolution: int = 150) -> list:
    """Returns a list of PNG bytes, one per page. Single-page for images.
    Uses pdfplumber's built-in renderer (no poppler/imagemagick needed)."""
    if doc_path.lower().endswith(_IMAGE_EXTS):
        from PIL import Image
        with Image.open(doc_path) as img:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            return [buf.getvalue()]

    import pdfplumber
    pages = []
    with pdfplumber.open(doc_path) as pdf:
        for page in pdf.pages:
            buf = io.BytesIO()
            page.to_image(resolution=resolution).save(buf, format="PNG")
            pages.append(buf.getvalue())
    return pages

# ── public entry point: same {field: {value, bbox, match_quality}} shape ────

def _align_record_llm(client, page_images: list, words: list,
                      template: dict, record: dict, field_types: dict) -> dict:
    fields = {k: v for k, v in record.items() if not k.startswith("_") and v is not None}
    null_fields = {k: v for k, v in record.items() if not k.startswith("_") and v is None}

    resolved = {}
    remaining = dict(fields)
    for page_no, page_bytes in enumerate(page_images, start=1):
        if not remaining:
            break
        page_result = localize_page(client, page_bytes, template, remaining, page_no)
        still_remaining = {}
        for field, value in remaining.items():
            bbox = page_result.get(field, {}).get("bbox")
            quality = verify_llm_box(bbox, value, words)
            if quality == "verified":
                resolved[field] = {"value": value, "bbox": bbox, "match_quality": "llm_verified"}
            elif bbox is not None:
                # found something on this page but couldn't verify it — keep
                # trying other pages in case the real occurrence is elsewhere,
                # but remember this as a fallback if nothing better turns up.
                still_remaining[field] = value
                resolved.setdefault(f"__unverified__{field}", {
                    "value": value, "bbox": bbox, "match_quality": "llm_unverified"})
            else:
                still_remaining[field] = value
        remaining = still_remaining

    out = {}
    for field, value in fields.items():
        if field in resolved:
            out[field] = resolved[field]
        elif f"__unverified__{field}" in resolved:
            out[field] = resolved[f"__unverified__{field}"]
        else:
            out[field] = {"value": value, "bbox": None, "match_quality": "not_found"}
    for field, value in null_fields.items():
        out[field] = {"value": None, "bbox": None, "match_quality": "null_value"}
    return out


def align_extraction_llm(extracted, field_types: dict, template: dict, doc_path: str,
                         client=None):
    """LLM-based counterpart to bbox_aligner.align_extraction(). Handles both
    template kinds:
        single (dict)       -> {field: {value, bbox, match_quality}}
        array  (list[dict]) -> [{...}, ...]
    `template` must include per-field descriptions for a good prompt — see
    build_prompt(). Pass `client` to reuse one genai.Client across calls
    (e.g. across array rows); otherwise one is created per call."""
    client = client or get_client()
    words = _load_words(doc_path)
    page_images = render_pages(doc_path)

    if isinstance(extracted, list):
        return [_align_record_llm(client, page_images, words, template, r, field_types)
                for r in extracted]
    return _align_record_llm(client, page_images, words, template, extracted, field_types)

# ── evaluation harness: measure LLM boxes against aligner boxes (IoU) ────────

def iou(a: dict, b: dict) -> float:
    if not a or not b or a["page"] != b["page"]:
        return 0.0
    ix0, iy0 = max(a["x0"], b["x0"]), max(a["y0"], b["y0"])
    ix1, iy1 = min(a["x1"], b["x1"]), min(a["y1"], b["y1"])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    area = ((a["x1"] - a["x0"]) * (a["y1"] - a["y0"]) +
            (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]) - inter)
    return inter / area if area else 0.0


def evaluate(llm_boxes: dict, aligner_boxes: dict) -> dict:
    """Run on a sample of real documents; decide with data.
    Rule of thumb: IoU >= 0.5 usable for review UI; < 0.3 is misleading."""
    report = {}
    for field, r in llm_boxes.items():
        g = aligner_boxes.get(field, {}).get("bbox")
        report[field] = {"iou": round(iou(r.get("bbox"), g), 3),
                         "llm_found": r.get("bbox") is not None,
                         "aligner_found": g is not None}
    return report
