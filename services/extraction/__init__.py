"""
Document extraction service.

Gemini-powered extraction of structured fields from uploaded documents, plus
template/attribute management (Cloud SQL / PostgreSQL) and forensic metadata
inspection.

Module map:
  extraction.py, metadata.py, attributes.py, templates.py -- routers.
  api.py -- combined `router` export (mirrors services.{aggregation,
            bbox_generator,mcp,validation}) plus a standalone `app` for
            `uvicorn services.extraction.api:app`.

See services/extraction/api.py for the FastAPI wiring.
"""
