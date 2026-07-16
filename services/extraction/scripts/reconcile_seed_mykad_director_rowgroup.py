"""Reconcile seed_templates_attributes.sql with the MyKad row_group migration
(scripts/migrate_mykad_director_rowgroup.py), so a freshly-seeded database matches
the already-migrated dev/prod databases.

Note: the seed had ALL of MyKad's per-director fields ungrouped (row_group NULL),
while the live DB carried a partial 'Director' group -- so this closes real drift,
not just a rename. It sets the five per-director rows to row_group='Directors' and
replaces the stored llm_prompt with the regenerated grouped one (pulled from the
already-migrated dev DB; prompt text is id-independent). Idempotent. DRY-RUN by
default; pass --apply to rewrite the seed in place.
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

TEMPLATE_NAME = "MyKad (Director ID or Passport)"
SEED_TEMPLATE_ID = 10
GROUP = "Directors"
SEED_ATTR_IDS = {12, 14, 76, 77, 78}  # Director Name/NRIC/Front IC/Back IC/ID Type


def _read_sql_string(text, i):
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
    idx, span = start, None
    for _ in range(n):
        q = text.index("'", idx)
        span = _read_sql_string(text, q)
        idx = span[1] + 1
    return span


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="rewrite the seed in place (default: dry-run)")
    args = ap.parse_args()

    db_tid = next((t["id"] for t in config.list_templates() if t["name"] == TEMPLATE_NAME), None)
    if not db_tid:
        sys.exit(f"Template {TEMPLATE_NAME!r} not found in DB -- run the migration first.")
    prompt = config.get_template(db_tid)["llm_prompt"]

    text = SEED.read_text()

    rows = 0
    for aid in sorted(SEED_ATTR_IDS):
        pat = re.compile(
            rf"(INSERT INTO template_attributes \(id, template_id, attribute_id, frequency, row_group\) "
            rf"VALUES \(\d+, {SEED_TEMPLATE_ID}, {aid}, '\w+', )(NULL|'[^']*')(\);)"
        )
        text, k = pat.subn(rf"\g<1>'{GROUP}'\g<3>", text)
        if k != 1:
            sys.exit(f"  !! tid={SEED_TEMPLATE_ID} aid={aid}: expected exactly 1 row, matched {k}")
        rows += 1

    marker = f"INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES ({SEED_TEMPLATE_ID}, "
    start = text.index(marker)
    open_i, close_i = _nth_string_after(text, start + len(marker), 4)  # llm_prompt = 4th string
    escaped = prompt.replace("'", "''")
    prompt_changed = text[open_i + 1:close_i] != escaped
    text = text[:open_i + 1] + escaped + text[close_i:]

    print(f"  {TEMPLATE_NAME} (seed id {SEED_TEMPLATE_ID}): {rows} rows -> row_group={GROUP!r}; "
          f"llm_prompt {'replaced' if prompt_changed else 'already current'}")

    if not args.apply:
        print(f"\nDRY-RUN complete. Re-run with --apply to write {SEED.name}.")
        return
    SEED.write_text(text)
    print(f"\nAPPLIED. Wrote {SEED.name}.")


if __name__ == "__main__":
    main()
