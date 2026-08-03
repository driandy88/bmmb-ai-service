"""
Stage 6 · Embed (brief §5) — PLACEHOLDER, implemented in Phase 4.

Embed each chunk with gemini-embedding-001, output_dimensionality=1536, task type
RETRIEVAL_DOCUMENT (queries use RETRIEVAL_QUERY — never the same type for both).
Use the batch path for the initial load; retry with backoff; checkpoint so a
failure resumes rather than restarts. Record embedding_model + dimensions in the
run manifest — changing either is a full re-embed and a versioned migration.
"""
from __future__ import annotations


def run(args) -> int:
    print("[stage6_embed] scaffold only — implemented in Phase 4. See README.md build order.")
    return 0
