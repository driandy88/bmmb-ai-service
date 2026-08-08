"""
HTTP surface (brief §5): POST /chat and GET /health.

The orchestrator is built once at startup (main.py) and stored on app.state;
routes stay thin — validate, delegate, return the envelope.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.api.schemas import ChatRequest, ChatResponse, HealthResponse, SourceDocResponse
from app.config.settings import get_settings

router = APIRouter()

# Per-token delay (seconds) so the stream is visibly incremental in the UI.
_STREAM_TOKEN_DELAY = 0.018


def _sse(event: str, data) -> str:
    """One Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

# Documents the extraction service accepts (Sheet 6.2 checklist). PDFs/images only.
_ALLOWED_DOC_MIME = {
    "application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
}
_MAX_DOC_BYTES = 20 * 1024 * 1024  # 20 MB, matching the extraction service


def get_orchestrator(app):
    """Lazily build + cache the orchestrator on app.state. Called from the
    lifespan at startup, and as a fallback here so the service also works when
    the lifespan wasn't run (e.g. a bare TestClient in a notebook)."""
    orch = getattr(app.state, "orchestrator", None)
    if orch is None:
        from app.orchestrator.graph import build_orchestrator
        orch = build_orchestrator()
        app.state.orchestrator = orch
    return orch


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    return get_orchestrator(request.app).handle(req)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """Same turn as POST /chat, delivered as Server-Sent Events so the UI can
    render the reply as it arrives:

      event: start  data: {"session_id": "..."}          — sent immediately
      event: token  data: {"text": "..."}                — reply, chunk by chunk
      event: done   data: {<full ChatResponse envelope>} — intent, ui_action, state, audit

    The pipeline runs once (off the event loop); the composed reply is then
    streamed token-by-token. Structured fields ride on the final `done` event so
    the client gets the buttons / eligibility outcome / slots exactly as with /chat.
    """
    orch = get_orchestrator(request.app)

    async def gen():
        yield _sse("start", {"session_id": req.session_id})
        try:
            resp = await asyncio.to_thread(orch.handle, req)
        except Exception as exc:  # noqa: BLE001 — surface as a stream error, don't 500 mid-stream
            yield _sse("error", {"message": str(exc)})
            return
        # Whitespace-preserving tokenisation so newlines/spacing render verbatim.
        for tok in re.findall(r"\s+|\S+", resp.reply):
            yield _sse("token", {"text": tok})
            if tok.strip():
                await asyncio.sleep(_STREAM_TOKEN_DELAY)
        yield _sse("done", resp.model_dump())

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},  # disable proxy buffering so tokens flush live
    )


@router.post("/chat/documents", response_model=ChatResponse)
async def chat_documents(
    request: Request,
    template_id: str = Form(..., description="Extraction template, e.g. audited_financial_statements"),
    files: list[UploadFile] = File(..., description="One or more PDF/image documents"),
    session_id: Optional[str] = Form(None),
    application_id: Optional[str] = Form(None),
    channel: str = Form("customer"),
    collected_slots: str = Form("{}", description="JSON of slots already gathered this session"),
) -> ChatResponse:
    """Document-upload eligibility: the chat agent calls the extraction service,
    maps a few Tier-1 figures out of the result, and continues the same
    eligibility flow (slot-fill / verdict) as a typed turn. Only Tier-1 figures
    are read; Tier-2 fields in the document are ignored."""
    try:
        slots = json.loads(collected_slots or "{}")
        if not isinstance(slots, dict):
            raise ValueError("collected_slots must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Bad collected_slots: {exc}")

    file_parts = []
    for f in files:
        ct = f.content_type or ""
        if ct not in _ALLOWED_DOC_MIME:
            raise HTTPException(status_code=400,
                                detail=f"Unsupported file type '{ct}' for '{f.filename}'.")
        data = await f.read()
        if not data:
            raise HTTPException(status_code=400, detail=f"'{f.filename}' is empty.")
        if len(data) > _MAX_DOC_BYTES:
            raise HTTPException(status_code=413, detail=f"'{f.filename}' exceeds 20 MB.")
        file_parts.append((f.filename, ct, data))

    return get_orchestrator(request.app).handle_document(
        template_id=template_id, file_parts=file_parts, session_id=session_id,
        application_id=application_id, channel=channel, collected_slots=slots,
    )


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Liveness + backend reachability. Never throws — reports degraded instead
    so an orchestrator/Vertex hiccup is visible rather than a hard 500."""
    settings = get_settings()
    checks: dict[str, str] = {}
    status = "ok"

    try:
        get_orchestrator(request.app)
        checks["orchestrator"] = "ready"
    except Exception as exc:  # noqa: BLE001
        checks["orchestrator"] = f"error: {exc}"
        status = "degraded"

    # Config load is a cheap liveness proxy for the deterministic core.
    try:
        from app.config.loader import load_config
        load_config()
        checks["config"] = "loaded"
    except Exception as exc:  # noqa: BLE001
        checks["config"] = f"error: {exc}"
        status = "degraded"

    # We don't ping Vertex/Cloud SQL on every health check (cost/latency); we
    # report the configured backends so ops can see what's wired.
    checks["llm_backend"] = settings.llm_backend
    checks["rag_backend"] = settings.rag_backend
    checks["audit_backend"] = settings.audit_backend

    return HealthResponse(
        status=status, llm_backend=settings.llm_backend, rag_backend=settings.rag_backend, checks=checks,
    )


# ── Citation source preview (Tier 1) ─────────────────────────────────────────

def get_source_preview(app):
    """Lazily build + cache the source-preview resolver on app.state."""
    sp = getattr(app.state, "source_preview", None)
    if sp is None:
        from app.integrations.source_preview import SourcePreview
        sp = SourcePreview(get_settings())
        app.state.source_preview = sp
    return sp


@router.get("/chat/source", response_model=SourceDocResponse)
def chat_source(doc_id: str, request: Request, channel: str = "customer") -> SourceDocResponse:
    """Resolve a citation's `doc_id` to a URL the browser can open at the cited page.
    Allowlist + access-tier gated; the client appends `#page=N` from the citation."""
    sp = get_source_preview(request.app)
    doc = sp.resolve(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Unknown source document.")
    if not sp.allowed(doc, channel):
        raise HTTPException(status_code=403, detail="Source not available on this channel.")
    url = sp.url_for(doc, channel)
    if not url:
        raise HTTPException(status_code=503, detail="Source preview is not available.")
    return SourceDocResponse(doc_id=doc.doc_id, doc_title=doc.title, url=url)


@router.get("/chat/source/raw")
def chat_source_raw(doc_id: str, request: Request, channel: str = "customer") -> Response:
    """Stream the source PDF bytes (proxy mode / signed-URL fallback). Same allowlist + gate."""
    sp = get_source_preview(request.app)
    doc = sp.resolve(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Unknown source document.")
    if not sp.allowed(doc, channel):
        raise HTTPException(status_code=403, detail="Source not available on this channel.")
    data = sp.download(doc)
    if data is None:
        raise HTTPException(status_code=503, detail="Source preview is not available.")
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{doc.doc_id}.pdf"',
                 "Cache-Control": "private, max-age=600"},
    )
