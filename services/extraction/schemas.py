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


# Mirrors universal_data_extractor's schemas.py so a frontend built against
# that project's API contract works against this service unchanged.

class AttributeOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    data_type: str  # "Alphabet" | "Alphanumeric" | "Numeric" | "Datetime" | "Boolean"
    example: Optional[str] = None


class TemplateAttributeOut(BaseModel):
    id: str
    attribute_id: str
    frequency: str  # "Unique" | "Multiple"
    row_group: Optional[str] = None
    attribute: AttributeOut


class TemplateOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    group_name: Optional[str] = None
    llm_prompt: Optional[str] = None
    template_attributes: list[TemplateAttributeOut] = []


# ── Write request bodies ─────────────────────────────────────────────────────

class AttributeCreate(BaseModel):
    name: str
    description: Optional[str] = None
    data_type: str  # "Alphabet" | "Alphanumeric" | "Numeric" | "Datetime" | "Boolean"
    example: Optional[str] = None


class AttributeUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    data_type: Optional[str] = None
    example: Optional[str] = None


class TemplateAttributeIn(BaseModel):
    attribute_id: str
    frequency: str = "Unique"  # "Unique" | "Multiple"
    row_group: Optional[str] = None


class TemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    group_name: Optional[str] = None
    llm_prompt: Optional[str] = None
    attributes: list[TemplateAttributeIn] = []


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    group_name: Optional[str] = None
    llm_prompt: Optional[str] = None
    attributes: Optional[list[TemplateAttributeIn]] = None
