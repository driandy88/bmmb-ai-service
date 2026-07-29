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
    # The document types an application package must contain before it can be
    # assessed at all -- one entry per mandatory slot, each listing the types
    # that satisfy it (any one is enough). Consumed by the
    # package.completeness gate; without it a near-empty package produces
    # nothing but "not applicable" results and reads as a clean pass.
    # An empty list means nothing is mandatory, which switches that gate off
    # entirely -- set it deliberately on any policy meant for real traffic.
    required_document_slots: list[list[str]] = Field(default_factory=list)
    # Per-entity-type overrides, keyed by the same canonical keys as
    # minimum_bank_statement_months_by_entity (resolved through
    # entity_type_aliases/fuzzy matching). An entity type with no override
    # uses required_document_slots.
    required_document_slots_by_entity: dict[str, list[list[str]]] = Field(default_factory=dict)

    def required_document_slots_for(self, resolved_entity_type_key: str | None) -> list[list[str]]:
        """Mandatory document slots for a resolved entity-type key.

        `resolved_entity_type_key` comes from
        rules._utils.resolve_entity_type_key -- None when the raw entity_type
        resolved to nothing, in which case the default slots apply (same
        fallback shape as the bank-statement minimum).
        """
        if resolved_entity_type_key is None:
            return self.required_document_slots
        return self.required_document_slots_by_entity.get(
            resolved_entity_type_key, self.required_document_slots,
        )


# Every mandatory document slot, in the strictest form: audited financial
# statements, no substitute. Rule 2's alternate path -- 2 years of LHDN tax
# declarations (Borang B) instead -- is open only to a Sole Prop/Partnership,
# so their slot list is derived from this one by widening that single slot.
# Derived, not restated, so a new mandatory slot added here can't be silently
# left out of the sole-prop list.
_REQUIRED_DOCUMENT_SLOTS = [
    ["ssm_corporate_form"],
    ["financial_statement"],
    ["bank_statement"],
    ["identity_document"],
    ["consent_form"],
    ["customer_information"],
]
_BORANG_B_ELIGIBLE_SLOTS = [
    ["financial_statement", "tax_declaration"] if slot == ["financial_statement"] else slot
    for slot in _REQUIRED_DOCUMENT_SLOTS
]


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
    required_document_slots=_REQUIRED_DOCUMENT_SLOTS,
    required_document_slots_by_entity={
        entity_key: _BORANG_B_ELIGIBLE_SLOTS
        for entity_key in ("sole prop", "sole proprietor", "sole proprietorship", "partnership")
    },
)
