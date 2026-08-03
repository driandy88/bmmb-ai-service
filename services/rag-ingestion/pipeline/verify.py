"""
Automated verification (Change Brief §4) — replaces the human sign-off gate.

Runs at the end of Stage 1 (and standalone via `cli.py verify`) with NO human
interaction and NO blocking prompt. It reads the two-pass artifacts Stage 1 saved
(page_NNN.md from Pass A, page_NNN.facts.json from Pass B) and runs, per page:

  Check 1 — cross-pass agreement: every Pass-B fact's verbatim number appears in
            Pass A's Markdown, and self-consistency (§3.2) did not fail.
  Check 2 — numeric sanity bounds (config/sanity_rules.yaml): a value outside its
            plausible range flags the page (catches RM5,000,000 -> RM5,000).
  Check 3 — cross-check against product truth: extracted financing_size_max vs the
            workbook quantum in the chat service's products.yaml. Mismatch flags as
            CONTRADICTS_PRODUCT_CONFIG (states both values, takes no side — the deck
            may simply be newer than the config).
  Guard   — program identity (§3.5): Pass B's program_code must match the code
            declared for the document in documents.yaml (protects MHP-i / MIHP-i).

Outputs (both under data/01_parsed/<doc_id>/):
  * verification.json — machine-readable per-page status + the flagged-page set that
    Stage 5 reads to mark chunks `needs_review` (excluded from customer retrieval).
  * review.html — page image beside extracted Markdown, numbers highlighted, flagged
    pages sorted to the top. Written silently every run; NOTHING blocks on it.

Behaviour: zero flags -> the pipeline continues unattended through Stage 7. Flags
present -> it STILL continues, but the flagged pages' chunks are quarantined from
the customer channel rather than blocking the clean pages.
"""
from __future__ import annotations

import base64
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from config.settings import Settings, get_settings

_ROOT = Path(__file__).resolve().parent.parent
_DOCS_PATH = _ROOT / "config" / "documents.yaml"
_SANITY_PATH = _ROOT / "config" / "sanity_rules.yaml"

# Currency amounts, percentages, plain numbers — normalised to a bare digit token
# (commas stripped) so two reads / a fact and the Markdown compare on digits alone.
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# For review.html highlighting we keep the printed form (RM / % included).
_HL_RE = re.compile(r"(RM\s?[\d,]+(?:\.\d+)?|\d+(?:\.\d+)?\s?%|\b\d[\d,]*(?:\.\d+)?\b)")


def numbers(text: str) -> list[str]:
    """Sorted, de-duplicated numeric tokens with thousands separators removed.
    The comparison primitive for self-consistency (Stage 1) and cross-pass (Check 1)."""
    return sorted({m.group(0).replace(",", "") for m in _NUM_RE.finditer(text or "")})


# ── config loading ───────────────────────────────────────────────────────────

def _load_documents() -> list[dict]:
    with open(_DOCS_PATH) as f:
        return yaml.safe_load(f) or []


def _load_sanity() -> dict:
    with open(_SANITY_PATH) as f:
        return yaml.safe_load(f) or {}


def _load_products(settings: Settings) -> dict[str, dict]:
    """program key -> quantum row from the chat service's products.yaml (single
    source of truth; not duplicated here). Empty if the file is unavailable."""
    path = (_ROOT / settings.products_yaml).resolve()
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return {row["program"]: row for row in (data.get("quantum") or []) if row.get("program")}


# ── the three checks + the identity guard ────────────────────────────────────

def _bound_for(field: str | None, unit: str | None, bounds: list[dict]) -> dict | None:
    """Match a fact to its bound rule: exact field name first, then an unambiguous
    unit (MYR / years). An ambiguous unit like '%' (four different rules) never
    matches by unit — only by field — so we don't bounds-check against the wrong range."""
    f = (field or "").strip().lower()
    for rule in bounds:
        if f in {x.lower() for x in (rule.get("fields") or [])}:
            return rule
    u = (unit or "").strip().lower()
    if u:
        by_unit = [r for r in bounds if (r.get("unit") or "").strip().lower() == u]
        if len(by_unit) == 1:
            return by_unit[0]
    return None


