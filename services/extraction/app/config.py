"""
Loads the template/attribute definitions from Cloud SQL (PostgreSQL) and
normalises them into the same shape universal_data_extractor's API returns:
a template has an id, name, description, group_name, llm_prompt, and a list
of template_attributes, each carrying its own frequency ("Unique"/"Multiple")
and optional row_group (attributes sharing a row_group are extracted
together as one correlated array of row-objects) plus the nested attribute
(name, description, data_type, example).

Templates/attributes are managed via the Express admin backend's
/api/templates and /api/attributes routes (bmmb-sme-financing-platform/
backend), which write to the same Cloud SQL database this module reads from:
`bmmb_{APP_ENV}` on instance `INSTANCE_CONNECTION_NAME`.

This is the ONLY place that understands the raw table shapes. Everything
downstream (schema_builder, prompts) works off get_template()'s normalised
output and doesn't care that the source is Cloud SQL.

Connects via the Cloud SQL Python Connector (not a raw host:port), so the
same code path works unchanged locally (IAM user + `gcloud auth
application-default login`) and on Cloud Run (attached service account) --
no Cloud SQL Auth Proxy sidecar needed. See README "Cloud SQL setup" for the
one-time instance/database/IAM setup this depends on.
"""
import os
import time

import sqlalchemy
from google.cloud.sql.connector import Connector, IPTypes

APP_ENV = os.getenv("APP_ENV", "dev")
if APP_ENV not in ("dev", "prod"):
    raise RuntimeError(f"APP_ENV must be 'dev' or 'prod', got {APP_ENV!r}")

# "project:region:instance", e.g. prototype-bmmb-1b62:asia-southeast1:docs-extractor
INSTANCE_CONNECTION_NAME = os.environ["INSTANCE_CONNECTION_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASS = os.environ["DB_PASS"]
DB_NAME = os.getenv("DB_NAME", f"bmmb_{APP_ENV}")
# Private IP requires the Cloud Run service to have a VPC connector attached;
# defaults to public IP (still TLS-encrypted + IAM-authorized by the connector).
_USE_PRIVATE_IP = os.getenv("DB_USE_PRIVATE_IP", "false").lower() == "true"

# Re-query Cloud SQL at most once per this many seconds. There's no external
# signal available to invalidate the cache on write (the Express admin routes
# and this service are separate processes), so a short TTL is used instead of
# caching forever. Kept small so admin edits propagate within ~30s; extraction
# spends 5-30s in Gemini anyway, so the extra query is invisible.
_CACHE_TTL_SECONDS = 30

_cache = {"data": None, "loaded_at": 0.0}

_connector = None
_engine = None


def _get_engine() -> sqlalchemy.engine.Engine:
    global _connector, _engine
    if _engine is None:
        _connector = Connector()

        def _getconn():
            return _connector.connect(
                INSTANCE_CONNECTION_NAME,
                "pg8000",
                user=DB_USER,
                password=DB_PASS,
                db=DB_NAME,
                ip_type=IPTypes.PRIVATE if _USE_PRIVATE_IP else IPTypes.PUBLIC,
            )

        # Pool is process-local and kept small: this service only reads
        # templates (a handful of small queries per cache refresh), not
        # request-volume traffic.
        _engine = sqlalchemy.create_engine(
            "postgresql+pg8000://",
            creator=_getconn,
            pool_size=5,
            max_overflow=2,
            pool_timeout=30,
            pool_recycle=1800,
        )
    return _engine


class TemplateNotFoundError(KeyError):
    pass


# The datatype/frequency Postgres enums (schema.sql) store the Python enum
# *names* from universal_data_extractor's models.py (e.g. "alphanumeric",
# "unique"), not the display values its Pydantic schemas serialise (e.g.
# "Alphanumeric", "Unique"). Reproduced here so this service's JSON output
# matches that project's API byte-for-byte instead of leaking the raw DB casing.
_DATA_TYPE_DISPLAY = {
    "alphabet": "Alphabet",
    "alphanumeric": "Alphanumeric",
    "numeric": "Numeric",
    "datetime": "Datetime",
    "boolean": "Boolean",
}
_FREQUENCY_DISPLAY = {
    "unique": "Unique",
    "multiple": "Multiple",
}


def _query_templates() -> dict:
    """Reads templates + template_attributes + attributes from Cloud SQL and
    groups them into {template_id: {id, name, description, group_name,
    llm_prompt, template_attributes: [{id, attribute_id, frequency,
    row_group, attribute: {id, name, description, data_type, example}}]}}
    -- the same shape universal_data_extractor's TemplateOut returns.
    """
    sql = sqlalchemy.text("""
        SELECT
            t.id AS template_id,
            t.name AS template_name,
            t.description AS template_description,
            t.group_name AS group_name,
            t.llm_prompt AS llm_prompt,
            ta.id AS template_attribute_id,
            ta.frequency AS frequency,
            ta.row_group AS row_group,
            a.id AS attribute_id,
            a.name AS attribute_name,
            a.description AS attribute_description,
            a.example AS example,
            a.data_type AS data_type
        FROM templates t
        JOIN template_attributes ta ON ta.template_id = t.id
        JOIN attributes a ON a.id = ta.attribute_id
        ORDER BY t.id, ta.id
    """)
    with _get_engine().connect() as conn:
        rows = conn.execute(sql).mappings().all()

    templates: dict = {}
    for row in rows:
        tmpl = templates.setdefault(
            row["template_id"],
            {
                "id": row["template_id"],
                "name": row["template_name"],
                "description": row["template_description"],
                "group_name": row["group_name"],
                "llm_prompt": row["llm_prompt"],
                "template_attributes": [],
            },
        )
        tmpl["template_attributes"].append({
            "id": row["template_attribute_id"],
            "attribute_id": row["attribute_id"],
            "frequency": _FREQUENCY_DISPLAY.get(row["frequency"], row["frequency"]),
            "row_group": row["row_group"],
            "attribute": {
                "id": row["attribute_id"],
                "name": row["attribute_name"],
                "description": row["attribute_description"],
                "data_type": _DATA_TYPE_DISPLAY.get(row["data_type"], row["data_type"]),
                "example": row["example"],
            },
        })

    return templates


def _load_all() -> dict:
    now = time.time()
    if _cache["data"] is None or (now - _cache["loaded_at"]) > _CACHE_TTL_SECONDS:
        _cache["data"] = _query_templates()
        _cache["loaded_at"] = now
    return _cache["data"]


def list_templates() -> list[dict]:
    """Full TemplateOut-shaped list for the /templates listing endpoint."""
    return list(_load_all().values())


def get_template(template_id: int) -> dict:
    templates = _load_all()
    if template_id not in templates:
        # A template created since the cache was loaded won't be in it yet --
        # force one fresh read before giving up.
        reload_config()
        templates = _load_all()
    if template_id not in templates:
        raise TemplateNotFoundError(
            f"Unknown template id {template_id}. "
            f"Available: {', '.join(str(k) for k in templates)}"
        )
    return templates[template_id]


def reload_config():
    """Clears the cache — useful in tests or to force a refresh sooner than
    the TTL (e.g. right after seeding/editing templates via the admin API)."""
    _cache["data"] = None
