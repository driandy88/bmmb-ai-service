"""Versioned business policies for deterministic validation."""

from pydantic import BaseModel, Field


class ValidationPolicy(BaseModel):
    """Business requirements used by validation rules.

    Rules own the calculation logic; policies own the configurable
    requirements. This makes a policy change auditable and avoids scattering
    entity-specific constants across rule modules.
    """

    policy_id: str
    minimum_bank_statement_months_by_entity: dict[str, int]
    default_minimum_bank_statement_months: int
    # Spelling variants/typos/Malay terms that resolve to one of the exact
    # keys above -- extended first when a new unrecognized variant shows up
    # (predictable, auditable); verify_bank_statement_duration's fuzzy-match
    # fallback only catches variants this table hasn't seen yet.
    entity_type_aliases: dict[str, str] = Field(default_factory=dict)
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
    entity_type_aliases={
        "perniagaan tunggal": "sole prop",
        "enterprise": "sole prop",
        "single proprietor": "sole prop",
        "perkongsian": "partnership",
        "llp": "partnership",
        "limited liability partnership": "partnership",
        "perkongsian liabiliti terhad": "partnership",
    },
    accepted_bank_currency="MYR",
)
