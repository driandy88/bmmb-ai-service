# Rule registry migration (Phase 5)

This documents the specific refactor that moved rule execution out of
`engine.py` into a `RULE_CATALOG`-driven registry, per the "Next migration
step" called out in [validation-architecture.md](validation-architecture.md).
For the broader Phase 4 refactor this builds on, see
[validation-refactor-summary.md](validation-refactor-summary.md).

## Before

`ValidationEngine.run()` was a single ~200-line method: one `if ssm_form_docs:
... else: skip(...)` block per rule, each manually gathering arguments from
`BundleContext` and calling the rule function. `RULE_CATALOG` already existed
as metadata (`rule_id`, `check_name`, `category`, `description`, used by the
`GET /rules` endpoint), but nothing executed rules off it — `engine.py` and
the catalog were two separate, hand-kept-in-sync descriptions of the same 16
rules.

## After

- **`rules/registry.py`** — one runner function per `RULE_CATALOG` entry
  (`_run_ssm_completeness`, `_run_bank_statement_duration`, etc.). Each
  runner takes a `RuleRunContext` (`bundle_context`, `policy`, `system_date`)
  and returns a `list[RuleOutcome]`. `RuleOutcome` is either a `result` dict
  (the rule applied) or a `skip_reason` (it didn't) — the same
  applicable/not-applicable distinction `engine.py`'s `add()`/`skip()`
  helpers used to encode, just returned as data instead of built directly
  onto the report.
- **`run_all_rules(context, policy, system_date)`** walks `RULE_CATALOG` in
  order and yields `(rule_id, RuleOutcome)` pairs by calling
  `RULE_RUNNERS[definition.rule_id]` for each entry. Catalog order was
  checked against the pre-migration `engine.py` block order and matches
  exactly, so the flattened outcome sequence is unchanged.
- **`engine.py`** shrank to: build a `BundleContext`, call `run_all_rules()`,
  and turn each outcome into a `CheckResult` (contract validation via
  `validate_rule_result`, `status` derivation via `_status_for_passed`). It no
  longer imports any individual rule function — only `run_all_rules` and
  `validate_rule_result` from `rules/`.

## Rules that aren't 1:1

Three catalog entries don't map to exactly one outcome per bundle:

- `financial_statement.freshness`, `.consecutive_years` and `.completeness`
  read from `financial_statement_docs`, falling back to
  `tax_declaration_docs` — Rule 2's alternate path letting a Sole
  Prop/Partnership submit 2 years of Borang B instead of audited financial
  statements. `.completeness` additionally distinguishes "no financial docs
  at all" from "tax declarations present, but they have no
  balance-sheet/P&L/cash-flow/auditor's-report breakdown to check" — two
  different skip reasons for two different reasons.
- `entity_name.match` runs `strict_match_entity_names` (falling back to
  `fuzzy_match_entity_names`) once per bank statement / financial statement
  / tax declaration / consent form document, so its runner returns one
  outcome per matching document — zero, one, or many.
- `identity_document.number_match` runs `strict_match_ic_numbers` once per
  SSM person who has a corresponding identity document, same variable-count
  shape.

## Why this is safe

- `RULE_RUNNERS` is a plain `dict[str, RuleRunner]`; a `RULE_CATALOG` entry
  with no matching key raises `KeyError` immediately rather than silently
  skipping a rule.
- `rules/registry.py` lives inside the `rules/` package (not
  `application/`) specifically to avoid a circular import:
  `application/__init__.py` imports `validate_bundle.py`, which imports
  `engine.py` — so if `engine.py` imported from `application`, importing
  `services.validation.engine` would recurse back into itself mid-load.
  `rules/` has no such dependency on `engine.py` or `application/`.
- `ValidationStatus`, `CheckResult`, `ValidationReport` and the
  `ValidationEngine` class/constructor signature in `engine.py` are
  unchanged; every existing import (`from services.validation.engine import
  ValidationEngine`, etc.) still works.

## Rules added since this migration

`bank_statement.bank_consistency` (`check_bank_statement_bank_consistency`,
`rules/date_logic.py`) was added after this migration to close a gap found
by comparing `RULE_CATALOG` against BMMB's requirements table: "a 6-month set
of bank statements must all be from the same bank" had no rule, even though
`BankStatementData.bank_name` was already populated by the extraction adapter
(Phase 4). Adding it required exactly 3 changes, none to `engine.py`:

1. The rule function itself in `rules/date_logic.py` (tri-state: `passed`
   is `None`/needs-review if any statement's `bank_name` is unknown, `False`
   if two or more statements report different known banks, `True` if every
   known name matches).
2. One `RuleDefinition` entry in `rules/catalog.py`.
3. One runner function + one `RULE_RUNNERS` entry in `rules/registry.py`.

That `engine.py` needed zero changes to add a 17th rule is the concrete
payoff of this migration: before it, this would have been a new
`if bank_statement_docs: add(...) else: skip(...)` block inserted into
`ValidationEngine.run()` directly.

One existing fixture (`examples/sample_bundle_passing.json`) needed a
`bank_name` added to both its bank_statement documents — it predates this
rule and had no bank name at all, which the new tri-state rule correctly
reads as "can't confirm consistency" (`needs_review`), flipping the fixture's
`overall_status` away from `passed`. This is expected: the fixture is meant
to represent a bundle where every check that *can* run does pass, and the
new rule can only pass when it has bank names to compare.

`bank_statement.currency` (`check_bank_statement_currency`, same file) closes
the adjacent gap: "bank statement data should be in ringgit; convert at the
current rate if not." Same tri-state shape as `bank_consistency`, but a
currency mismatch is deliberately a **warning** (`passed=None`,
`needs_review`), not a fail (`passed=False`) — a non-MYR statement isn't
necessarily non-compliant, it just needs manual conversion/review before its
balances can be compared like-for-like. `ValidationPolicy.accepted_bank_currency`
(added in Phase 4, unused until now) is the expected currency; a rule
argument (`accepted_currency`) rather than a hardcoded `"MYR"`, so a
different policy could require a different currency.

`BankStatementData.currency` is still hardcoded to `null` by
`extraction_adapter.py` (no source field in the current Bank Statements
template — see
[validation-extraction-fields.md](validation-extraction-fields.md)), so in
production this rule will read `needs_review` on every bundle until that
extraction gap closes; a `null` currency and a confirmed-mismatched currency
both need_review for the same underlying reason (can't confirm the accepted
currency). `examples/sample_bundle_passing.json` sets `currency` directly
(it's a hand-built canonical bundle, not adapter output) so the fixture can
still represent a fully-passing bundle.

## Rules removed after this migration

`consent.count` (`verify_consent_form_count`) and `form_d.expiry`
(`validate_form_d_expiry`) were removed after comparing `RULE_CATALOG`
against BMMB's requirements table — neither had a corresponding row (the
table's consent row only requires signatures, not a specific form count; its
SSM row only requires form presence, not Form D's expiry-vs-tenure
validity). Removing them was a pure registry change, same as adding a rule:

1. Deleted the rule function (`rules/completeness.py`,
   `rules/date_logic.py`) and its unit tests (`test_rules.py`).
2. Deleted the `RuleDefinition` entry (`rules/catalog.py`), the runner
   function and its `RULE_RUNNERS` entry (`rules/registry.py`).
3. Removed both from `rules/__init__.py`'s imports/exports.
4. Removed `BundleContext.directors_by_nric` (`domain/context.py`) — it
   existed only to feed `consent.count`'s director-count argument and had no
   other caller once that rule was gone.
5. Updated the engine-wiring tests in `test_engine.py` that referenced
   either check by name.

Note that `form_d.expiry` had been effectively dead code even before removal:
its runner guarded on `hasattr(form_d_doc.data, "expiry_date")`, but
`form_d_doc.data` is always a `SsmCorporateFormData`, which never defines an
`expiry_date` field (only `IdentityDocumentData` does, for an unrelated MyKad
expiry) — so that `hasattr` check was always `False` and the rule always
skipped, in every bundle, by construction. Removing it changes nothing
observable.

## Test coverage

- `services/validation/tests/test_rule_registry.py` (new) — exercises
  `run_all_rules()` directly: every produced `rule_id` is a real catalog
  entry, outcome order matches catalog order, an applicable rule returns a
  `result` (not a `skip_reason`) and vice versa, and `entity_name.match`
  yields exactly one outcome per matching document.
- `services/validation/tests/test_engine.py` and
  `test_application_boundaries.py` (unchanged) — all 144 pre-existing tests,
  including `test_application_service_preserves_engine_results`
  (byte-identical `ValidationEngine.run()` output before/after) and the full
  passing/failing/skipped/tax-declaration-alternate-path suites, pass
  unmodified against the new implementation.
- `TestCheckBankStatementBankConsistency` (`test_rules.py`) and
  `TestNewRulesWiring::test_mixed_bank_statement_banks_is_caught` /
  `test_missing_bank_name_needs_review_not_fail` (`test_engine.py`) cover the
  `bank_statement.bank_consistency` rule added above, at both the unit and
  engine-wiring level.
- `TestCheckBankStatementCurrency` (`test_rules.py`) and
  `TestNewRulesWiring::test_non_myr_currency_needs_review_not_fail`
  (`test_engine.py`) cover `bank_statement.currency`, including that a
  mismatch produces `needs_review`/`passed=None`, never `passed=False`.
- Full suite: `pytest services/validation/tests/ -v` → 153 passed (after
  removing `consent.count` and `form_d.expiry` and their tests, per "Rules
  removed after this migration" above).
