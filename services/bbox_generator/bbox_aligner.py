"""
bbox_aligner.py — locate LLM-extracted values inside a PDF and return
normalized bounding boxes.

Approach (the "hybrid" from our discussion):
  LLM does semantics (extracts verbatim values) ->
  PDF text layer / OCR provides word geometry ->
  this aligner matches value -> words -> merged box.

The LLM is never asked for coordinates.
"""
import re
from datetime import datetime
from difflib import SequenceMatcher

import pdfplumber

# ── Step 1: word-level geometry from the document ───────────────────────────

def extract_words(pdf_path: str) -> list[dict]:
    """Every word on every page, with its box, normalized to 0-1 page coords."""
    words = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            W, H = page.width, page.height
            for w in page.extract_words():
                words.append({
                    "page": page_no,
                    "text": w["text"],
                    # normalized 0-1 so the frontend can scale to any zoom level
                    "x0": w["x0"] / W, "y0": w["top"] / H,
                    "x1": w["x1"] / W, "y1": w["bottom"] / H,
                })
    return words


def extract_words_image(image_path: str, min_conf: int = 30,
                        upscale: int = 3, page: int = 1) -> list[dict]:
    """Image counterpart of extract_words(): OCR provides the geometry.

    Preprocessing (upscale + grayscale + autocontrast) matters a lot on real
    ID photos — raw OCR on a security-patterned MyKad missed the NRIC entirely
    until this was added. For production, swap pytesseract for Document AI OCR
    (same word+box output shape, far better on degraded/patterned documents);
    this function is the local/dev fallback.
    """
    import pytesseract
    from PIL import Image, ImageOps

    img = Image.open(image_path)
    W0, H0 = img.size
    proc = ImageOps.autocontrast(
        img.convert("L").resize((W0 * upscale, H0 * upscale), Image.LANCZOS)
    )
    PW, PH = proc.size

    data = pytesseract.image_to_data(proc, output_type=pytesseract.Output.DICT)
    words = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        if not text or int(data["conf"][i]) < min_conf:  # drop OCR noise
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append({
            "page": page, "text": text,
            "x0": x / PW, "y0": y / PH, "x1": (x + w) / PW, "y1": (y + h) / PH,
        })
    return words

# ── Step 2: type-aware normalization (handles the "drift" cases) ────────────

_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y",
                 "%B %d, %Y", "%d.%m.%Y"]

def _try_parse_date(s: str):
    s = s.strip().rstrip(".,;")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def _norm_token(s: str) -> str:
    """Case-fold, strip punctuation and thousand separators."""
    return re.sub(r"[^\w]", "", s).lower()

def tokens_match(value_tok: str, doc_tok: str, data_type: str) -> bool:
    if data_type == "date":
        return False  # dates are matched at phrase level, not token level
    return _norm_token(value_tok) == _norm_token(doc_tok)

# ── Step 3: find a value in the word stream, merge boxes ────────────────────

def find_value_bbox(value: str, words: list[dict], data_type: str = "string",
                    ocr_tolerance: float | None = 0.82):
    """Returns (bbox_dict, match_quality) or (None, 'not_found').

    Strategy:
      1. dates: slide a 1-4 word window, parse both sides as dates, compare
      2. everything else: token-sequence match with normalization
    """
    if value is None:
        return None, "null_value"

    # -- date matching: parse, don't string-compare --
    if data_type == "date":
        target = _try_parse_date(str(value))
        if target:
            for size in (3, 2, 4, 1):  # "14 March 2023" is 3 words
                for i in range(len(words) - size + 1):
                    window = words[i:i + size]
                    if len({w["page"] for w in window}) > 1:
                        continue
                    phrase = " ".join(w["text"] for w in window)
                    if _try_parse_date(phrase) == target:
                        return _merge(window), "exact_date"
        # fall through to string matching if parsing fails

    # -- token sequence matching --
    value_toks = [t for t in re.split(r"\s+", str(value).strip()) if _norm_token(t)]
    if not value_toks:
        return None, "empty_value"

    n = len(value_toks)
    for i in range(len(words) - n + 1):
        window = words[i:i + n]
        if len({w["page"] for w in window}) > 1:
            continue
        if all(tokens_match(vt, w["text"], data_type)
               for vt, w in zip(value_toks, window)):
            return _merge(window), "exact"

    # -- fuzzy fallback 1: doc has the value split/joined differently --
    joined_target = _norm_token("".join(value_toks))
    for size in range(max(1, n - 2), n + 3):
        for i in range(len(words) - size + 1):
            window = words[i:i + size]
            if len({w["page"] for w in window}) > 1:
                continue
            joined_window = _norm_token("".join(w["text"] for w in window))
            if joined_window == joined_target:
                return _merge(window), "fuzzy"

    # -- fuzzy fallback 2: OCR misreads (SEBASTIAI ~ SEBASTIAN), noise tokens --
    # Edit-distance tolerance; only meaningful for OCR'd sources, but harmless
    # on clean PDFs since exact passes will already have matched.
    if ocr_tolerance:
        best_window, best_ratio = None, 0.0
        for size in range(max(1, n - 2), n + 3):
            for i in range(len(words) - size + 1):
                window = words[i:i + size]
                if len({w["page"] for w in window}) > 1:
                    continue
                joined_window = _norm_token("".join(w["text"] for w in window))
                r = SequenceMatcher(None, joined_target, joined_window).ratio()
                if r > best_ratio:
                    best_window, best_ratio = window, r
        if best_window and best_ratio >= ocr_tolerance:
            return _merge(best_window), f"fuzzy_ocr ({best_ratio:.2f})"

    return None, "not_found"

def _merge(window: list[dict]) -> dict:
    return {
        "page": window[0]["page"],
        "x0": min(w["x0"] for w in window), "y0": min(w["y0"] for w in window),
        "x1": max(w["x1"] for w in window), "y1": max(w["y1"] for w in window),
    }

# ── Step 4: align a whole extraction result ─────────────────────────────────

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff")

def align_fields(extracted: dict, field_types: dict, doc_path: str) -> dict:
    """extracted: {field: value} from the LLM.
    field_types: {field: data_type} from the template config.
    doc_path: PDF (text layer) or image (OCR) — routed by extension.
    Returns {field: {value, bbox, match_quality}} — the bbox/match_quality
    are exactly what you'd store next to each extracted field in the DB."""
    if doc_path.lower().endswith(_IMAGE_EXTS):
        words = extract_words_image(doc_path)
    else:
        words = extract_words(doc_path)
    out = {}
    for field, value in extracted.items():
        bbox, quality = find_value_bbox(value, words, field_types.get(field, "string"))
        out[field] = {"value": value, "bbox": bbox, "match_quality": quality}
    return out
