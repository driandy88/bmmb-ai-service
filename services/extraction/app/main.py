import os

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.attributes import router as attributes_router
from app.extraction import router as extraction_router
from app.templates import router as templates_router

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

app.include_router(extraction_router)
app.include_router(templates_router)
app.include_router(attributes_router)


@app.get("/")
def root():
    return {"message": "Document Extraction Service is running.", "docs": "/docs"}
