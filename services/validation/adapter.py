"""
Adapter from raw (pre-canonical) extraction fields to the ValidationBundle schema.

This models the real-world step between an extraction agent's raw output
(whatever field names/shapes it happens to produce) and the canonical
bundle our rules/engine operate on. It contains ONE deliberate bug in
_adapt_consent_form, used by examples/test_conflict_example.py to
demonstrate a blind spot in the deterministic engine: it can only see the
canonical bundle, so an adapter mapping bug looks identical to a real
compliance gap.

The bug: raw consent-form extraction has two similarly-named fields,
"Entity Name" (the actual entity) and "Authorized Names" (the signatories'
names). _adapt_consent_form's `.get("Authorized Names", ...)` fallback
order means it prefers "Authorized Names" whenever present, silently
overwriting entity_name with the signatory names instead of the entity —
and never maps "Authorized Names"/"Authorized NRICs" into
individual_name/nric_passport at all, leaving those blank.
"""

from datetime import date
from typing import Any, Dict, List

from .bundle import (
    BundleMetadata,
    ConsentFormData,
    ConsentFormDoc,
    PersonInfo,
    SsmCorporateFormData,
    SsmCorporateDoc,
    ValidationBundle,
)


def _adapt_ssm_corporate_form(item: Dict[str, Any]) -> SsmCorporateDoc:
    fields = item["raw_fields"]
    shareholders = [
        PersonInfo(name=s["Name"], nric_passport=s["NRIC/Passport"])
        for s in fields.get("Shareholders", [])
    ]
    directors = [
        PersonInfo(name=d["Name"], nric_passport=d["NRIC/Passport"], position=d.get("Position"))
        for d in fields.get("Directors", [])
    ]
    return SsmCorporateDoc(
        document_id=item["document_id"],
        document_type="ssm_corporate_form",
        document_subtype=item.get("document_subtype"),
        data=SsmCorporateFormData(
            entity_name=fields["Entity Name"],
            business_registration_number=fields["Business Registration Number"],
            entity_type=fields["Entity Type"],
            directors=directors or None,
            shareholders=shareholders or None,
        ),
    )


def _adapt_consent_form(item: Dict[str, Any]) -> ConsentFormDoc:
    fields = item["raw_fields"]

    # BUG: intended as "fall back to Authorized Names if Entity Name is
    # missing", but Entity Name is always present too, so this always
    # picks Authorized Names when it exists — clobbering the real entity
    # name with the signatory names.
    entity_name = fields.get("Authorized Names", fields.get("Entity Name", ""))

    # BUG: individual_name/nric_passport are never populated from
    # "Authorized Names"/"Authorized NRICs" — whoever wrote this adapter
    # branch assumed the line above already handled it.
    individual_name = ""
    nric_passport = ""

    return ConsentFormDoc(
        document_id=item["document_id"],
        document_type="consent_form",
        data=ConsentFormData(
            entity_name=entity_name,
            individual_name=individual_name,
            nric_passport=nric_passport,
            signature_present=bool(fields.get("Signature Captured", False)),
        ),
    )


_ADAPTERS = {
    "ssm_corporate_form": _adapt_ssm_corporate_form,
    "consent_form": _adapt_consent_form,
}


def adapt_raw_extraction(raw: Dict[str, Any]) -> ValidationBundle:
    """Convert a raw-extraction-shaped dict (see examples/raw_extraction_example.json) into a ValidationBundle."""
    documents = []
    document_types_present: List[str] = []
    for item in raw["raw_documents"]:
        adapter = _ADAPTERS.get(item["document_type"])
        if adapter is None:
            raise ValueError(f"No adapter registered for document_type={item['document_type']!r}")
        documents.append(adapter(item))
        document_types_present.append(item["document_type"])

    return ValidationBundle(
        bundle_id=raw["bundle_id"],
        metadata=BundleMetadata(
            total_documents_received=len(documents),
            system_date=date.fromisoformat(raw["system_date"]),
            document_types_present=document_types_present,
        ),
        extracted_documents=documents,
    )
