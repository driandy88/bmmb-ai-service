#!/usr/bin/env python3
"""
rag-ingestion — offline knowledge-layer pipeline for the BMMB Customer Service Agent.

One entrypoint, seven stages. Each stage reads the previous stage's output
directory and writes its own, so every stage is independently runnable,
idempotent, and inspectable (open ``data/03_curated/`` and read the Markdown
without running anything else). No stage reaches back more than one step; a
failed stage never corrupts an earlier one.

    python cli.py stage1 --doc mihp_i
    python cli.py stage4 --corpus program
    python cli.py stage7 --dry-run

Annual refresh is a single-document operation (§7b) — never a full rebuild:

    python cli.py all --doc mihp_i --version 2027.1     # parse -> ... -> index, one program

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
    ("stage1", "pipeline.stage1_parse",  "Parse PDF pages -> Markdown (Gemini vision)",             ("doc", "version", "force")),
    ("stage2", "pipeline.stage2_verify", "Build the human-review report + record sign-off (gate)",  ("doc", "version", "approve", "by")),
    ("stage3", "pipeline.stage3_curate", "Reorganise pages -> per-program canonical docs",          ("doc", "version")),
    ("stage4", "pipeline.stage4_chunk",  "Chunk canonical docs (breadcrumbed, tables intact)",      ("doc", "version", "corpus")),
    ("stage5", "pipeline.stage5_enrich", "Attach the metadata schema to each chunk",                ("doc", "version", "corpus")),
    ("stage6", "pipeline.stage6_embed",  "Batch-embed chunks via Vertex (RETRIEVAL_DOCUMENT)",      ("doc", "version", "corpus", "force")),
    ("stage7", "pipeline.stage7_index",  "Upsert chunks into Cloud SQL pgvector",                   ("doc", "version", "corpus", "dry_run", "supersede")),
]
_STAGE_MODULES = [m for _, m, _, _ in STAGES]

# Reusable option specs, so each stage only exposes the flags it actually uses.
_ARGS: dict[str, tuple[tuple[str, ...], dict]] = {
    "doc":       (("--doc",),       dict(help="doc_id from config/documents.yaml (default: every document)")),
    "version":   (("--version",),   dict(help="version label for this run (e.g. 2027.1); default: the doc's manifest version")),
    "corpus":    (("--corpus",),    dict(help="limit to one corpus: program | guidelines_shariah | sales_dir")),
    "force":     (("--force",),     dict(action="store_true", help="re-run work already done instead of skipping")),
    "dry_run":   (("--dry-run",),   dict(action="store_true", dest="dry_run", help="report changes without writing")),
    "supersede": (("--supersede",), dict(action="store_true", help="expire the prior version instead of deleting it (§7b)")),
    "approve":   (("--approve",),   dict(action="store_true", help="record sign-off for the document(s) instead of building the report")),
    "by":        (("--by",),        dict(help='approver name/role, required with --approve')),
}


def _add_args(sp: argparse.ArgumentParser, argkeys) -> None:
    for ak in argkeys:
        flags, kw = _ARGS[ak]
        sp.add_argument(*flags, **kw)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rag-ingestion",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="stage", metavar="<stage>", required=True,
                           title="stages", description="run one at a time, in order")

    all_sp = sub.add_parser("all", help="run stage1..stage7 in order for one document (annual refresh)")
    _add_args(all_sp, ("doc", "version", "force"))
    all_sp.set_defaults(_module="__all__")

    for key, module, help_, argkeys in STAGES:
        sp = sub.add_parser(key, help=help_)
        _add_args(sp, argkeys)
        sp.set_defaults(_module=module)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _load_env()  # populate os.environ before any stage imports config.settings

    if args._module == "__all__":
        for module in _STAGE_MODULES:
            rc = int(importlib.import_module(module).run(args) or 0)
            if rc != 0:
                print(f"[all] stopping — {module} returned {rc}")
                return rc
        return 0

    return int(importlib.import_module(args._module).run(args) or 0)


def _load_env() -> None:
    """Load the service .env after arg-parsing (so --help stays dependency-free)."""
    try:
        from pathlib import Path

        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv(Path(__file__).resolve().parent / ".env")


if __name__ == "__main__":
    raise SystemExit(main())
