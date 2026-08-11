"""
FastAPI router for the extraction service.

Gemini-powered document extraction API. Templates and attributes are
managed via Cloud SQL (PostgreSQL).

To mount into another service's own FastAPI app (so it shares that app's
prefix/middleware/auth instead of running as a separate process):

    from services.extraction.api import router
    app.include_router(router)

To run this service standalone, straight from this module:

    uvicorn services.extraction.api:app --reload
"""
import os

from dotenv import load_dotenv
load_dotenv()

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .attributes import router as attributes_router
from .extraction import router as extraction_router
from .metadata import router as metadata_router
from .templates import router as templates_router

router = APIRouter()
router.include_router(extraction_router)
router.include_router(metadata_router)
router.include_router(templates_router)
router.include_router(attributes_router)


# Standalone app, for `uvicorn services.extraction.api:app`. Hosts embedding
# this elsewhere should include `router` above instead.
app = FastAPI(
    title="Document Extraction Service",
    description="Gemini-powered document extraction API. Templates and "
                 "attributes are managed via Cloud SQL (PostgreSQL).",
    version="1.0.0",
)

# Comma-separated list in env, e.g. ALLOWED_ORIGINS="http://localhost:3000,https://app.example.com"
_origins = os.getenv("ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _origins == "*" else [o.strip() for o in _origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
