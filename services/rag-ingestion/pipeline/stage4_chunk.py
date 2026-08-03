"""
Stage 4 · Chunk (brief §5) — PLACEHOLDER, implemented in Phase 3.

Split curated docs on Markdown headings, one chunk per logical section. Target
300–800 tokens (ceiling ~1,000); sections under ~50 tokens merge upward. NEVER
split a Markdown table — keep heading + table whole even past the ceiling.
Prepend a breadcrumb to the embedded text ("MIHP-i › Financing size per SME › " +
body). No overlap for section chunks. Writes data/04_chunks/<corpus>/<doc_id>.jsonl.
"""
from __future__ import annotations


def run(args) -> int:
    print("[stage4_chunk] scaffold only — implemented in Phase 3. See README.md build order.")
    return 0
