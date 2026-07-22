"""
Completeness validation tools for BMMB document bundle checks.

Same contract as tools/date_logic.py: every function returns

    {
        "passed": bool,
        "message": str,
        "details": {...}
    }

Inputs are plain dicts (or objects with matching attributes aren't
supported here — pass `.model_dump()` / plain dicts from the parsed
ValidationBundle) so these tools stay independent of the pydantic schema.

Docstrings are written Google-style (with an Args: section) because the
Gemini function-calling binding sends the whole docstring as the tool's
description verbatim; per-argument text lives here, not in a separate
schema field.
"""

from typing import Dict, List

from ._utils import normalize_id

# NOTE: nested-object parameters are typed as `List[Dict[str, object]]`, not
# `List[SomeTypedDict]` or `List[Dict[str, Any]]`. Gemini's automatic
# function-calling *schema generation* accepts TypedDict and `Any` fine,
# but its *argument execution* does not: TypedDict raises "TypedDict does
# not support instance and class checks"; a bare (unparameterized) `Dict`
# raises "not enough values to unpack (expected 2, got 0)" since the SDK
# calls typing.get_args() expecting a (key_type, value_type) pair; and
# `Dict[str, Any]` raises "typing.Any cannot be used with isinstance()"
# since the SDK isinstance-checks each value against the value type.
# `Dict[str, object]` is the only combination that survives both schema
# generation and execution, at the cost of a looser schema.

def verify_financial_sections_present(financial_statement_data: List[Dict[str, object]]) -> Dict:
    """Check the Balance Sheet / P&L / Cash Flow / Auditor's Report flags on financial statements.

    Use this for every financial_statement document in the bundle to confirm
    the extraction agent found all 4 required sections in each one. Each
    flag is a tri-state: True (confirmed present), False (confirmed
    absent -- a real gap), or null (extraction couldn't determine it --
    "needs review", not the same as a confirmed absence).

    Args:
        financial_statement_data: One entry per financial_statement
            document, with its entity_name, financial_year_end, and the 4
            tri-state section-present flags.
    """
    section_flags = {
        "balance_sheet_present": "Balance Sheet",
        "profit_and_loss_present": "Profit & Loss",
        "cash_flow_present": "Cash Flow",
        "auditors_report_present": "Auditor's Report",
    }

    incomplete_documents = []  # has at least one confirmed-absent (False) section
    needs_review_documents = []  # no confirmed-absent sections, but at least one unconfirmed (null)
    for doc in financial_statement_data:
        missing_sections = [label for flag, label in section_flags.items() if doc.get(flag) is False]
        unconfirmed_sections = [label for flag, label in section_flags.items() if doc.get(flag) is None]
        entry = {
            "entity_name": doc.get("entity_name"),
            "financial_year_end": doc.get("financial_year_end"),
        }
        if missing_sections:
            incomplete_documents.append({**entry, "missing_sections": missing_sections})
        elif unconfirmed_sections:
            needs_review_documents.append({**entry, "unconfirmed_sections": unconfirmed_sections})

    passed = False if incomplete_documents else (None if needs_review_documents else True)

    if incomplete_documents:
        message = f"{len(incomplete_documents)} financial statement(s) are missing required sections."
    elif needs_review_documents:
        message = f"{len(needs_review_documents)} financial statement(s) have unconfirmed sections -- needs review."
    else:
        message = "All financial statements include the required sections."

    return {
        "passed": passed,
        "message": message,
        "details": {
            "documents_checked": len(financial_statement_data),
            "incomplete_documents": incomplete_documents,
            "needs_review_documents": needs_review_documents,
        },
    }


def find_missing_ic_documents(ssm_people: List[Dict[str, object]], ic_documents: List[Dict[str, object]]) -> Dict:
    """Compare SSM directors/shareholders against uploaded IC documents and return anyone missing.

    Use this to confirm every director/shareholder listed on the SSM forms
    has a corresponding identity_document uploaded, matched by NRIC/passport
    number.

    Args:
        ssm_people: Directors/shareholders from the SSM corporate form(s),
            each with name and nric_passport.
        ic_documents: The identity_document documents in the bundle, each
            with individual_name, nric_passport, front_image_present, and
            back_image_present.
    """
    ic_ids = {normalize_id(doc["nric_passport"]) for doc in ic_documents}

    missing_people = [
        {"name": person["name"], "nric_passport": person["nric_passport"]}
        for person in ssm_people
        if normalize_id(person["nric_passport"]) not in ic_ids
    ]
    passed = len(missing_people) == 0

    return {
        "passed": passed,
        "message": (
            "IC documents present for all SSM directors/shareholders."
            if passed
            else f"Missing IC document(s) for {len(missing_people)} person(s)."
        ),
        "details": {
            "ssm_people_count": len(ssm_people),
            "ic_documents_count": len(ic_documents),
            "missing_people": missing_people,
        },
    }


