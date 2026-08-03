"""
Stage 2 · Verify (brief §5) — the human gate.

Builds a side-by-side review artifact (data/02_verified/<doc_id>/review.html): each
parsed page image next to its extracted Markdown, every numeric value highlighted,
so a product owner can confirm the transcription BEFORE anything is indexed. Sign-off
is recorded to signoff.json; Stage 3 refuses to run without a valid one, and a new
document version invalidates an old sign-off (re-verify on refresh).

    python cli.py stage2 --doc mihp_i                                 # build the review report
    python cli.py stage2 --doc mihp_i --approve --by "Aisyah (Product)"   # record the sign-off

`signoff_ok()` is the gate Stage 3 imports.
"""
from __future__ import annotations

import base64
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from config.settings import get_settings

_ROOT = Path(__file__).resolve().parent.parent
_DOCS_PATH = _ROOT / "config" / "documents.yaml"

# Currency amounts, percentages, plain numbers — the values a reviewer checks
# against the slide image. Highlighting them is the whole point of the report.
_NUM = re.compile(r"(RM\s?[\d,]+(?:\.\d+)?|\d+(?:\.\d+)?\s?%|\b\d[\d,]*(?:\.\d+)?\b)")


def _load_documents() -> list[dict]:
    with open(_DOCS_PATH) as f:
        return yaml.safe_load(f) or []


# ── Sign-off (the gate Stage 3 depends on) ──────────────────────────────────

def _verified_dir(doc_id: str) -> Path:
    return _ROOT / get_settings().data_dir / "02_verified" / doc_id


def load_signoff(doc_id: str) -> dict | None:
    p = _verified_dir(doc_id) / "signoff.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def signoff_ok(doc_id: str, version: str | None = None) -> tuple[bool, str]:
    """(ok, reason). Valid = file exists, has an approver, and its doc_version
    matches the version being processed — a re-version needs a fresh sign-off."""
    s = load_signoff(doc_id)
    if not s:
        return False, "no signoff.json — build the report and record sign-off first"
    if not s.get("approved_by"):
        return False, "signoff.json is missing approved_by"
    if version and s.get("doc_version") not in (None, version):
        return False, f"sign-off is for v{s.get('doc_version')}, not v{version} — re-verify"
    return True, f"approved by {s['approved_by']} at {s.get('approved_at')}"


def _record_signoff(doc: dict, by: str, version: str | None) -> Path:
    out_dir = _verified_dir(doc["doc_id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "doc_id": doc["doc_id"],
        "approved_by": by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "doc_version": version or doc.get("version"),
    }
    path = out_dir / "signoff.json"
    path.write_text(json.dumps(rec, indent=2))
    return path


# ── Review report ────────────────────────────────────────────────────────────

def _highlight(md: str) -> str:
    return _NUM.sub(r"<mark>\1</mark>", html.escape(md))


def _parsed_pages(parsed_dir: Path) -> list[int]:
    return sorted(int(p.stem.split("_")[1]) for p in parsed_dir.glob("page_*.md"))


def _build_report(doc: dict, settings) -> tuple[Path, bool] | None:
    import fitz  # pymupdf

    doc_id = doc["doc_id"]
    parsed_dir = _ROOT / settings.data_dir / "01_parsed" / doc_id
    pages = _parsed_pages(parsed_dir)
    if not pages:
        print(f"[stage2_verify] {doc_id}: nothing parsed — run stage1 first. Skipping.")
        return None

    pdf_path = _ROOT / settings.data_dir / "00_raw" / (doc.get("source_pdf") or f"{doc_id}.pdf")
    version = doc.get("version")
    signed, reason = signoff_ok(doc_id, version)

    pdf = fitz.open(pdf_path)
    rows = []
    for page_no in pages:
        md = (parsed_dir / f"page_{page_no:03d}.md").read_text()
        png = pdf.load_page(page_no - 1).get_pixmap(dpi=110).tobytes("png")
        b64 = base64.b64encode(png).decode()
        rows.append(
            f'<section class="row"><div class="col img"><div class="pno">page {page_no}</div>'
            f'<img src="data:image/png;base64,{b64}" alt="page {page_no}"></div>'
            f'<div class="col md"><pre>{_highlight(md)}</pre></div></section>'
        )
    pdf.close()

    tier = doc.get("default_access_tier", "customer")
    head = html.escape(f'{doc_id} · {doc.get("title","")} · v{version} · '
                       f'{len(pages)} pages · tier={tier} · {settings.vision_model_id}')
    banner = (f'<div class="ok">&#10003; SIGNED — {html.escape(reason)}</div>' if signed
              else f'<div class="no">&#10007; NOT SIGNED — {html.escape(reason)}</div>')
    page = (
        f'<!doctype html><meta charset="utf-8"><title>Review — {html.escape(doc_id)}</title>'
        "<style>"
        "body{font:14px/1.5 system-ui,-apple-system,sans-serif;margin:0;color:#14110f;background:#f4f1eb}"
        "header{position:sticky;top:0;background:#0E2E63;color:#fff;padding:14px 20px;z-index:2}"
        "header h1{margin:0 0 4px;font-size:16px}header .sub{font-size:12px;opacity:.85}"
        ".ok{color:#7DE0A6;font-weight:700;margin-top:6px}.no{color:#ffb4a2;font-weight:700;margin-top:6px}"
        ".row{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px 20px;"
        "border-bottom:1px solid #e3dcd1;align-items:start}"
        ".col.img img{width:100%;border:1px solid #ccc;border-radius:6px}"
        ".pno{font-size:12px;color:#8c8378;margin-bottom:6px}"
        "pre{white-space:pre-wrap;word-wrap:break-word;background:#fff;border:1px solid #e7dfd3;"
        "border-radius:6px;padding:12px;margin:0;font:12.5px/1.5 ui-monospace,monospace}"
        "mark{background:#ffe08a;padding:0 2px;border-radius:2px}"
        "@media(max-width:800px){.row{grid-template-columns:1fr}}"
        "</style>"
        f'<header><h1>Stage 2 review — verify the transcription before indexing</h1>'
        f'<div class="sub">{head}</div>{banner}</header>{"".join(rows)}'
    )
    out_dir = _verified_dir(doc_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "review.html"
    out.write_text(page)
    return out, signed


def run(args) -> int:
    settings = get_settings()
    documents = _load_documents()
    if args.doc:
        documents = [d for d in documents if d["doc_id"] == args.doc]
        if not documents:
            print(f"[stage2_verify] no doc_id={args.doc!r} in documents.yaml")
            return 1

    if getattr(args, "approve", False):
        by = getattr(args, "by", None)
        if not by:
            print('[stage2_verify] --approve requires --by "<name>"')
            return 1
        for doc in documents:
            version = getattr(args, "version", None) or doc.get("version")
            path = _record_signoff(doc, by, version)
            print(f"[stage2_verify] {doc['doc_id']}: signed off by {by} (v{version}) -> {path}")
        return 0

    for doc in documents:
        result = _build_report(doc, settings)
        if result:
            out, signed = result
            print(f"[stage2_verify] {doc['doc_id']}: review -> {out} "
                  f"({'signed' if signed else 'NOT signed'})")
    return 0
