# Validation Rules

Deterministic checks run by the validation service. Source of truth is
[`rules/catalog.py`](../rules/catalog.py) (the `RULE_CATALOG`); thresholds live in
[`domain/policies.py`](../domain/policies.py) (`BMMB_SME_POLICY_V1`).

- **Policy:** `bmmb-sme-2026-01`
- **Active rules:** 16
- **Live catalog:** `GET /rules` on a running server returns this same list as JSON.

Each rule returns one of three outcomes:

| Outcome | Meaning |
|---|---|
| **PASS** | Check ran and the requirement is met. |
| **FAIL** | Check ran and the requirement is not met. |
| **NEEDS REVIEW** | Ran but the data was inconclusive (e.g. an unconfirmed signature). |
| **N/A (skipped)** | The document this rule needs isn't in the bundle. A skip never fails the bundle on its own. |

**Aggregate status:** any FAIL → the report is `failed`; else any NEEDS REVIEW →
`needs_review`; else `passed`. Note `overall_passed` (the older boolean) is
`True` for a needs-review report — only a FAIL flips it. Read `overall_status`
if you care about the difference.

**Rules never short-circuit each other.** Every catalog rule runs against every
bundle regardless of what any other rule concluded, so one failure can't mask or
suppress another.

### Who each rule applies to

Directors and shareholders are **not** held to the same requirements. A
shareholder is not required to submit an identity document or sign a consent
form, so a shareholder-only party can never be the reason those rules fail.

| Rule | Applies to |
|---|---|
| `identity_document.coverage` | directors only |
| `consent.signature` | directors only |
| `identity_document.number_match` | all SSM people, but only a **director** with no confidently-matched document is flagged |

---

## Package Completeness

| Rule ID | Check | What it verifies |
|---|---|---|
| `package.completeness` | `verify_required_documents_present` | The submitted package contains every mandatory document type for the entity. |

This is the one rule that **never returns N/A**. Every other rule skips when the
document it reads is absent, so without this gate a package with nothing in it
produces nothing but skips and reads as a clean pass.

Mandatory document types (all of them required):

| Slot | Sdn Bhd / default | Sole Prop / Partnership |
|---|---|---|
| SSM | `ssm_corporate_form` | `ssm_corporate_form` |
| Financials | `financial_statement` | `financial_statement` **or** `tax_declaration` |
| Bank | `bank_statement` | `bank_statement` |
| Identity | `identity_document` | `identity_document` |
| Consent | `consent_form` | `consent_form` |
| Customer info | `customer_information` | `customer_information` |

Only a Sole Prop / Partnership may satisfy the financials slot with Borang B
(`tax_declaration`); a Sdn Bhd must produce audited financial statements. The
entity type is resolved through the same alias/fuzzy table the bank-statement
minimum uses, so "Perniagaan Tunggal" or "Enterprise" get the sole-prop slots.

---

## SSM Corporate Forms

SSM is now extracted as one combined **`SSM Business Registration`** template
instead of the three per-form templates (Form 24 / 44 / 49) — see
[`docs/ssm-one-form.md`](../../../docs/ssm-one-form.md). There is **no SSM
completeness rule**: rather than counting distinct forms, the extraction
adapter raises an `AdapterWarning` for each incomplete field (missing
Incorporation Date, Registered Address, MSIC/Main Business, directors, or
shareholders). Incomplete SSM data surfaces as a warning, not a failing check.

The legacy `SSM Form 24 / 44 / 49` keys are still accepted for backward
compatibility. SSM data still feeds the cross-document matching rules below.

---

## Financial Statements

