"""
bbox_aligner.py (v3) — locate LLM-extracted values inside a PDF/image and
return normalized bounding boxes.

Pipeline position: extraction agent (LLM) -> {field: value} -> THIS (pure code)
-> {field: value, bbox, match_quality}. No LLM here.

v3 generalizations (the "works for all form types" pass):
  1. ALL-CANDIDATES + SCORING — never first-occurrence-wins. Every plausible
     location is collected, then scored by: match tier, the LLM's _locations
     hint (soft prior), section proximity, and row-overlap with the record's
     anchor field. Highest score wins; reading order only breaks ties.
  2. TYPE-AWARE VALUE PARSING —
       numbers: 340980 matches "340,980.00", "RM 328,400.00", "(3,124,300)"
       dates/months: "July 2023" matches "Julai 2023", "Jul 2023",
                     "31 Disember 2023" windows (Malay month map included)
  3. ROW ANCHORING for array templates — the record's first field (e.g. month,
     financial_year) is located first; other fields prefer candidates sharing
     that row band. Fixes repeated values across table rows/pages.
  4. HINTS ARE SOFT — _locations comes from the LLM, so it only adds score
     weight; it can never force a match the geometry doesn't support.

Also exposes snap_to_words(): "LLM points, OCR measures" — given a coarse
box proposed by an LLM vision call (llm_bbox.py), snap it to the nearest
find_value_candidates() window that actually matches the value. A
successful snap is self-verifying (it only ever lands on a window
containing the value) and has OCR-exact edges, which is a materially
better reconciliation than asking the LLM to reason about which table
column/row a value belongs to.
"""
import math
import re
from datetime import datetime
from difflib import SequenceMatcher

import pdfplumber

# ── geometry sources ─────────────────────────────────────────────────────────

def extract_words(pdf_path: str) -> list[dict]:
    """Every word on every page, box normalized to 0-1 page coords."""
    words = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            W, H = page.width, page.height
            for w in page.extract_words():
                words.append({
                    "page": page_no, "text": w["text"],
                    "x0": w["x0"] / W, "y0": w["top"] / H,
                    "x1": w["x1"] / W, "y1": w["bottom"] / H,
                })
    return words


def extract_words_image(image_path: str, min_conf: int = 30,
                        upscale: int = 3, page: int = 1) -> list[dict]:
    """Image counterpart: OCR provides geometry. Preprocessing (upscale +
    grayscale + autocontrast) matters a lot on real ID photos. For production,
    swap pytesseract for Document AI OCR (same output shape, far better on
    degraded/patterned documents); this is the local/dev fallback."""
    import pytesseract
    from PIL import Image, ImageOps

    img = Image.open(image_path)
    W0, H0 = img.size
    proc = ImageOps.autocontrast(
        img.convert("L").resize((W0 * upscale, H0 * upscale), Image.LANCZOS))
    PW, PH = proc.size
    data = pytesseract.image_to_data(proc, output_type=pytesseract.Output.DICT)
    words = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        if not text or int(data["conf"][i]) < min_conf:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append({"page": page, "text": text,
                      "x0": x / PW, "y0": y / PH, "x1": (x + w) / PW, "y1": (y + h) / PH})
    return words

# ── normalization: strings, numbers, dates (incl. Malay months) ─────────────

_MS_MONTHS = {"januari": "january", "februari": "february", "mac": "march",
              "mei": "may", "jun": "june", "julai": "july", "ogos": "august",
              "oktober": "october", "disember": "december",
              "jan": "jan", "feb": "feb", "apr": "apr", "jul": "jul",
              "ogo": "aug", "okt": "oct", "dis": "dec", "sep": "sep"}

_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y",
                 "%B %d, %Y", "%d.%m.%Y", "%B %Y", "%b %Y", "%d/%m/%y"]

def _translate_ms(s: str) -> str:
    return " ".join(_MS_MONTHS.get(tok.lower(), tok) for tok in s.split())

def _try_parse_date(s: str):
    s = _translate_ms(str(s).strip().rstrip(".,;"))
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().replace(day=1) \
                   if fmt in ("%B %Y", "%b %Y") else datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def _month_of(d):  # month-granularity comparison for "%B %Y"-style values
    return (d.year, d.month) if d else None

_NUM_RE = re.compile(r"^\(?(?:rm)?\s*-?[\d,]+(?:\.\d+)?\)?$", re.I)

def _try_parse_number(s: str):
    s = str(s).strip()
    if not _NUM_RE.match(s.replace(" ", "")):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s2 = re.sub(r"[^\d.\-]", "", s)
    if s2 in ("", "-", "."):
        return None
    try:
        v = float(s2)
        return -v if neg and v > 0 else v
    except ValueError:
        return None

