"""
Stage 1 · Parse (brief §5) — PLACEHOLDER, implemented in Phase 1.

Rasterise each PDF page (~150 DPI) and transcribe to Markdown with Gemini vision
using prompts/extraction.md: headings -> `##`, tables -> real Markdown tables,
every figure/currency/percentage/date preserved exactly, `[unreadable]` rather
than a guess. Reads data/00_raw/<doc_id>.pdf, writes data/01_parsed/<doc_id>/
page_NNN.md + manifest.json. Idempotent: skip parsed pages unless --force.
"""
from __future__ import annotations


def run(args) -> int:
    print("[stage1_parse] scaffold only — implemented in Phase 1. See README.md build order.")
    return 0
