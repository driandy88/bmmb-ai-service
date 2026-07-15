"""Reconcile seed_templates_attributes.sql with the SSM/director-cluster row_group
migration (scripts/migrate_ssm_director_rowgroups.py), so a freshly-seeded database
matches the already-migrated dev/prod databases.

Two edits per affected template, mirroring the migration exactly:
  1. template_attributes: set row_group on the grouped rows (Directors / Shareholders).
  2. templates.llm_prompt: replace the stored prompt with the regenerated grouped one.

The regenerated prompt is pulled straight from the (already-migrated) dev DB -- prompt
text is id-independent (built from attribute names/descriptions/examples + frequency +
row_group), so it is byte-identical in the integer-id seed world. Idempotent: re-running
sets the same values. DRY-RUN by default; pass --apply to rewrite the seed in place.

    python scripts/reconcile_seed_ssm_director_rowgroups.py            # dry-run: report
    python scripts/reconcile_seed_ssm_director_rowgroups.py --apply    # rewrite the seed
"""
import argparse
import os
import re
import sys
from pathlib import Path

import certifi

_EXTRACTION_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXTRACTION_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_EXTRACTION_DIR / ".env")
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from app import config  # noqa: E402

SEED = _EXTRACTION_DIR / "seed_templates_attributes.sql"

# seed template id -> (template name in DB, group name, {seed attribute ids to group})
CLUSTERS = {
    2:  ("SSM Form 24",  "Shareholders", {9, 10, 11}),   # Shareholder Name/Address/Percentage
    4:  ("SSM Form 49",  "Directors",    {12, 13, 14}),  # Director Name/Address/NRIC
    11: ("Consent Form", "Directors",    {12, 14}),      # Director Name/NRIC
    14: ("CTOS Report",  "Directors",    {12, 14}),      # Director Name/NRIC
}


def _read_sql_string(text, i):
    """Span (open_quote_idx, close_quote_idx) of the SQL string literal starting at
    text[i] == "'", treating '' as an escaped quote."""
    assert text[i] == "'", f"expected opening quote at {i}"
    j = i + 1
    while j < len(text):
        if text[j] == "'":
            if j + 1 < len(text) and text[j + 1] == "'":
                j += 2
                continue
            return i, j
        j += 1
    raise ValueError("unterminated SQL string literal")


def _nth_string_after(text, start, n):
    """Span of the n-th SQL string literal at or after `start`."""
    idx, span = start, None
    for _ in range(n):
        q = text.index("'", idx)
        span = _read_sql_string(text, q)
        idx = span[1] + 1
    return span


def _set_row_groups(text, tid, group, aids):
    changed = 0
    for aid in sorted(aids):
        pat = re.compile(
            rf"(INSERT INTO template_attributes \(id, template_id, attribute_id, frequency, row_group\) "
            rf"VALUES \(\d+, {tid}, {aid}, '\w+', )(NULL|'[^']*')(\);)"
        )
        text, k = pat.subn(rf"\g<1>'{group}'\g<3>", text)
        if k != 1:
            sys.exit(f"  !! tid={tid} aid={aid}: expected exactly 1 template_attributes row, matched {k}")
        changed += 1
    return text, changed


def _replace_llm_prompt(text, tid, new_prompt):
    # VALUES (<id>, '<name>', '<description>', '<group_name>', '<llm_prompt>') -- llm_prompt
    # is the 4th string literal after the integer id.
    marker = f"INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES ({tid}, "
    start = text.index(marker)
    open_i, close_i = _nth_string_after(text, start + len(marker), 4)
    escaped = new_prompt.replace("'", "''")
    if text[open_i + 1:close_i] == escaped:
        return text, False
    return text[:open_i + 1] + escaped + text[close_i:], True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="rewrite the seed in place (default: dry-run)")
    args = ap.parse_args()

    text = SEED.read_text()
    total_rows, total_prompts = 0, 0
    for tid, (name, group, aids) in CLUSTERS.items():
        db_tid = next((t["id"] for t in config.list_templates() if t["name"] == name), None)
        if not db_tid:
            sys.exit(f"Template {name!r} not found in DB -- run the migration first.")
        prompt = config.get_template(db_tid)["llm_prompt"]

        text, n_rows = _set_row_groups(text, tid, group, aids)
        text, changed = _replace_llm_prompt(text, tid, prompt)
        total_rows += n_rows
        total_prompts += int(changed)
        print(f"  {name} (seed id {tid}): {n_rows} rows -> row_group={group!r}; "
              f"llm_prompt {'replaced' if changed else 'already current'}")

    if not args.apply:
        print(f"\nDRY-RUN complete. Would set {total_rows} row_group values and replace "
              f"{total_prompts} llm_prompt(s). Re-run with --apply to write {SEED.name}.")
        return
    SEED.write_text(text)
    print(f"\nAPPLIED. Wrote {SEED.name}: {total_rows} row_group values, {total_prompts} llm_prompt(s).")


if __name__ == "__main__":
    main()
