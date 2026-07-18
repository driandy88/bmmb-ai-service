# Validation service refactor summary (Phase 4)

This is a high-level summary of the Phase 4 refactor of `services/validation`.
For the full layout and rationale see [validation-architecture.md](validation-architecture.md);
for the API/data contract see [validation-contract.md](validation-contract.md);
for the extraction field gaps surfaced along the way see
[validation-extraction-fields.md](validation-extraction-fields.md).

## Why

Rule requirements (required SSM forms, statement coverage, freshness windows,
required application fields) were hardcoded as module-level constants
scattered across `rules/*.py`, and `engine.py` mixed document-grouping logic
with rule orchestration. Check results only exposed a nullable `passed`
boolean, collapsing "not applicable" and "needs human review" into the same
`None` value. This refactor introduces clearer seams without changing any
existing behavior or breaking existing callers.

## What changed

- **New layers added** (`application/`, `domain/`, `adapters/`, `ai/`,
  `infrastructure/`) alongside the existing implementation files
  (`engine.py`, `bundle.py`, `extraction_adapter.py`, `agent.py`, `rules/`),
  which remain as the compatibility layer. Nothing was deleted or moved yet.
- **`ValidationPolicy` / `BMMB_SME_POLICY_V1`** (`domain/policies.py`) —
  required SSM forms, minimum bank statement months, financial/bank statement
  max-age windows and required application fields are now versioned data on
  a policy object instead of scattered constants. `ValidationEngine` accepts
  a `policy` argument (defaults to `BMMB_SME_POLICY_V1`) and every
  `ValidationReport` now carries a `policy_id`.
- **Explicit check status** — `CheckResult` gained `rule_id` (a stable
  identifier from `rules/catalog.py`, independent of the human-readable
  `check` label) and `status` (`passed` / `failed` / `needs_review` /
  `not_applicable`), replacing the old `passed=None` overload. `passed` and
  `overall_passed` are unchanged for backwards compatibility;
  `ValidationReport.overall_status` is the new aggregate.
- **`BundleContext`** (`domain/context.py`) — extracted the document
  grouping / entity metadata / SSM-party collection block that used to live
  inline in `ValidationEngine.run`.
- **Rule result contract** (`rules/contracts.py`) — every rule's return dict
  is validated against `RuleResult` (`passed`, `message`, `details`) before
  it's added to a report, catching malformed rule output early.
- **`ValidationApplicationService` / `ExtractionValidationApplicationService`**
  (`application/`) — new use-case entry points; `agent.py` now calls these
  instead of constructing `ValidationEngine` / calling `build_validation_bundle`
  directly.
- **AI reviewer extracted** (`ai/client.py`, `ai/reviewer.py`) — Gemini
  prompt construction, retry/backoff and severity clamping moved out of
  `agent.py` into their own module; `agent.py` keeps thin backwards-compatible
  wrappers (`review_deterministic_report`, `_clamp_severity`).
- **`infrastructure/settings.py`** — `ValidationSettings.from_env()` replaces
  ad hoc `os.environ.get(...)` calls for `GCP_PROJECT_ID`/`VERTEX_LOCATION`,
  plus new `VALIDATION_AI_MODEL`, `VALIDATION_AI_MAX_RETRIES`,
  `VALIDATION_AI_RETRY_BACKOFF_SECONDS` env vars (added to `.env.example` /
  `env.example`).
- **`DocumentProvenance`** (`bundle.py`) — every canonical document type now
  carries an optional `provenance.source_template`, populated by
  `extraction_adapter.py` so a rule or AI reviewer can trace a value back to
  its source extraction template.
- **New `GET /rules` endpoint** (`api.py`) — returns the active policy id and
  the full `RULE_CATALOG` (rule id, check name, category, description).
- **New optional canonical fields**: `FinancialStatementData.audited`,
  `BankStatementData.bank_name` / `currency` / `account_type`. `bank_name` is
  now mapped from the existing `Bank Name` extraction field; `audited`,
  `currency` and `account_type` have no source field yet and are left `null`
  with an adapter warning (tracked in validation-extraction-fields.md).
- **`verify_application_details_completeness`** now checks a policy-driven
  set of required fields (adds `financing_amount`, `product_type`,
  `tenure_months`, `repayment_frequency` to the default three).

## Compatibility

No public behavior changed: `overall_passed`, existing `check` labels, and
existing rule function signatures (new params are optional with defaults)
are all preserved. Existing tests pass unmodified; new tests
(`test_application_boundaries.py`, `test_infrastructure.py`, plus additions
to `test_engine.py`) cover the new seams.

## Next step (done — see Phase 5)

Rule execution has since been migrated from `engine.py` into a
`RULE_CATALOG`-driven registry (`rules/registry.py`); `engine.py` is now a
thin compatibility wrapper. See
[validation-rule-registry-migration.md](validation-rule-registry-migration.md)
for that migration in detail, and validation-architecture.md's Phase 5
section for the updated layout and the next step after that.
