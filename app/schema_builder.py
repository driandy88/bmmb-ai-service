"""
Builds the Gemini `response_schema` dict and the extraction prompt for a given
template, driven entirely by app/templates_config.json (via app.config).

No database involved. This is the whole point of this service: given a
template key, produce (1) a JSON schema Gemini must fill exactly, and
(2) a human-readable prompt describing each field.
"""
from app.config import get_template

_TYPE_MAP = {
    "string": "STRING",
    "float": "NUMBER",
    "date": "STRING",  # no native Gemini date type; the field description carries the expected format
}


def _field_schema(meta: dict) -> dict:
    dtype = meta.get("data_type", "string")
    if dtype == "list[string]":
        return {
            "type": "ARRAY",
            "description": meta.get("description"),
            "nullable": True,
            "items": {"type": "STRING"},
        }
    return {
        "type": _TYPE_MAP.get(dtype, "STRING"),
        "description": meta.get("description"),
        "nullable": True,
    }


def build_gemini_schema(template_key: str) -> dict:
    """Returns a dict suitable for GenerateContentConfig(response_schema=...).

    Every field is nullable and listed in `required` — this is deliberate: it
    forces Gemini to emit `null` for anything it can't find rather than
    omitting the key, so the caller always gets a complete, predictable shape.
    """
    tmpl = get_template(template_key)
    properties = {name: _field_schema(meta) for name, meta in tmpl["fields"].items()}
    obj_schema = {
        "type": "OBJECT",
        "properties": properties,
        "required": list(properties.keys()),
    }
    if tmpl["kind"] == "array":
        return {
            "type": "ARRAY",
            "description": tmpl["description"],
            "items": obj_schema,
        }
    return obj_schema


def generate_extraction_prompt(template_key: str) -> str:
    """Builds the field-level instruction string injected into every Gemini call."""
    tmpl = get_template(template_key)
    lines = [f'You are extracting structured data from a "{template_key}" document.']
    if tmpl["description"]:
        lines.append(tmpl["description"])
    if tmpl["kind"] == "array":
        lines.append(
            "This document type may contain MULTIPLE instances (e.g. multiple "
            "months, years, or individuals). Return one object per instance in a JSON array."
        )
    lines += ["", "Fields to extract:", ""]

    for i, (name, meta) in enumerate(tmpl["fields"].items(), 1):
        dtype = meta.get("data_type", "string")
        example = meta.get("example")
        example_info = f" — e.g. {example}" if example is not None else ""
        lines.append(f"{i}. {name}  |  Type: {dtype}{example_info}")
        if meta.get("description"):
            lines.append(f"   {meta['description']}")

    lines += [
        "",
        "Return null for any field not found or unclear in the document. "
        "Do not guess or infer values that are not present.",
    ]
    return "\n".join(lines)
