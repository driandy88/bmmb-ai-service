"""Reshape the 'Bank Statements' template: monthly-summary parallel arrays -> per-transaction daily rows.

Why: real Malaysian statements (e.g. Standard Chartered) often have NO monthly
summary block, so the old template returned nulls. The new shape transcribes
every daily transaction row; monthly/yearly totals are computed downstream by
services/aggregation (POST /aggregate/bank), never summed by the LLM. See
services/extraction/manual_extraction_test.ipynb section 6 for the head-to-head.

Idempotent. DRY-RUN by default (prints the plan, writes nothing); pass --apply
to write. Targets whichever database .env selects (APP_ENV / DB_NAME), so:

    # against dev first (APP_ENV=dev in .env)
    python scripts/migrate_bank_template_to_daily.py            # dry-run: review the plan
    python scripts/migrate_bank_template_to_daily.py --apply    # write to bmmb_dev
    # then, once verified, point .env at prod (APP_ENV=prod / DB_NAME=bmmb_prod) and repeat

The old bank attributes (Bank Statement Month, Monthly Deposit/Withdrawal/End
Balance) are left in the global `attributes` table but unlinked from this
template -- harmless orphans, and a cleaner rollback path than deleting them.
"""
import argparse
import os
import sys
from pathlib import Path

import certifi

