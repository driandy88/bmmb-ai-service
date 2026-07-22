"""
Maps raw extraction results into a ValidationBundle.

Input contract: `extracted_by_template`, a dict keyed by the extraction
template's *name* exactly as it appears in Cloud SQL (e.g. "SSM Form 24",
"Bank Statements" -- see services.extraction's GET /templates/), one entry
per POST /extract call already made for this application. Each value is
that call's raw `extracted_data` (attribute-name-keyed, i.e. the
`data.extracted_data` object inside POST /extract's response) -- see
examples/extraction_results_example.json for a full worked example.

Two categories of "the data isn't what we need," handled differently:

1. Caller-supplied overrides missing entirely (entity_type, tenure_months,
   repayment_frequency, Borang B's entity_name/financial_year_ends) --
   these have NO source anywhere in extraction, so there's no reasonable
   fallback. Raises AdapterDataGapError; the caller has an integration bug
   (forgot to pass an override) that needs fixing before this can run at
   all, not a per-document data-quality issue.

2. Anomalies WITHIN a given extraction result -- a null value where a real
   one was expected (Gemini returned null per the "return null if not
   found" instruction baked into every extraction prompt), or Multiple-
   valued fields whose arrays don't line up (see the row_group correlation
   risk below). These do NOT raise or block: the bundle is still built,
   using a conservative placeholder (empty string / False / 0 / truncated
   array) for the anomalous value, and an AdapterWarning is recorded
   describing exactly what was found (current_state) vs what was expected
   (expected_state). build_validation_bundle() returns these warnings
   alongside the bundle (AdapterResult), and
   services.validation.agent.run_agentic_validation_from_extraction() feeds
   them straight into the AI review prompt -- so a null/misaligned value
   still produces a complete, reviewable report instead of an unhandled
   exception, and the reviewer doesn't have to reverse-engineer the anomaly
   from the raw JSON, it's already stated as "expected X, current Y."

Correlation via row_group: every template whose per-person fields used to
come back as parallel arrays now carries them in a row_group, so extraction
returns one correlated object per person and the builders read those
directly -- SSM Form 24 (Shareholders), SSM Form 49 (Directors), Consent
Form (Directors) and MyKad (Directors: Name/NRIC/Front & Back Side IC
Present/ID Type). No field is index-zipped anymore, so a name can no longer
drift onto another person's NRIC or IC-present flags.
"""
from datetime import date
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .bundle import (
    BankStatementData,
    BankStatementDoc,
    BundleMetadata,
    ConsentFormData,
    ConsentFormDoc,
    CustomerInfoData,
    CustomerInfoDirector,
    CustomerInfoDoc,
    DocumentProvenance,
    FinancialStatementData,
    FinancialStatementDoc,
    IdentityDoc,
    IdentityDocumentData,
    MonthlyBankBalance,
    PersonInfo,
    SsmCorporateDoc,
    SsmCorporateFormData,
    TaxDeclarationData,
    TaxDeclarationDoc,
    ValidationBundle,
)


class AdapterDataGapError(ValueError):
    """Raised only when a caller-supplied override (no source anywhere in
    extraction) is missing entirely -- see the module docstring's category 1.
    The message names exactly which extraction attribute (once added via the
    CRUD API) would let the caller stop passing this override by hand."""


class AdapterWarning(BaseModel):
    """One data-quality anomaly found while building a document: a null
    where a value was expected, or a correlation/array-length problem.
    Always states both current_state and expected_state explicitly, so a
    downstream reviewer (the AI review step, a human, a UI) doesn't have to
    infer what's wrong from the raw values -- it's already spelled out."""

    document_type: str
    document_id: str
    field: str
    message: str
    current_state: str
    expected_state: str


class AdapterResult(BaseModel):
    bundle: ValidationBundle
    warnings: list[AdapterWarning] = Field(default_factory=list)


# ── Null-safe field helpers ──────────────────────────────────────────────────
# Each returns a conservative placeholder and records a warning when the
# source value is None (present-but-null, or missing outright -- treated
# identically, since extraction's own "return null if not found" convention
# makes them equivalent in practice).

def _safe_str(value: Any, *, warnings: list[AdapterWarning], document_type: str, document_id: str, field: str) -> str:
    if value is None:
        warnings.append(AdapterWarning(
            document_type=document_type, document_id=document_id, field=field,
            message=f"{field} was null in the extraction result.",
            current_state="null (not extracted / not found on the document)",
            expected_state="a non-empty string value",
        ))
        return ""
    return str(value)


