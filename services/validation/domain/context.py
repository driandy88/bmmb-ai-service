"""Normalized document context used by the validation application layer."""

from dataclasses import dataclass

from ..bundle import (
    BankStatementDoc,
    ConsentFormDoc,
    CustomerInfoDoc,
    FinancialStatementDoc,
    IdentityDoc,
    SsmCorporateDoc,
    TaxDeclarationDoc,
    ValidationBundle,
)


@dataclass(frozen=True)
class BundleContext:
    """Document groups and shared parties derived once from a bundle."""

    financial_statement_docs: list[FinancialStatementDoc]
    tax_declaration_docs: list[TaxDeclarationDoc]
    bank_statement_docs: list[BankStatementDoc]
    identity_docs: list[IdentityDoc]
    consent_form_docs: list[ConsentFormDoc]
    ssm_form_docs: list[SsmCorporateDoc]
    customer_info_doc: CustomerInfoDoc | None
    entity_name: str
    entity_type: str
    ssm_people_by_nric: dict[str, object]

    @classmethod
    def from_bundle(cls, bundle: ValidationBundle) -> "BundleContext":
        docs = bundle.extracted_documents
        financial_statement_docs = [d for d in docs if d.document_type == "financial_statement"]
        tax_declaration_docs = [d for d in docs if d.document_type == "tax_declaration"]
        bank_statement_docs = [d for d in docs if d.document_type == "bank_statement"]
        identity_docs = [d for d in docs if d.document_type == "identity_document"]
        consent_form_docs = [d for d in docs if d.document_type == "consent_form"]
        ssm_form_docs = [d for d in docs if d.document_type == "ssm_corporate_form"]
        customer_info_doc = next((d for d in docs if d.document_type == "customer_information"), None)

        entity_name = ssm_form_docs[0].data.entity_name if ssm_form_docs else ""
        entity_type = ssm_form_docs[0].data.entity_type if ssm_form_docs else ""

        ssm_people_by_nric = {}
        for doc in ssm_form_docs:
            for group in (doc.data.directors, doc.data.shareholders):
                for person in group or []:
                    ssm_people_by_nric[person.nric_passport] = person

        return cls(
            financial_statement_docs=financial_statement_docs,
            tax_declaration_docs=tax_declaration_docs,
            bank_statement_docs=bank_statement_docs,
            identity_docs=identity_docs,
            consent_form_docs=consent_form_docs,
            ssm_form_docs=ssm_form_docs,
            customer_info_doc=customer_info_doc,
            entity_name=entity_name,
            entity_type=entity_type,
            ssm_people_by_nric=ssm_people_by_nric,
        )

    @property
    def ssm_people(self) -> list[dict]:
        return [person.model_dump(mode="json") for person in self.ssm_people_by_nric.values()]
