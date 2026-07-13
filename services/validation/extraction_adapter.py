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

Known correlation risk: SSM Form 24 (Shareholder Name/Address/Percentage)
and SSM Form 49 (Director Name/Address/NRIC) are NOT row_group-correlated in
the current seed data, so build_ssm_corporate_docs() zips them by array
index -- correct only if Gemini happened to return them in matching order.
MyKad's "Director" row_group covers Director Name/NRIC/Back Side IC
Present/ID Type but NOT Front Side IC Present, so that one field is also
index-zipped in as a best effort. Retrofitting row_group onto the ungrouped
fields (already possible via the CRUD API) removes this risk for real --
until then, any length mismatch shows up as an AdapterWarning instead of a
crash or a silently-wrong pairing.
"""
import calendar
from datetime import date, datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel

from .bundle import (
    BankStatementData,
    BankStatementDoc,
    BundleMetadata,
    ConsentFormData,
    ConsentFormDoc,
    CustomerInfoData,
    CustomerInfoDoc,
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
    warnings: list[AdapterWarning] = []


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


def _parse_month_year(value: str) -> tuple[date, date]:
    """'July 2023' -> (2023-07-01, 2023-07-31): the first and last calendar
    day of that month, used as one statement period's start/end date."""
    dt = datetime.strptime(value.strip(), "%B %Y")
    start = date(dt.year, dt.month, 1)
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    end = date(dt.year, dt.month, last_day)
    return start, end


def _zip_positional(
    extracted: dict,
    *field_names: str,
    warnings: list[AdapterWarning],
    document_type: str,
    document_id: str,
) -> list[tuple]:
    """Zip several Multiple-valued fields by array index. On a length
    mismatch, truncates to the shortest array (rather than crashing) and
    records an AdapterWarning stating each field's actual length against
    the expectation that they all match -- the bundle still builds, and the
    mismatch is explicit for the AI review step to look closely at, instead
    of a silently-wrong positional pairing or an unhandled exception."""
    lists = [_safe_list(extracted.get(name), warnings=warnings, document_type=document_type,
                         document_id=document_id, field=name) for name in field_names]
    lengths = {len(l) for l in lists}
    if len(lengths) > 1:
        actual = ", ".join(f"{name}={len(l)}" for name, l in zip(field_names, lists))
        warnings.append(AdapterWarning(
            document_type=document_type, document_id=document_id,
            field="/".join(field_names),
            message=(
                f"Array length mismatch across correlated fields {field_names} -- "
                f"truncated to the shortest so the bundle can still be built; "
                f"verify these are actually aligned row-by-row."
            ),
            current_state=actual,
            expected_state=f"all {len(field_names)} arrays the same length (one entry per correlated row)",
        ))
    min_len = min(lengths) if lengths else 0
    truncated = [l[:min_len] for l in lists]
    return list(zip(*truncated))


# ── SSM corporate forms ──────────────────────────────────────────────────────

_SSM_SUBTYPE_BY_TEMPLATE = {
    "SSM Form 24": "form_24",
    "SSM Form 44": "form_44",
    "SSM Form 49": "form_49",
    "SSM Form 9 & 28": "form_9",
    "Form 32A": "form_32a",
}