def _optional_str(
    value: Any, *, warnings: list[AdapterWarning], document_type: str,
    document_id: str, field: str,
) -> Optional[str]:
    """Return ``None`` for an unavailable optional string field.

    Required legacy fields use ``_safe_str`` and its empty-string placeholder.
    New optional canonical fields must retain the difference between an empty
    value and no source signal, so they use this helper instead.
    """
    if value is None:
        warnings.append(AdapterWarning(
            document_type=document_type, document_id=document_id, field=field,
            message=f"{field} was null or unavailable in the extraction result.",
            current_state="null / no signal available",
            expected_state="a non-empty string value when present",
        ))
        return None
    return str(value)


def _safe_bool(
    value: Any, *, warnings: list[AdapterWarning], document_type: str, document_id: str, field: str,
    default: Optional[bool] = False,
) -> Optional[bool]:
    """`default` matters: use `False` only for fields where "not present in
    extraction" and "confirmed false" are genuinely the same thing to a
    caller. For presence/confirmation fields (signature captured, IC image
    present, financial-statement section present) they are NOT the same --
    pass `default=None` so "unknown" stays unknown instead of silently
    becoming a compliance failure (see ConsentFormData.signature_present's
    docstring)."""
    if value is None:
        warnings.append(AdapterWarning(
            document_type=document_type, document_id=document_id, field=field,
            message=f"{field} was null in the extraction result.",
            current_state="null (not extracted / not found on the document)",
            expected_state="true or false",
        ))
        return default
    return bool(value)


def _safe_float(value: Any, *, warnings: list[AdapterWarning], document_type: str, document_id: str, field: str) -> float:
    if value is None:
        warnings.append(AdapterWarning(
            document_type=document_type, document_id=document_id, field=field,
            message=f"{field} was null in the extraction result.",
            current_state="null (not extracted / not found on the document)",
            expected_state="a numeric value",
        ))
        return 0.0
    return float(value)


def _safe_list(value: Any, *, warnings: list[AdapterWarning], document_type: str, document_id: str, field: str) -> list:
    if value is None:
        warnings.append(AdapterWarning(
            document_type=document_type, document_id=document_id, field=field,
            message=f"{field} was null in the extraction result (expected an array).",
            current_state="null",
            expected_state="an array (possibly empty)",
        ))
        return []
    return value


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _parse_ddmmyyyy(value: str) -> date:
    """Extraction normalises dates to 'DD-MM-YYYY' strings; ValidationBundle
    fields are typed `date` (ISO), so this conversion is mandatory, not
    cosmetic -- Pydantic will reject 'DD-MM-YYYY' outright."""
    day, month, year = value.split("-")
    return date(int(year), int(month), int(day))


# ── SSM corporate forms ──────────────────────────────────────────────────────

_SSM_SUBTYPE_BY_TEMPLATE = {
    "SSM Form 24": "form_24",
    "SSM Form 44": "form_44",
    "SSM Form 49": "form_49",
    "SSM Form 9 & 28": "form_9",
    "Form 32A": "form_32a",
}

# SSM Business Registration is now extracted as ONE combined template instead
# of the three per-form templates above (see docs/ssm-one-form.md). The
# per-form keys are still accepted for backward compatibility with in-flight
# and historical bundles.
_SSM_COMBINED_TEMPLATE = "SSM Business Registration"

# Fields the removed verify_ssm_completeness rule used to guard indirectly.
# With that rule gone, the adapter raises an AdapterWarning per missing field
# so an incomplete SSM extraction still surfaces, just as a warning rather
# than a failing check. Entity Name / Business Registration Number are already
# warned on by _safe_str below, so they're not repeated here.
_SSM_COMBINED_REQUIRED_SCALARS = {
    "Incorporation Date": "the company's incorporation date",
    "Registered Address": "the registered office address",
}


