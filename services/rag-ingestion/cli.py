#!/usr/bin/env python3
"""
rag-ingestion — offline knowledge-layer pipeline for the BMMB Customer Service Agent.

One entrypoint, seven stages. Each stage reads the previous stage's output
directory and writes its own, so every stage is independently runnable,
idempotent, and inspectable (open ``data/03_curated/`` and read the Markdown
without running anything else). No stage reaches back more than one step; a
failed stage never corrupts an earlier one.

    python cli.py stage1 --doc talk_pp_commercial_financing
    python cli.py stage4 --corpus program
    python cli.py stage7 --dry-run

Run from the service root (``services/rag-ingestion/``) so ``config`` and
``pipeline`` import as top-level packages. See README.md for the build order and
the RAG Design Document for the rationale behind each decision.
"""
from __future__ import annotations

import argparse
import importlib

# stage key -> (module, one-line help, arg keys). Handlers are lazy-imported
# inside main() so `--help` runs with zero third-party dependencies installed.
STAGES: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("stage1", "pipeline.stage1_parse",  "Parse PDF pages -> Markdown (Gemini vision)",             ("doc", "force")),
    ("stage2", "pipeline.stage2_verify", "Build the human-review report + record sign-off (gate)",  ("doc",)),
    ("stage3", "pipeline.stage3_curate", "Reorganise pages -> per-program canonical docs",          ("doc",)),
    ("stage4", "pipeline.stage4_chunk",  "Chunk canonical docs (breadcrumbed, tables intact)",      ("doc", "corpus")),
    ("stage5", "pipeline.stage5_enrich", "Attach the metadata schema to each chunk",                ("doc", "corpus")),
    ("stage6", "pipeline.stage6_embed",  "Batch-embed chunks via Vertex (RETRIEVAL_DOCUMENT)",      ("corpus", "force")),
    ("stage7", "pipeline.stage7_index",  "Upsert chunks into Cloud SQL pgvector",                   ("corpus", "dry_run")),
]

# Reusable option specs, so each stage only exposes the flags it actually uses.
_ARGS: dict[str, tuple[tuple[str, ...], dict]] = {
    "doc":     (("--doc",),     dict(help="doc_id from config/documents.yaml (default: every document)")),
    "corpus":  (("--corpus",),  dict(help="limit to one corpus: program | guidelines_shariah | sales_dir")),
    "force":   (("--force",),   dict(action="store_true", help="re-run work already done instead of skipping")),
    "dry_run": (("--dry-run",), dict(action="store_true", dest="dry_run", help="report changes without writing")),
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rag-ingestion",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="stage", metavar="<stage>", required=True,
                           title="stages", description="run one at a time, in order")
    for key, module, help_, argkeys in STAGES:
        sp = sub.add_parser(key, help=help_)
        for ak in argkeys:
            flags, kw = _ARGS[ak]
            sp.add_argument(*flags, **kw)
        sp.set_defaults(_module=module)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    module = importlib.import_module(args._module)   # lazy: only this stage's deps load
    return int(module.run(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
