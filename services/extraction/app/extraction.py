from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.config import TemplateNotFoundError, get_template, list_templates
from app.gemini_client import GeminiCallError, GeminiConfigError, GeminiParseError, run_extraction
from app.schema_builder import build_gemini_schema, generate_extraction_prompt
from app.schemas import ApiResponse, TemplateDetail

router = APIRouter(tags=["Extraction"])

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/templates", response_model=list[TemplateDetail] | list[dict])
def get_templates():
    """Lightweight listing — key, description, kind, field_count for every template."""
    return list_templates()


@router.get("/templates/{template_key}", response_model=TemplateDetail)
def get_template_detail(template_key: str):
    try:
        return get_template(template_key)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/extract", status_code=status.HTTP_200_OK)
async def extract_document(
    template: str = Form(..., description="Template key, e.g. 'business_registration_ssm'"),
    file: UploadFile = File(...),
    model: str = Form("gemini-2.5-flash", description="Gemini model id"),
):
    try:
        tmpl = get_template(template)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type '{content_type}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}."
            ),
        )

    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds the 20 MB size limit.")
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    schema = build_gemini_schema(template)
    prompt = generate_extraction_prompt(template)

    try:
        extracted = run_extraction(
            file_bytes=file_bytes,
            mime_type=content_type,
            prompt=prompt,
            schema=schema,
            model=model,
        )
    except GeminiConfigError as exc:
        raise HTTPException(status_code=500, detail=f"Service misconfigured: {exc}")
    except GeminiCallError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}")
    except GeminiParseError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to parse Gemini response: {exc}")

    return ApiResponse(data={
        "template": template,
        "template_kind": tmpl["kind"],
        "model": model,
        "extracted_data": extracted,
    })