"""
Stage 5 · Enrich (brief §5; Change Brief §2, §4).

Attaches the full metadata schema to every chunk: a deterministic chunk_id
(hash of doc_id + version + content, so an unchanged doc re-runs as a no-op upsert
and a new version yields new ids — §7b), plus doc_title / source_uri deep-link /
dates / lang from documents.yaml.

This is where automated verification's page flags become the chunk-level
`needs_review` (Change Brief §4): a chunk is `needs_review=true` if ANY of its
source pages was flagged in verification.json — that chunk is later excluded from
customer-channel retrieval, exactly like access_tier. `approved_by` / `approved_at`
are no longer sourced from a human sign-off (that gate was removed, §2); they stay
in the record as `None`, reserved for formal product-team approval later.

Reads data/04_chunks/<corpus>/<doc_id>.jsonl → writes data/05_enriched/<corpus>/<doc_id>.jsonl.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from config.settings import get_settings
from pipeline.verify import flagged_pages

_ROOT = Path(__file__).resolve().parent.parent
_DOCS_PATH = _ROOT / "config" / "documents.yaml"


def _documents_by_id() -> dict[str, dict]:
    with open(_DOCS_PATH) as f:
        return {d["doc_id"]: d for d in (yaml.safe_load(f) or [])}


def _chunk_id(doc_id: str, version: str, section: str, content: str) -> str:
    h = hashlib.sha256(f"{doc_id}|{version}|{section}|{content}".encode()).hexdigest()
    return f"{doc_id}:{version}:{h[:16]}"


def _deeplink(uri: str | None, pages: list[int]) -> str | None:
    return f"{uri}#page={min(pages)}" if uri and pages else uri


def _enrich_record(chunk: dict, doc: dict, flagged: set[int]) -> dict:
    version = str(chunk.get("version") or doc.get("version"))
    pages = chunk.get("pages", [])
    return {
        "chunk_id": _chunk_id(chunk["doc_id"], version, chunk["section"], chunk["content"]),
        "corpus": chunk["corpus"],
        "program_code": chunk.get("program_code"),
        "section": chunk["section"],
        "doc_id": chunk["doc_id"],
        "doc_title": doc.get("title"),
        "source_uri": _deeplink(doc.get("source_uri"), pages),
        "version": version,
        "effective_date": str(doc.get("effective_date")) if doc.get("effective_date") else None,
        "expiry_date": str(doc.get("expiry_date")) if doc.get("expiry_date") else None,
        "content_type": chunk.get("content_type", "standard"),
        "access_tier": chunk.get("access_tier", "customer"),
        "lang": doc.get("lang", "en"),
        "content": chunk["content"],
        "needs_review": bool(set(pages) & flagged),   # §4: any flagged source page taints the chunk
        "approved_by": None,                           # reserved for formal approval (§2)
        "approved_at": None,
        # carried for inspection / debugging (not DB columns)
        "_breadcrumb": chunk.get("breadcrumb"),
        "_pages": pages,
        "_tokens": chunk.get("tokens"),
    }


def run(args) -> int:
    settings = get_settings()
    docs = _documents_by_id()
    chunks_root = _ROOT / settings.data_dir / "04_chunks"
    if not chunks_root.exists():
        print("[stage5_enrich] no chunks — run stage4 first.")
        return 1

    corpora = [args.corpus] if getattr(args, "corpus", None) else [p.name for p in chunks_root.iterdir() if p.is_dir()]
    total = 0
    for corpus in corpora:
        cdir = chunks_root / corpus
        if not cdir.exists():
            continue
        for jf in sorted(cdir.glob("*.jsonl")):
            doc_id = jf.stem
            if args.doc and doc_id != args.doc:
                continue
            doc = docs.get(doc_id, {})
            flagged = flagged_pages(doc_id, settings)
            out_records = []
            for line in jf.read_text().splitlines():
                if line.strip():
                    out_records.append(_enrich_record(json.loads(line), doc, flagged))
            out_dir = _ROOT / settings.data_dir / "05_enriched" / corpus
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{doc_id}.jsonl"
            out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out_records) + "\n")
            n_review = sum(1 for r in out_records if r["needs_review"])
            total += len(out_records)
            note = f" ({n_review} needs_review)" if n_review else ""
            print(f"[stage5_enrich] {corpus}/{doc_id}: {len(out_records)} chunks{note} -> {out.name}")
    print(f"[stage5_enrich] total {total} enriched chunks.")
    return 0
