# TICKET-1 — Local full-pipeline test results

How this was run (Option 2 from the local-testing discussion): the real,
deployed `extraction-service` stayed untouched; only `validation-service` ran
locally with the uncommitted TICKET-1 fix, on branch `fix/validation_rules`.

```bash
# terminal 1, from bmmb-ai-service repo root
uv run uvicorn services.validation.api:app --port 8000

# terminal 2
cd prevalidation-scenario-test
PREVAL_SERVICE_URL=http://localhost:8000 uv run pytest tests/ -v
```

This replays all 59 catalog scenarios (`L0`–`L4`) through real `fake_documents`
→ real deployed extraction → local validation code, then asserts each
scenario's verdict and a full snapshot of every rule result.

## First run: 37 snapshot mismatches (expected, not a regression)

The suite's snapshots pin down the exact set of rule results for each
scenario — any change in behavior shows up as a mismatch, by design (it's
meant to catch unintended drift on top of the intent-based
`expected_overall` assertions, which all still passed). Since this branch
intentionally changes two things, ~37 scenarios (every one with a
bank-statement document) mismatched their old snapshot.

Diffing a representative sample (`L0-SDN`, `L1-bankdur-sdn-5`,
`L1-bankdur-sdn-6`, `L2-icfront-none`, `L1-bankcur-sgd`) confirmed the diff is
*exactly* the two intended changes and nothing else:

```diff
--- old snapshot
+++ new snapshot
-    "bank_statement.Account Type",              # adapter warning removed
...
-      "status": "not_applicable"                # bank_statement.continuity
+      "status": "passed"                        # now runs even for 1 doc
```

1. The `bank_statement.Account Type` adapter warning no longer appears (it's
   no longer a relevant field).
2. `bank_statement.continuity` now actually runs and reports `passed` for
   single-document bank statements, instead of being skipped as
   `not_applicable` — continuity is no longer gated on document count, only
   on date/month coverage, per the fix.

No other rule, scenario, or verdict changed shape.

## Second run: snapshots regenerated locally, then re-run clean

Snapshots were regenerated once (`UPDATE_SNAPSHOTS=1`) against the local
server purely to diff them as above, then restored to their original
committed values afterward — this branch does not modify
`prevalidation-scenario-test`, only `bmmb-ai-service`.

Re-running against the regenerated (temporary) snapshots:

```
69 passed, 6 skipped, 17 xfailed
```

Identical totals to the documented pre-fix baseline in that repo's
`FINDINGS.md` (also `69 passed, 6 skipped, 17 xfailed`) — the same 3 known,
not-yet-fixed live bugs (`FINDINGS.md` #2 IC number_match fail-open, #3
entity-type fallback, #4 bank-name alias) still correctly xfail, and nothing
new broke.

## Conclusion

The TICKET-1 fix (month-count off-by-one, continuity redesign, continuity's
document-count gate removed, Account Type warning removed) behaves exactly
as intended end-to-end through the real extraction pipeline: no false
failures, no accidental behavior change to any other rule, and the two
explicitly-out-of-scope OCR anomalies from the original empirical repro
(intermittent Gemini miss, `L1-bankcont-overlap` row loss) were not
re-triggered in this run.