| Rule ID | Check | What it verifies |
|---|---|---|
| `financial_statement.freshness` | `calculate_financial_18_month_rule` | Latest financial year-end is within the allowed age. |
| `financial_statement.consecutive_years` | `check_financial_consecutive_years` | Documents cover **at least** two consecutive years, with no missing or duplicated year. |
| `financial_statement.completeness` | `verify_financial_sections_present` | Each statement contains the required sections (balance sheet, P&L, cash flow, auditor's report). |

**Threshold:** financial statements must be no older than **18 months**.
For a Sole Prop / Partnership with no audited statements, these rules fall back
to the tax-declaration (Borang B) documents.

### Consecutive years

Two years is the **minimum**, not the exact requirement — three or four
consecutive years pass. Continuity is judged on the financial-year *label* (the
calendar year each FYE falls in), not on an exact date delta, so ordinary
year-to-year FYE drift doesn't read as a skipped year:

| Situation | Outcome |
|---|---|
| Unbroken run of ≥2 years | **PASS** |
| Fewer than 2 statements | **FAIL** |
| A year missing from the run (2023, 2025) | **FAIL** — `missing_years` |
| Two statements for the same year | **FAIL** — `duplicate_years` |
| Consecutive years, interval materially ≠ 12 months | **NEEDS REVIEW** — `irregular_intervals` |

The interval between consecutive statements is allowed to drift **±45 days**
from the one-year anniversary, which absorbs month-end drift (31 Dec → 30 Dec),
leap days (29 Feb → 28 Feb) and 52/53-week fiscal calendars. Beyond that it's a
genuinely short or long accounting period — real, but a human should look at it,
so it's needs-review rather than a hard fail. A missing year always outranks an
irregular interval.

### Audited status

The template has no explicit audited-status attribute, so `audited` is
**inferred from the Auditor's Report section**: present → audited, confirmed
absent → not audited, unconfirmed → unknown (never "confirmed unaudited"). The
inference is recorded as an `AdapterWarning` so a reviewer can see the value
wasn't read off the document directly. No rule fails on `audited` on its own —
a confirmed-missing auditor's report already fails
`financial_statement.completeness`.

---

## Bank Statements

| Rule ID | Check | What it verifies |
|---|---|---|
| `bank_statement.continuity` | `check_bank_statement_continuity` | Statement periods have no gaps or overlaps (needs 2+ statements). |
| `bank_statement.duration` | `verify_bank_statement_duration` | Statements meet the required coverage duration. |
| `bank_statement.freshness` | `check_bank_statement_freshness` | The most recent statement is recent enough. |
| `bank_statement.overdraft` | `check_bank_statement_overdraft` | Every month's ending balance is not overdrawn. |
| `bank_statement.bank_consistency` | `check_bank_statement_bank_consistency` | All statements in the set are from the same bank. |
| `bank_statement.currency` | `check_bank_statement_currency` | Statement currency matches the accepted currency. |

**Thresholds:**
- Minimum coverage — Sdn Bhd: **6 months**; Sole Prop / Partnership: **12 months**
- Freshness — latest statement no older than **2 months**
- Accepted currency — **MYR**

### Per-rule outcomes

| Rule | PASS | FAIL | NEEDS REVIEW |
|---|---|---|---|
| `continuity` | covered months form one unbroken run | a gap or an overlap between covered months | — |
| `duration` | months covered ≥ the entity minimum | below the minimum, **or** statements aren't continuous | — |
| `freshness` | latest statement end date within 2 months | older than 2 months | — |
| `overdraft` | no month ends negative | any month ends negative | — |
| `bank_consistency` | one canonical bank across the set | 2+ distinct banks | any statement has no bank name |
| `currency` | all MYR | — (never fails) | any non-MYR, or any unknown currency |

Coverage is counted from the **distinct calendar months** the statements have
transaction data for, not from a start/end date subtraction — so a gap inside a
single consolidated document is visible, and month-end alignment can't
undercount by one. Continuity works identically for 1 document or 6.

Bank names resolve to a canonical identity before comparison (alias table first,
then conservative fuzzy match), so "Maybank" / "Maybank Berhad" / "Malayan
Banking Berhad" count as one bank. Entity type resolves through its own
alias/fuzzy table, so "Perniagaan Tunggal", "Enterprise" or "llp" reach the
correct minimum instead of silently falling through to the lenient 6-month
default.

**Currency never hard-fails by design** — a foreign-currency statement needs
manual conversion before its balances are comparable, which is a review task,
not a confirmed compliance failure.

---

## Identity Documents (MyKad / Passport)

| Rule ID | Check | What it verifies |
|---|---|---|
| `identity_document.front_and_back` | `check_ic_front_and_back` | Each identity document carries the images its type requires. |
| `identity_document.coverage` | `find_missing_ic_documents` | Every **director** has a corresponding identity document, matched by NRIC/passport. |

A director may hold **either** a Malaysian MyKad **or** a passport; both are
accepted identity documents. What counts as a complete document differs by type:

| ID type | Required images |
|---|---|
| MyKad | front **and** back |
| Passport | bio-data page only (a passport has no IC-style back) |
| Not stated / unrecognized | held to the stricter MyKad requirement |

The type comes from the `ID Type` attribute, read from the identity document
itself first and falling back to the SSM form's declaration for that person
(joined by NRIC/passport) when the document doesn't state one. An `ID Type`
that extraction returned but this service doesn't recognize is recorded as an
`AdapterWarning` and treated as not stated.

Each image is tri-state: confirmed present (**PASS**), confirmed missing
(**FAIL**), or unreadable/undetermined (**NEEDS REVIEW** — never a pass).

---

## Consent Form

| Rule ID | Check | What it verifies |
|---|---|---|
| `consent.signature` | `verify_consent_signatures` | Every required party has a signed consent form, matched by NRIC/passport. |

Signature is tri-state: confirmed signed (**PASS**), confirmed unsigned or missing
form (**FAIL**), or unconfirmed (**NEEDS REVIEW**).

---

## Customer Information Form

| Rule ID | Check | What it verifies |
|---|---|---|
| `customer_information.completeness` | `verify_customer_information_completeness` | Every field on the Customer Information Form is filled in. |

Sourced from the `Customer Information Form` template (which replaced the old
Application Details source). **Every field is mandatory:** each director's
particulars (name, address, email, religion, marital status, estimated monthly
income, experience, higher education, emergency contact name/number/relationship,
spouse name/contact) plus the company fields (age, number of staff, office
address/status/monthly rent/telephone, email, auditor firm/contact person/number).

---

## Cross-Document Matching

These run once per matching document/person rather than once per bundle, so they
can produce any number of results (including zero). Results are grouped under the
SSM form as the source of truth.

| Rule ID | Check | What it verifies |
|---|---|---|
| `entity_name.match` | `strict_match_entity_names` | The entity name matches across documents (bank statement, financial statement, tax declaration, consent form) against the SSM form. Falls back to fuzzy match if strict fails. |
| `identity_document.number_match` | `strict_match_ic_numbers` | Each party's NRIC/passport on their identity document matches the number recorded on the SSM form. Applies to passport numbers exactly as it does to NRICs. |

One result per document (`entity_name.match`) or per person
(`identity_document.number_match`), named `strict_match_entity_names[doc_id]` /
`strict_match_ic_numbers[person name]`.

`identity_document.number_match` joins person → document **by name**, not by
NRIC. Joining on the NRIC would key on the very field being compared, so a
changed number would fail to key at all and the person would silently drop out
of the report instead of failing. A director with no confidently name-matched
document is NEEDS REVIEW; a shareholder without one is skipped silently.

---

## What is deliberately not checked

| Not checked | Why |
|---|---|
| SSM form completeness | No rule counts distinct SSM forms — incomplete SSM fields surface as `AdapterWarning`s instead. |
| `audited` as its own rule | A confirmed-missing auditor's report already fails `financial_statement.completeness`; a separate rule would report the same gap twice. |
| ID type agreeing across documents | If SSM declares a passport but a MyKad was uploaded, coverage still passes — it asks whether *a* document exists for that number, not whether it's the right type. |
| Entity name on the identity document or CIF | `entity_name.match` runs over bank statements, financial statements, tax declarations and consent forms only. |
| Borang B freshness / count policy | Deferred (TICKET-9) — pending a business decision on 1 vs 2 years and which date drives freshness. |
