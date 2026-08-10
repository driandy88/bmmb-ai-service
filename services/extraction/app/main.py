import os

from dotenv import load_dotenv
load_dotenv()

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .attributes import router as attributes_router
from .extraction import router as extraction_router
from .metadata import router as metadata_router
from .templates import router as templates_router

# Single combined router for this service, so a host app can do:
#   from services.extraction.app.main import router as extraction_router
#   app.include_router(extraction_router, prefix="/extraction", tags=["extraction"])
# Mirrors the router-export convention used by services.{aggregation,bbox_generator,mcp,validation}.
router = APIRouter()
router.include_router(extraction_router)
router.include_router(metadata_router)
router.include_router(templates_router)
router.include_router(attributes_router)

# Comma-separated list in env, e.g. ALLOWED_ORIGINS="http://localhost:3000,https://app.example.com"
_origins = os.getenv("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS = ["*"] if _origins == "*" else [o.strip() for o in _origins.split(",")]

# Standalone app, for `uvicorn app.main:app`. Hosts embedding this elsewhere
# (e.g. the root services/main.py) should include `router` above instead.
app = FastAPI(
    title="Document Extraction Service",
    description="Gemini-powered document extraction API. Templates and "
                 "attributes are managed via Cloud SQL (PostgreSQL).",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/")
def root():
    return {"message": "Document Extraction Service is running.", "docs": "/docs"}
