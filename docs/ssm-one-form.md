# SSM Single-Template Migration — Changes Required in the AI Validation Service

## Summary

The application backend has switched **SSM Business Registration** from **three per-form
templates** (`SSM Form 24`, `SSM Form 44`, `SSM Form 49`) to **one combined template**
(`SSM Business Registration`). All files uploaded to the SSM slot are now sent to that single
template in one extraction call, and the merged result is emitted to the validation service under
**one key** instead of three.

The validation service's **SSM-completeness rule currently counts distinct forms** (24 / 44 / 49).
After this change those form keys no longer appear, so that rule must be reworked to accept the
single combined document. This document describes exactly what changes.

---

## 1. How the validation service is called (unchanged transport)

- **Endpoint:** `POST {VALIDATION_SERVICE_URL}/validate/from-extraction?enable_ai_review={true|false}`
- **Body:** a JSON object keyed by **template name** → that document's `extracted_data`:

```json
{
  "<Template Name>": { ...extracted_data... },
  "<Template Name>": { ...extracted_data... }
}
```

Only the **SSM entry** changes shape. Every other document (Bank Statements, Financial Statements,
MyKad, etc.) is unchanged.

---

## 2. What the SSM entry looked like BEFORE

The old per-form flow extracted each SSM form separately, merged them, and emitted the merged data
under **each form name it detected**, so the completeness rule could count them:

```json
{
  "SSM Form 24": { ...merged ssm data... },
  "SSM Form 44": { ...same merged data... },
  "SSM Form 49": { ...same merged data... }
}
```

## 3. What the SSM entry looks like NOW

One key, one object. `Document Type` is a **scalar** (`"SSM Business Registration"`), and the
directors / shareholders are **nested arrays of row-objects**:

```json
{
  "SSM Business Registration": {
    "Document Type": "SSM Business Registration",
    "Entity Name": "ValueCapital Sdn. Bhd.",
    "Business Registration Number": "715023-H",
    "Incorporation Date": "24 Apr 2013",
    "MSIC Code": "46590",
    "Main Business": "Wholesale trade",
    "Sub-businesses": "Retail of hardware supplies; Logistics services",
    "Business Description": "General wholesale trading of hardware.",
    "Registered Address": "23, Jalan Anggerik 4, Bukit Indah, 81200 Johor Bahru",
    "Directors": [
      {
        "Director Name": "Rowan Atkinson",
        "Director NRIC or Passport Number": "550106-12-5821",
        "ID Type": "MyKad",
        "Corresponding Director Address": "Keningau, Sabah"
      }
    ],
    "Shareholders": [
      {
        "Shareholder Name": "Rowan Atkinson",
        "Shareholder Percentage": "60%",
        "Shareholder Address": "Keningau",
        "Corresponding Shareholder Address": "Keningau",
        "Shareholder Entity's Business Registration Number": "N/A"
      }
    ]
  }
}
```

### Full field inventory of the combined template (18 attributes)

| Field (JSON key) | Shape | Source form (old) |
| --- | --- | --- |
| `Document Type` | scalar | all |
| `Entity Name` | scalar | 24 / 44 / 49 |
| `Business Registration Number` | scalar | 24 |
| `Incorporation Date` | scalar | 9 & 28 |
| `MSIC Code` | scalar | 24 |
| `Main Business` | scalar | 24 / 49 |
| `Sub-businesses` | scalar | 24 |
| `Business Description` | scalar | 24 |
| `Registered Address` | scalar | 44 |
| `Directors[].Director Name` | array | 49 |
| `Directors[].Director NRIC or Passport Number` | array | 49 |
| `Directors[].ID Type` | array | 49 |
| `Directors[].Corresponding Director Address` | array | 49 |
| `Shareholders[].Shareholder Name` | array | 24 |
| `Shareholders[].Shareholder Percentage` | array | 24 |
| `Shareholders[].Shareholder Address` | array | 24 |
| `Shareholders[].Corresponding Shareholder Address` | array | 24 |
| `Shareholders[].Shareholder Entity's Business Registration Number` | array | 24 |

Empty groups arrive as `null` (not `[]`).

---

## 4. Changes required in the AI validation service

### 4.1 Recognize the new key
Add `"SSM Business Registration"` as a valid bundle key for the SSM document type. It replaces the
three `SSM Form 24 / 44 / 49` keys as the single source of SSM data.

### 4.2 Replace the "count distinct forms" completeness rule
The old rule verified that Forms 24, 44 and 49 were all present (three keys). That is no longer
meaningful — there is one combined document. Rework SSM completeness to validate **field
presence within the combined `extracted_data`** instead. Suggested checks:

- **Company identity:** `Entity Name`, `Business Registration Number`, `Incorporation Date` all present.
- **Registered office:** `Registered Address` present.
- **Classification:** `MSIC Code` and/or `Main Business` present.
- **Directors:** `Directors` is a non-empty array; each row has `Director Name` and
  `Director NRIC or Passport Number`.
- **Shareholders:** `Shareholders` is a non-empty array; each row has `Shareholder Name` and
  `Shareholder Percentage` (optionally validate the percentages sum to ~100%).

### 4.3 Handle the array/scalar shapes
- `Directors` and `Shareholders` are **arrays of objects**, or `null` when empty — guard for `null`.
- All other SSM fields are **scalars** (not arrays), including `Document Type`.

### 4.4 Backward compatibility (recommended)
In-flight or historical applications may still carry the old per-form keys. Keep accepting
`SSM Form 24 / 44 / 49` (old path) **in addition to** `SSM Business Registration` (new path) for a
transition period, rather than replacing outright.

---

## 5. Field coverage notes

`Registered Address` (Form 44's field) **is now included** in the combined template as a `Unique`
scalar — so a registered-address completeness rule can read it directly (see §3 / §4.2). No gap here.

Still **not** in the combined template — confirm no validation rule depends on them:
- `Business Address` (the trading / operating address, distinct from the registered office).
- `Business Nature` as a field separate from `Main Business` (the template exposes `Main Business`
  only).

If a rule needs either, add the attribute to the `SSM Business Registration` template the same way
`Registered Address` was added.

---

## 6. Test fixtures to update

Any sample/fixture that encodes SSM as separate forms (e.g. objects with
`"document_type": "ssm_corporate_form", "document_subtype": "form_24"`) should be updated to the
single combined shape shown in §3, so the rule tests exercise the new payload.

---

## 7. Reference — backend side (already changed)

For traceability, the corresponding backend edits (already applied):

| File | Change |
| --- | --- |
| `backend/src/modules/extraction/application/oneflow-extraction.usecases.ts` | `DOC_TYPE_TEMPLATE.SSM.name` → `"SSM Business Registration"` |
| `backend/src/modules/extraction/application/extract-document.usecase.ts` | removed `"SSM"` from `PER_FILE_BUNDLE` (all files sent in one call) |
| `backend/src/modules/extraction/application/document-type-check.ts` | added `"SSM Business Registration": "SSM"` |
| `backend/src/modules/validation/application/build-validation-bundle.ts` | doc updated; SSM now emits under the single `"SSM Business Registration"` key |

The backend now emits the SSM entry under `"SSM Business Registration"`; **this document covers the
matching change the AI validation service still needs.**
