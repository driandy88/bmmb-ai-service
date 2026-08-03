"""
Stage 1 · Parse (brief §5; Change Brief §3) — PDF pages -> clean Markdown via
Gemini vision, now with automated accuracy hardening so ingestion runs unattended.

The source decks are designed slides, so plain text extraction fails on them; we
rasterise each page (PNG, no downscale) and read it TWICE, two independent ways:

  * Pass A — layout transcription (prompts/extraction.md): page -> Markdown,
    headings `##`, tables as Markdown tables, every figure verbatim.
  * Pass B — structured fact extraction (prompts/fact_extraction.md): JSON facts,
    each with its `verbatim` source string, plus the page's program_code + has_table.

Disagreement between two independent reads is the strongest signal of a misread,
so we also run self-consistency (§3.2) on numeric-dense pages: transcribe twice at
150 DPI and diff the numbers; if they differ, re-render at 300 DPI and take the
majority of three. Tables render at 300 DPI (§3.3). Everything Pass B sees is saved
to page_NNN.facts.json; verify.py (called automatically at the end) turns it into a
verification report — replacing the old human sign-off gate.

Reads  : data/00_raw/<doc_id>.pdf   (+ config/documents.yaml for page_overrides)
Writes : data/01_parsed/<doc_id>/page_NNN.md  +  page_NNN.facts.json  +  manifest.json
         data/01_parsed/<doc_id>/verification.json + review.html  (via verify.py)

Idempotent: a page with both artifacts is skipped unless --force. Pages marked
`skip: true` are never parsed. Resumable: a mid-run failure re-runs and resumes.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from config.settings import Settings, get_settings
from pipeline.verify import numbers, verify_document

_ROOT = Path(__file__).resolve().parent.parent          # services/rag-ingestion/
_PROMPT_A = _ROOT / "prompts" / "extraction.md"          # Pass A — layout transcription
_PROMPT_B = _ROOT / "prompts" / "fact_extraction.md"     # Pass B — structured facts
_DOCS_PATH = _ROOT / "config" / "documents.yaml"


def _load_documents() -> list[dict]:
    with open(_DOCS_PATH) as f:
        return yaml.safe_load(f) or []


def _skip_pages(doc: dict) -> set[int]:
    """1-based page numbers marked `skip: true` in page_overrides."""
    out: set[int] = set()
    for ov in doc.get("page_overrides") or []:
        if ov.get("skip"):
            out.update(int(p) for p in ov.get("pages", []))
    return out


def _client(s: Settings):
    from google import genai
    return genai.Client(vertexai=True, project=s.gcp_project_id, location=s.vertex_location)


def _render(pdf, i: int, dpi: int) -> bytes:
    """One page -> PNG bytes at the given DPI. Full size, no downscale (§3.3)."""
    return pdf.load_page(i).get_pixmap(dpi=dpi).tobytes("png")


# A Markdown table header-separator row (| --- | --- |) — the cheap pre-check that
# backs up Pass B's has_table so a missed table still triggers self-consistency (§3.2).
_MD_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]*-{3,}[\s:|-]*\|", re.M)


def _looks_tabular(md: str) -> bool:
    return bool(_MD_TABLE_SEP.search(md or ""))


def _transcribe(client, model: str, prompt: str, png: bytes, *, retries: int = 3) -> str:
    """Pass A: one page image -> Markdown. temperature=0 for faithful, repeatable output."""
    from google.genai import types
    part = types.Part.from_bytes(data=png, mime_type="image/png")
    cfg = types.GenerateContentConfig(temperature=0, max_output_tokens=8192)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(model=model, contents=[prompt, part], config=cfg)
            return (resp.text or "").strip()
        except Exception as e:  # transient Vertex error — back off and retry
            last = e
            time.sleep(2 * (attempt + 1))
    raise last  # exhausted retries; let the stage stop so a re-run resumes


def _parse_facts_json(text: str) -> dict:
    """Pass B returns JSON; tolerate an accidental ```json fence. Raise on garbage."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return json.loads(t)


def _extract_facts(client, model: str, prompt: str, png: bytes, *, retries: int = 3) -> dict:
    """Pass B: one page image -> structured facts JSON. response_mime_type forces JSON.
    If it never parses, return an empty structure carrying `_parse_error` so verify.py
    flags the page rather than silently trusting a page it could not fact-check."""
    from google.genai import types
    part = types.Part.from_bytes(data=png, mime_type="image/png")
    cfg = types.GenerateContentConfig(temperature=0, max_output_tokens=4096,
                                      response_mime_type="application/json")
    last = ""
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(model=model, contents=[prompt, part], config=cfg)
            last = resp.text or ""
            return _parse_facts_json(last)
        except json.JSONDecodeError:
            time.sleep(1 * (attempt + 1))          # bad JSON — retry the read
        except Exception:
            time.sleep(2 * (attempt + 1))          # transient Vertex error — back off
    return {"program_code": None, "has_table": False, "facts": [],
            "unreadable_regions": [], "_parse_error": True}


def _majority(cands: list[str]) -> tuple[str, bool]:
    """Group hi-res transcriptions by their numeric signature; if >=2 of 3 agree,
    return that transcription and True. No majority -> first candidate and False."""
    sigs: dict[tuple, list[str]] = {}
    for md in cands:
        sigs.setdefault(tuple(numbers(md)), []).append(md)
    for _, group in sigs.items():
        if len(group) >= 2:
            return group[0], True
    return cands[0], False


def _process_page(client, s: Settings, pdf, i: int, prompt_a: str, prompt_b: str) -> dict:
    """Two-pass read + self-consistency for one page. Returns the chosen Markdown,
    Pass-B facts, the DPI actually used, and the self-consistency outcome."""
    base_png = _render(pdf, i, s.parse_dpi)
    md = _transcribe(client, s.vision_model_id, prompt_a, base_png)
    facts = _extract_facts(client, s.vision_model_id, prompt_b, base_png)

    dense = (bool(facts.get("has_table")) or _looks_tabular(md)
             or len(facts.get("facts") or []) >= s.self_consistency_min_facts)
    dpi_used = s.parse_dpi
    consistency = "not_checked"

    if dense:
        md2 = _transcribe(client, s.vision_model_id, prompt_a, base_png)  # §3.2 second read
        if numbers(md) == numbers(md2):
            consistency = "identical"
        else:
            hi_png = _render(pdf, i, s.parse_dpi_tables)                  # escalate resolution
            cands = [_transcribe(client, s.vision_model_id, prompt_a, hi_png) for _ in range(3)]
            md, agreed = _majority(cands)
            facts = _extract_facts(client, s.vision_model_id, prompt_b, hi_png)  # re-read facts at hi-res
            dpi_used = s.parse_dpi_tables
            consistency = "resolved_hi_res" if agreed else "no_majority"

    return {"md": md, "facts": facts, "dpi": dpi_used, "self_consistency": consistency}


def run(args) -> int:
    import fitz  # pymupdf (heavy; imported lazily so --help stays dep-free)

    settings = get_settings()  # config.settings loaded .env at import time
    if not settings.gcp_project_id:
        print("[stage1_parse] GCP_PROJECT_ID is not set — cannot call Vertex. Add it to .env.")
        return 1

    prompt_a = _PROMPT_A.read_text()
    prompt_b = _PROMPT_B.read_text()
    documents = _load_documents()
    if args.doc:
        documents = [d for d in documents if d["doc_id"] == args.doc]
        if not documents:
            print(f"[stage1_parse] no doc_id={args.doc!r} in documents.yaml")
            return 1

    client = _client(settings)
    raw_dir = _ROOT / settings.data_dir / "00_raw"
    out_root = _ROOT / settings.data_dir / "01_parsed"

    force = getattr(args, "force", False)
    for doc in documents:
        doc_id = doc["doc_id"]
        pdf_path = raw_dir / (doc.get("source_pdf") or f"{doc_id}.pdf")
        if not pdf_path.exists():
            print(f"[stage1_parse] missing {pdf_path} — place the source PDF there. Skipping {doc_id}.")
            continue

        out_dir = out_root / doc_id
        out_dir.mkdir(parents=True, exist_ok=True)
        skip = _skip_pages(doc)
        include = {int(p) for p in (doc.get("include_pages") or [])}   # empty = every page
        version = getattr(args, "version", None) or doc.get("version")

        pdf = fitz.open(pdf_path)
        n = pdf.page_count
        scope = f"only {sorted(include)}" if include else "all"
        print(f"[stage1_parse] {doc_id} v{version}: {n} pages · pages {scope} · "
              f"skip {sorted(skip) or '—'} · {settings.parse_dpi}/{settings.parse_dpi_tables} DPI · "
              f"model {settings.vision_model_id}")

        parsed, cached, skipped, excluded = [], [], [], []
        for i in range(n):
            page_no = i + 1
            if include and page_no not in include:
                excluded.append(page_no)          # doc scoped to specific pages (e.g. internal criteria)
                continue
            md_out = out_dir / f"page_{page_no:03d}.md"
            facts_out = out_dir / f"page_{page_no:03d}.facts.json"
            if page_no in skip:
                skipped.append(page_no)
                continue
            if md_out.exists() and facts_out.exists() and not force:
                cached.append(page_no)
                continue
            res = _process_page(client, settings, pdf, i, prompt_a, prompt_b)
            md_out.write_text(res["md"] + "\n")
            facts_out.write_text(json.dumps(
                {"page": page_no, "dpi": res["dpi"], "self_consistency": res["self_consistency"],
                 **res["facts"]}, ensure_ascii=False, indent=2))
            parsed.append(page_no)
            nfacts = len(res["facts"].get("facts") or [])
            print(f"  page {page_no:>3}/{n} -> {md_out.name} ({len(res['md'])} chars · "
                  f"{nfacts} facts · {res['dpi']} DPI · {res['self_consistency']})")
        pdf.close()

        manifest = {
            "doc_id": doc_id,
            "title": doc.get("title"),
            "version": version,
            "program_code": doc.get("program_code"),
            "default_access_tier": doc.get("default_access_tier", "customer"),
            "page_count": n,
            "parsed": parsed,
            "cached": cached,
            "skipped": skipped,
            "excluded": excluded,
            "vision_model": settings.vision_model_id,
            "dpi": settings.parse_dpi,
            "dpi_tables": settings.parse_dpi_tables,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"[stage1_parse] {doc_id}: parsed {len(parsed)}, cached {len(cached)}, "
              f"skipped {len(skipped)} -> {out_dir}")

        # ── Automated verification replaces the human gate (Change Brief §4) ──
        # Runs every time, silently, on the saved artifacts. Never blocks.
        summary = verify_document(doc, settings)
        if summary:
            print(f"[stage1_parse] {doc_id}: verify -> {summary['passed']} pass, "
                  f"{summary['flagged']} flagged"
                  + (f" ({', '.join(summary['reasons'])})" if summary['reasons'] else "")
                  + f" · report {summary['report']}")
    return 0
