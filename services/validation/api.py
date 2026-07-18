"""
FastAPI router for the validation service.

To mount into another service's own FastAPI app (so it shares that app's
prefix/middleware/auth instead of running as a separate process):

    from services.validation.api import router
    app.include_router(router)

To run this service standalone, straight from this module:

    uvicorn services.validation.api:app --reload
"""

from datetime import date
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Body, FastAPI, HTTPException, Query
from pydantic import BaseModel

from .agent import run_agentic_validation, run_agentic_validation_from_extraction
from .bundle import ValidationBundle
from .domain.policies import BMMB_SME_POLICY_V1
from .rules import RULE_CATALOG
from .schemas import AgenticValidationReport

router = APIRouter(tags=["validation"])


class ValidateRequest(BaseModel):
    bundle: ValidationBundle
    raw_extraction: Optional[dict] = None
    enable_ai_review: bool = True


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/rules")
def rules_catalog():
    """Return the active policy and deterministic rule catalog."""
    return {
        "policy_id": BMMB_SME_POLICY_V1.policy_id,
        "rules": [asdict(rule) for rule in RULE_CATALOG],
    }


# The flat per-rule `deterministic.results` list is redundant with the grouped
# `deterministic.results_by_document` view (the one the frontend maps from), so
# it's dropped from the HTTP response. It stays on the model for internal use
# (the AI reviewer's prompt, overall_passed/overall_status) -- see schemas.py.
_RESPONSE_EXCLUDE = {"deterministic": {"results"}}


@router.post("/validate", response_model=AgenticValidationReport,
             response_model_exclude=_RESPONSE_EXCLUDE)
def validate(request: ValidateRequest):
    try:
        return run_agentic_validation(
            request.bundle,
            request.raw_extraction,
            enable_ai_review=request.enable_ai_review,
        )
    except SystemExit as e:
        # run_agentic_validation raises SystemExit when AI review is requested
        # but GCP_PROJECT_ID isn't configured server-side.
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/validate/from-extraction", response_model=AgenticValidationReport,
             response_model_exclude=_RESPONSE_EXCLUDE)
def validate_from_extraction(
    extracted_by_template: dict = Body(
        ...,
        description="One entry per POST /extract call, keyed by template name -- "
                    "the request body is exactly this dict, e.g. the unmodified "
                    "contents of examples/extraction_results_example.json.",
    ),
    bundle_id: Optional[str] = Query(None),
    system_date: Optional[date] = Query(None, description="Defaults to today if omitted."),
    entity_type: Optional[str] = Query(
        None, description="Defaults to extracted_by_template['Application Details']"
                           "['Business Entity Type'] if omitted."),
    tenure_months: Optional[int] = Query(None),
    repayment_frequency: Optional[str] = Query(None),
    signature_present: Optional[bool] = Query(None),
    enable_ai_review: bool = Query(True),
):
    """No field here can cause a 400 -- anything not supplied and not
    derivable from `extracted_by_template` is defaulted and recorded in the
    response's `adapter_warnings` instead (see build_validation_bundle()'s
    docstring). This endpoint's whole point is that a raw extraction dump
    is always a valid request on its own."""
    try:
        return run_agentic_validation_from_extraction(
            extracted_by_template,
            bundle_id=bundle_id,
            system_date=system_date,
            entity_type=entity_type,
            tenure_months=tenure_months,
            repayment_frequency=repayment_frequency,
            signature_present=signature_present,
            enable_ai_review=enable_ai_review,
        )
    except SystemExit as e:
        raise HTTPException(status_code=503, detail=str(e))


# Standalone app, for `uvicorn services.validation.api:app`. Host services
# embedding this elsewhere should use `router` above instead.
app = FastAPI(title="Validation Agent API")
app.include_router(router)
