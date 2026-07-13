from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.config import TemplateNotFoundError, get_template, list_templates
from app.gemini_client import GeminiCallError, GeminiConfigError, GeminiParseError, run_extraction
from app.schema_builder import build_gemini_schema, generate_extraction_prompt
from app.schemas import ApiResponse, TemplateOut

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


@router.get("/templates", response_model=list[TemplateOut])
def get_templates():
    return list_templates()


@router.get("/templates/{template_id}", response_model=TemplateOut)
def get_template_detail(template_id: int):
    try:
        return get_template(template_id)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/extract", status_code=status.HTTP_200_OK)
async def extract_document(
    template_id: int = Form(...),
    files: list[UploadFile] = File(...),
    model: str = Form("gemini-2.5-flash", description="Gemini model id"),
):
    try:
        tmpl = get_template(template_id)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required.")

    file_parts = []
    for f in files:
        content_type = f.content_type or ""
        if content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported file type '{content_type}' for '{f.filename}'. "
                    f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}."
                ),
            )
        file_bytes = await f.read()
        if len(file_bytes) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"'{f.filename}' exceeds the 20 MB size limit.")
        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail=f"'{f.filename}' is empty.")
        file_parts.append((f.filename, content_type, file_bytes))

    schema = build_gemini_schema(template_id)
    prompt = generate_extraction_prompt(template_id)

    try:
        extracted = run_extraction(
            files=file_parts,
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
        "template_id": template_id,
        "template_name": tmpl["name"],
        "documents": [filename for filename, _, _ in file_parts],
        "model": model,
        "extracted_data": extracted,
    })