def _norm_token(s: str) -> str:
    return re.sub(r"[^\w]", "", s).lower()

# ── candidate collection (all plausible locations, tiered) ───────────────────

_TIER_SCORE = {"exact_number": 5, "exact_date": 5, "exact": 5, "fuzzy": 2, "fuzzy_ocr": 1}

def _merge(window):
    return {"page": window[0]["page"],
            "x0": min(w["x0"] for w in window), "y0": min(w["y0"] for w in window),
            "x1": max(w["x1"] for w in window), "y1": max(w["y1"] for w in window)}

def _windows(words, size):
    for i in range(len(words) - size + 1):
        window = words[i:i + size]
        if len({w["page"] for w in window}) == 1:
            yield i, window

def find_value_candidates(value, words, data_type="string",
                          ocr_tolerance=0.82) -> list[dict]:
    """All plausible locations for `value`, each {bbox, tier, order}."""
    if value is None:
        return []
    cands, seen = [], set()

    def add(window, tier, order):
        key = (window[0]["page"], id(window[0]), id(window[-1]))
        if key in seen:
            return
        seen.add(key)
        cands.append({"bbox": _merge(window), "tier": tier, "order": order,
                      "_y0": window[0]["y0"], "_y1": window[-1]["y1"]})

    # numeric path (declared type OR value is int/float)
    target_num = None
    if data_type in ("number", "float", "numeric", "int") or isinstance(value, (int, float)):
        target_num = float(value) if isinstance(value, (int, float)) else _try_parse_number(value)
    if target_num is not None:
        for size in (1, 2):           # "328,400.00" / "RM 328,400.00"
            for i, window in _windows(words, size):
                n = _try_parse_number(" ".join(w["text"] for w in window))
                if n is not None and abs(n - target_num) < 1e-6:
                    add(window, "exact_number", i)

    # date/month path (declared OR value parses as a date)
    target_date = _try_parse_date(value) if not isinstance(value, (int, float)) else None
    if data_type == "date" or target_date:
        month_only = bool(re.match(r"^[A-Za-z]+ \d{4}$", str(value).strip()))
        for size in (3, 2, 4, 1):
            for i, window in _windows(words, size):
                d = _try_parse_date(" ".join(w["text"] for w in window))
                if d and ((_month_of(d) == _month_of(target_date)) if month_only
                          else d == target_date):
                    add(window, "exact_date", i)

    # token-sequence exact
    value_toks = [t for t in re.split(r"\s+", str(value).strip()) if _norm_token(t)]
    if value_toks:
        n = len(value_toks)
        for i, window in _windows(words, n):
            if all(_norm_token(v) == _norm_token(w["text"])
                   for v, w in zip(value_toks, window)):
                add(window, "exact", i)

        # fuzzy: split/joined tokens
        joined_target = _norm_token("".join(value_toks))
        for size in range(max(1, n - 2), n + 3):
            for i, window in _windows(words, size):
                if _norm_token("".join(w["text"] for w in window)) == joined_target:
                    add(window, "fuzzy", i)

        # fuzzy_ocr: edit-distance tolerance (best only)
        if ocr_tolerance and not cands:
            best, best_r, best_i = None, 0.0, 0
            for size in range(max(1, n - 2), n + 3):
                for i, window in _windows(words, size):
                    r = SequenceMatcher(None, joined_target,
                                        _norm_token("".join(w["text"] for w in window))).ratio()
                    if r > best_r:
                        best, best_r, best_i = window, r, i
            if best and best_r >= ocr_tolerance:
                add(best, f"fuzzy_ocr ({best_r:.2f})", best_i)
    return cands

# ── scoring: hints + section + row anchor ────────────────────────────────────

def _find_section_y(section: str, page: int, words) -> float | None:
    toks = [t for t in re.split(r"\W+", section) if len(t) > 2][:2]
    if not toks:
        return None
    page_words = [w for w in words if w["page"] == page]
    for i in range(len(page_words) - len(toks) + 1):
        if all(_norm_token(t) == _norm_token(w["text"])
               for t, w in zip(toks, page_words[i:i + len(toks)])):
            return page_words[i]["y0"]
    return None

