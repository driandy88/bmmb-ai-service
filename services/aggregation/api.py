"""
FastAPI app for the aggregation / normalization service.

Deterministic post-extraction transforms — no LLM, no GCP, no database.

To run standalone (build context is the repo root, package is
`services.aggregation` — same convention as services.validation):

    uvicorn services.aggregation.api:app --reload

To mount the router into another service's FastAPI app instead:

    from services.aggregation.api import router
    app.include_router(router)
"""
from fastapi import APIRouter, FastAPI

from .bank import aggregate_bank
from .schemas import BankAggregateRequest, BankAggregateResponse

router = APIRouter(tags=["aggregation"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/aggregate/bank", response_model=BankAggregateResponse)
def aggregate_bank_endpoint(request: BankAggregateRequest):
    """Daily transaction rows -> monthly totals -> yearly averages, per
    (bank, account), plus a per-statement balance-continuity integrity check.

    The request body is the raw extraction of one or more bank-statement
    documents; nothing here can 400 on a well-formed body — a statement with
    no transactions simply yields an account with empty monthly/yearly lists.
    """
    return aggregate_bank([d.model_dump() for d in request.documents])


# Standalone app, for `uvicorn services.aggregation.api:app`. Hosts embedding
# this elsewhere should include `router` above instead.
app = FastAPI(title="Aggregation / Normalization API")
app.include_router(router)
