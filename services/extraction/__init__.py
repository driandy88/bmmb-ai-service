"""
Document extraction service.

Gemini-powered extraction of structured fields from uploaded documents, plus
template/attribute management (Cloud SQL / PostgreSQL) and forensic metadata
inspection.

Module map (all under app/):
  extraction.py, metadata.py, attributes.py, templates.py -- routers.
  main.py -- combined `router` export (mirrors services.{aggregation,
             bbox_generator,mcp,validation}) plus a standalone `app` for
             `uvicorn app.main:app`.

See services/extraction/app/main.py for the FastAPI wiring.
"""