def _score(c, hint, section_y, anchor_bbox, row_tol=0.006):
    tier_key = c["tier"].split(" ")[0]
    s = _TIER_SCORE.get(tier_key, 1)
    if hint and c["bbox"]["page"] == hint.get("page"):
        s += 4
    if section_y is not None and c["bbox"]["page"] == (hint or {}).get("page") \
            and c["bbox"]["y0"] >= section_y - row_tol:
        s += 2
    if anchor_bbox and c["bbox"]["page"] == anchor_bbox["page"]:
        cy = (c["bbox"]["y0"] + c["bbox"]["y1"]) / 2
        if anchor_bbox["y0"] - row_tol <= cy <= anchor_bbox["y1"] + row_tol:
            s += 6
    return s

def _pick(cands, hint, words, anchor_bbox):
    if not cands:
        return None, "not_found"
    section_y = None
    if hint and hint.get("section") and hint.get("page"):
        section_y = _find_section_y(hint["section"], hint["page"], words)
    best = max(cands, key=lambda c: (_score(c, hint, section_y, anchor_bbox),
                                     -c["bbox"]["page"], -c["order"]))
    return best["bbox"], best["tier"]

# ── public API ────────────────────────────────────────────────────────────────

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff")

def _load_words(doc_path):
    return extract_words_image(doc_path) if doc_path.lower().endswith(_IMAGE_EXTS) \
        else extract_words(doc_path)

def find_value_bbox(value, words, data_type="string", ocr_tolerance=0.82):
    """Back-compat single-value API: best candidate, no hints/anchor."""
    if value is None:
        return None, "null_value"
    cands = find_value_candidates(value, words, data_type, ocr_tolerance)
    return _pick(cands, None, words, None)


def snap_to_words(proposal: dict, value, words, data_type="string",
                  radius: float = 0.18):
    """LLM points -> OCR measures. Given a coarse LLM proposal (a box), snap
    to the matching-value word window nearest its center, within `radius`
    (normalized page units). The returned box has OCR-exact edges, and
    snapping only ever lands on a window that actually contains the value —
    so a successful snap is self-verifying. Returns (bbox, "snapped:<tier>")
    or (None, "snap_failed")."""
    cx = (proposal["x0"] + proposal["x1"]) / 2
    cy = (proposal["y0"] + proposal["y1"]) / 2
    cands = [c for c in find_value_candidates(value, words, data_type)
             if c["bbox"]["page"] == proposal["page"]]
    best, best_d = None, radius
    for c in cands:
        b = c["bbox"]
        d = math.hypot((b["x0"] + b["x1"]) / 2 - cx, (b["y0"] + b["y1"]) / 2 - cy)
        if d < best_d:
            best, best_d = c, d
    if best:
        return best["bbox"], f"snapped:{best['tier']}"
    return None, "snap_failed"

def align_fields(extracted: dict, field_types: dict, doc_path: str) -> dict:
    """Back-compat: single record, no hints/anchoring."""
    words = _load_words(doc_path)
    out = {}
    for field, value in extracted.items():
        bbox, quality = find_value_bbox(value, words, field_types.get(field, "string"))
        out[field] = {"value": value, "bbox": bbox, "match_quality": quality}
    return out

def align_extraction(extracted, field_types: dict, doc_path: str,
                     use_hints: bool = True, anchor_field: str | None = "auto"):
    """Full pipeline entry point. Handles both template kinds:
        single (dict)       -> {field: {value, bbox, match_quality}}
        array  (list[dict]) -> [{...}, ...]
    Uses each record's `_locations` (LLM hints, soft) and row-anchors array
    records on `anchor_field` ("auto" = first non-underscore field)."""
    words = _load_words(doc_path)

    def _align_one(record: dict) -> dict:
        hints = record.get("_locations", {}) if use_hints else {}
        fields = [k for k in record if not k.startswith("_")]
        anchor = fields[0] if anchor_field == "auto" and fields else anchor_field

        # pass 1: locate the anchor (hint-guided, no row constraint yet)
        anchor_bbox = None
        if anchor and record.get(anchor) is not None:
            cands = find_value_candidates(record[anchor], words,
                                          field_types.get(anchor, "string"))
            anchor_bbox, _ = _pick(cands, hints.get(anchor), words, None)

        out = {}
        for field in fields:
            value = record[field]
            if value is None:
                out[field] = {"value": None, "bbox": None, "match_quality": "null_value"}
                continue
            cands = find_value_candidates(value, words, field_types.get(field, "string"))
            row_anchor = anchor_bbox if field != anchor else None
            bbox, quality = _pick(cands, hints.get(field), words, row_anchor)
            out[field] = {"value": value, "bbox": bbox, "match_quality": quality,
                          "n_candidates": len(cands)}
        return out

    if isinstance(extracted, list):
        return [_align_one(r) for r in extracted]
    return _align_one(extracted)
