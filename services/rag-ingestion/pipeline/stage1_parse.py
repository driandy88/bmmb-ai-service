"""
Stage 1 · Parse (brief §5) — PDF pages -> clean Markdown via Gemini vision.

The source decks are designed slides, so plain text extraction fails on them; we
rasterise each page (~150 DPI) and transcribe it with a vision model using
prompts/extraction.md (headings -> `##`, tables -> real Markdown tables, every
figure/currency/percentage/date preserved exactly, `[unreadable]` over a guess).

Reads  : data/00_raw/<doc_id>.pdf   (+ config/documents.yaml for page_overrides)
Writes : data/01_parsed/<doc_id>/page_NNN.md  +  manifest.json

Idempotent: a page already parsed is skipped unless --force. Pages marked
`skip: true` in documents.yaml (company profile / filler) are never parsed.
Resumable: a mid-run failure just re-runs and picks up where it left off.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from config.settings import Settings, get_settings

_ROOT = Path(__file__).resolve().parent.parent          # services/rag-ingestion/
_PROMPT_PATH = _ROOT / "prompts" / "extraction.md"
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


def _transcribe(client, model: str, prompt: str, png: bytes, *, retries: int = 3) -> str:
    """One page image -> Markdown. temperature=0 for faithful, repeatable output."""
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


def run(args) -> int:
    import fitz  # pymupdf (heavy; imported lazily so --help stays dep-free)

    settings = get_settings()  # config.settings loaded .env at import time
    if not settings.gcp_project_id:
        print("[stage1_parse] GCP_PROJECT_ID is not set — cannot call Vertex. Add it to .env.")
        return 1

    prompt = _PROMPT_PATH.read_text()
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
              f"skip {sorted(skip) or '—'} · {settings.parse_dpi} DPI · model {settings.vision_model_id}")

        parsed, cached, skipped, excluded = [], [], [], []
        for i in range(n):
            page_no = i + 1
            if include and page_no not in include:
                excluded.append(page_no)          # doc scoped to specific pages (e.g. internal criteria)
                continue
            out = out_dir / f"page_{page_no:03d}.md"
            if page_no in skip:
                skipped.append(page_no)
                continue
            if out.exists() and not force:
                cached.append(page_no)
                continue
            pix = pdf.load_page(i).get_pixmap(dpi=settings.parse_dpi)
            md = _transcribe(client, settings.vision_model_id, prompt, pix.tobytes("png"))
            out.write_text(md + "\n")
            parsed.append(page_no)
            print(f"  page {page_no:>3}/{n} -> {out.name} ({len(md)} chars)")
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
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"[stage1_parse] {doc_id}: parsed {len(parsed)}, cached {len(cached)}, "
              f"skipped {len(skipped)} -> {out_dir}")
    return 0
