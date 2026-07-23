from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.extraction import ALLOWED_MIME_TYPES, MAX_FILE_SIZE
from app.metadata_extractor import MetadataError, get_file_metadata
from app.schemas import ApiResponse

router = APIRouter(tags=["Metadata"])


@router.post("/extract-metadata", status_code=status.HTTP_200_OK)
async def extract_metadata(file: UploadFile = File(...)):
    """Dump the raw file-level metadata (EXIF/XMP/PDF/software tags) for a single
    uploaded file. Stripped files return a thin dict, not an error."""
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type '{content_type}' for '{file.filename}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}."
            ),
        )

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail=f"'{file.filename}' is empty.")
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"'{file.filename}' exceeds the 20 MB size limit.")

    try:
        metadata = get_file_metadata(file_bytes)
    except MetadataError as exc:
        raise HTTPException(status_code=502, detail=f"Metadata extraction failed: {exc}")

    return ApiResponse(data={"filename": file.filename, "metadata": metadata})
