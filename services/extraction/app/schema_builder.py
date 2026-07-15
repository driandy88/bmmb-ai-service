"""
Builds the Gemini `response_schema` dict and the extraction prompt for a
given template, driven by app.config (Cloud SQL). Ported from
universal_data_extractor's utils.py so this service's schema/prompt shape
matches that project's exactly: per-attribute frequency (Unique/Multiple)
drives scalar-vs-array, and attributes sharing a non-null row_group are
extracted together as one correlated array of row-objects.
"""
from app.config import get_template

_TYPE_MAP = {
    "Numeric": "NUMBER",
    "Boolean": "BOOLEAN",
    # Alphabet / Alphanumeric / Datetime all have no native Gemini type
    # distinct from string; the field description carries the expected format.
}


def _scalar_schema(data_type: str) -> dict:
    return {"type": _TYPE_MAP.get(data_type, "STRING"), "nullable": True}


def _location_schema() -> dict:
    """One {real_page, shown_page, section, document} slot per field/group,
    so the caller can point a document preview at the page a value was
    actually read from."""
    return {
        "type": "OBJECT",
        "nullable": True,
        "properties": {
            "real_page": {"type": "INTEGER", "nullable": True},
            "shown_page": {"type": "STRING", "nullable": True},
            "section": {"type": "STRING", "nullable": True},
            "document": {"type": "STRING", "nullable": True},
        },
    }


def _attr_schema(data_type: str, frequency: str) -> dict:
    base = _scalar_schema(data_type)
    if frequency == "Multiple":
        return {"type": "ARRAY", "nullable": True, "items": base}
    return base


def _partition(template_attributes: list[dict]):
    """Split a template's attributes into standalone fields and row_group
    clusters. Attributes sharing the same non-null row_group are extracted
    together as one correlated array of row-objects instead of independent
    fields."""
    ungrouped = []
    grouped: dict[str, list[dict]] = {}
    for ta in template_attributes:
        if ta["row_group"]:
            grouped.setdefault(ta["row_group"], []).append(ta)
        else:
            ungrouped.append(ta)
    return ungrouped, grouped


def build_gemini_schema(template_id: str) -> dict:
    """Returns a dict suitable for GenerateContentConfig(response_schema=...).

    Every top-level field/group also carries a sibling `_locations` entry so
    a document viewer can jump straight to where a value was read from.
    """
    tmpl = get_template(template_id)
    ungrouped, grouped = _partition(tmpl["template_attributes"])

    properties = {}
    location_props = {}

    for ta in ungrouped:
        attr = ta["attribute"]
        properties[attr["name"]] = _attr_schema(attr["data_type"], ta["frequency"])
        location_props[attr["name"]] = _location_schema()

    for group_name, members in grouped.items():
        row_props = {m["attribute"]["name"]: _scalar_schema(m["attribute"]["data_type"]) for m in members}
        properties[group_name] = {
            "type": "ARRAY",
            "nullable": True,
            "items": {"type": "OBJECT", "properties": row_props},
        }
        location_props[group_name] = _location_schema()

    if location_props:
        properties["_locations"] = {
            "type": "OBJECT",
            "nullable": True,
            "properties": location_props,
        }

    return {"type": "OBJECT", "properties": properties}


def render_extraction_prompt(tmpl: dict) -> str:
    """Builds the prompt text from a template's current attributes, ignoring
    any stored llm_prompt. Used both as generate_extraction_prompt()'s
    fallback and by scripts/regenerate_prompts.py to bring a stale stored
    prompt back in sync with template_attributes."""
    lines = [f'You are extracting structured data from a "{tmpl["name"]}" document.']
    if tmpl["description"]:
        lines.append(f"Document description: {tmpl['description']}")
    lines += ["", "Fields to extract:", ""]

    ungrouped, grouped = _partition(tmpl["template_attributes"])
    i = 1

    for ta in ungrouped:
        attr = ta["attribute"]
        freq_note = " (multiple occurrences expected)" if ta["frequency"] == "Multiple" else ""
        example_info = f" — e.g. {attr['example']}" if attr["example"] else ""
        lines.append(f"{i}. {attr['name']}  |  Type: {attr['data_type']}{freq_note}{example_info}")
        if attr["description"]:
            lines.append(f"   {attr['description']}")
        i += 1

    for group_name, members in grouped.items():
        lines.append(f"{i}. {group_name} (repeating group — extract one row object per occurrence, "
                     f"with these columns):")
        for m in members:
            attr = m["attribute"]
            example_info = f" — e.g. {attr['example']}" if attr["example"] else ""
            lines.append(f"   - {attr['name']}  |  Type: {attr['data_type']}{example_info}")
            if attr["description"]:
                lines.append(f"       {attr['description']}")
        i += 1

    lines += [
        "",
        "For each field above, also populate its entry in _locations with:",
        "  real_page  — the actual sequential page number of the source document/PDF file, counting the",
        "               first page as 1 regardless of any printed page numbers or cover/title pages",
        "               (null if unknown). This is used to jump to the right page in the file.",
        "  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a",
        "               footer or header page number, which may be a roman numeral or differ from",
        "               real_page due to unnumbered front matter) (null if no visible label).",
        "  section    — nearest heading or section title on that page (null if unknown)",
        "  document   — the source document name the value came from, if more than one was provided (null if unknown)",
        "",
        "Return null for any field not found or unclear in the document.",
    ]
    return "\n".join(lines)


def generate_extraction_prompt(template_id: str) -> str:
    """Returns the template's stored llm_prompt if present (the normal case
    -- templates are seeded/authored with a precomputed prompt), otherwise
    builds an equivalent one from its attributes."""
    tmpl = get_template(template_id)
    if tmpl["llm_prompt"]:
        return tmpl["llm_prompt"]
    return render_extraction_prompt(tmpl)
