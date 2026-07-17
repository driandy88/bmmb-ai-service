# Validation contract

This document describes the Phase 1 contract for deterministic validation.

## Check results

Every deterministic result contains both the existing `passed` field and the
explicit `status` field:

| `status` | `passed` | Meaning |
| --- | --- | --- |
| `passed` | `true` | The rule was applicable and passed. |
| `failed` | `false` | The rule was applicable and found a confirmed compliance failure. |
| `needs_review` | `null` | The rule ran, but extraction or evidence was inconclusive. |
| `not_applicable` | `null` | The rule could not run because its required document or input was absent. |

The `check` field is retained for backwards compatibility. New consumers
should use `rule_id` as the stable identifier. Dynamic check labels may include
a document id, while `rule_id` identifies the underlying rule consistently.

## Overall result

`ValidationReport.overall_passed` remains backwards compatible:

- `false` if any check has `passed=false`;
- `true` if there are no failed checks, including when checks are skipped.

`ValidationReport.overall_status` provides the more expressive aggregate:

1. `failed` if any check failed;
2. `needs_review` if no check failed but at least one check needs review;
3. `passed` otherwise, including reports containing only not-applicable checks.

AI review must not change deterministic `passed`, `status`, or overall results.
It may only add findings and explanatory narrative.

## Policy

Every report includes `policy_id`. The default policy is
`bmmb-sme-2026-01`, defined by `BMMB_SME_POLICY_V1`. A policy contains the
business requirements used by rules, such as required SSM forms, bank
statement coverage and freshness limits.
