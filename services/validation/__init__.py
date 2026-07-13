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

- Starting from raw extraction results instead of an already-built bundle
  (calls extraction_adapter.py internally, then the same
  pipeline as above). Only the extraction results are required -- everything
  else is auto-derived or defaulted, with a warning recorded for anything
  that had to be guessed (see build_validation_bundle()'s docstring):
    from services.validation import run_agentic_validation_from_extraction
    report = run_agentic_validation_from_extraction(
        {"SSM Form 24": {...}, ...},
    )

- As a FastAPI router, to mount into another service's app (see api.py):
    from services.validation.api import router
    app.include_router(router)
  (Not imported into this package's top-level namespace, so importing
  services.validation doesn't require fastapi to be installed unless you
  actually use the router.)

Submodules:
    bundle              — The canonical ValidationBundle schema (pydantic).
    rules               — Individual BMMB rule functions (date logic, completeness, name/ID matching).
    engine              — ValidationEngine: runs every applicable rule against a bundle.
    extraction_adapter  — Maps services.extraction's raw POST /extract output into a ValidationBundle.
    schemas             — AgenticValidationReport / AIFinding / AIReview output models.
    agent               — run_agentic_validation() / run_agentic_validation_from_extraction():
                           engine runs in Python, one Gemini call reviews the result.
    api                 — FastAPI APIRouter exposing /health, /validate, /validate/from-extraction.
"""

from .agent import run_agentic_validation, run_agentic_validation_from_extraction
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
    "run_agentic_validation_from_extraction",
]
