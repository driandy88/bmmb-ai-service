"""Reshape the 'Financial Statements (Sdn Bhd)' template: 16 per-year parallel-array
fields -> one 'Financials By Year' row_group (one correlated row-object per comparative
year column), keyed by Financial Statement Date.

Why: the figures are currently Multiple ungrouped, so each comes back as its own flat
array with nothing binding a value to its year -- feed in more than one statement and the
arrays bleed across documents (the same failure we fixed for Bank Statements). Grouping
them makes each year one object; misalignment becomes structurally impossible. See the
head-to-head in services/extraction/manual_extraction_test.ipynb section 5.

Unlike the bank migration this creates NO new attributes -- all 21 already exist, so it
only re-wires them. Idempotent. DRY-RUN by default; pass --apply to write. Targets whichever
database .env selects (APP_ENV / DB_NAME) -- run against dev first, then prod:

    python scripts/migrate_fs_template_to_per_year.py            # dry-run: review the plan
    python scripts/migrate_fs_template_to_per_year.py --apply    # write to bmmb_dev
    # then, once verified + deployed: APP_ENV=prod ... --apply
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

TEMPLATE_NAME = "Financial Statements (Sdn Bhd)"
GROUP = "Financials By Year"

NEW_DESCRIPTION = (
    "A full set of financial statements for a private limited company (Sdn Bhd), prepared under MPERS or "
    "MFRS: directors' report, auditors' report, statement of financial position, statement of profit or "
    "loss, statement of changes in equity, statement of cash flows, and notes. The statements present two "
    "or three comparative year columns side by side, so the per-year figures are extracted as a "
    "'Financials By Year' row group -- ONE row object per year column, keyed by that column's Financial "
    "Statement Date, rather than as parallel arrays. Some figures appear only in the notes (notably "
    "depreciation and amortisation, and director advances), so the notes must be read. Four Boolean "
    "attributes record which constituent statements are present, since abridged or unaudited filings "
    "routinely omit the cash flow statement and the auditors' report. Document type: Audited or Unaudited "
    "Financial Statements."
)

# One row-object per comparative year column; Financial Statement Date keys the row.
PER_YEAR_FIELDS = [
    "Financial Statement Date",
    "Revenue or Turnover or Sales",
    "Costs or COGS",
    "Gross Profit",
    "Expenses or Opex or SG&A or Overheads",
    "Operating Profit or EBIT",
    "EBITDA",
    "Financing Cost",
    "Depreciation & Amortisation",
    "Other Income",
    "Profit Before Tax",
    "Net Profit",
    "Asset Value or Total Current Assets",
    "Liability Value",
    "Net Worth or Total Equity",
    "Advances Due to Director",
]

# Whole-document facts (one value per statement), kept ungrouped and Unique.
UNGROUPED_FIELDS = [
    "Balance Sheet Present",
    "Profit and Loss Statement Present",
    "Cash Flow Statement Present",
    "Auditor's Report Present",
    "Document Type",
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
    name_to_id = {ta["attribute"]["name"]: ta["attribute"]["id"] for ta in current["template_attributes"]}

    # Every field this migration references must already exist on the template -- fail loudly
    # rather than silently dropping one.
    missing = [n for n in PER_YEAR_FIELDS + UNGROUPED_FIELDS if n not in name_to_id]
    if missing:
        sys.exit(f"These expected attributes are not on the template: {missing}")
    extra = [n for n in name_to_id if n not in PER_YEAR_FIELDS + UNGROUPED_FIELDS]
    if extra:
        print(f"NOTE: template has attributes this migration doesn't map (left as-is): {extra}\n")

    print("Current wiring:")
    for ta in current["template_attributes"]:
        print(f"   - {ta['attribute']['name']:40s} freq={ta['frequency']:9s} row_group={ta['row_group']}")

    wiring = []
    for name in UNGROUPED_FIELDS:
        wiring.append({"attribute_id": name_to_id[name], "frequency": "Unique", "row_group": None})
    for name in PER_YEAR_FIELDS:
        wiring.append({"attribute_id": name_to_id[name], "frequency": "Multiple", "row_group": GROUP})

    if not args.apply:
        print(f"\nDRY-RUN complete. Nothing written. Would group {len(PER_YEAR_FIELDS)} fields under "
              f"{GROUP!r} and keep {len(UNGROUPED_FIELDS)} ungrouped. Re-run with --apply to write to {db}.")
        return

    config.update_template(tid, {"description": NEW_DESCRIPTION}, wiring)
    config.update_template(tid, {"llm_prompt": render_extraction_prompt(config.get_template(tid))}, None)

    print("\nAPPLIED. New wiring:")
    for ta in config.get_template(tid)["template_attributes"]:
        print(f"   - {ta['attribute']['name']:40s} freq={ta['frequency']:9s} row_group={ta['row_group']}")


if __name__ == "__main__":
    main()
