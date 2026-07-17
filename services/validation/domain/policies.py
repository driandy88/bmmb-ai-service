"""Versioned business policies for deterministic validation."""

from pydantic import BaseModel, Field


class ValidationPolicy(BaseModel):
    """Business requirements used by validation rules.

    Rules own the calculation logic; policies own the configurable
    requirements. This makes a policy change auditable and avoids scattering
    entity-specific constants across rule modules.
    """

    policy_id: str
    required_ssm_forms_by_entity: dict[str, set[str]]
    default_required_ssm_forms: set[str]
    minimum_bank_statement_months_by_entity: dict[str, int]
    default_minimum_bank_statement_months: int
    financial_statement_max_age_months: int = 18
    bank_statement_max_age_months: int = 2
    required_application_fields: set[str] = Field(default_factory=set)
    accepted_bank_currency: str = "MYR"


BMMB_SME_POLICY_V1 = ValidationPolicy(
    policy_id="bmmb-sme-2026-01",
    required_ssm_forms_by_entity={
        "sole prop": {"form_b", "form_d"},
        "sole proprietor": {"form_b", "form_d"},
        "sole proprietorship": {"form_b", "form_d"},
        "partnership": {"form_b", "form_d"},
    },
    default_required_ssm_forms={"form_24", "form_44", "form_49"},
    minimum_bank_statement_months_by_entity={
        "sole prop": 12,
        "sole proprietor": 12,
        "sole proprietorship": 12,
        "partnership": 12,
    },
    default_minimum_bank_statement_months=6,
    required_application_fields={
        "main_contact_names",
        "main_contact_emails",
        "main_contact_phone_numbers",
        "financing_amount",
        "product_type",
        "tenure_months",
        "repayment_frequency",
    },
    accepted_bank_currency="MYR",
)