def _warn_incomplete_combined_ssm(
    extracted: dict, document_id: str, warnings: list[AdapterWarning]
) -> None:
    """Emit an AdapterWarning for each incomplete field on a combined SSM doc."""
    for field, expected in _SSM_COMBINED_REQUIRED_SCALARS.items():
        if not extracted.get(field):
            warnings.append(AdapterWarning(
                document_type="ssm_corporate_form", document_id=document_id, field=field,
                message=f"SSM Business Registration is missing '{field}'.",
                current_state="null or empty", expected_state=expected,
            ))
    # Classification: at least one of MSIC Code / Main Business should be present.
    if not extracted.get("MSIC Code") and not extracted.get("Main Business"):
        warnings.append(AdapterWarning(
            document_type="ssm_corporate_form", document_id=document_id, field="MSIC Code",
            message="SSM Business Registration has neither 'MSIC Code' nor 'Main Business'.",
            current_state="both null or empty", expected_state="an MSIC code or a main-business description",
        ))
    # Directors must be a non-empty array (null when empty, per the template).
    if not extracted.get("Directors"):
        warnings.append(AdapterWarning(
            document_type="ssm_corporate_form", document_id=document_id, field="Directors",
            message="SSM Business Registration lists no directors.",
            current_state="null or empty", expected_state="at least one director row",
        ))
    if not extracted.get("Shareholders"):
        warnings.append(AdapterWarning(
            document_type="ssm_corporate_form", document_id=document_id, field="Shareholders",
            message="SSM Business Registration lists no shareholders.",
            current_state="null or empty", expected_state="at least one shareholder row",
        ))


def _build_ssm_doc(
    extracted: dict,
    *,
    document_id: str,
    document_subtype: Optional[str],
    source_template: str,
    entity_type: str,
    warnings: list[AdapterWarning],
    warn_incomplete: bool,
) -> SsmCorporateDoc:
    """Build one SsmCorporateDoc from either the combined or a per-form payload.

    Both shapes carry the same fields we validate against: scalar Entity Name /
    Business Registration Number, a Directors row_group (Name + NRIC), and a
    Shareholders row_group. `warn_incomplete` turns on the field-presence
    warnings for the combined template (the old completeness rule's job).
    """
    directors = None
    if "Directors" in extracted:
        director_rows = _safe_list(extracted.get("Directors"), warnings=warnings,
                                   document_type="ssm_corporate_form", document_id=document_id, field="Directors")
        directors = [
            PersonInfo(
                name=_safe_str((row or {}).get("Director Name"), warnings=warnings,
                                document_type="ssm_corporate_form", document_id=document_id,
                                field=f"Directors[{i}].Director Name"),
                nric_passport=_safe_str((row or {}).get("Director NRIC or Passport Number"), warnings=warnings,
                                         document_type="ssm_corporate_form", document_id=document_id,
                                         field=f"Directors[{i}].Director NRIC or Passport Number"),
            )
            for i, row in enumerate(director_rows)
        ]

    # The combined SSM Business Registration template now carries a
    # "Shareholder NRIC or Passport Number" attribute, so shareholders can be
    # built the same way as directors and reach find_missing_ic_documents.
    # The legacy per-form path (SSM Form 24) never had that attribute -- if no
    # row carries the NRIC key, keep degrading to "no shareholders on this doc"
    # rather than fabricating blank-NRIC people that would pollute IC coverage.
    _SHAREHOLDER_NRIC = "Shareholder NRIC or Passport Number"
    shareholders = None
    if "Shareholders" in extracted:
        shareholder_rows = _safe_list(extracted.get("Shareholders"), warnings=warnings,
                                      document_type="ssm_corporate_form", document_id=document_id, field="Shareholders")
        if any(_SHAREHOLDER_NRIC in (row or {}) for row in shareholder_rows):
            shareholders = [
                PersonInfo(
                    name=_safe_str((row or {}).get("Shareholder Name"), warnings=warnings,
                                    document_type="ssm_corporate_form", document_id=document_id,
                                    field=f"Shareholders[{i}].Shareholder Name"),
                    nric_passport=_safe_str((row or {}).get(_SHAREHOLDER_NRIC), warnings=warnings,
                                             document_type="ssm_corporate_form", document_id=document_id,
                                             field=f"Shareholders[{i}].{_SHAREHOLDER_NRIC}"),
                )
                for i, row in enumerate(shareholder_rows)
            ]
        elif shareholder_rows:
            warnings.append(AdapterWarning(
                document_type="ssm_corporate_form", document_id=document_id, field="Shareholders",
                message="Shareholders present but no Shareholder NRIC or Passport "
                        "Number attribute on any row to match them against IC/consent documents.",
                current_state=f"{len(shareholder_rows)} shareholder name(s), 0 shareholder NRIC(s)",
                expected_state="a Shareholder NRIC or Passport Number value per shareholder",
            ))

    if warn_incomplete:
        _warn_incomplete_combined_ssm(extracted, document_id, warnings)

    return SsmCorporateDoc(
        document_id=document_id,
        document_type="ssm_corporate_form",
        document_subtype=document_subtype,
        provenance=DocumentProvenance(source_template=source_template),
        data=SsmCorporateFormData(
            entity_name=_safe_str(extracted.get("Entity Name"), warnings=warnings,
                                   document_type="ssm_corporate_form", document_id=document_id, field="Entity Name"),
            business_registration_number=_safe_str(
                extracted.get("Business Registration Number"), warnings=warnings,
                document_type="ssm_corporate_form", document_id=document_id, field="Business Registration Number",
            ),
            entity_type=entity_type,
            directors=directors,
            shareholders=shareholders,
        ),
    )


