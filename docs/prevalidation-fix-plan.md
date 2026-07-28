# Pre-validation Agent — Fix Plan

Source: `services/validation/docs/ticket-after-testing.md` (manual QA against the full
pipeline: real document upload → OCR extraction → validation rules → AI review),
cross-checked against the automated scenario suite at
`/Users/indra/Documents/BMMB/prevalidation-scenario-test` (hits the deployed
validation-service directly with clean synthetic payloads, AI review off) and an
empirical full-pipeline repro (real `fake_documents` → live extraction API → live
validation API, AI review on) for the highest-impact ticket.

This document is the outcome of a `/grill-me` session reconciling both sources.
Several original ticket descriptions turned out to be inaccurate about *where* the
bug lives (validation rules vs. OCR extraction) — corrected root causes are noted
per ticket.

## Scope

Fixing TICKET-1 through TICKET-8, plus two additional confirmed bugs surfaced by
the automated suite (FINDINGS #1, #2) that weren't in the original ticket list.
**TICKET-9 (Borang B / sole-prop policy decisions) is explicitly deferred** — it's
a policy call, not a bug, and blocks on business/compliance sign-off separate from
this effort.

**Sequencing:** TICKET-1 first (highest impact, was suspected of cascading into
most other bank-statement rule failures) — implement, verify empirically, then
triage the rest. Turned out TICKET-1 was *not* actually cascading into the others;
see below.

**Verification method, per ticket:** implement fix → empirical repro via a
subagent using real `fake_documents` through the live extraction/validation
pipeline → add/update pytest regression tests → move to next ticket.

---

## TICKET-1 — Bank statement month-count is unreliable / systematically undercounts

**Original suspicion (ticket-after-testing.md):** upstream extraction issue —
possible file-count/temperature limits, multi-file/mixed-shape uploads worst
affected, cascading into false FAILED across nearly every bank-statement rule.

**Actual root cause (confirmed empirically, not extraction-side at all):** a
deterministic off-by-one bug in `services/validation/rules/date_logic.py:242-246`
(`verify_bank_statement_duration`):

```python
rd = relativedelta(latest_end, earliest_start)
months_covered = rd.years * 12 + rd.months
if rd.days > 0:
    months_covered += 1
```

Whenever the last transaction's day-of-month is ≤ the first transaction's
day-of-month (e.g. `2026-01-31 → 2026-06-30`, a very ordinary statement-period
pattern), `rd.days == 0` and the `+1` never fires — undercounting the true
inclusive month count by exactly one. Confirmed directly:

```
relativedelta(2026-06-30, 2026-01-31) → 0y 5m 0d  → months_covered = 5  (wrong; should be 6)
relativedelta(2026-06-30, 2026-01-01) → 0y 5m 29d → months_covered = 6  (correct, by luck)
```

L0-SDN's transactions happened to start on the 1st of the month, so it dodged the
bug — which is exactly why some scenarios looked broken and others didn't, matching
the ticket's "inconsistent, cascading" description. Extraction itself was verified
correct in every scenario tested (L0-SDN exact match to ground truth,
L3-mixed-upload 6/6, L1-bankcont-gap's intentional gap reproduced correctly).

A second, structural issue was found alongside it: **`bank_statement.continuity`
can never fire via `/validate/from-extraction`**, because the adapter always folds
every uploaded file into one consolidated document, and the rule as currently
written requires 2+ separate `BankStatementDoc` objects to compare.
`services/aggregation` was checked as a possible fix for this and ruled out — it's
not wired into the pipeline at all (nothing calls it), and even if it were, it
checks a different kind of continuity (per-statement running-balance integrity,
no cross-document date comparison).

### Fix

1. Count months as the number of distinct `(year, month)` buckets already
   computed in `build_bank_statement_doc`, not via `relativedelta` day-remainder
   arithmetic. Immune to date-alignment edge cases by construction.
2. Redesign `check_bank_statement_continuity` to detect gaps by walking those same
   sorted `(year, month)` buckets from earliest to latest, flagging any missing
   month — works identically for 1 file or 6, no adapter restructuring needed.
3. Broaden date-format parsing for transaction rows that currently fail to parse
   (silently dropped today with only a log warning).

### Explicitly out of scope (tracked as follow-up)

- Intermittent OCR/Gemini extraction miss observed once on `L1-bankbank-format`
  (`Transactions: null` on first call, succeeded on immediate retry) — real but
  non-deterministic, a `services/extraction` reliability question.
- Possible transaction-row loss on `L1-bankcont-overlap` (7 source files → 6 rows
  returned) — not yet root-caused.

---

## TICKET-2 — CIF required fields return `null` instead of "Not Available"

**Correction:** the field never actually carries a literal `null` through to the
bundle — `build_customer_information_doc`
(`services/validation/extraction_adapter.py:763-804`) already coerces missing CIF
fields to `""` via `blank()`, and `verify_customer_information_completeness`
already flags blank fields by label. The string `"Not Available"` doesn't exist
anywhere in the codebase today.

### Fix

Replace the `""` blank-string sentinel with the literal string `"Not Available"`
for missing CIF fields, so it's unambiguous in the report/output rather than an
empty-looking value. Completeness-rule flagging behavior is unchanged.

---

## TICKET-3 — Bank name normalization needs verification

**Confirmed** — matches the automated suite's `FINDINGS.md` #4 exactly. No bank-name
normalization exists at all today; `bank_name` is compared with exact string
matching, so `"Maybank"`, `"Maybank Berhad"`, and `"Malayan Banking Berhad"` are
treated as different banks and fail `bank_statement.bank_consistency`.

### Fix

Alias table for known Malaysian banks as the primary path (predictable, auditable),
with a conservative fuzzy-match fallback (strip legal suffixes, case-fold, high
similarity threshold) only when no alias hits — catches unseen variants without
risking conflation of two genuinely different banks.

---

## TICKET-4 — Currency check not rejecting non-MYR statements

**Correction:** `check_bank_statement_currency`
(`services/validation/rules/date_logic.py:390-441`) **by design** returns
NEEDS_REVIEW (not FAILED) for non-MYR currency — an existing test
(`tests/test_rules.py:359-360`) asserts exactly this, with a docstring explaining a
currency mismatch "needs manual conversion... not a hard fail." Confirmed correct
by the live automated suite (`L1-bankcur-sgd` passes as NEEDS_REVIEW, matching
expectation) and by the empirical full-pipeline repro.

### Fix

**No rule change.** Policy decision made: keep NEEDS_REVIEW as designed rather than
converting to a hard blocker.

---

## TICKET-5 — IC front/back "unreadable" (None) not triggering NEEDS_REVIEW

**Correction:** `check_ic_front_and_back`
(`services/validation/rules/completeness.py:131-175`) already computes
`passed = False if incomplete else (None if needs_review else True)` correctly, and
the adapter passes `default=None` for unreadable images. Confirmed correct by the
live automated suite (`L2-icfront-none` passes as expected NEEDS_REVIEW).

### Fix

**No code change expected.** The rule logic is correct in isolation; the manual QA
discrepancy most likely stemmed from OCR/extraction of the real degraded image
producing a different flag value than intended, not a validation-logic bug.
Verify only — do not implement changes unless verification turns up something new.

---

## TICKET-6 — Passport-based identity documents mishandled

**Confirmed** — no passport-specific logic exists anywhere. IC and passport both
flow through the same `nric_passport` / `front_image_present` /
`back_image_present` fields, and IC-specific checks apply identically regardless of
document type, so a passport-holding director is reported "Missing 1 IC."

### Fix (design decisions made)

- Accept passport as a recognized alternate identity-document type alongside IC.
- Require one passport bio-data-page image + passport-number match — no
  front/back split (passports don't have an IC-style "back").
- Update identity-document rules (`check_ic_front_and_back`,
  `find_missing_ic_documents`) so a passport-holder director is validated against
  the passport requirements instead of being flagged for a missing IC.

---

## TICKET-7 — Entity-type spelling variants and resolved bank-statement minimum

**Confirmed** — matches `FINDINGS.md` #3 / open decision D6 exactly.
`domain/policies.py:22-32`'s `minimum_bank_statement_months_by_entity` only
recognizes a handful of English spellings; any unrecognized string (typos,
alternate spellings, Malay terms like `Perniagaan Tunggal`, `Enterprise`, `llp`,
`""`) falls through to `default_minimum_bank_statement_months = 6` — the lenient
default, which happens to equal Sdn Bhd's real requirement, making the fallback
invisible. This is a policy-bypass risk for real Malaysian sole props.

### Fix

Fuzzy-match known aliases/typos/Malay terms to canonical entity types (extend the
alias table), rather than falling through silently to the lenient default.

---

## TICKET-8 — Entity name mismatch — confirm correct fail routing

**Confirmed no bug.** `_run_entity_name_match`
(`services/validation/rules/registry.py:243-253`) and `run_all_rules`
(`registry.py:289-299`) show rules never short-circuit each other — every catalog
rule always runs regardless of prior results, and each `CheckResult` is
independent. A name mismatch cannot structurally "leak" into or be masked by
another rule. Confirmed correct by the live automated suite (`L1-name-real` passes
as expected).

### Fix

**Close as verified, no code change.** Confirm/add a regression test for the
`L1-name-real` scenario if one doesn't already exist.

---

## New: FINDINGS #1 — No completeness gate (vacuous pass on empty bundle)

Not in the original ticket list; surfaced by the automated suite. `POST
/validate/from-extraction` with an empty body `{}` returns 13 results, all
`not_applicable`, zero failures — nothing objects to a package with nothing in it.
Because almost every rule degrades to `not_applicable` on a missing document, a
nearly-empty bundle reads as a clean pass.

### Fix

Add a completeness gate outside the rule loop, keyed on the mandatory document
slots for the resolved `entity_type`, emitted as its own distinct
`package.completeness` result — so a near-empty bundle can no longer read as
"all passed."

---

## New: FINDINGS #2 — `identity_document.number_match` is fail-open

Not in the original ticket list; surfaced by the automated suite. Changing a
director's IC number on any document (MyKad or SSM form) makes that director's
comparison **disappear from the report** (2 results → 1) instead of failing. The
rule keys its per-party output on the IC value it's meant to be comparing, so a
mismatch produces two different keys and the pair silently stops being compared.
No document, no direction of change, produces a `failed` status. Contrast:
`entity_name.match` does not have this bug — a real name mismatch fails correctly,
which is the shape this rule should be fixed to match.

### Fix

Fix the per-party keying so an IC mismatch produces a `failed` comparison result
instead of the pair vanishing from the report.

---

## Deferred — TICKET-9: Sole Prop / Borang B rules (policy decisions, not bugs)

Blocking test completion but not addressed in this effort:

- `L1-borangb-consecutive`: does a sole prop need 2 years of Borang B filings, or
  is 1 sufficient?
- `L1-borangb-date-source`: lock in that assessment year (not basis-period-end or
  filing date) drives freshness.
- `L1-borangb-seasonal-nov`: decide whether Borang B gets its own freshness
  threshold, an exemption from the 18-month rule, or accepts seasonal rejections.

Also relevant context for whoever picks this up: the deployed `/validate/from-extraction`
adapter has no mapping for the Borang B / tax-declaration template at all (`FINDINGS.md` #5)
— sole-prop scenarios currently only work via the `/validate` bundle transport.
