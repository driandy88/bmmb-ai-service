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


def _locations_schema(field_names: list[str]) -> dict:
    """One {page, section} slot per field, so the caller can point a document
    preview at the page a value was actually read from. Not in `required` —
    unlike the data fields, we'd rather silently do without it than force a
    schema-validation failure if a field-scale document confuses the model.
    """
    return {
        "type": "OBJECT",
        "nullable": True,
        "properties": {
            name: {
                "type": "OBJECT",
                "nullable": True,
                "properties": {
                    "page": {"type": "INTEGER", "nullable": True},
                    "section": {"type": "STRING", "nullable": True},
                },
            }
            for name in field_names
        },
    }


def build_gemini_schema(template_key: str) -> dict:
    """Returns a dict suitable for GenerateContentConfig(response_schema=...).

    Every field is nullable and listed in `required` — this is deliberate: it
    forces Gemini to emit `null` for anything it can't find rather than
    omitting the key, so the caller always gets a complete, predictable shape.

    Each object also carries a sibling `_locations` map (one {page, section}
    per field) — for "array" templates this means every row gets its own
    locations, which is what a per-row "jump to page" UI needs.
    """
    tmpl = get_template(template_key)
    properties = {name: _field_schema(meta) for name, meta in tmpl["fields"].items()}
    field_names = list(properties.keys())
    properties["_locations"] = _locations_schema(field_names)
    obj_schema = {
        "type": "OBJECT",
        "properties": properties,
        "required": field_names,
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
        "",
        "For each field above, also populate its entry in _locations with:",
        "  page    — 1-based page number where the value appears (null if unknown)",
        "  section — nearest heading or section title on that page (null if unknown)",
        "If this document type may contain multiple instances, give each instance's "
        "object its own _locations reflecting where THAT instance's values were found.",
    ]
    return "\n".join(lines)