def build_ssm_corporate_docs(
    extracted_by_template: dict[str, dict],
    *,
    entity_type: Optional[str] = None,
    warnings: Optional[list[AdapterWarning]] = None,
) -> list[SsmCorporateDoc]:
    """Build the SSM corporate document(s) from `extracted_by_template`.

    Prefers the single combined "SSM Business Registration" template
    (docs/ssm-one-form.md); also accepts the legacy per-form "SSM Form
    24/44/49" keys for backward compatibility. Both can appear, though in
    practice only one shape does.

    `entity_type` ("Sdn Bhd"/"Sole Proprietor"/"Partnership") has no source
    attribute in extraction at all -- it must be passed explicitly (e.g. the
    API `entity_type` param). If omitted, records an AdapterWarning and
    defaults to "".

    Completeness is no longer a validation rule: instead of counting distinct
    forms, this raises an AdapterWarning per missing field on the combined doc
    (see _warn_incomplete_combined_ssm). `warnings`, if passed, collects those
    plus the usual null-field warnings -- see module docstring.
    """
    warnings = warnings if warnings is not None else []

    if entity_type is None:
        warnings.append(AdapterWarning(
            document_type="ssm_corporate_form", document_id="(all)",
            field="entity_type",
            message="No entity_type supplied -- defaulted to \"\". There is no source "
                    "attribute for it in extraction; pass it explicitly (e.g. the API "
                    "entity_type param).",
            current_state="no signal available (defaulted to \"\")",
            expected_state="\"Sdn Bhd\", \"Sole Proprietor\", or \"Partnership\"",
        ))
        entity_type = ""

    docs: list[SsmCorporateDoc] = []

    # New single-template path.
    combined = extracted_by_template.get(_SSM_COMBINED_TEMPLATE)
    if combined is not None:
        docs.append(_build_ssm_doc(
            combined, document_id="ssm_business_registration", document_subtype=None,
            source_template=_SSM_COMBINED_TEMPLATE, entity_type=entity_type,
            warnings=warnings, warn_incomplete=True,
        ))

    # Legacy per-form path (backward compatibility).
    for template_name, subtype in _SSM_SUBTYPE_BY_TEMPLATE.items():
        extracted = extracted_by_template.get(template_name)
        if extracted is None:
            continue
        docs.append(_build_ssm_doc(
            extracted, document_id=f"ssm_{subtype}", document_subtype=subtype,
            source_template=template_name, entity_type=entity_type,
            warnings=warnings, warn_incomplete=False,
        ))
    return docs


# ── Financial statements / tax declaration ──────────────────────────────────

_FS_SECTION_FLAGS = {
    "balance_sheet_present": "Balance Sheet Present",
    "profit_and_loss_present": "Profit and Loss Statement Present",
    "cash_flow_present": "Cash Flow Statement Present",
    "auditors_report_present": "Auditor's Report Present",
}


