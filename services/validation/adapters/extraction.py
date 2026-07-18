"""Compatibility import surface for the extraction adapter."""

from ..extraction_adapter import (
    AdapterDataGapError,
    AdapterResult,
    AdapterWarning,
    build_bank_statement_doc,
    build_consent_form_docs,
    build_customer_information_doc,
    build_financial_statement_docs,
    build_identity_documents,
    build_ssm_corporate_docs,
    build_validation_bundle,
)

__all__ = [
    "AdapterDataGapError", "AdapterResult", "AdapterWarning",
    "build_bank_statement_doc", "build_consent_form_docs",
    "build_customer_information_doc", "build_financial_statement_docs",
    "build_identity_documents", "build_ssm_corporate_docs",
    "build_validation_bundle",
]
