"""
Stage 5 · Enrich (brief §5) — PLACEHOLDER, implemented in Phase 4.

Attach the full metadata schema to every chunk: deterministic chunk_id (same
input -> same id, for idempotent upsert), corpus, program_code, section, doc_id /
doc_title / source_uri (deep-linked to the page), version / effective_date /
expiry_date, content_type, access_tier, lang, and the Stage-2 approved_by /
approved_at. Reads data/04_chunks/, writes data/05_enriched/.
"""
from __future__ import annotations


def run(args) -> int:
    print("[stage5_enrich] scaffold only — implemented in Phase 4. See README.md build order.")
    return 0
