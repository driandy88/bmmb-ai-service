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

# Required SSM form subtypes by entity type.
_REQUIRED_SSM_FORMS_BY_ENTITY = {
    "sole prop": {"form_b", "form_d"},
    "sole proprietor": {"form_b", "form_d"},
    "sole proprietorship": {"form_b", "form_d"},
    "partnership": {"form_b", "form_d"},
}
_DEFAULT_REQUIRED_SSM_FORMS = {"form_24", "form_44", "form_49"}  # Sdn Bhd


def verify_ssm_completeness(entity_type: str, ssm_document_subtypes: List[str]) -> Dict:
    """Check that the correct combination of SSM forms is present for the entity type.

    BMMB requires Form 24 + Form 44 + Form 49 for a Sdn Bhd, and Form B +
    Form D for a Sole Proprietor/Partnership. Use this once you know the
    entity_type and have collected every ssm_corporate_form document's
    document_subtype from the bundle.

    Args:
        entity_type: The entity type from the SSM corporate form, e.g.
            "Sdn Bhd" or "Sole Proprietor".
        ssm_document_subtypes: The document_subtype of every
            ssm_corporate_form document present in the bundle, e.g.
            ["form_24", "form_49"].
    """
    required = _REQUIRED_SSM_FORMS_BY_ENTITY.get(
        entity_type.strip().lower(), _DEFAULT_REQUIRED_SSM_FORMS
    )
    provided = {s.strip().lower() for s in ssm_document_subtypes}
    missing = sorted(required - provided)
    passed = len(missing) == 0

    return {
        "passed": passed,
        "message": (
            f"All required SSM forms present for '{entity_type}'."
            if passed
            else f"Missing SSM form(s) for '{entity_type}': {', '.join(missing)}."
        ),
        "details": {
            "entity_type": entity_type,
            "required_forms": sorted(required),
            "provided_forms": sorted(provided),
            "missing_forms": missing,
        },
    }


def verify_financial_sections_present(financial_statement_data: List[Dict[str, object]]) -> Dict:
    """Check the Balance Sheet / P&L / Cash Flow / Auditor's Report flags on financial statements.

    Use this for every financial_statement document in the bundle to confirm
    the extraction agent found all 4 required sections in each one.

    Args:
        financial_statement_data: One entry per financial_statement
            document, with its entity_name, financial_year_end, and the 4
            boolean section-present flags.
    """
    section_flags = {
        "balance_sheet_present": "Balance Sheet",
        "profit_and_loss_present": "Profit & Loss",
        "cash_flow_present": "Cash Flow",
        "auditors_report_present": "Auditor's Report",
    }

    incomplete_documents = []
    for doc in financial_statement_data:
        missing_sections = [
            label for flag, label in section_flags.items() if not doc.get(flag, False)
        ]
        if missing_sections:
            incomplete_documents.append(
                {
                    "entity_name": doc.get("entity_name"),
                    "financial_year_end": doc.get("financial_year_end"),
                    "missing_sections": missing_sections,
                }
            )

    passed = len(incomplete_documents) == 0

    return {
        "passed": passed,
        "message": (
            "All financial statements include the required sections."
            if passed
            else f"{len(incomplete_documents)} financial statement(s) are missing required sections."
        ),
        "details": {
            "documents_checked": len(financial_statement_data),
            "incomplete_documents": incomplete_documents,
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
    uploads (e.g. front of NRIC submitted but not the back).

    Args:
        ic_documents: The identity_document documents in the bundle, each
            with individual_name, nric_passport, front_image_present, and
            back_image_present.
    """
    incomplete = []
    for doc in ic_documents:
        front = doc.get("front_image_present", False)
        back = doc.get("back_image_present", False)
        if not (front and back):
            missing_sides = []
            if not front:
                missing_sides.append("front")
            if not back:
                missing_sides.append("back")
            incomplete.append(
                {
                    "individual_name": doc.get("individual_name"),
                    "nric_passport": doc.get("nric_passport"),
                    "missing_sides": missing_sides,
                }
            )

    passed = len(incomplete) == 0

    return {
        "passed": passed,
        "message": (
            "All IC documents have both front and back images present."
            if passed
            else f"{len(incomplete)} IC document(s) are missing front and/or back images."
        ),
        "details": {
            "ic_documents_count": len(ic_documents),
            "incomplete_documents": incomplete,
        },
    }


def verify_consent_signatures(ssm_people: List[Dict[str, object]], consent_forms: List[Dict[str, object]]) -> Dict:
    """Check that a signed Consent Form exists for every required SSM director/shareholder.

    Use this to confirm every director/shareholder listed on the SSM forms
    has a matching consent_form document, and that its signature_present
    flag is true, matched by NRIC/passport number.

    Args:
        ssm_people: Directors/shareholders from the SSM corporate form(s),
            each with name and nric_passport.
        consent_forms: The consent_form documents in the bundle, each with
            individual_name, nric_passport, and signature_present.
    """
    consent_by_id = {normalize_id(form["nric_passport"]): form for form in consent_forms}

    missing_consent = []
    unsigned_consent = []
    for person in ssm_people:
        person_id = normalize_id(person["nric_passport"])
        form = consent_by_id.get(person_id)
        if form is None:
            missing_consent.append({"name": person["name"], "nric_passport": person["nric_passport"]})
        elif not form.get("signature_present", False):
            unsigned_consent.append({"name": person["name"], "nric_passport": person["nric_passport"]})

    passed = len(missing_consent) == 0 and len(unsigned_consent) == 0

    return {
        "passed": passed,
        "message": (
            "All required parties have a signed Consent Form."
            if passed
            else (
                f"{len(missing_consent)} missing Consent Form(s), "
                f"{len(unsigned_consent)} unsigned Consent Form(s)."
            )
        ),
        "details": {
            "ssm_people_count": len(ssm_people),
            "consent_forms_count": len(consent_forms),
            "missing_consent": missing_consent,
            "unsigned_consent": unsigned_consent,
        },
    }
