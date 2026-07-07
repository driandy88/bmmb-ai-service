"""
Loads templates_config.json and normalises every document-type section into a
consistent shape, regardless of whether the source JSON used "fields" (single
object per document) or a "*_object_fields" key (array of objects per document
— e.g. one entry per bank statement month, per financial year, per director IC).

This is the ONLY place that understands the raw JSON's key-naming quirks.
Everything downstream (schema_builder, prompts) works off get_template()'s
normalised output and doesn't care how the source file was shaped.
"""
import json
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "templates_config.json"

# Any section key ending in this suffix is treated as "one object per instance,
# multiple instances possible" -> the Gemini schema wraps it in an ARRAY.
_ARRAY_FIELDS_SUFFIX = "_object_fields"
_SINGLE_FIELDS_KEY = "fields"

_RESERVED_TOP_LEVEL_KEYS = {"schema_version", "description"}


class TemplateNotFoundError(KeyError):
    pass


def _normalise_section(key: str, raw: dict) -> dict:
    """Turn one raw JSON section into {key, description, kind, fields}."""
    fields_key = None
    if _SINGLE_FIELDS_KEY in raw:
        fields_key, kind = _SINGLE_FIELDS_KEY, "single"
    else:
        for k in raw:
            if k.endswith(_ARRAY_FIELDS_SUFFIX):
                fields_key, kind = k, "array"
                break
    if fields_key is None:
        raise ValueError(
            f"Template section '{key}' has neither a 'fields' key nor a "
            f"'*{_ARRAY_FIELDS_SUFFIX}' key — cannot determine its shape."
        )
    return {
        "key": key,
        "description": raw.get("_section_description", ""),
        "kind": kind,  # "single" | "array"
        "fields": raw[fields_key],  # {field_name: {field_name, description, example, data_type}}
    }


@lru_cache(maxsize=1)
def _load_all() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    templates = {}
    for key, value in raw.items():
        if key in _RESERVED_TOP_LEVEL_KEYS:
            continue
        templates[key] = _normalise_section(key, value)
    return templates


def list_templates() -> list[dict]:
    """Summary view for the /templates listing endpoint."""
    return [
        {"key": t["key"], "description": t["description"], "kind": t["kind"],
         "field_count": len(t["fields"])}
        for t in _load_all().values()
    ]


def get_template(template_key: str) -> dict:
    templates = _load_all()
    if template_key not in templates:
        raise TemplateNotFoundError(
            f"Unknown template '{template_key}'. Available: {', '.join(templates)}"
        )
    return templates[template_key]


def reload_config():
    """Clears the cache — useful in tests or if templates_config.json changes at runtime."""
    _load_all.cache_clear()
