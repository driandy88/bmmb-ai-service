"""Validation domain layer.

This package is the future home for canonical models and compliance rules.
Compatibility exports are kept while the service is migrated incrementally.
"""

from .policies import BMMB_SME_POLICY_V1, ValidationPolicy

__all__ = ["BMMB_SME_POLICY_V1", "ValidationPolicy"]