def build_financial_statement_docs(
    extracted_by_template: dict[str, dict],
    *,
    entity_name: str,
    warnings: Optional[list[AdapterWarning]] = None,
) -> list[FinancialStatementDoc]:
    """One FinancialStatementDoc per row in the "Financial Statements (Sdn
    Bhd)" template's "Financials By Year" row_group (one row per comparative
    year, keyed by that row's Financial Statement Date). The 4 section-present
    flags are Unique (one value for the whole document), so every fanned-out
    year shares them.

    `entity_name` has no source attribute on this template -- thread it in
    from the matching SSM extraction call.
    """
    warnings = warnings if warnings is not None else []
    extracted = extracted_by_template.get("Financial Statements (Sdn Bhd)")
    if not extracted:
        return []

    if "Audited" not in extracted:
        warnings.append(AdapterWarning(
            document_type="financial_statement", document_id="financial_statement",
            field="audited",
            message="No explicit audited-status attribute exists in the current financial-statement template.",
            current_state="no signal available (left as null)",
            expected_state="true or false confirming whether the statements are audited",
        ))

    rows = _safe_list(extracted.get("Financials By Year"), warnings=warnings,
                      document_type="financial_statement", document_id="financial_statement",
                      field="Financials By Year")
    docs = []
    for i, row in enumerate(rows):
        document_id = f"financial_statement_{i}"
        fye = (row or {}).get("Financial Statement Date")
        if fye is None:
            warnings.append(AdapterWarning(
                document_type="financial_statement", document_id=document_id,
                field=f"Financials By Year[{i}].Financial Statement Date",
                message="A Financials By Year row had no Financial Statement Date; this year is skipped.",
                current_state="null",
                expected_state="a 'DD-MM-YYYY' date",
            ))
            continue
        docs.append(FinancialStatementDoc(
            document_id=document_id,
            document_type="financial_statement",
            data=FinancialStatementData(
                entity_name=entity_name,
                financial_year_end=_parse_ddmmyyyy(fye),
                audited=None,
                **{
                    field: _safe_bool(extracted.get(attr), warnings=warnings, document_type="financial_statement",
                                       document_id=document_id, field=attr, default=None)
                    for field, attr in _FS_SECTION_FLAGS.items()
                },
            ),
            provenance=DocumentProvenance(source_template="Financial Statements (Sdn Bhd)"),
        ))
    return docs


def build_tax_declaration_docs(
    extracted_by_template: dict[str, dict],
    *,
    entity_name: Optional[str] = None,
    financial_year_ends: Optional[list[str]] = None,
) -> list[TaxDeclarationDoc]:
    """Rule 2's alternate path: 2 years of LHDN tax declarations (extraction's
    "Borang B" template) for a Sole Prop/Partnership without audited FS.

    GAP: extraction's "Borang B" template currently only has "Profit Before
    Tax" wired -- no "Entity Name" or a per-year date attribute at all, so
    entity_name/financial_year_ends can't be read from the extraction result
    today and must be supplied explicitly. Raises AdapterDataGapError if
    they aren't (category 1 -- no source anywhere, not a per-document
    anomaly), rather than fabricating a tax declaration with a guessed
    entity name or date. Add "Entity Name" and a "Financial Year End" date
    attribute to the Borang B template to close this gap permanently.
    """
    extracted = extracted_by_template.get("Borang B")
    if not extracted:
        return []
    if not entity_name or not financial_year_ends:
        raise AdapterDataGapError(
            "Borang B extraction has no Entity Name or per-year date attribute -- "
            "entity_name and financial_year_ends must be supplied explicitly "
            "until those attributes are added to the Borang B template."
        )
    return [
        TaxDeclarationDoc(
            document_id=f"tax_declaration_{i}",
            document_type="tax_declaration",
            provenance=DocumentProvenance(source_template="Borang B"),
            data=TaxDeclarationData(entity_name=entity_name, financial_year_end=_parse_ddmmyyyy(fye)),
        )
        for i, fye in enumerate(financial_year_ends)
    ]


# ── Bank statements ──────────────────────────────────────────────────────────

