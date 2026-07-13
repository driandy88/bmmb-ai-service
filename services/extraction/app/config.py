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


class AttributeNotFoundError(KeyError):
    pass


class NameAlreadyExistsError(ValueError):
    pass


class AttributeInUseError(ValueError):
    """Raised when deleting an attribute still referenced by one or more templates."""
    def __init__(self, message: str, template_names: list[str]):
        super().__init__(message)
        self.template_names = template_names


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
_DATA_TYPE_DB = {v: k for k, v in _DATA_TYPE_DISPLAY.items()}
_FREQUENCY_DB = {v: k for k, v in _FREQUENCY_DISPLAY.items()}


def _query_templates() -> dict:
    """Reads templates + template_attributes + attributes from Cloud SQL and
    groups them into {template_id: {id, name, description, group_name,
    llm_prompt, template_attributes: [{id, attribute_id, frequency,
    row_group, attribute: {id, name, description, data_type, example}}]}}
    -- the same shape universal_data_extractor's TemplateOut returns.

    LEFT JOINs (not inner) because a template can have zero attributes --
    right after creation via POST /templates/, or if all of them are later
    removed via PUT -- and must still show up with template_attributes: [].
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
        LEFT JOIN template_attributes ta ON ta.template_id = t.id
        LEFT JOIN attributes a ON a.id = ta.attribute_id
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
        if row["template_attribute_id"] is None:
            continue  # template has no attributes -- LEFT JOIN produced an all-NULL row
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


# ── Attributes: read + write ────────────────────────────────────────────────

def list_attributes() -> list[dict]:
    sql = sqlalchemy.text("SELECT id, name, description, data_type, example FROM attributes ORDER BY id")
    with _get_engine().connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "data_type": _DATA_TYPE_DISPLAY.get(row["data_type"], row["data_type"]),
            "example": row["example"],
        }
        for row in rows
    ]


def get_attribute(attribute_id: int) -> dict:
    for attr in list_attributes():
        if attr["id"] == attribute_id:
            return attr
    raise AttributeNotFoundError(f"Unknown attribute id {attribute_id}.")


def create_attribute(name: str, description: str | None, data_type: str, example: str | None) -> dict:
    with _get_engine().begin() as conn:
        exists = conn.execute(
            sqlalchemy.text("SELECT 1 FROM attributes WHERE name = :name"), {"name": name}
        ).first()
        if exists:
            raise NameAlreadyExistsError(f"Attribute name {name!r} already exists.")
        row = conn.execute(
            sqlalchemy.text("""
                INSERT INTO attributes (name, description, data_type, example)
                VALUES (:name, :description, :data_type, :example)
                RETURNING id
            """),
            {
                "name": name,
                "description": description,
                "data_type": _DATA_TYPE_DB.get(data_type, data_type),
                "example": example,
            },
        ).mappings().first()
    return get_attribute(row["id"])


def update_attribute(attribute_id: int, fields: dict) -> dict:
    """`fields` holds only the keys the caller actually supplied (name,
    description, data_type, example) -- partial update, matching
    AttributeUpdate's exclude_unset semantics."""
    get_attribute(attribute_id)  # raises AttributeNotFoundError if missing
    if not fields:
        return get_attribute(attribute_id)

    updates = dict(fields)
    if "data_type" in updates:
        updates["data_type"] = _DATA_TYPE_DB.get(updates["data_type"], updates["data_type"])

    set_clause = ", ".join(f"{col} = :{col}" for col in updates)
    with _get_engine().begin() as conn:
        if "name" in updates:
            clash = conn.execute(
                sqlalchemy.text("SELECT 1 FROM attributes WHERE name = :name AND id != :id"),
                {"name": updates["name"], "id": attribute_id},
            ).first()
            if clash:
                raise NameAlreadyExistsError(f"Attribute name {updates['name']!r} already exists.")
        conn.execute(
            sqlalchemy.text(f"UPDATE attributes SET {set_clause} WHERE id = :id"),
            {**updates, "id": attribute_id},
        )
    return get_attribute(attribute_id)