def build_ssm_corporate_docs(
    extracted_by_template: dict[str, dict],
    *,
    entity_type: Optional[str] = None,
    warnings: Optional[list[AdapterWarning]] = None,
) -> list[SsmCorporateDoc]:
    """One SsmCorporateDoc per SSM template present in `extracted_by_template`.

    `entity_type` ("Sdn Bhd"/"Sole Proprietor"/"Partnership") has no source
    attribute on any SSM template -- per the rules table, it's entered by
    SME Sales on the Application Details page. If not passed explicitly,
    this reads it straight from `extracted_by_template["Application
    Details"]["Business Entity Type"]` when that template is present in the
    same call (it usually will be); otherwise records an AdapterWarning and
    defaults to "".

    NOTE: extraction currently has no template at all for SSM's own Form B
    (sole prop registration) / Form D (partnership registration) -- only
    Form 24/44/49 (Sdn Bhd) and Form 9&28/32A. A Sole Prop/Partnership
    bundle can't produce an ssm_corporate_form doc with document_subtype
    "form_b"/"form_d" until those templates are added.

    `warnings`, if passed, collects AdapterWarnings for null fields (e.g. a
    null Entity Name) and array-length mismatches between Director
    Name/NRIC -- see module docstring.
    """
    warnings = warnings if warnings is not None else []

    if entity_type is None:
        entity_type = (extracted_by_template.get("Application Details") or {}).get("Business Entity Type")
        if entity_type is None:
            warnings.append(AdapterWarning(
                document_type="ssm_corporate_form", document_id="(all)",
                field="entity_type",
                message="No entity_type override supplied and 'Application Details' -> "
                        "'Business Entity Type' isn't available either -- defaulted to \"\".",
                current_state="no signal available (defaulted to \"\")",
                expected_state="\"Sdn Bhd\", \"Sole Proprietor\", or \"Partnership\"",
            ))
            entity_type = ""

    docs: list[SsmCorporateDoc] = []
    for template_name, subtype in _SSM_SUBTYPE_BY_TEMPLATE.items():
        extracted = extracted_by_template.get(template_name)
        if extracted is None:
            continue
        document_id = f"ssm_{subtype}"

        directors = None
        if "Director Name" in extracted:
            directors = [
                PersonInfo(
                    name=_safe_str(name, warnings=warnings, document_type="ssm_corporate_form",
                                    document_id=document_id, field=f"Director Name[{i}]"),
                    nric_passport=_safe_str(nric, warnings=warnings, document_type="ssm_corporate_form",
                                             document_id=document_id, field=f"Director NRIC or Passport Number[{i}]"),
                )
                for i, (name, nric) in enumerate(_zip_positional(
                    extracted, "Director Name", "Director NRIC or Passport Number",
                    warnings=warnings, document_type="ssm_corporate_form", document_id=document_id,
                ))
            ]

        # GAP: SSM Form 24 has "Shareholder Name"/"Address"/"Percentage" but
        # no "Shareholder NRIC or Passport Number" attribute at all -- and
        # PersonInfo.nric_passport is required, so shareholders can't be
        # built without one. `shareholders` is Optional on
        # SsmCorporateFormData (unlike e.g. tenure_months), so this degrades
        # to "no shareholders on this doc" rather than blocking the whole
        # bundle -- but it means find_missing_ic_documents/
        # verify_consent_signatures won't see these shareholders at all.
        # Add a "Shareholder NRIC or Passport Number" attribute to close
        # this gap for real.
        shareholders = None
        if "Shareholder Name" in extracted:
            warnings.append(AdapterWarning(
                document_type="ssm_corporate_form", document_id=document_id,
                field="Shareholder Name",
                message="Shareholder Name present but no Shareholder NRIC or Passport "
                        "Number attribute exists to match them against IC/consent documents.",
                current_state=f"{len(extracted.get('Shareholder Name') or [])} shareholder name(s), 0 shareholder NRIC(s)",
                expected_state="a Shareholder NRIC or Passport Number value per shareholder",
            ))

        docs.append(SsmCorporateDoc(
            document_id=document_id,
            document_type="ssm_corporate_form",
            document_subtype=subtype,
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
    """One FinancialStatementDoc per year column on the "Financial
    Statements (Sdn Bhd)" template's "Financial Statement Date" (Multiple --
    one entry per comparative year). The 4 section-present flags are Unique
    (one value for the whole document), so every fanned-out year shares them.

    `entity_name` has no source attribute on this template -- thread it in
    from the matching SSM extraction call.
    """
    warnings = warnings if warnings is not None else []
    extracted = extracted_by_template.get("Financial Statements (Sdn Bhd)")
    if not extracted:
        return []

    fye_dates = _safe_list(extracted.get("Financial Statement Date"), warnings=warnings,
                            document_type="financial_statement", document_id="financial_statement",
                            field="Financial Statement Date")
    docs = []
    for i, fye in enumerate(fye_dates):
        document_id = f"financial_statement_{i}"
        if fye is None:
            warnings.append(AdapterWarning(
                document_type="financial_statement", document_id=document_id,
                field=f"Financial Statement Date[{i}]",
                message="A Financial Statement Date entry was null; this year column is skipped.",
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
                **{
                    field: _safe_bool(extracted.get(attr), warnings=warnings, document_type="financial_statement",
                                       document_id=document_id, field=attr, default=None)
                    for field, attr in _FS_SECTION_FLAGS.items()
                },
            ),
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
    """One consolidated BankStatementDoc spanning every month in the "Bank
    Statements" template's Multiple arrays (Bank Statement Month/Withdrawal/
    Deposit/End Balance), covering the full min-to-max date range with a
    monthly_balances breakdown for the overdraft check.

    `entity_name` has no source attribute on this template -- thread it in
    from the matching SSM extraction call.
    """
    warnings = warnings if warnings is not None else []
    extracted = extracted_by_template.get("Bank Statements")
    if not extracted:
        return None
    document_id = "bank_statement"

    rows = _zip_positional(
        extracted, "Bank Statement Month", "Monthly End Balance",
        warnings=warnings, document_type="bank_statement", document_id=document_id,
    )
    rows = [(month, balance) for month, balance in rows if month is not None]
    if not rows:
        return None

    periods = [_parse_month_year(month) for month, _ in rows]
    start = min(p[0] for p in periods)
    end = max(p[1] for p in periods)

    return BankStatementDoc(
        document_id=document_id,
        document_type="bank_statement",
        data=BankStatementData(
            entity_name=entity_name,
            statement_start_date=start,
            statement_end_date=end,
            monthly_balances=[
                MonthlyBankBalance(
                    month=month,
                    end_balance=_safe_float(balance, warnings=warnings, document_type="bank_statement",
                                             document_id=document_id, field=f"Monthly End Balance[{month}]"),
                )
                for month, balance in rows
            ],
        ),
    )


# ── Identity documents (MyKad) ───────────────────────────────────────────────

def build_identity_documents(
    extracted_by_template: dict[str, dict],
    *,
    warnings: Optional[list[AdapterWarning]] = None,
) -> list[IdentityDoc]:
    """One IdentityDoc per director, fanned out from the MyKad template's
    per-director Multiple fields. Director Name/NRIC/Back Side IC Present/
    ID Type share row_group="Director" in the current schema (reliable
    correlation); Front Side IC Present is not in that group and is
    index-zipped in as a best effort (see module docstring).

    GAP: no "expiry_date" attribute exists on the MyKad template --
    IdentityDocumentData.expiry_date is always None until one is added.
    """
    warnings = warnings if warnings is not None else []
    extracted = extracted_by_template.get("MyKad (Director ID or Passport)")
    if not extracted:
        return []
    document_id = "identity_document"

    rows = _zip_positional(
        extracted,
        "Director Name", "Director NRIC or Passport Number",
        "Front Side IC Present", "Back Side IC Present",
        warnings=warnings, document_type="identity_document", document_id=document_id,
    )
    return [
        IdentityDoc(
            document_id=f"identity_document_{i}",
            document_type="identity_document",
            data=IdentityDocumentData(
                individual_name=_safe_str(name, warnings=warnings, document_type="identity_document",
                                           document_id=f"identity_document_{i}", field="Director Name"),
                nric_passport=_safe_str(nric, warnings=warnings, document_type="identity_document",
                                         document_id=f"identity_document_{i}", field="Director NRIC or Passport Number"),
                front_image_present=_safe_bool(front, warnings=warnings, document_type="identity_document",
                                                document_id=f"identity_document_{i}", field="Front Side IC Present",
                                                default=None),
                back_image_present=_safe_bool(back, warnings=warnings, document_type="identity_document",
                                               document_id=f"identity_document_{i}", field="Back Side IC Present",
                                               default=None),
                expiry_date=None,
            ),
        )
        for i, (name, nric, front, back) in enumerate(rows)
    ]


# ── Consent forms ─────────────────────────────────────────────────────────────

def build_consent_form_docs(
    extracted_by_template: dict[str, dict],
    *,
    signature_present: Optional[bool] = None,
    warnings: Optional[list[AdapterWarning]] = None,
) -> list[ConsentFormDoc]:
    """One ConsentFormDoc per signatory on the Consent Form template.

    GAP: no "signature_present" attribute exists on the Consent Form
    template -- there's currently no extracted signal for whether a
    signature was actually captured. `signature_present` must be supplied
    explicitly (e.g. from a separate signature-detection step, or manual
    confirmation); if omitted, stays None ("not confirmed" -- distinct from
    False/"confirmed unsigned"; verify_consent_signatures treats the two
    differently), recorded as an AdapterWarning, not silently. Add a
    "Signature Present" boolean attribute to the Consent Form template to
    close this gap for real.
    """
    warnings = warnings if warnings is not None else []
    extracted = extracted_by_template.get("Consent Form")
    if not extracted:
        return []
    document_id = "consent_form"

    entity_name = _safe_str(extracted.get("Entity Name"), warnings=warnings, document_type="consent_form",
                             document_id=document_id, field="Entity Name")
    rows = _zip_positional(
        extracted, "Director Name", "Director NRIC or Passport Number",
        warnings=warnings, document_type="consent_form", document_id=document_id,
    )

    if signature_present is None:
        warnings.append(AdapterWarning(
            document_type="consent_form", document_id=document_id,
            field="signature_present",
            message="No signature_present override supplied and no source attribute exists "
                    "on the Consent Form template -- left as null (not confirmed either way).",
            current_state="no signal available (null -- not confirmed, not denied)",
            expected_state="true if a captured signature was confirmed, false if confirmed absent",
        ))

    return [
        ConsentFormDoc(
            document_id=f"consent_form_{i}",
            document_type="consent_form",
            data=ConsentFormData(
                entity_name=entity_name,
                individual_name=_safe_str(name, warnings=warnings, document_type="consent_form",
                                           document_id=f"consent_form_{i}", field="Director Name"),
                nric_passport=_safe_str(nric, warnings=warnings, document_type="consent_form",
                                         document_id=f"consent_form_{i}", field="Director NRIC or Passport Number"),
                position=None,
                signature_present=signature_present,  # stays None if not confirmed either way
            ),
        )
        for i, (name, nric) in enumerate(rows)
    ]


# ── Customer information / Application Details ───────────────────────────────

def build_customer_information_doc(
    extracted_by_template: dict[str, dict],
    *,
    tenure_months: Optional[int] = None,
    repayment_frequency: Optional[str] = None,
    warnings: Optional[list[AdapterWarning]] = None,
) -> Optional[CustomerInfoDoc]:
    """CustomerInfoDoc built from the "Application Details" template (not
    "Customer Information Form", despite the name -- see the extraction ->
    validation assessment: main contact / financing fields live on
    Application Details).

    GAP: no "tenure_months" or "repayment_frequency" attribute exists on
    Application Details -- both are required (non-Optional) fields on
    CustomerInfoData. If not supplied, defaults to 0 / "Unknown" and
    records an AdapterWarning rather than blocking bundle construction --
    note this does mean validate_form_d_expiry's tenure-coverage math is
    meaningless until a real value is supplied (that check is also
    currently always skipped for an unrelated reason -- see engine.py).
    """
    warnings = warnings if warnings is not None else []
    extracted = extracted_by_template.get("Application Details")
    if not extracted:
        return None
    document_id = "customer_information"

    if tenure_months is None:
        warnings.append(AdapterWarning(
            document_type="customer_information", document_id=document_id,
            field="tenure_months",
            message="No tenure_months override supplied and no source attribute exists "
                    "on Application Details -- defaulted to 0.",
            current_state="no signal available (defaulted to 0)",
            expected_state="the requested financing tenure in months",
        ))
        tenure_months = 0
    if repayment_frequency is None:
        warnings.append(AdapterWarning(
            document_type="customer_information", document_id=document_id,
            field="repayment_frequency",
            message="No repayment_frequency override supplied and no source attribute exists "
                    "on Application Details -- defaulted to \"Unknown\".",
            current_state="no signal available (defaulted to \"Unknown\")",
            expected_state="e.g. \"Monthly\", \"Quarterly\"",
        ))
        repayment_frequency = "Unknown"

    return CustomerInfoDoc(
        document_id=document_id,
        document_type="customer_information",
        data=CustomerInfoData(
            main_contact_names=_safe_list(extracted.get("Main Contact Name"), warnings=warnings,
                                           document_type="customer_information", document_id=document_id,
                                           field="Main Contact Name"),
            main_contact_emails=_safe_list(extracted.get("Main Contact Email"), warnings=warnings,
                                            document_type="customer_information", document_id=document_id,
                                            field="Main Contact Email"),
            main_contact_phone_numbers=_safe_list(extracted.get("Main Contact Phone Number"), warnings=warnings,
                                                   document_type="customer_information", document_id=document_id,
                                                   field="Main Contact Phone Number"),
            financing_amount=_safe_float(extracted.get("Proposed Financing Amount"), warnings=warnings,
                                          document_type="customer_information", document_id=document_id,
                                          field="Proposed Financing Amount"),
            product_type=_safe_str(extracted.get("Proposed Program"), warnings=warnings,
                                    document_type="customer_information", document_id=document_id,
                                    field="Proposed Program"),
            tenure_months=tenure_months,
            repayment_frequency=repayment_frequency,
        ),
    )


# ── Top-level orchestrator ───────────────────────────────────────────────────

def build_validation_bundle(
    extracted_by_template: dict[str, dict],
    *,
    bundle_id: Optional[str] = None,
    system_date: Optional[date] = None,
    entity_type: Optional[str] = None,
    tenure_months: Optional[int] = None,
    repayment_frequency: Optional[str] = None,
    signature_present: Optional[bool] = None,
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
    omitted. `entity_type` is read from `extracted_by_template["Application
    Details"]["Business Entity Type"]` when not passed explicitly (see
    build_ssm_corporate_docs). `tenure_months`/`repayment_frequency`/
    `signature_present`/`tax_declaration_*` have no source in extraction at
    all yet (see each build_* function's docstring for the specific gap and
    the attribute to add) and default to conservative placeholders with a
    warning when omitted.
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
        *build_consent_form_docs(extracted_by_template, signature_present=signature_present, warnings=warnings),
    ]

    bank_doc = build_bank_statement_doc(extracted_by_template, entity_name=entity_name, warnings=warnings)
    if bank_doc:
        documents.append(bank_doc)

    customer_info_doc = build_customer_information_doc(
        extracted_by_template, tenure_months=tenure_months, repayment_frequency=repayment_frequency,
        warnings=warnings,
    )
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
