# Validation service architecture

The validation service is being migrated incrementally toward separated
application, domain, adapter and AI layers.

## Current Phase 5 layout

```text
services/validation/
├── application/
│   ├── validate_bundle.py       # deterministic validation use case
│   └── validate_extraction.py   # raw extraction -> bundle use case
├── domain/
│   ├── context.py               # normalized BundleContext
│   ├── models/                  # canonical model import surface
│   ├── policies.py              # versioned business requirements
│   └── rules/                   # rule contract/catalog import surface
├── adapters/
│   └── extraction.py            # external extraction adapter import surface
├── ai/
│   ├── client.py                # Vertex client construction
│   └── reviewer.py              # Gemini review call and guardrails
├── infrastructure/
│   └── settings.py              # environment-backed runtime settings
├── rules/
│   ├── registry.py              # RULE_CATALOG-driven rule execution (Phase 5)
│   └── ...                      # rule implementations
├── bundle.py                    # current canonical model implementation
├── extraction_adapter.py        # current adapter implementation
├── engine.py                    # thin CheckResult-building wrapper (Phase 5)
├── agent.py                     # current AI/application orchestration
└── api.py                       # current FastAPI entry point
```

The old implementation files remain in place deliberately. They are the
compatibility layer for existing imports, tests and deployment commands. New
application code should use the new boundaries:

```python
from services.validation.application import ValidationApplicationService
from services.validation.domain.models import ValidationBundle
```

## Phase 4 changes

- `BundleContext` centralizes document grouping, entity metadata and SSM-party
  collection that used to be assembled directly inside `engine.py`.
- `ValidationApplicationService` is the use-case boundary for deterministic
  validation.
- `ExtractionValidationApplicationService` is the use-case boundary for raw
  extraction normalization.
- `BMMB_SME_POLICY_V1` is the versioned default policy. Rules receive policy
  values for entity-specific forms, statement coverage and freshness limits.
- `ai.reviewer` owns Gemini prompt submission, retries, response parsing and
  severity guardrails. `agent.py` remains the orchestration and compatibility
  entry point.
- `infrastructure.settings` owns Vertex model, location and retry settings.
- `domain.models`, `domain.rules` and `adapters.extraction` provide stable
  import surfaces while the implementation files are migrated incrementally.
- The AI layer now calls the application service instead of constructing the
  deterministic engine directly.

## Phase 5 changes

- Rule execution moved from `engine.py`'s hand-written `if`/`add`/`skip`
  blocks into `rules/registry.py`, a `RULE_CATALOG`-driven runner. Every
  catalog entry (`rules/catalog.py`) now has a matching runner function that
  decides applicability, binds arguments from `BundleContext`/`ValidationPolicy`
  and returns the outcome(s) it produced. `run_all_rules(context, policy,
  system_date)` walks the catalog in order and yields `(rule_id, RuleOutcome)`
  pairs.
- `ValidationEngine.run()` is now a thin loop: it calls `run_all_rules()` and
  turns each outcome into a `CheckResult` (validating the rule-result
  contract, deriving `status` from `passed`). All rule selection logic that
  used to live inline in `engine.py` is gone from that file.
- Most rules map one catalog entry to exactly one outcome. Three don't:
  - `financial_statement.freshness` / `.consecutive_years` / `.completeness`
    read from `financial_statement_docs`, falling back to
    `tax_declaration_docs` (Rule 2's alternate path for a Sole
    Prop/Partnership with no audited statements).
  - `entity_name.match` and `identity_document.number_match` run once per
    matching document/person, so a single catalog entry can yield any number
    of outcomes (including zero).
- Registry order matches `RULE_CATALOG` order, which was verified to match
  the pre-migration `engine.py` block order exactly, so the flattened
  `CheckResult` sequence a caller sees is unchanged.
- See [validation-rule-registry-migration.md](validation-rule-registry-migration.md)
  for the detailed before/after and the test coverage added for this step.

## Next migration step

`engine.py` is now the compatibility wrapper described in Phase 4 — it holds
`ValidationStatus`, `CheckResult`, `ValidationReport` and a thin
`ValidationEngine.run()`, with no rule logic of its own. The next safe move
is deciding whether those model classes and `ValidationEngine` itself should
physically relocate into `domain`/`application` (with `engine.py` re-exporting
for compatibility), now that nothing depends on `engine.py` for rule
execution specifically.