def check_ic_front_and_back(ic_documents: List[Dict[str, object]]) -> Dict:
    """Verify that front_image_present and back_image_present are both true for every IC.

    Use this for every identity_document in the bundle to catch partial
    uploads (e.g. front of NRIC submitted but not the back). Each side is a
    tri-state: True (confirmed present), False (confirmed missing -- a real
    gap), or null (extraction couldn't tell -- "needs review", not the same
    as a confirmed miss).

    Args:
        ic_documents: The identity_document documents in the bundle, each
            with individual_name, nric_passport, front_image_present, and
            back_image_present.
    """
    incomplete = []  # at least one side confirmed False
    needs_review = []  # no confirmed-False side, but at least one null side
    for doc in ic_documents:
        front = doc.get("front_image_present")
        back = doc.get("back_image_present")
        missing_sides = [side for side, val in (("front", front), ("back", back)) if val is False]
        unconfirmed_sides = [side for side, val in (("front", front), ("back", back)) if val is None]
        entry = {"individual_name": doc.get("individual_name"), "nric_passport": doc.get("nric_passport")}
        if missing_sides:
            incomplete.append({**entry, "missing_sides": missing_sides})
        elif unconfirmed_sides:
            needs_review.append({**entry, "unconfirmed_sides": unconfirmed_sides})

    passed = False if incomplete else (None if needs_review else True)

    if incomplete:
        message = f"{len(incomplete)} IC document(s) are missing front and/or back images."
    elif needs_review:
        message = f"{len(needs_review)} IC document(s) have unconfirmed front/back images -- needs review."
    else:
        message = "All IC documents have both front and back images present."

    return {
        "passed": passed,
        "message": message,
        "details": {
            "ic_documents_count": len(ic_documents),
            "incomplete_documents": incomplete,
            "needs_review_documents": needs_review,
        },
    }


_CUSTOMER_INFO_DIRECTOR_LABELS = {
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
_CUSTOMER_INFO_COMPANY_LABELS = {
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


def verify_customer_information_completeness(customer_information: Dict[str, object]) -> Dict:
    """Check that EVERY field on the Customer Information Form is filled in.

    Use this once the customer_information document has been extracted, to
    confirm no company field or director-particulars field was left blank.

    Args:
        customer_information: The customer_information document's data --
            company_* fields plus a `directors` list, each director carrying
            the personal-particulars fields (name, address, email, ...).
    """
    missing_fields = [
        label
        for field, label in _CUSTOMER_INFO_COMPANY_LABELS.items()
        if not customer_information.get(field)
    ]

    directors = customer_information.get("directors") or []
    if not directors:
        missing_fields.append("Directors (no director particulars provided)")
    for i, director in enumerate(directors):
        for field, label in _CUSTOMER_INFO_DIRECTOR_LABELS.items():
            if not director.get(field):
                missing_fields.append(f"Director[{i}] {label}")

    passed = len(missing_fields) == 0
    return {
        "passed": passed,
        "message": (
            "All Customer Information Form fields are completed."
            if passed
            else f"Missing {len(missing_fields)} Customer Information Form field(s): "
                 f"{', '.join(missing_fields)}."
        ),
        "details": {
            "missing_fields": missing_fields,
        },
    }


def verify_consent_signatures(ssm_people: List[Dict[str, object]], consent_forms: List[Dict[str, object]]) -> Dict:
    """Check that a signed Consent Form exists for every required SSM director/shareholder.

    Use this to confirm every director/shareholder listed on the SSM forms
    has a matching consent_form document, and that its signature_present
    flag is true, matched by NRIC/passport number. signature_present is a
    tri-state: True (confirmed signed), False (confirmed unsigned -- a real
    gap), or null (not confirmed either way -- "needs review", not the same
    as a confirmed-unsigned form).

    Args:
        ssm_people: Directors/shareholders from the SSM corporate form(s),
            each with name and nric_passport.
        consent_forms: The consent_form documents in the bundle, each with
            individual_name, nric_passport, and signature_present.
    """
    consent_by_id = {normalize_id(form["nric_passport"]): form for form in consent_forms}

    missing_consent = []  # no consent_form found at all -- a real gap
    unsigned_consent = []  # consent_form found, signature_present is confirmed False -- a real gap
    unconfirmed_consent = []  # consent_form found, signature_present is null -- needs review
    for person in ssm_people:
        person_id = normalize_id(person["nric_passport"])
        form = consent_by_id.get(person_id)
        entry = {"name": person["name"], "nric_passport": person["nric_passport"]}
        if form is None:
            missing_consent.append(entry)
        elif form.get("signature_present") is False:
            unsigned_consent.append(entry)
        elif form.get("signature_present") is None:
            unconfirmed_consent.append(entry)

    passed = (
        False if (missing_consent or unsigned_consent)
        else (None if unconfirmed_consent else True)
    )

    if missing_consent or unsigned_consent:
        message = (
            f"{len(missing_consent)} missing Consent Form(s), "
            f"{len(unsigned_consent)} confirmed-unsigned Consent Form(s)."
        )
    elif unconfirmed_consent:
        message = f"{len(unconfirmed_consent)} Consent Form(s) have an unconfirmed signature -- needs review."
    else:
        message = "All required parties have a signed Consent Form."

    return {
        "passed": passed,
        "message": message,
        "details": {
            "ssm_people_count": len(ssm_people),
            "consent_forms_count": len(consent_forms),
            "missing_consent": missing_consent,
            "unsigned_consent": unsigned_consent,
            "unconfirmed_consent": unconfirmed_consent,
        },
    }
