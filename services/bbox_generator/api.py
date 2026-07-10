"""
FastAPI router for the bbox-generator service.

Given the file a document was extracted from, plus its extracted
{field: value} result and each field's data_type, this returns a normalized
(0-1) bounding box per field for highlighting in the document preview.

Two alignment strategies, picked via the `method` field:
  - "ocr" (default) — bbox_aligner.py: pure PDF text-layer / OCR word
    geometry matching. No LLM call.
  - "llm" — llm_bbox.py: Gemini vision localizes each value directly on the
    page image, then a deterministic verifier (reusing bbox_aligner's word
    geometry) confirms the box before it's trusted. Requires `template`
    (name/description + per-field descriptions) for a useful prompt, and
    GCP_PROJECT_ID/VERTEX_LOCATION configured on this service.

To mount into another service's own FastAPI app (so it shares that app's
prefix/middleware/auth instead of running as a separate process):

    from services.bbox_generator.api import router
    app.include_router(router)

To run this service standalone, straight from this module:

    uvicorn services.bbox_generator.api:app --reload
"""
import json
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, FastAPI, Form, HTTPException, UploadFile

from .bbox_aligner import align_fields
from .llm_bbox import LlmConfigError, align_extraction_llm

router = APIRouter(tags=["bbox_generator"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/align")
async def align(
    file: UploadFile,
    extracted: str = Form(...),
    field_types: str = Form(...),
    method: str = Form("ocr"),
    template: Optional[str] = Form(None),
):
    """extracted / field_types are JSON-encoded {field: value} / {field: data_type}
    dicts — the same shapes extraction-service already returns/consumes.
    method: "ocr" (default) or "llm". template is required JSON when
    method="llm" — see llm_bbox.build_prompt() for its shape."""
    try:
        extracted_data = json.loads(extracted)
        field_types_data = json.loads(field_types)
        template_data = json.loads(template) if template else None
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {e}")

    if method not in ("ocr", "llm"):
        raise HTTPException(status_code=400, detail=f"unknown method: {method!r}")
    if method == "llm" and not template_data:
        raise HTTPException(status_code=400, detail="template is required when method=llm")

    suffix = Path(file.filename or "").suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(await file.read())
        tmp.flush()
        try:
            if method == "llm":
                result = align_extraction_llm(extracted_data, field_types_data, template_data, tmp.name)
            else:
                result = align_fields(extracted_data, field_types_data, tmp.name)
        except LlmConfigError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"alignment failed: {e}")

    return result


# Standalone app, for `uvicorn services.bbox_generator.api:app`. Host services
# embedding this elsewhere should use `router` above instead.
app = FastAPI(title="Bbox Generator API")
app.include_router(router)