def build_bank_statement_doc(
    extracted_by_template: dict[str, dict],
    *,
    entity_name: str,
    warnings: Optional[list[AdapterWarning]] = None,
) -> Optional[BankStatementDoc]:
    """One consolidated BankStatementDoc built from the "Bank Statements"
    template's daily Transactions row_group. The monthly end balance is the
    balance of the last-dated transaction in each calendar month (the same
    rollup services/aggregation does for the frontend); the statement period
    spans the earliest-to-latest transaction date. The overdraft check reads
    the monthly end balances.

    `entity_name` has no source attribute on this template -- thread it in
    from the matching SSM extraction call.
    """
    warnings = warnings if warnings is not None else []
    extracted = extracted_by_template.get("Bank Statements")
    if not extracted:
        return None
    document_id = "bank_statement"

    txns = _safe_list(
        extracted.get("Transactions"), warnings=warnings,
        document_type="bank_statement", document_id=document_id, field="Transactions",
    )

    # Group transaction rows by calendar month, skipping undatable rows (the
    # adapter never crashes on bad data -- it records a warning and moves on).
    by_month: dict[tuple[int, int], list] = {}
    for i, row in enumerate(txns):
        raw_date = (row or {}).get("Transaction Date")
        try:
            d = date.fromisoformat(str(raw_date))
        except (TypeError, ValueError):
            if raw_date:
                warnings.append(AdapterWarning(
                    document_type="bank_statement", document_id=document_id,
                    field=f"Transactions[{i}].Transaction Date",
                    message=f"unparseable transaction date {raw_date!r}; row excluded from monthly balances.",
                    current_state=str(raw_date), expected_state="an ISO date (YYYY-MM-DD)",
                ))
            continue
        by_month.setdefault((d.year, d.month), []).append((d, row))

    if not by_month:
        return None

    bank_name = _optional_str(
        extracted.get("Bank Name"), warnings=warnings,
        document_type="bank_statement", document_id=document_id, field="Bank Name",
    )

    # Currency is read from the template's "Currency" attribute. For now a
    # non-MYR statement is only warned (the check_bank_statement_currency rule
    # already treats a mismatch as needs-review, not a hard fail -- it needs
    # manual conversion before balances compare like-for-like).
    currency = _optional_str(
        extracted.get("Currency"), warnings=warnings,
        document_type="bank_statement", document_id=document_id, field="Currency",
    )
    if currency is not None and currency.strip().upper() != "MYR":
        warnings.append(AdapterWarning(
            document_type="bank_statement", document_id=document_id, field="Currency",
            message=f"Bank statement currency is {currency!r}, not MYR -- needs conversion and manual review.",
            current_state=currency, expected_state="MYR",
        ))

    # Account Type still has no source attribute on the Bank Statements
    # template -- keep it explicitly unknown and surface the integration gap.
    warnings.append(AdapterWarning(
        document_type="bank_statement", document_id=document_id, field="Account Type",
        message="Account Type has no source attribute in the current Bank Statements template.",
        current_state="no signal available (left as null)",
        expected_state="an account type, e.g. current or savings",
    ))

    monthly_balances = []
    all_dates: list[date] = []
    for (year, month) in sorted(by_month):
        rows = sorted(by_month[(year, month)], key=lambda dr: dr[0])  # by date; stable -> ties keep input order
        all_dates.extend(d for d, _ in rows)
        last_row = rows[-1][1]
        monthly_balances.append(MonthlyBankBalance(
            month=date(year, month, 1).strftime("%B %Y"),
            end_balance=_safe_float(
                last_row.get("Transaction Balance"), warnings=warnings,
                document_type="bank_statement", document_id=document_id,
                field=f"Transaction Balance[{year}-{month:02d}]",
            ),
        ))

    return BankStatementDoc(
        document_id=document_id,
        document_type="bank_statement",
        data=BankStatementData(
            entity_name=entity_name,
            bank_name=bank_name,
            currency=currency,
            account_type=None,
            statement_start_date=min(all_dates),
            statement_end_date=max(all_dates),
            monthly_balances=monthly_balances,
        ),
        provenance=DocumentProvenance(source_template="Bank Statements"),
    )


# ── Identity documents (MyKad) ───────────────────────────────────────────────

def build_identity_documents(
    extracted_by_template: dict[str, dict],
    *,
    warnings: Optional[list[AdapterWarning]] = None,
) -> list[IdentityDoc]:
    """One IdentityDoc per director, read from the MyKad template's "Directors"
    row_group -- one correlated object per director carrying Director Name/NRIC/
    Front Side IC Present/Back Side IC Present/ID Type, so a name can't drift onto
    another director's NRIC or IC-present flags.

    GAP: no "expiry_date" attribute exists on the MyKad template --
    IdentityDocumentData.expiry_date is always None until one is added.
    """
    warnings = warnings if warnings is not None else []
    extracted = extracted_by_template.get("MyKad (Director ID or Passport)")
    if not extracted:
        return []
    document_id = "identity_document"

    rows = _safe_list(extracted.get("Directors"), warnings=warnings,
                      document_type="identity_document", document_id=document_id, field="Directors")
    return [
        IdentityDoc(
            document_id=f"identity_document_{i}",
            document_type="identity_document",
            provenance=DocumentProvenance(source_template="MyKad (Director ID or Passport)"),
            data=IdentityDocumentData(
                individual_name=_safe_str((row or {}).get("Director Name"), warnings=warnings, document_type="identity_document",
                                           document_id=f"identity_document_{i}", field=f"Directors[{i}].Director Name"),
                nric_passport=_safe_str((row or {}).get("Director NRIC or Passport Number"), warnings=warnings, document_type="identity_document",
                                         document_id=f"identity_document_{i}", field=f"Directors[{i}].Director NRIC or Passport Number"),
                front_image_present=_safe_bool((row or {}).get("Front Side IC Present"), warnings=warnings, document_type="identity_document",
                                                document_id=f"identity_document_{i}", field=f"Directors[{i}].Front Side IC Present",
                                                default=None),
                back_image_present=_safe_bool((row or {}).get("Back Side IC Present"), warnings=warnings, document_type="identity_document",
                                               document_id=f"identity_document_{i}", field=f"Directors[{i}].Back Side IC Present",
                                               default=None),
                expiry_date=None,
            ),
        )
        for i, row in enumerate(rows)
    ]


