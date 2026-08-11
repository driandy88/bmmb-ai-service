"""
Root FastAPI app for the BMMB AI service monorepo.

Mounts all 6 per-service routers under one process / one port / one /docs,
each behind its own path prefix:

    /extraction     services.extraction  (api.py)
    /chatbot        services.chat        (api.py)
    /validation     services.validation  (api.py)
    /aggregation    services.aggregation (api.py)
    /bbox_generator services.bbox_generator (api.py)
    /mcp            services.mcp         (api.py)

Routing-prefix decision: each service keeps its routes exactly as it defines
them -- including its own `GET /health` -- just mounted under its prefix, e.g.
extraction's health check becomes `GET /extraction/health`. This module adds
one more, root-level `GET /health` as an aggregate liveness check.

chat is mounted under `/chatbot` rather than `/chat` -- its own router already
defines `POST /chat`, `POST /chat/stream`, etc. (see
services/chat/app/api/routes.py), so mounting it at `/chat` would produce
`/chat/chat`, `/chat/chat/stream`, .... `/chatbot` keeps the service prefix
distinct from the resource name underneath it.

FastAPI supports exactly one lifespan per app. Of the 6, only chat needs
startup wiring: it builds the LangGraph orchestrator once and caches it on
`app.state` (see services/chat/api.py). `app.include_router()` doesn't create
a separate ASGI app, so chat's handlers -- which look up the orchestrator via
`get_orchestrator(request.app)` -- see exactly the same `app.state` this root
app's lifespan populates. So we just reuse chat's lifespan as-is for the root
app; nothing else needs one.

Run locally:
    uvicorn main:app --reload
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from services.aggregation.api import router as aggregation_router
from services.bbox_generator.api import router as bbox_generator_router
from services.chat.api import lifespan as chat_lifespan
from services.chat.api import router as chat_router
from services.chat.app.config.settings import get_settings as get_chat_settings
from services.extraction.api import router as extraction_router
from services.mcp.api import router as mcp_router
from services.validation.api import router as validation_router

app = FastAPI(
    title="BMMB Unified AI Service",
    description="Extraction, chat, validation, aggregation, bbox generation "
                "and MCP, mounted behind one process. See each prefix's own "
                "/docs section for details.",
    version="1.0.0",
    lifespan=chat_lifespan,
)

# Merged CORS: union of each service's own allow_origins config. Only
# extraction and chat configure CORS today -- the other four are internal
# services with no CORSMiddleware of their own, so there's nothing of theirs
# to union in. "*" from either side wins outright (it's a strict superset of
# any explicit origin list). Both services parse the same env var
# (ALLOWED_ORIGINS) the same way -- see services/extraction/api.py and
# services/chat/app/config/settings.py:Settings.origins_list().
_raw_extraction_origins = os.getenv("ALLOWED_ORIGINS", "*")
_extraction_origins = (
    ["*"] if _raw_extraction_origins == "*"
    else [o.strip() for o in _raw_extraction_origins.split(",")]
)
_chat_origins = get_chat_settings().origins_list()
if "*" in _extraction_origins or "*" in _chat_origins:
    _allow_origins = ["*"]
else:
    _allow_origins = sorted(set(_extraction_origins) | set(_chat_origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# aggregation/bbox_generator/mcp/validation/extraction already tag their own
# router (APIRouter(tags=[...])) -- passing `tags=` again here would just
# duplicate that tag on every operation (e.g. extraction's /health and
# /extract would carry both "Extraction" and "extraction"), so we only pass
# it where the router doesn't already carry one (chat).
app.include_router(extraction_router, prefix="/extraction")
app.include_router(chat_router, prefix="/chatbot", tags=["chat"])
app.include_router(validation_router, prefix="/validation")
app.include_router(aggregation_router, prefix="/aggregation")
app.include_router(bbox_generator_router, prefix="/bbox_generator")
app.include_router(mcp_router, prefix="/mcp")


def _add_binary_format(node: object) -> None:
    """Pydantic v2 emits OpenAPI 3.1 style file schemas (`contentMediaType`
    only, no `format`). Swagger UI's array-of-files widget only checks
    `items.format == "binary"` and never falls back to `contentMediaType`,
    so every `list[UploadFile]` field (e.g. POST /extraction/extract's
    `files`) renders as a plain string-array input instead of a file
    picker. Adding `format` alongside `contentMediaType` fixes the widget
    without changing what the schema describes."""
    if isinstance(node, dict):
        if node.get("type") == "string" and "contentMediaType" in node:
            node.setdefault("format", "binary")
        for value in node.values():
            _add_binary_format(value)
    elif isinstance(node, list):
        for item in node:
            _add_binary_format(item)


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        description=app.description,
        version=app.version,
        routes=app.routes,
    )
    _add_binary_format(schema.get("components", {}).get("schemas", {}))
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _custom_openapi

_MOUNTED = ("extraction", "chat", "validation", "aggregation", "bbox_generator", "mcp")


@app.get("/", tags=["health"])
def root():
    return {"message": "BMMB Unified AI Service is running.", "docs": "/docs", "services": _MOUNTED}


@app.get("/health", tags=["health"])
def health():
    """Root-level aggregate liveness check -- if this process is up and
    serving, all 6 routers below are mounted and importable (a broken import
    would have failed at startup, before this was ever reachable). Each
    service also keeps its own check under its prefix (e.g. GET
    /extraction/health) for per-service probes."""
    return {"status": "ok", "services": _MOUNTED}
