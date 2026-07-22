"""Regenerate the stored llm_prompt for the Financial Statements (Sdn Bhd)
template so it matches the current render_extraction_prompt() output -- in
particular the per-row `_locations` list instructions added for per-value
provenance. The response_schema + system instruction already drive the model
correctly; this only stops the STORED prompt from describing the old
single-slot `_locations` shape.

One-time: run once after the prompt-generation logic (or the template's fields)
change. It does not run per extraction. DRY-RUN by default -- prints a unified
diff of stored vs regenerated. Pass --apply to UPDATE the Cloud SQL row. The
seed file is updated separately (by hand / a reconcile edit) so a fresh re-seed
carries the same prompt.
"""
import argparse
import difflib
import os
import sys
from pathlib import Path

import certifi
import sqlalchemy

_EXTRACTION_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXTRACTION_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_EXTRACTION_DIR / ".env")
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from app.config import _get_engine, get_template, list_templates  # noqa: E402
from app.schema_builder import render_extraction_prompt  # noqa: E402

TEMPLATE_NAME = "Financial Statements (Sdn Bhd)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="UPDATE the DB (default: dry-run)")
    args = ap.parse_args()

    tid = next((t["id"] for t in list_templates() if t["name"] == TEMPLATE_NAME), None)
    if tid is None:
        sys.exit(f"Template {TEMPLATE_NAME!r} not found.")
    tmpl = get_template(tid)

    old = tmpl["llm_prompt"] or ""
    # render_extraction_prompt rebuilds from the template's attributes and does
    # NOT read the stored prompt or append the runtime language guidance -- so
    # this is exactly what a stored prompt should hold.
    new = render_extraction_prompt(tmpl)

    if old == new:
        print("Stored prompt already matches the regenerated one -- nothing to do.")
        return

    diff = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile="stored (old)", tofile="regenerated (new)", lineterm="",
    )
    print("\n".join(diff))

    if not args.apply:
        print("\n--- DRY-RUN. Re-run with --apply to UPDATE the DB. ---")
        return

    with _get_engine().begin() as conn:
        conn.execute(
            sqlalchemy.text("UPDATE templates SET llm_prompt = :p WHERE id = :id"),
            {"p": new, "id": tid},
        )
    print(f"\nDB updated: llm_prompt for {TEMPLATE_NAME!r} (id={tid}).")
    print("Remember to update seed_templates_attributes.sql to match.")


if __name__ == "__main__":
    main()
