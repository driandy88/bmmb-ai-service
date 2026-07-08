"""
BMMB document bundle validation service.

Two ways to validate a bundle:

- Deterministic only (fast, free, no LLM):
    from services.validation import ValidationBundle, ValidationEngine
    report = ValidationEngine().run(bundle)  # -> ValidationReport

- Agentic (runs the deterministic engine first, then one Gemini call reviews
  that report against the raw pre-adapter extraction, if available, to
  catch adapter mapping bugs the deterministic engine can't see on its own
  — see engine.py's and agent.py's module docstrings for why):
    from services.validation import run_agentic_validation
    report = run_agentic_validation(bundle, raw_extraction)  # -> AgenticValidationReport

  Pass enable_ai_review=False to skip the Gemini call and get deterministic-
  only results back (no GCP project/credentials required in that case):
    report = run_agentic_validation(bundle, raw_extraction, enable_ai_review=False)

- As a FastAPI router, to mount into another service's app (see api.py):
    from services.validation.api import router
    app.include_router(router)
  (Not imported into this package's top-level namespace, so importing
  services.validation doesn't require fastapi to be installed unless you
  actually use the router.)

Submodules:
    bundle    — ValidationBundle schema (pydantic) for the canonical, already-extracted document bundle.
    rules     — Individual BMMB rule functions (date logic, completeness, name/ID matching).
    engine    — ValidationEngine: runs every applicable rule against a bundle.
    adapter   — Example raw-extraction -> ValidationBundle mapping (see examples/ for the bug it demonstrates).
    schemas   — AgenticValidationReport / AIFinding / AIReview output models.
    agent     — run_agentic_validation(): engine runs in Python, one Gemini call reviews the result.
    api       — FastAPI APIRouter exposing /health and /validate for host services to mount.
"""

from .agent import run_agentic_validation
from .bundle import ValidationBundle
from .engine import CheckResult, ValidationEngine, ValidationReport
from .schemas import AgenticValidationReport, AIFinding

__all__ = [
    "ValidationBundle",
    "ValidationEngine",
    "ValidationReport",
    "CheckResult",
    "AgenticValidationReport",
    "AIFinding",
    "run_agentic_validation",
]