# ── Consent forms ─────────────────────────────────────────────────────────────

def build_consent_form_docs(
    extracted_by_template: dict[str, dict],
    *,
    warnings: Optional[list[AdapterWarning]] = None,
) -> list[ConsentFormDoc]:
    """One ConsentFormDoc per signatory on the Consent Form template.

    The live template puts the signatories in an "Applicants" row_group; each
    row carries Director Name, Director NRIC or Passport Number, and a
    "Consent Form Signature" boolean. "Directors" is accepted as a fallback
    key for older extraction output / fixtures.

    Per-row signature comes from that "Consent Form Signature" boolean. A null
    boolean stays None ("not confirmed" -- distinct from False/"confirmed
    unsigned"; verify_consent_signatures treats the two differently), recorded
    as an AdapterWarning, not silently.
    """
    warnings = warnings if warnings is not None else []
    extracted = extracted_by_template.get("Consent Form")
    if not extracted:
        return []
    document_id = "consent_form"

    entity_name = _safe_str(extracted.get("Entity Name"), warnings=warnings, document_type="consent_form",
                             document_id=document_id, field="Entity Name")
    # Live template row_group is "Applicants"; fall back to "Directors" for
    # older extraction output.
    row_group_key = "Applicants" if "Applicants" in extracted else "Directors"
    rows = _safe_list(extracted.get(row_group_key), warnings=warnings,
                      document_type="consent_form", document_id=document_id, field=row_group_key)

    docs = []
    for i, row in enumerate(rows):
        row = row or {}
        row_signature = _safe_bool(
            row.get("Consent Form Signature"), warnings=warnings, document_type="consent_form",
            document_id=f"consent_form_{i}", field=f"{row_group_key}[{i}].Consent Form Signature",
            default=None,  # null stays "not confirmed", never a silent False
        )
        docs.append(ConsentFormDoc(
            document_id=f"consent_form_{i}",
            document_type="consent_form",
            provenance=DocumentProvenance(source_template="Consent Form"),
            data=ConsentFormData(
                entity_name=entity_name,
                individual_name=_safe_str(row.get("Director Name"), warnings=warnings, document_type="consent_form",
                                           document_id=f"consent_form_{i}", field=f"{row_group_key}[{i}].Director Name"),
                nric_passport=_safe_str(row.get("Director NRIC or Passport Number"), warnings=warnings, document_type="consent_form",
                                         document_id=f"consent_form_{i}", field=f"{row_group_key}[{i}].Director NRIC or Passport Number"),
                position=None,
                signature_present=row_signature,
            ),
        ))
    return docs


# ── Customer information ─────────────────────────────────────────────────────

# Customer Information Form template -> CustomerInfoData field mapping.
# model field: extraction attribute name.
_CUSTOMER_INFO_DIRECTOR_FIELDS = {
    "name": "Director Name",
    "address": "Director Address",
    "email": "Director Email Address",
    "religion": "Director Religion",
    "marital_status": "Director Marital Status",
    "estimated_monthly_income": "Director Estimated Monthly Income",
    "experience_in_current_business": "Director Experience in Current Business",
    "higher_education": "Director Higher Education",
    "emergency_contact_name": "Director Emergency Contact Name",
    "emergency_contact_number": "Director Emergency Contact Number",
    "emergency_contact_relationship": "Director Emergency Contact Relationship",
    "spouse_name": "Director Spouse Name",
    "spouse_contact_number": "Director Spouse Contact Number",
}
_CUSTOMER_INFO_COMPANY_FIELDS = {
    "company_age": "Company Age",
    "company_number_of_staff": "Company Number of Staff",
    "company_current_office_address": "Company Current Office Address",
    "company_office_status": "Company Office Status",
    "company_office_monthly_rent": "Company Office Monthly Rent",
    "company_office_telephone": "Company Office Telephone",
    "company_email_address": "Company Email Address",
    "company_auditor_firm_name": "Company Auditor Firm Name",
    "company_auditor_contact_person": "Company Auditor Contact Person",
    "company_auditor_contact_number": "Company Auditor Contact Number",
}


