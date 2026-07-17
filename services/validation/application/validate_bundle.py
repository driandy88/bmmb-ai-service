"""Application service for deterministic bundle validation."""

import logging
from time import perf_counter

from ..bundle import ValidationBundle
from ..domain.policies import BMMB_SME_POLICY_V1, ValidationPolicy
from ..engine import ValidationEngine, ValidationReport

logger = logging.getLogger(__name__)


class ValidationApplicationService:
    """Use-case boundary around deterministic validation.

    Keeping this boundary separate from the legacy engine lets API and AI
    orchestration depend on an application use case instead of constructing
    the engine directly. The engine remains injectable for testing.
    """

    def __init__(
        self,
        engine: ValidationEngine | None = None,
        policy: ValidationPolicy = BMMB_SME_POLICY_V1,
    ):
        self.engine = engine or ValidationEngine(policy=policy)

    def validate(self, bundle: ValidationBundle) -> ValidationReport:
        started = perf_counter()
        report = self.engine.run(bundle)
        logger.info(
            "validation.completed bundle_id=%s policy_id=%s status=%s duration_ms=%.2f",
            bundle.bundle_id,
            report.policy_id,
            report.overall_status.value,
            (perf_counter() - started) * 1000,
        )
        return report
