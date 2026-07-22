"""Application service for validation starting from raw extraction."""

from datetime import date
from typing import Optional

from ..bundle import ValidationBundle
from ..extraction_adapter import AdapterResult, build_validation_bundle


class ExtractionValidationApplicationService:
    """Own the raw-extraction-to-bundle use case and its adapter boundary."""

    def build_bundle(
        self,
        extracted_by_template: dict,
        *,
        bundle_id: Optional[str] = None,
        system_date: Optional[date] = None,
        entity_type: Optional[str] = None,
        tax_declaration_entity_name: Optional[str] = None,
        tax_declaration_fye_dates: Optional[list[str]] = None,
    ) -> AdapterResult:
        return build_validation_bundle(
            extracted_by_template,
            bundle_id=bundle_id,
            system_date=system_date,
            entity_type=entity_type,
            tax_declaration_entity_name=tax_declaration_entity_name,
            tax_declaration_fye_dates=tax_declaration_fye_dates,
        )
