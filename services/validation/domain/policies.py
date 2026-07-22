"""Versioned business policies for deterministic validation."""

from pydantic import BaseModel


class ValidationPolicy(BaseModel):
    """Business requirements used by validation rules.

    Rules own the calculation logic; policies own the configurable
    requirements. This makes a policy change auditable and avoids scattering
    entity-specific constants across rule modules.
    """

    policy_id: str
    minimum_bank_statement_months_by_entity: dict[str, int]
    default_minimum_bank_statement_months: int
    financial_statement_max_age_months: int = 18
    bank_statement_max_age_months: int = 2
    accepted_bank_currency: str = "MYR"


BMMB_SME_POLICY_V1 = ValidationPolicy(
    policy_id="bmmb-sme-2026-01",
    minimum_bank_statement_months_by_entity={
        "sole prop": 12,
        "sole proprietor": 12,
        "sole proprietorship": 12,
        "partnership": 12,
    },
    default_minimum_bank_statement_months=6,
    accepted_bank_currency="MYR",
)
