from typing import Any, Optional
from pydantic import BaseModel


class ApiResponse(BaseModel):
    """Consistent wrapper for every successful response.

    Error responses use FastAPI's default HTTPException shape
    (HTTP 4xx/5xx with a `detail` field) and are NOT wrapped in this envelope.
    """
    success: bool = True
    data: Any
    error: Optional[str] = None


class TemplateSummary(BaseModel):
    key: str
    description: str
    kind: str  # "single" | "array"
    field_count: int


class FieldDetail(BaseModel):
    description: Optional[str] = None
    example: Any = None
    data_type: str


class TemplateDetail(BaseModel):
    key: str
    description: str
    kind: str
    fields: dict[str, FieldDetail]
