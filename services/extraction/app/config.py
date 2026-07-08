"""
Loads the template/attribute definitions from BigQuery and normalises every
document-type "template" row into a consistent shape, regardless of whether
it was authored as kind="single" (one object per document) or kind="array"
(array of objects per document -- e.g. one entry per bank statement month,
per financial year, per director IC).

Templates/attributes are managed via the Express admin backend's
/api/templates and /api/attributes routes (bmmb-sme-financing-platform/
backend), which write to the same BigQuery dataset this module reads from:
`docs_extractor_{APP_ENV}` in project `GCP_PROJECT_ID`.

This is the ONLY place that understands the raw table shapes. Everything
downstream (schema_builder, prompts) works off get_template()'s normalised
output and doesn't care that the source is BigQuery.
"""
import os
import time

from google.cloud import bigquery

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "prototype-bmmb-1b62")
APP_ENV = os.getenv("APP_ENV", "dev")
if APP_ENV not in ("dev", "prod"):
    raise RuntimeError(f"APP_ENV must be 'dev' or 'prod', got {APP_ENV!r}")

DATASET_ID = f"docs_extractor_{APP_ENV}"

# Re-query BigQuery at most once per this many seconds. There's no external
# signal available to invalidate the cache on write (the Express admin routes
# and this service are separate processes), so a short TTL is used instead of
# caching forever.
_CACHE_TTL_SECONDS = 5 * 60

_cache = {"data": None, "loaded_at": 0.0}

_client = None


def _get_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=PROJECT_ID)
    return _client


def _table_ref(name: str) -> str:
    return f"`{PROJECT_ID}.{DATASET_ID}.{name}`"


class TemplateNotFoundError(KeyError):
    pass


def _query_templates() -> dict:
    """Reads templates + template_attributes + attributes from BigQuery and
    groups them into the same normalised shape the old JSON-backed
    `_load_all()` returned: {service_template_key: {key, description, kind,
    fields: {field_name: {description, example, data_type}}}}.

    Only templates with a non-null service_template_key are exposed here --
    that column is what wires a BigQuery template row to this service.
    attribute_columns (Table-type sub-columns) are not joined in: none of the
    seeded templates use Table-type attributes, and schema_builder.py has no
    support for nested column schemas today, so skipping it keeps this query
    simple without losing any current functionality.
    """
    client = _get_client()
    sql = f"""
        SELECT
            t.service_template_key AS key,
            t.description AS template_description,
            t.kind AS kind,
            a.name AS field_name,
            a.description AS field_description,
            a.example AS example,
            a.data_type AS data_type
        FROM {_table_ref('templates')} t
        JOIN {_table_ref('template_attributes')} ta ON ta.template_id = t.id
        JOIN {_table_ref('attributes')} a ON a.id = ta.attribute_id
        WHERE t.service_template_key IS NOT NULL
        ORDER BY t.id, ta.id
    """
    rows = list(client.query(sql).result())

    templates: dict = {}
    for row in rows:
        tmpl = templates.setdefault(
            row.key,
            {
                "key": row.key,
                "description": row.template_description or "",
                "kind": row.kind or "single",
                "fields": {},
            },
        )
        tmpl["fields"][row.field_name] = {
            "description": row.field_description,
            "example": row.example,
            "data_type": row.data_type,
        }

    return templates


def _load_all() -> dict:
    now = time.time()
    if _cache["data"] is None or (now - _cache["loaded_at"]) > _CACHE_TTL_SECONDS:
        _cache["data"] = _query_templates()
        _cache["loaded_at"] = now
    return _cache["data"]


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
    """Clears the cache — useful in tests or to force a refresh sooner than
    the TTL (e.g. right after seeding/editing templates via the admin API)."""
    _cache["data"] = None
