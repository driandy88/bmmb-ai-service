"""
Stage 7 · Index (brief §5) — PLACEHOLDER, implemented in Phase 4.

Upsert enriched chunks into Cloud SQL rag_chunks keyed on chunk_id (idempotent),
populate content_tsv for keyword search. Support --dry-run (report inserts/
updates/deletes without writing) and --corpus scoping. Print a post-index report:
chunks per corpus, per access_tier, and chunks expiring within 30 days.
"""
from __future__ import annotations


def run(args) -> int:
    print("[stage7_index] scaffold only — implemented in Phase 4. See README.md build order.")
    return 0
