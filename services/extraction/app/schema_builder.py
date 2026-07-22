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

    Every declared data field/group is marked `required` (while staying
    `nullable`), so the model must emit the key for each one -- a genuinely
    absent value comes back as an explicit `null` (or empty array) rather than
    a dropped key, keeping the output shape uniform for anything that reads the
    raw JSON row-by-row and field-by-field. Row_group row-objects require every
    column for the same reason.

    Provenance lives in a sibling `_locations` block so a document viewer can
    jump straight to where a value was read from. Each ungrouped field gets one
    location object there. A row_group instead gets a LIST under
    `_locations[group]` -- one entry per row, in row order, each carrying a
    `_row_key` (a copy of that row's identifying value, e.g. its Financial
    Statement Date) plus one location per column. reshape_locations() later folds
    that list into a dict keyed by `_row_key`, so the caller reads
    `_locations[group][<row key>][<column>]`. Per-VALUE, per-row provenance is the
    point: feed in several years of statements and each year's figures sit in a
    different file, and within one year Revenue, Total Equity and Depreciation
    sit on different pages -- one location for the whole group (or whole row) can
    only ever be right for one field. All location metadata is optional (not
    required) so a required key never nudges the model to invent a page number it
    couldn't find.
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
            "items": {"type": "OBJECT", "properties": row_props, "required": list(row_props)},
        }
        # Per-row provenance: a list parallel to the rows above. Gemini's schema
        # can't declare a dict keyed by a runtime date, so the model emits a list
        # and reshape_locations() keys it by `_row_key` afterwards. Each entry
        # carries `_row_key` (copy of the row's identifying value, for matching)
        # plus one location per column. All optional so an un-locatable row emits.
        loc_row_props = {"_row_key": {"type": "STRING", "nullable": True}}
        for m in members:
            loc_row_props[m["attribute"]["name"]] = _location_schema()
        location_props[group_name] = {
            "type": "ARRAY",
            "nullable": True,
            "items": {"type": "OBJECT", "properties": loc_row_props},
        }

    # Captured before adding _locations so the metadata block stays optional.
    data_fields = list(properties)

    if location_props:
        properties["_locations"] = {
            "type": "OBJECT",
            "nullable": True,
            "properties": location_props,
        }

    return {"type": "OBJECT", "properties": properties, "required": data_fields}


def reshape_locations(result: dict, template_id: str) -> dict:
    """Fold each row_group's `_locations[group]` list into a dict keyed by the
    row's `_row_key`, so provenance is looked up by the same key that identifies
    the data row: `_locations[group][<row key>][<column>] = {real_page, ...}`.

    build_gemini_schema() has the model emit that provenance as a list (a Gemini
    response_schema can't declare an object keyed by a runtime value like a date),
    so this is where the list becomes the date-keyed dict callers actually want.
    Mutates and returns `result`; a no-op when there's no `_locations`, so a
    mocked or ungrouped response passes straight through.
    """
    if not isinstance(result, dict):
        return result
    locations = result.get("_locations")
    if not isinstance(locations, dict):
        return result

    _, grouped = _partition(get_template(template_id)["template_attributes"])
    for group_name in grouped:
        rows = locations.get(group_name)
        if not isinstance(rows, list):
            continue  # null, absent, or already reshaped -- leave as-is
        keyed: dict = {}
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            raw_key = row.pop("_row_key", None)
            key = str(raw_key) if raw_key not in (None, "") else f"row_{i + 1}"
            # Two rows sharing a key (e.g. the same date) would otherwise clobber
            # each other; suffix the later ones so every row's provenance survives.
            unique, n = key, 2
            while unique in keyed:
                unique, n = f"{key} ({n})", n + 1
            # Drop null/empty cell locations so absent provenance isn't noise.
            keyed[unique] = {col: loc for col, loc in row.items() if loc}
        locations[group_name] = keyed
    return result


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
        "Also populate _locations to record where each value was read from:",
        "  - For each ungrouped field, set _locations[<field name>] to one location object.",
        "  - For each repeating group, set _locations[<group name>] to a LIST with one entry per",
        "    extracted row, in the same order as the rows. Each entry has `_row_key` — a copy of",
        "    that row's identifying value (for financial statements, its Financial Statement Date),",
        "    so it can be matched to its row — plus one location object per column.",
        "Each location object has:",
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


# Cross-cutting extraction guidance that applies identically to every template.
# It's appended once here, at prompt-generation time, rather than duplicated into
# each template's stored llm_prompt -- these BMMB documents are routinely in Malay
# (Bahasa Malaysia) or bilingual, so the field labels on the page often differ from
# the English attribute names. This tells the model to match on meaning (and common
# Malay labels) while keeping the output keys in English, so a Malay-only SSM form or
# bank statement still populates the same schema.
_GLOBAL_LANGUAGE_GUIDANCE = (
    "\n\nLanguage note: the document may be wholly or partly in Malay (Bahasa Malaysia), "
    "or bilingual. Identify each field by its meaning, matching Malay labels as well as "
    "English -- for example Nama Syarikat = company name, No. Pendaftaran = registration "
    "number, Alamat (Berdaftar) = (registered) address, Sifat Perniagaan = nature of "
    "business, Pengarah = director, Pemegang Saham = shareholder, Tarikh = date, "
    "Baki = balance, Debit / Kredit = debit / credit. Extract the value regardless of the "
    "label's language, but keep every output key exactly as named above, in English."
)


def generate_extraction_prompt(template_id: str) -> str:
    """Returns the template's stored llm_prompt if present (the normal case
    -- templates are seeded/authored with a precomputed prompt), otherwise
    builds an equivalent one from its attributes. The global language guidance
    is appended in either case so it applies uniformly across every template."""
    tmpl = get_template(template_id)
    base = tmpl["llm_prompt"] if tmpl["llm_prompt"] else render_extraction_prompt(tmpl)
    return base + _GLOBAL_LANGUAGE_GUIDANCE