def delete_attribute(attribute_id: int) -> None:
    get_attribute(attribute_id)  # raises AttributeNotFoundError if missing
    with _get_engine().begin() as conn:
        used_in = conn.execute(
            sqlalchemy.text("""
                SELECT DISTINCT t.name FROM templates t
                JOIN template_attributes ta ON ta.template_id = t.id
                WHERE ta.attribute_id = :id
            """),
            {"id": attribute_id},
        ).scalars().all()
        if used_in:
            raise AttributeInUseError(
                f"Attribute id {attribute_id} is still used by template(s): {', '.join(used_in)}.",
                list(used_in),
            )
        conn.execute(sqlalchemy.text("DELETE FROM attributes WHERE id = :id"), {"id": attribute_id})


# ── Templates: write ─────────────────────────────────────────────────────────

def _sync_template_attributes(conn, template_id: int, attribute_entries: list[dict]) -> None:
    conn.execute(
        sqlalchemy.text("DELETE FROM template_attributes WHERE template_id = :id"),
        {"id": template_id},
    )
    for entry in attribute_entries:
        exists = conn.execute(
            sqlalchemy.text("SELECT 1 FROM attributes WHERE id = :id"),
            {"id": entry["attribute_id"]},
        ).first()
        if not exists:
            raise AttributeNotFoundError(f"Unknown attribute id {entry['attribute_id']}.")
        conn.execute(
            sqlalchemy.text("""
                INSERT INTO template_attributes (template_id, attribute_id, frequency, row_group)
                VALUES (:template_id, :attribute_id, :frequency, :row_group)
            """),
            {
                "template_id": template_id,
                "attribute_id": entry["attribute_id"],
                "frequency": _FREQUENCY_DB.get(entry.get("frequency", "Unique"), "unique"),
                "row_group": entry.get("row_group"),
            },
        )


def create_template(
    name: str,
    description: str | None,
    group_name: str | None,
    llm_prompt: str | None,
    attributes: list[dict],
) -> dict:
    with _get_engine().begin() as conn:
        exists = conn.execute(
            sqlalchemy.text("SELECT 1 FROM templates WHERE name = :name"), {"name": name}
        ).first()
        if exists:
            raise NameAlreadyExistsError(f"Template name {name!r} already exists.")
        row = conn.execute(
            sqlalchemy.text("""
                INSERT INTO templates (name, description, group_name, llm_prompt)
                VALUES (:name, :description, :group_name, :llm_prompt)
                RETURNING id
            """),
            {"name": name, "description": description, "group_name": group_name, "llm_prompt": llm_prompt},
        ).mappings().first()
        template_id = row["id"]
        _sync_template_attributes(conn, template_id, attributes)
    reload_config()
    return get_template(template_id)


def update_template(template_id: int, fields: dict, attributes: list[dict] | None) -> dict:
    """`fields` holds only the keys the caller actually supplied (name,
    description, group_name, llm_prompt) -- partial update. `attributes`,
    if not None, fully replaces the template's attribute wiring."""
    get_template(template_id)  # raises TemplateNotFoundError if missing

    with _get_engine().begin() as conn:
        if fields:
            if "name" in fields:
                clash = conn.execute(
                    sqlalchemy.text("SELECT 1 FROM templates WHERE name = :name AND id != :id"),
                    {"name": fields["name"], "id": template_id},
                ).first()
                if clash:
                    raise NameAlreadyExistsError(f"Template name {fields['name']!r} already exists.")
            set_clause = ", ".join(f"{col} = :{col}" for col in fields)
            conn.execute(
                sqlalchemy.text(f"UPDATE templates SET {set_clause} WHERE id = :id"),
                {**fields, "id": template_id},
            )
        if attributes is not None:
            _sync_template_attributes(conn, template_id, attributes)
    reload_config()
    return get_template(template_id)


def delete_template(template_id: int) -> None:
    get_template(template_id)  # raises TemplateNotFoundError if missing
    with _get_engine().begin() as conn:
        conn.execute(sqlalchemy.text("DELETE FROM template_attributes WHERE template_id = :id"), {"id": template_id})
        conn.execute(sqlalchemy.text("DELETE FROM templates WHERE id = :id"), {"id": template_id})
    reload_config()
