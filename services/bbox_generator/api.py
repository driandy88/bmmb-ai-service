"""
FastAPI router for the bbox-generator service.

Given the file the LLM already extracted from, plus its extracted
{field: value} result and each field's data_type, this returns a normalized
(0-1) bounding box per field for highlighting in the document preview.
No LLM call happens here — see bbox_aligner.py's module docstring.

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

from fastapi import APIRouter, FastAPI, Form, HTTPException, UploadFile

from .bbox_aligner import align_fields

router = APIRouter(tags=["bbox_generator"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/align")
async def align(
    file: UploadFile,
    extracted: str = Form(...),
    field_types: str = Form(...),
):
    """extracted / field_types are JSON-encoded {field: value} / {field: data_type}
    dicts — the same shapes extraction-service already returns/consumes."""
    try:
        extracted_data = json.loads(extracted)
        field_types_data = json.loads(field_types)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {e}")

    suffix = Path(file.filename or "").suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(await file.read())
        tmp.flush()
        try:
            result = align_fields(extracted_data, field_types_data, tmp.name)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"alignment failed: {e}")

    return result


# Standalone app, for `uvicorn services.bbox_generator.api:app`. Host services
# embedding this elsewhere should use `router` above instead.
app = FastAPI(title="Bbox Generator API")
app.include_router(router)
