# Validation extraction fields

This is the living list of fields needed by the validation service. Update this
file whenever a new canonical validation field is added.

Format: `document -> fields`

## Fields to add or confirm in the extraction agent

- `Bank Statements` -> `Currency`
- `Bank Statements` -> `Account Type`
- `Financial Statements (Sdn Bhd)` -> `Audited`

Expected meanings:

- `Currency`: the statement currency, preferably normalized to an ISO code such as `MYR`.
- `Account Type`: the account classification, such as `current`, `savings`, or another clearly identified type.
- `Audited`: whether the financial statements are explicitly confirmed as audited (`true` or `false`).

## Fields already available and now mapped by validation

- `Bank Statements` -> `Bank Name`

`Bank Name` already exists in the current extraction output. The validation
adapter now maps it into `BankStatementData.bank_name`.

## System-generated validation metadata

These do not need to be added to the extraction agent:

- `All validation documents` -> `provenance.source_template`

The adapter generates `provenance.source_template` from the extraction
template name, for example `Bank Statements` or `Financial Statements (Sdn
Bhd)`.

## Current canonical field names

The extraction field names above are mapped into these canonical bundle fields:

- `Bank Statements` -> `bank_name`, `currency`, `account_type`
- `Financial Statements (Sdn Bhd)` -> `audited`
- `All validation documents` -> `provenance.source_template`

Fields with no extraction source remain `null` and produce an adapter warning
until the extraction agent supplies them.
