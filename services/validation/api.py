"""
FastAPI router for the validation service.

To mount into another service's own FastAPI app (so it shares that app's
prefix/middleware/auth instead of running as a separate process):

    from services.validation.api import router
    app.include_router(router)

To run this service standalone, straight from this module:

    uvicorn services.validation.api:app --reload
"""

from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from .agent import run_agentic_validation
from .bundle import ValidationBundle
from .schemas import AgenticValidationReport

router = APIRouter(tags=["validation"])


class ValidateRequest(BaseModel):
    bundle: ValidationBundle
    raw_extraction: Optional[dict] = None
    enable_ai_review: bool = True


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/validate", response_model=AgenticValidationReport)
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


# Standalone app, for `uvicorn services.validation.api:app`. Host services
# embedding this elsewhere should use `router` above instead.
app = FastAPI(title="Validation Agent API")
app.include_router(router)
