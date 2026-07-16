"""Group the correlated per-entity fields on the SSM/director-cluster templates into row_groups,
so a director's name/NRIC/address (or a shareholder's name/address/percentage) can no longer
misalign across parallel arrays -- the same fix applied to bank and financial statements.

Groups only the *correlated* Multiple fields. Single Multiple lists with nothing to align
against (Business Nature, Legal Case Report, and SSM 24's lone Director NRIC) are left as-is.
Column names are kept consistent across templates ('Director Name', 'Director NRIC or Passport
Number', 'Director Address') so the downstream person-merge is trivial.

No new attributes -- pure re-wiring. Idempotent. DRY-RUN by default; pass --apply to write.
Targets whichever database .env selects (APP_ENV / DB_NAME) -- dev first, then prod.
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

# template name -> {group name -> [correlated field names to move into it]}
MIGRATIONS = {
    "SSM Form 49": {"Directors": ["Director Name", "Director NRIC or Passport Number", "Director Address"]},
    "Consent Form": {"Directors": ["Director Name", "Director NRIC or Passport Number"]},
    "CTOS Report": {"Directors": ["Director Name", "Director NRIC or Passport Number"]},
    "SSM Form 24": {"Shareholders": ["Shareholder Name", "Shareholder Address", "Shareholder Percentage"]},
}


def _template_id_by_name(name):
    return next((t["id"] for t in config.list_templates() if t["name"] == name), None)


def _migrate_one(name, groups, apply):
    tid = _template_id_by_name(name)
    if not tid:
        print(f"  !! template {name!r} not found -- skipped"); return
    tmpl = config.get_template(tid)

    field_to_group = {f: g for g, fields in groups.items() for f in fields}
    present = {ta["attribute"]["name"] for ta in tmpl["template_attributes"]}
    missing = [f for f in field_to_group if f not in present]
    if missing:
        print(f"  !! {name}: expected fields not on template: {missing} -- skipped"); return

    wiring = []
    for ta in tmpl["template_attributes"]:
        aname = ta["attribute"]["name"]
        aid = ta["attribute"]["id"]
        if aname in field_to_group:
            wiring.append({"attribute_id": aid, "frequency": "Multiple", "row_group": field_to_group[aname]})
        else:
            wiring.append({"attribute_id": aid, "frequency": ta["frequency"], "row_group": None})

    grouped = {g: fields for g, fields in groups.items()}
    print(f"  {name}: group " + "; ".join(f"{g} <- {fields}" for g, fields in grouped.items()))
    if not apply:
        return
    config.update_template(tid, {}, wiring)
    config.update_template(tid, {"llm_prompt": render_extraction_prompt(config.get_template(tid))}, None)
    new_groups = sorted({ta["row_group"] for ta in config.get_template(tid)["template_attributes"] if ta["row_group"]})
    print(f"     applied -> row_groups now: {new_groups}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run, no writes)")
    args = ap.parse_args()

    db = os.getenv("DB_NAME", f"bmmb_{os.getenv('APP_ENV', 'dev')}")
    print(f"Target: {db}  |  APP_ENV={os.getenv('APP_ENV', 'dev')}  |  "
          f"mode={'APPLY (writing)' if args.apply else 'DRY-RUN (no writes)'}\n")

    for name, groups in MIGRATIONS.items():
        _migrate_one(name, groups, args.apply)

    if not args.apply:
        print("\nDRY-RUN complete. Nothing written. Re-run with --apply to write to " + db + ".")


if __name__ == "__main__":
    main()
