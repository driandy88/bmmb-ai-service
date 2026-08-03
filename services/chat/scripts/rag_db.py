"""Apply / inspect the RAG schema in the `bmmb` database (docs/RAG_PLAN.md phase 0).

    python scripts/rag_db.py render     # print the rendered DDL, touch nothing
    python scripts/rag_db.py apply      # create extension, tables, indexes (idempotent)
    python scripts/rag_db.py inspect    # show what's actually there + row counts
    python scripts/rag_db.py drop --yes # DESTRUCTIVE: drop both RAG tables

`render` needs no credentials. The other three read connection settings from
.env (INSTANCE_CONNECTION_NAME / DB_USER / DB_PASS, or RAG_DB_URL).

Targets RAG_DB_NAME (default `bmmb`) — the shared database, not bmmb_dev. The
extraction service's tables live there too; everything created here is
`rag_`-prefixed and `apply` never drops anything.
"""
import argparse
import sys
from pathlib import Path

_CHAT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CHAT_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_CHAT_DIR / ".env")

import sqlalchemy  # noqa: E402

from app.agents.rag import db  # noqa: E402
from app.config.settings import get_settings  # noqa: E402

RAG_TABLES = ("rag_chunks", "rag_documents")  # child first — FK order for DROP


def cmd_render(_args) -> int:
    print(db.render_schema())
    return 0


def cmd_apply(_args) -> int:
    settings = get_settings()
    sql = db.render_schema(settings)
    engine = db.get_engine(settings)
    print(f"Applying RAG schema to db={settings.rag_db_name} "
          f"(embedding dim={settings.rag_embedding_dim}, fts={settings.rag_fts_config})")
    with engine.begin() as conn:
        for stmt in db.split_statements(sql):
            print(f"  → {' '.join(stmt.split())[:88]}")
            conn.execute(sqlalchemy.text(stmt))
    print("Done. Run `inspect` to verify.")
    return 0


def cmd_inspect(_args) -> int:
    settings = get_settings()
    engine = db.get_engine(settings)
    with engine.connect() as conn:
        ext = conn.execute(sqlalchemy.text(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        )).scalar()
        print(f"db          : {settings.rag_db_name}")
        print(f"pgvector    : {ext or 'NOT INSTALLED'}")

        for table in reversed(RAG_TABLES):
            exists = conn.execute(sqlalchemy.text("SELECT to_regclass(:t)"), {"t": table}).scalar()
            if not exists:
                print(f"{table:12}: missing")
                continue
            count = conn.execute(sqlalchemy.text(f"SELECT count(*) FROM {table}")).scalar()
            print(f"{table:12}: {count} rows")

        if conn.execute(sqlalchemy.text("SELECT to_regclass('rag_chunks')")).scalar():
            dim = conn.execute(sqlalchemy.text("""
                SELECT format_type(a.atttypid, a.atttypmod)
                  FROM pg_attribute a
                 WHERE a.attrelid = 'rag_chunks'::regclass AND a.attname = 'embedding'
            """)).scalar()
            print(f"  embedding column: {dim} (settings say {settings.rag_embedding_dim})")
            rows = conn.execute(sqlalchemy.text("""
                SELECT corpus, count(*) AS chunks, count(embedding) AS embedded,
                       count(DISTINCT document_id) AS docs
                  FROM rag_chunks GROUP BY corpus ORDER BY corpus
            """)).all()
            for r in rows:
                print(f"  {r.corpus:22} {r.docs} docs, {r.chunks} chunks, {r.embedded} embedded")
    return 0


def cmd_drop(args) -> int:
    if not args.yes:
        print("Refusing to drop without --yes. This deletes ALL ingested chunks.")
        return 1
    settings = get_settings()
    engine = db.get_engine(settings)
    print(f"Dropping {', '.join(RAG_TABLES)} from db={settings.rag_db_name}")
    with engine.begin() as conn:
        for table in RAG_TABLES:
            conn.execute(sqlalchemy.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    print("Dropped.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("render", help="print the rendered DDL (no credentials needed)")
    sub.add_parser("apply", help="create the RAG tables/indexes (idempotent)")
    sub.add_parser("inspect", help="show what exists and how much is in it")
    drop = sub.add_parser("drop", help="DESTRUCTIVE: drop the RAG tables")
    drop.add_argument("--yes", action="store_true", help="confirm the drop")

    args = parser.parse_args()
    handler = {"render": cmd_render, "apply": cmd_apply,
               "inspect": cmd_inspect, "drop": cmd_drop}[args.command]
    try:
        return handler(args)
    except db.RagDbNotConfigured as exc:
        # Missing config is a setup problem, not a bug — say so without a traceback.
        print(f"Not configured: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