def _as_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _check_page(page_no: int, md: str, facts_data: dict, doc: dict,
                bounds: list[dict], product_max: int | None) -> dict:
    """All checks for one page -> {status, checks[]}. status='flag' if any check flags."""
    checks: list[dict] = []

    def flag(category: str, detail: str):
        checks.append({"category": category, "status": "flag", "detail": detail})

    # Couldn't fact-check the page at all -> conservative flag (a flagged page is cheap).
    if facts_data.get("_parse_error"):
        flag("fact_parse", "Pass B fact extraction did not return valid JSON — page not verifiable")
    if facts_data.get("_missing"):
        flag("fact_parse", "no page_NNN.facts.json — re-parse with the two-pass Stage 1")

    facts = facts_data.get("facts") or []

    # ── Check 1 · cross-pass agreement + self-consistency ────────────────────
    sc = facts_data.get("self_consistency")
    if sc == "no_majority":
        flag("self_consistency", "two hi-res re-reads did not agree on the numbers (§3.2)")
    pa_nums = set(numbers(md))
    for fact in facts:
        vb = (fact.get("verbatim") or "").strip()
        if not vb:
            continue                        # no source string to check faithfully
        vnums = set(numbers(vb))
        if vnums and not vnums <= pa_nums:
            flag("cross_pass",
                 f"{fact.get('field')}={fact.get('value')} (\"{vb}\") not found in Pass A transcription")

    # ── Check 2 · numeric sanity bounds ──────────────────────────────────────
    for fact in facts:
        val = _as_number(fact.get("value"))
        if val is None:
            continue
        rule = _bound_for(fact.get("field"), fact.get("unit"), bounds)
        if not rule:
            continue
        lo, hi = rule.get("min"), rule.get("max")
        if (lo is not None and val < lo) or (hi is not None and val > hi):
            flag("out_of_bounds",
                 f"{fact.get('field')}={fact.get('value')} {rule.get('unit','')} outside "
                 f"{rule['kind']} range [{lo}, {hi}]")

    # ── Check 3 · cross-check against product truth ──────────────────────────
    if product_max is not None:
        for fact in facts:
            if (fact.get("field") or "").lower() == "financing_size_max":
                val = _as_number(fact.get("value"))
                if val is not None and val != float(product_max):
                    flag("contradicts_product_config",
                         f"financing_size_max={fact.get('value')} contradicts products.yaml "
                         f"({int(product_max)}) — deck may be newer; report only, no side taken")

    # ── Guard · program identity (§3.5) ──────────────────────────────────────
    doc_pc = (doc.get("program_code") or "").strip().upper()
    page_pc = (facts_data.get("program_code") or "").strip().upper()
    if doc_pc and page_pc and page_pc != doc_pc:
        flag("program_identity",
             f"page reads program {facts_data.get('program_code')!r}, document is declared {doc.get('program_code')!r}")

    status = "flag" if any(c["status"] == "flag" for c in checks) else "pass"
    return {"page": page_no, "status": status, "self_consistency": sc,
            "program_code": facts_data.get("program_code"), "checks": checks}


# ── report artifacts ─────────────────────────────────────────────────────────

def _parsed_pages(parsed_dir: Path) -> list[int]:
    return sorted(int(p.stem.split("_")[1]) for p in parsed_dir.glob("page_*.md"))


def _read_facts(parsed_dir: Path, page_no: int) -> dict:
    p = parsed_dir / f"page_{page_no:03d}.facts.json"
    if not p.exists():
        return {"_missing": True, "facts": []}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"_parse_error": True, "facts": []}


def _highlight(md: str) -> str:
    return _HL_RE.sub(r"<mark>\1</mark>", html.escape(md))