def build_customer_information_doc(
    extracted_by_template: dict[str, dict],
    *,
    warnings: Optional[list[AdapterWarning]] = None,
) -> Optional[CustomerInfoDoc]:
    """CustomerInfoDoc built from the "Customer Information Form" template --
    director personal particulars (one row per director) plus company info.

    Null fields are coerced to "" here (not warned per-field): the
    verify_customer_information_completeness rule is the single place that
    reports which fields are unfilled, so the adapter doesn't also flood
    adapter_warnings with one entry per blank cell.
    """
    warnings = warnings if warnings is not None else []
    extracted = extracted_by_template.get("Customer Information Form")
    if not extracted:
        return None
    document_id = "customer_information"

    def blank(value: object) -> str:
        return "" if value is None else str(value)

    director_rows = _safe_list(extracted.get("Directors"), warnings=warnings,
                               document_type="customer_information", document_id=document_id, field="Directors")
    directors = [
        CustomerInfoDirector(**{
            model_field: blank((row or {}).get(attr))
            for model_field, attr in _CUSTOMER_INFO_DIRECTOR_FIELDS.items()
        })
        for row in director_rows
    ]

    return CustomerInfoDoc(
        document_id=document_id,
        document_type="customer_information",
        provenance=DocumentProvenance(source_template="Customer Information Form"),
        data=CustomerInfoData(
            directors=directors,
            **{model_field: blank(extracted.get(attr))
               for model_field, attr in _CUSTOMER_INFO_COMPANY_FIELDS.items()},
        ),
    )


# ── Top-level orchestrator ───────────────────────────────────────────────────

def build_validation_bundle(
    extracted_by_template: dict[str, dict],
    *,
    bundle_id: Optional[str] = None,
    system_date: Optional[date] = None,
    entity_type: Optional[str] = None,
    tax_declaration_entity_name: Optional[str] = None,
    tax_declaration_fye_dates: Optional[list[str]] = None,
) -> AdapterResult:
    """Assembles a full ValidationBundle from every extraction result
    available in `extracted_by_template` -- the ONLY required argument, so
    a raw extraction results dump (e.g.
    examples/extraction_results_example.json, unmodified) is a valid call
    on its own. Missing document types are simply omitted (ValidationEngine
    already treats an absent document_type as "skip that check", not a
    failure) -- callers don't need to know in advance which templates were
    actually run.

    Returns an AdapterResult{bundle, warnings}: `warnings` collects every
    anomaly found while building the bundle -- null values, array
    misalignments, AND every keyword arg below that wasn't supplied and
    couldn't be derived (see module docstring). Always check it, even when
    the bundle built successfully; a warning means a value was defaulted
    rather than genuinely present, which matters even though it never
    blocks bundle construction.

    `bundle_id`/`system_date` default to a generated id / today's date if
    omitted. `entity_type` has no source attribute in extraction and must be
    passed explicitly (see build_ssm_corporate_docs); `tax_declaration_*` have
    no extraction source yet. Each defaults to a conservative placeholder with
    a warning when omitted.
    """
    warnings: list[AdapterWarning] = []
    bundle_id = bundle_id or f"bundle-{uuid4()}"
    system_date = system_date or date.today()

    ssm_docs = build_ssm_corporate_docs(extracted_by_template, entity_type=entity_type, warnings=warnings)
    entity_name = ssm_docs[0].data.entity_name if ssm_docs else ""

    tax_declaration_docs = (
        build_tax_declaration_docs(
            extracted_by_template,
            entity_name=tax_declaration_entity_name or entity_name or None,
            financial_year_ends=tax_declaration_fye_dates,
        )
        if "Borang B" in extracted_by_template and tax_declaration_fye_dates
        else []
    )

    documents = [
        *ssm_docs,
        *tax_declaration_docs,
        *build_financial_statement_docs(extracted_by_template, entity_name=entity_name, warnings=warnings),
        *build_identity_documents(extracted_by_template, warnings=warnings),
        *build_consent_form_docs(extracted_by_template, warnings=warnings),
    ]

    bank_doc = build_bank_statement_doc(extracted_by_template, entity_name=entity_name, warnings=warnings)
    if bank_doc:
        documents.append(bank_doc)

    customer_info_doc = build_customer_information_doc(extracted_by_template, warnings=warnings)
    if customer_info_doc:
        documents.append(customer_info_doc)

    bundle = ValidationBundle(
        bundle_id=bundle_id,
        metadata=BundleMetadata(
            total_documents_received=len(documents),
            system_date=system_date,
            document_types_present=sorted({d.document_type for d in documents}),
        ),
        extracted_documents=documents,
    )
    return AdapterResult(bundle=bundle, warnings=warnings)
