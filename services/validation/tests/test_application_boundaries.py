"""Tests for the Phase 4 application/domain boundary modules."""

from services.validation.application.validate_bundle import ValidationApplicationService
from services.validation.application.validate_extraction import ExtractionValidationApplicationService
from services.validation.domain.context import BundleContext
from services.validation.engine import ValidationEngine
from services.validation.bundle import ValidationBundle


def test_bundle_context_centralizes_document_grouping(passing_bundle_raw):
    bundle = ValidationBundle(**passing_bundle_raw)
    context = BundleContext.from_bundle(bundle)

    assert context.entity_name == "ALPHA TECH SOLUTIONS SDN BHD"
    assert context.entity_type == "Sdn Bhd"
    assert len(context.ssm_form_docs) == 3
    assert len(context.financial_statement_docs) == 2
    assert len(context.bank_statement_docs) == 2
    assert len(context.ssm_people) == 2


def test_application_service_preserves_engine_results(passing_bundle_raw):
    bundle = ValidationBundle(**passing_bundle_raw)
    direct = ValidationEngine().run(bundle)
    through_service = ValidationApplicationService().validate(bundle)

    assert through_service.model_dump() == direct.model_dump()


def test_extraction_application_service_returns_adapter_result():
    result = ExtractionValidationApplicationService().build_bundle(
        {
            "SSM Form 24": {
                "Entity Name": "SOLO SDN BHD",
                "Business Registration Number": "202301000001",
            },
            "Application Details": {
                "Business Entity Type": "Sdn Bhd",
                # Contacts come from the "Main Contacts" row_group -- one
                # correlated object per contact (name/email/phone stay aligned).
                "Main Contacts": [
                    {
                        "Main Contact Name": "A",
                        "Main Contact Email": "a@example.com",
                        "Main Contact Phone Number": "0123456789",
                    },
                ],
                "Proposed Financing Amount": 1000,
                "Proposed Program": "SME Term Financing",
            },
        },
        entity_type="Sdn Bhd",
        tenure_months=60,
        repayment_frequency="Monthly",
        signature_present=True,
    )

    assert result.bundle.extracted_documents
    assert result.warnings == []