def _write_review_html(doc: dict, settings: Settings, parsed_dir: Path,
                       page_results: dict[int, dict]) -> Path:
    """Side-by-side image + Markdown, numbers highlighted, flagged pages first.
    Rendered silently every run — it exists so a human CAN look, not so they must."""
    doc_id = doc["doc_id"]
    pages = _parsed_pages(parsed_dir)
    pdf_path = _ROOT / settings.data_dir / "00_raw" / (doc.get("source_pdf") or f"{doc_id}.pdf")

    imgs: dict[int, str] = {}
    if pdf_path.exists():
        import fitz  # pymupdf — only needed to render the review thumbnails
        pdf = fitz.open(pdf_path)
        for page_no in pages:
            png = pdf.load_page(page_no - 1).get_pixmap(dpi=110).tobytes("png")
            imgs[page_no] = base64.b64encode(png).decode()
        pdf.close()

    # flagged pages sorted to the top, then by page number
    order = sorted(pages, key=lambda p: (page_results[p]["status"] != "flag", p))
    rows = []
    for page_no in order:
        pr = page_results[page_no]
        md = (parsed_dir / f"page_{page_no:03d}.md").read_text()
        flagged = pr["status"] == "flag"
        reasons = "".join(
            f'<li><b>{html.escape(c["category"])}</b>: {html.escape(c["detail"])}</li>'
            for c in pr["checks"] if c["status"] == "flag")
        badge = (f'<div class="flag">&#9873; FLAGGED — excluded from customer retrieval'
                 f'<ul>{reasons}</ul></div>' if flagged else '<div class="pass">&#10003; passed</div>')
        img = (f'<img src="data:image/png;base64,{imgs[page_no]}" alt="page {page_no}">'
               if page_no in imgs else '<div class="noimg">(source PDF not present — text only)</div>')
        rows.append(
            f'<section class="row{" f" if flagged else ""}">'
            f'<div class="col img"><div class="pno">page {page_no}</div>{img}{badge}</div>'
            f'<div class="col md"><pre>{_highlight(md)}</pre></div></section>')

    n_flag = sum(1 for p in pages if page_results[p]["status"] == "flag")
    verified = doc.get("verified") is True     # advisory only (§5) — does not gate
    tier = doc.get("default_access_tier", "customer")
    head = html.escape(f'{doc_id} · {doc.get("title","")} · v{doc.get("version")} · '
                       f'{len(pages)} pages · tier={tier} · {settings.vision_model_id}')
    summary = (f'<div class="{"no" if n_flag else "ok"}">'
               f'{"&#9873;" if n_flag else "&#10003;"} {len(pages)-n_flag} passed · {n_flag} flagged '
               f'(auto-verification, no human gate)</div>')
    advisory = ('<div class="adv">&#128065; a human marked this document verified: true (advisory)</div>'
                if verified else '')
    page = (
        f'<!doctype html><meta charset="utf-8"><title>Verification — {html.escape(doc_id)}</title>'
        "<style>"
        "body{font:14px/1.5 system-ui,-apple-system,sans-serif;margin:0;color:#14110f;background:#f4f1eb}"
        "header{position:sticky;top:0;background:#0E2E63;color:#fff;padding:14px 20px;z-index:2}"
        "header h1{margin:0 0 4px;font-size:16px}header .sub{font-size:12px;opacity:.85}"
        ".ok{color:#7DE0A6;font-weight:700;margin-top:6px}.no{color:#ffd27d;font-weight:700;margin-top:6px}"
        ".adv{color:#cfe3ff;font-size:12px;margin-top:4px}"
        ".row{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px 20px;"
        "border-bottom:1px solid #e3dcd1;align-items:start}"
        ".row.f{background:#fff4f0;border-left:4px solid #e5484d}"
        ".col.img img{width:100%;border:1px solid #ccc;border-radius:6px}"
        ".pno{font-size:12px;color:#8c8378;margin-bottom:6px}"
        ".pass{color:#2b825b;font-weight:600;font-size:12px;margin-top:8px}"
        ".flag{color:#b42318;font-weight:700;font-size:12px;margin-top:8px}"
        ".flag ul{margin:4px 0 0;padding-left:18px;font-weight:400}"
        ".noimg{font-size:12px;color:#8c8378;padding:20px 0}"
        "pre{white-space:pre-wrap;word-wrap:break-word;background:#fff;border:1px solid #e7dfd3;"
        "border-radius:6px;padding:12px;margin:0;font:12.5px/1.5 ui-monospace,monospace}"
        "mark{background:#ffe08a;padding:0 2px;border-radius:2px}"
        "@media(max-width:800px){.row{grid-template-columns:1fr}}"
        "</style>"
        f'<header><h1>Automated verification — no human gate (Change Brief §4)</h1>'
        f'<div class="sub">{head}</div>{summary}{advisory}</header>{"".join(rows)}')

    out = parsed_dir / "review.html"
    out.write_text(page)
    return out


