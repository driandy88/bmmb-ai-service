"""
Stage 3 · Curate (brief §5) — the curation body is Phase 3; the SIGN-OFF GATE is
Phase 2 and lives here now.

Reorganise page-ordered Markdown into one canonical document per program with
consistent sections (Overview · Eligibility · Financing size · Purpose · Tenure ·
Fees & benefits · Required documents), route pages across corpora, drop filler, and
CLASSIFY access_tier (internal criteria pages -> internal, §11). Writes
data/03_curated/<corpus>/<program_code>.md with YAML front-matter.

Hard gate (§5, §11): Stage 3 REFUSES to run for any document without a valid
Stage-2 sign-off. This is not a warning — no sign-off, no curation, no indexing.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from config.settings import get_settings
from pipeline.stage2_verify import signoff_ok

_ROOT = Path(__file__).resolve().parent.parent
_DOCS_PATH = _ROOT / "config" / "documents.yaml"


def _load_documents() -> list[dict]:
    with open(_DOCS_PATH) as f:
        return yaml.safe_load(f) or []


def run(args) -> int:
    get_settings()
    documents = _load_documents()
    if args.doc:
        documents = [d for d in documents if d["doc_id"] == args.doc]
        if not documents:
            print(f"[stage3_curate] no doc_id={args.doc!r} in documents.yaml")
            return 1

    # ── Hard gate: every targeted document needs a valid Stage-2 sign-off ──
    blocked = []
    for doc in documents:
        version = getattr(args, "version", None) or doc.get("version")
        ok, reason = signoff_ok(doc["doc_id"], version)
        if not ok:
            blocked.append((doc["doc_id"], reason))
    if blocked:
        for doc_id, reason in blocked:
            print(f"[stage3_curate] REFUSED {doc_id}: {reason}")
        print('[stage3_curate] Stage 3 requires a Stage-2 sign-off (§11 hard gate). '
              'Run: cli.py stage2 --doc <id> --approve --by "<name>"')
        return 1

    print(f"[stage3_curate] sign-off OK for {', '.join(d['doc_id'] for d in documents)} — "
          "curation body is implemented in Phase 3.")
    return 0