_EXTRACTION_DIR = Path(__file__).resolve().parents[1]   # services/extraction
sys.path.insert(0, str(_EXTRACTION_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_EXTRACTION_DIR / ".env")
os.environ.setdefault("SSL_CERT_FILE", certifi.where())  # Python 3.14 / macOS cert fix

from app import config  # noqa: E402
from app.schema_builder import render_extraction_prompt  # noqa: E402

TEMPLATE_NAME = "Bank Statements"
TRANSACTION_GROUP = "Transactions"

NEW_DESCRIPTION = (
    "One or more months of a current or savings account statement, one bank per file. Real statements are "
    "a chronological listing of DAILY transactions and frequently have NO monthly summary block, so figures "
    "are taken by transcribing every transaction row (date, description, debit, credit, running balance). "
    "Monthly and yearly totals are computed downstream by the aggregation service, NOT summed here by the "
    "model. Header fields (bank name, account number, statement period) are Unique; each transaction is one "
    "row of the Transactions group. Malay labels may appear: Debit, Kredit, Baki (balance). Document type: "
    "Monthly Bank Account Statements."
)

# Header fields — Unique (one value per statement). (name, data_type, description, example)
HEADER_ATTRS = [
    ("Bank Name", "Alphanumeric",
     "The issuing bank of this statement, from the statement header or footer (e.g. 'Maybank Berhad', "
     "'CIMB Bank Berhad', 'RHB Bank Berhad', 'Public Bank Berhad', 'Standard Chartered Bank Malaysia "
     "Berhad'). Return the full bank name as printed. One value per statement.",
     "Standard Chartered Bank Malaysia Berhad"),
    ("Account Number Masked", "Alphanumeric",
     "The account number this statement covers, from the header. Keep any masking the bank already applies "
     "(e.g. '****4321'); never unmask or invent digits. One value per statement.",
     "****4321"),
    ("Statement Period", "Alphanumeric",
     "The date range the statement covers, from the period header (e.g. '01 Jan 2026 to 30 Jun 2026'). "
     "Return as printed. One value per statement.",
     "01 Jan 2026 to 30 Jun 2026"),
]

# Transaction rows — one Transactions group row per printed line. (name, data_type, description, example)
TRANSACTION_ATTRS = [
    ("Transaction Date", "Datetime",
     "The posting/value date of one transaction row, from the transaction listing. You MUST return it in "
     "strict ISO format YYYY-MM-DD and nothing else -- convert any printed form to ISO (e.g. '23 Jan 2026' "
     "-> '2026-01-23', '23/01/2026' -> '2026-01-23'); never return the day-month-name form or a range. One "
     "row per printed transaction line, in the order printed.",
     "2026-01-23"),
    ("Transaction Description", "Alphanumeric",
     "The narrative/description of one transaction row as printed (payee, reference, or transaction type).",
     "IBG TRANSFER TO SUPPLIER"),
    ("Transaction Debit", "Numeric",
     "The debit (money out / withdrawal / outflow) amount of one transaction row, as a positive number in "
     "RM. Null if the row is a credit rather than a debit.",
     "3500.00"),
    ("Transaction Credit", "Numeric",
     "The credit (money in / deposit / inflow) amount of one transaction row, as a positive number in RM. "
     "Null if the row is a debit rather than a credit.",
     "12000.00"),
    ("Transaction Balance", "Numeric",
     "The running account balance printed after one transaction row, in RM. Negative if the account is "
     "overdrawn.",
     "8500.00"),
]


def _attr_id_by_name(name):
    return next((a["id"] for a in config.list_attributes() if a["name"] == name), None)


def _template_id_by_name(name):
    return next((t["id"] for t in config.list_templates() if t["name"] == name), None)


def _ensure_attribute(name, data_type, description, example, apply):
    """Create the attribute, or refresh its description/example if it already exists. Idempotent."""
    existing = _attr_id_by_name(name)
    if existing:
        if apply:
            config.update_attribute(existing, {"description": description, "example": example,
                                               "data_type": data_type})
        return existing, ("refreshed" if apply else "exists")
    if apply:
        return config.create_attribute(name, description, data_type, example)["id"], "created"
    return None, "would-create"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run, no writes)")
    args = ap.parse_args()

    db = os.getenv("DB_NAME", f"bmmb_{os.getenv('APP_ENV', 'dev')}")
    mode = "APPLY (writing)" if args.apply else "DRY-RUN (no writes)"
    print(f"Target: {db}  |  APP_ENV={os.getenv('APP_ENV', 'dev')}  |  mode={mode}\n")

    tid = _template_id_by_name(TEMPLATE_NAME)
    if not tid:
        sys.exit(f"Template {TEMPLATE_NAME!r} not found in {db}.")

    current = config.get_template(tid)
    print("Current wiring:")
    for ta in current["template_attributes"]:
        print(f"   - {ta['attribute']['name']:24s} freq={ta['frequency']:9s} row_group={ta['row_group']}")

    wiring = []
    print("\nAttributes:")
    for name, dt, desc, ex in HEADER_ATTRS:
        aid, status = _ensure_attribute(name, dt, desc, ex, args.apply)
        print(f"   - {name:24s} [{status}]")
        wiring.append({"attribute_id": aid, "frequency": "Unique", "row_group": None})

    doc_id = _attr_id_by_name("Document Type")   # shared, must already exist
    if not doc_id:
        sys.exit("Shared 'Document Type' attribute not found — unexpected; aborting.")
    wiring.append({"attribute_id": doc_id, "frequency": "Unique", "row_group": None})
    print(f"   - {'Document Type':24s} [existing/shared]")

    for name, dt, desc, ex in TRANSACTION_ATTRS:
        aid, status = _ensure_attribute(name, dt, desc, ex, args.apply)
        print(f"   - {name:24s} [{status}]  (group={TRANSACTION_GROUP})")
        wiring.append({"attribute_id": aid, "frequency": "Multiple", "row_group": TRANSACTION_GROUP})

    if not args.apply:
        print("\nDRY-RUN complete. Nothing written. Re-run with --apply to write to "
              f"{db}. (Old monthly-summary attributes would be unlinked but not deleted.)")
        return

    config.update_template(tid, {"description": NEW_DESCRIPTION}, wiring)
    # regenerate and store the llm_prompt from the new attribute set
    config.update_template(tid, {"llm_prompt": render_extraction_prompt(config.get_template(tid))}, None)

    print("\nAPPLIED. New wiring:")
    for ta in config.get_template(tid)["template_attributes"]:
        print(f"   - {ta['attribute']['name']:24s} freq={ta['frequency']:9s} row_group={ta['row_group']}")


if __name__ == "__main__":
    main()
