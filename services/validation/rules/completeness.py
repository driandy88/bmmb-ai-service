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
from ..domain.policies import BMMB_SME_POLICY_V1, ValidationPolicy

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

def verify_ssm_completeness(
    entity_type: str,
    ssm_document_subtypes: List[str],
    policy: ValidationPolicy = BMMB_SME_POLICY_V1,
) -> Dict:
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
    required = policy.required_ssm_forms_by_entity.get(
        entity_type.strip().lower(), policy.default_required_ssm_forms
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


