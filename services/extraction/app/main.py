import os

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.extraction import router as extraction_router

app = FastAPI(
    title="Document Extraction Service",
    description="Standalone Gemini-powered document extraction API. "
                 "No database — templates are defined in app/templates_config.json.",
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


@app.get("/")
def root():
    return {"message": "Document Extraction Service is running.", "docs": "/docs"}
