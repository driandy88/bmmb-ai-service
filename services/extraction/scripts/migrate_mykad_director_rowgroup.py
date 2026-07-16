"""Complete and normalise the MyKad template's per-director row_group.

The live DB already had a 'Director' (singular) row_group covering Director
Name/NRIC/Back Side IC Present/ID Type, but NOT Front Side IC Present -- so one
correlated field was left as a loose parallel array, and the group name was
inconsistent with the 'Directors'/'Shareholders' groups on the SSM cluster. This
migration:

  - renames the group 'Director' -> 'Directors' (matching SSM Form 49 / Consent /
    CTOS, so the downstream person-merge keys are uniform), and
  - folds 'Front Side IC Present' into it,

so every per-director field on MyKad (Name, NRIC, Front Side IC Present, Back
Side IC Present, ID Type) is one correlated object per director. Document Type
stays Unique/ungrouped.

No new attributes -- pure re-wiring. Idempotent. DRY-RUN by default; pass --apply
to write. Targets whichever database .env selects (APP_ENV / DB_NAME) -- dev
first, then prod.
"""
import argparse
import os
import sys
from pathlib import Path

import certifi

_EXTRACTION_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXTRACTION_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_EXTRACTION_DIR / ".env")
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from app import config  # noqa: E402
from app.schema_builder import render_extraction_prompt  # noqa: E402

TEMPLATE_NAME = "MyKad (Director ID or Passport)"
GROUP = "Directors"
GROUPED_FIELDS = [
    "Director Name",
    "Director NRIC or Passport Number",
    "Front Side IC Present",
    "Back Side IC Present",
    "ID Type",
]


def _template_id_by_name(name):
    return next((t["id"] for t in config.list_templates() if t["name"] == name), None)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run, no writes)")
    args = ap.parse_args()

    db = os.getenv("DB_NAME", f"bmmb_{os.getenv('APP_ENV', 'dev')}")
    print(f"Target: {db}  |  APP_ENV={os.getenv('APP_ENV', 'dev')}  |  "
          f"mode={'APPLY (writing)' if args.apply else 'DRY-RUN (no writes)'}\n")

    tid = _template_id_by_name(TEMPLATE_NAME)
    if not tid:
        sys.exit(f"Template {TEMPLATE_NAME!r} not found in {db}.")

    current = config.get_template(tid)
    present = {ta["attribute"]["name"] for ta in current["template_attributes"]}
    missing = [f for f in GROUPED_FIELDS if f not in present]
    if missing:
        sys.exit(f"These expected fields are not on the template: {missing}")

    print("Current wiring:")
    for ta in current["template_attributes"]:
        print(f"   - {ta['attribute']['name']:36s} freq={ta['frequency']:9s} row_group={ta['row_group']}")

    grouped = set(GROUPED_FIELDS)
    wiring = []
    for ta in current["template_attributes"]:
        aname = ta["attribute"]["name"]
        aid = ta["attribute"]["id"]
        if aname in grouped:
            wiring.append({"attribute_id": aid, "frequency": "Multiple", "row_group": GROUP})
        else:
            wiring.append({"attribute_id": aid, "frequency": ta["frequency"], "row_group": None})

    if not args.apply:
        print(f"\nDRY-RUN complete. Would group {len(GROUPED_FIELDS)} fields under {GROUP!r} "
              f"(rename 'Director' -> 'Directors', fold in Front Side IC Present). "
              f"Re-run with --apply to write to {db}.")
        return

    config.update_template(tid, {}, wiring)
    config.update_template(tid, {"llm_prompt": render_extraction_prompt(config.get_template(tid))}, None)

    print("\nAPPLIED. New wiring:")
    for ta in config.get_template(tid)["template_attributes"]:
        print(f"   - {ta['attribute']['name']:36s} freq={ta['frequency']:9s} row_group={ta['row_group']}")


if __name__ == "__main__":
    main()