# ── public entry points ──────────────────────────────────────────────────────

def verify_document(doc: dict, settings: Settings | None = None) -> dict | None:
    """Run all checks for one document, write verification.json + review.html, and
    return a summary. Returns None if the document has not been parsed yet."""
    settings = settings or get_settings()
    doc_id = doc["doc_id"]
    parsed_dir = _ROOT / settings.data_dir / "01_parsed" / doc_id
    pages = _parsed_pages(parsed_dir)
    if not pages:
        return None

    bounds = (_load_sanity().get("bounds") or [])
    crosscheck = (_load_sanity().get("program_crosscheck") or {})
    products = _load_products(settings)
    product_key = crosscheck.get(doc.get("program_code"))
    product_row = products.get(product_key) if product_key else None
    product_max = product_row.get("max") if product_row else None

    page_results: dict[int, dict] = {}
    for page_no in pages:
        md = (parsed_dir / f"page_{page_no:03d}.md").read_text()
        facts_data = _read_facts(parsed_dir, page_no)
        page_results[page_no] = _check_page(page_no, md, facts_data, doc, bounds, product_max)

    flagged_pages = [p for p in pages if page_results[p]["status"] == "flag"]
    reasons = sorted({c["category"] for p in flagged_pages for c in page_results[p]["checks"]
                      if c["status"] == "flag"})
    report = _write_review_html(doc, settings, parsed_dir, page_results)

    verification = {
        "doc_id": doc_id,
        "version": str(doc.get("version")),
        "program_code": doc.get("program_code"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "product_crosscheck": {"key": product_key, "max": product_max} if product_key else None,
        "summary": {"pages": len(pages), "passed": len(pages) - len(flagged_pages),
                    "flagged": len(flagged_pages), "reasons": reasons},
        "flagged_pages": flagged_pages,
        "pages": [page_results[p] for p in pages],
    }
    (parsed_dir / "verification.json").write_text(json.dumps(verification, indent=2, ensure_ascii=False))

    return {"passed": len(pages) - len(flagged_pages), "flagged": len(flagged_pages),
            "reasons": reasons, "report": str(report), "flagged_pages": flagged_pages}


def flagged_pages(doc_id: str, settings: Settings | None = None) -> set[int]:
    """The set of pages verification flagged for a document, read from verification.json.
    Stage 5 uses it to mark the derived chunks `needs_review`. Empty if never verified."""
    settings = settings or get_settings()
    p = _ROOT / settings.data_dir / "01_parsed" / doc_id / "verification.json"
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text()).get("flagged_pages") or [])
    except (json.JSONDecodeError, OSError):
        return set()


def run(args) -> int:
    """CLI: re-run verification on already-parsed docs (no model calls, no re-parse)."""
    settings = get_settings()
    documents = _load_documents()
    if getattr(args, "doc", None):
        documents = [d for d in documents if d["doc_id"] == args.doc]
        if not documents:
            print(f"[verify] no doc_id={args.doc!r} in documents.yaml")
            return 1

    for doc in documents:
        summary = verify_document(doc, settings)
        if summary is None:
            print(f"[verify] {doc['doc_id']}: nothing parsed — run stage1 first. Skipping.")
            continue
        line = (f"[verify] {doc['doc_id']}: {summary['passed']} pass, {summary['flagged']} flagged"
                + (f" ({', '.join(summary['reasons'])})" if summary["reasons"] else "")
                + f" · report {summary['report']}")
        print(line)
        if getattr(args, "report", False) and summary["flagged_pages"]:
            print(f"         flagged pages (excluded from customer retrieval): {summary['flagged_pages']}")
    return 0
