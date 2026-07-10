"""
llm_bbox.py — LLM-based bounding-box localization (Gemini), with verification.

Design: for each extracted value, prompt Gemini with (value, template field
description, page image) -> box_2d coordinates. Gemini natively outputs
box_2d as [ymin, xmin, ymax, xmax] normalized 0-1000.

IMPORTANT — every LLM box is reconciled against bbox_aligner.py's OCR word
geometry before being trusted, in two steps:
  1. snap_to_words() (preferred): the LLM's box is treated as a coarse
     proposal — "roughly here" — and snapped to the nearest word window that
     actually matches the value. Self-verifying by construction (it can only
     land on a window containing the value) and gives OCR-exact edges. This
     is what fixes the LLM picking the wrong table column/row: even if the
     proposed box is in the wrong column, snapping finds the nearest
     matching occurrence rather than trusting the proposal's position.
  2. verify_llm_box() (fallback): if nothing was within snapping radius,
     falls back to a plain "is the value literally inside this box" check.
     Measured on real docs, raw LLM boxes score IoU ~0.4-0.65 on simple cards
     and ~0.1-0.35 on dense tables (sometimes the wrong cell entirely) — this
     turns a confidently-wrong box into an explicit "unverified" flag.

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
from PIL import Image

from .bbox_aligner import (
    _load_words,
    _norm_token,
    _try_parse_date,
    _try_parse_number,
    snap_to_words,
)

# Max normalized distance (page units) for snap_to_words to accept a match.
# Larger = more rescues of imprecise LLM boxes, but more risk of snapping to
# an equal-valued neighbor (e.g. the same amount on an adjacent row).
SNAP_RADIUS = float(os.environ.get("BBOX_SNAP_RADIUS", "0.18"))

MODEL = "gemini-2.5-flash"  # pin exact version here, same convention as gemini_client.py

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "asia-southeast1")

_client = None  # lazy singleton -- see get_client()


class LlmConfigError(RuntimeError):
    """Raised when required config (e.g. GCP_PROJECT_ID) is missing."""


def get_client() -> genai.Client:
    """Reuses one genai.Client per process instead of creating a fresh one
    (with its own auth handshake) on every /align request -- this was a
    measurable chunk of the "why is this so slow" latency, since Cloud Run
    keeps the process warm across requests anyway."""
    global _client
    if _client is not None:
        return _client
    if not GCP_PROJECT_ID:
        raise LlmConfigError(
            "GCP_PROJECT_ID is not set. Set it as an environment variable "
            "(Cloud Run: --set-env-vars=GCP_PROJECT_ID=...; local: in .env)."
        )
    _client = genai.Client(vertexai=True, project=GCP_PROJECT_ID, location=VERTEX_LOCATION)
    return _client

# ── the prompt: value-as-param + template description + page image ──────────

LOCALIZE_PROMPT = """You are a document localization engine. Treat the page
image below exactly like a picture: your only job is to find the precise
region where each already-extracted value is drawn in that picture, and
draw a tight bounding box around it. You are NOT extracting, verifying, or
correcting the values — they are already correct. You are only pointing at
where they visually appear on the page.

Document type: {template_name}
{template_description}

All of the values below come from the SAME row/instance of this document
(e.g. all belong to the same year's column in a multi-year financial
table, or the same person's row in a multi-signatory list). They must all
be found in the same visual column/row band on the page — use that
constraint actively: if you can confidently place one value, use its
column/row position on the page to resolve the others whenever a value
could plausibly match more than one place.

Values to locate:
{field_blocks}

How to find each box_2d, step by step:
1. Visually scan the ENTIRE page image for text matching the value (see the
   formatting-variance rules below — the page text rarely matches the
   value's exact string).
2. Once you find it, draw a box around ONLY that value's rendered text —
   not its label, not surrounding whitespace, not the whole table cell/row.
3. Express the box as box_2d = [ymin, xmin, ymax, xmax], each an integer
   0-1000, normalized to THIS image's full width/height, where (0,0) is the
   TOP-LEFT corner of the image and (1000,1000) is the BOTTOM-RIGHT corner.

Formatting-variance rules:
- Numbers: thousand separators, currency prefixes, parentheses for
  negatives all count as the same number (e.g. 340980 == "340,980.00" ==
  "RM 340,980.00" == "(340,980.00)" for a negative value).
- Dates: may appear in a different format or language, including Malay
  month names (e.g. "14 Mac 2023" == "14 March 2023" == "2023-03-14").
- If a value appears more than once on the page, prefer the occurrence in
  this record's own row/column (see above) over any other instance.

If you cannot find a value anywhere on THIS page, set found=false. Never
guess or invent a box — a missing box is far better than a wrong one.
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

# Cap the longer edge before sending to Gemini. A financial statement
# rendered at 150dpi can exceed 2000px on its long edge -- that costs more
# upload time and more image tokens per request without improving
# localization accuracy (Gemini's vision encoder downsamples internally
# anyway), so this was pure wasted latency.
_MAX_DIM = 1600


def _encode_png(img: Image.Image, max_dim: int = _MAX_DIM) -> bytes:
    if max(img.size) > max_dim:
        scale = max_dim / max(img.size)
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def render_pages(doc_path: str, resolution: int = 150) -> list:
    """Returns a list of PNG bytes, one per page. Single-page for images.
    Uses pdfplumber's built-in renderer (no poppler/imagemagick needed)."""
    if doc_path.lower().endswith(_IMAGE_EXTS):
        with Image.open(doc_path) as img:
            return [_encode_png(img)]

    import pdfplumber
    pages = []
    with pdfplumber.open(doc_path) as pdf:
        for page in pdf.pages:
            pages.append(_encode_png(page.to_image(resolution=resolution).original))
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
            if bbox is None:
                still_remaining[field] = value
                continue

            # 1) snap: treat the LLM's box as "roughly here" and snap to the
            # nearest OCR word window that actually matches the value — this
            # corrects a wrong-column/wrong-row proposal instead of just
            # accepting or rejecting it outright.
            snapped_bbox, snap_tier = snap_to_words(
                bbox, value, words, field_types.get(field, "string"), radius=SNAP_RADIUS)
            if snapped_bbox:
                resolved[field] = {"value": value, "bbox": snapped_bbox,
                                   "match_quality": f"llm_{snap_tier}"}
                continue

            # 2) snap failed (nothing matching nearby) — fall back to a
            # plain containment check on the LLM's raw proposal.
            quality = verify_llm_box(bbox, value, words)
            if quality == "verified":
                resolved[field] = {"value": value, "bbox": bbox, "match_quality": "llm_verified"}
            else:
                # found something on this page but couldn't verify it — keep
                # trying other pages in case the real occurrence is elsewhere,
                # but remember this as a fallback if nothing better turns up.
                still_remaining[field] = value
                resolved.setdefault(f"__unverified__{field}", {
                    "value": value, "bbox": bbox, "match_quality": "llm_unverified"})
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
