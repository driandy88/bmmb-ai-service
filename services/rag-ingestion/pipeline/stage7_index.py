"""
Stage 7 · Index (brief §5, §7b).

Upserts embedded chunks into Cloud SQL `rag_chunks` (pgvector) keyed on chunk_id —
idempotent re-runs. Populates content_tsv for the keyword half of hybrid search.
Self-applies db/schema.sql (all statements are IF NOT EXISTS) so the table + HNSW/
GIN indexes exist before the first upsert.

    python cli.py stage7 --dry-run                 # report inserts/updates, write nothing
    python cli.py stage7 --doc mihp_i --supersede  # expire prior versions of this doc (§7b)

Prints a post-index report: chunks per corpus, per access_tier, and chunks expiring
(or already expired) so refreshes are scheduled, not discovered by a customer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from config.settings import Settings, get_settings

_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA = _ROOT / "db" / "schema.sql"

_UPSERT = """
INSERT INTO rag_chunks
  (chunk_id, corpus, program_code, section, doc_id, doc_title, source_uri, version,
   effective_date, expiry_date, content_type, access_tier, lang, content, content_tsv,
   embedding, needs_review, approved_by, approved_at, indexed_at)
VALUES
  (%(chunk_id)s, %(corpus)s, %(program_code)s, %(section)s, %(doc_id)s, %(doc_title)s,
   %(source_uri)s, %(version)s, %(effective_date)s, %(expiry_date)s, %(content_type)s,
   %(access_tier)s, %(lang)s, %(content)s, to_tsvector('english', %(content)s),
   %(embedding)s::vector, %(needs_review)s, %(approved_by)s, %(approved_at)s, now())
ON CONFLICT (chunk_id) DO UPDATE SET
  corpus=EXCLUDED.corpus, program_code=EXCLUDED.program_code, section=EXCLUDED.section,
  doc_id=EXCLUDED.doc_id, doc_title=EXCLUDED.doc_title, source_uri=EXCLUDED.source_uri,
  version=EXCLUDED.version, effective_date=EXCLUDED.effective_date, expiry_date=EXCLUDED.expiry_date,
  content_type=EXCLUDED.content_type, access_tier=EXCLUDED.access_tier, lang=EXCLUDED.lang,
  content=EXCLUDED.content, content_tsv=EXCLUDED.content_tsv, embedding=EXCLUDED.embedding,
  needs_review=EXCLUDED.needs_review, approved_by=EXCLUDED.approved_by,
  approved_at=EXCLUDED.approved_at, indexed_at=now()
"""


def _connect(s: Settings):
    import psycopg
    if not s.db_password:
        raise RuntimeError("DB_PASS not set — add the Cloud SQL password to .env.")
    return psycopg.connect(host=s.db_host, port=s.db_port, dbname=s.db_name,
                           user=s.db_user, password=s.db_password, connect_timeout=10)


def _apply_schema(conn) -> None:
    sql = "\n".join(re.sub(r"--.*$", "", ln) for ln in _SCHEMA.read_text().splitlines())
    for stmt in (s.strip() for s in sql.split(";")):
        if stmt:
            conn.execute(stmt)


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(map(str, vec)) + "]"


def _load_records(root: Path, corpus_filter, doc_filter) -> list[dict]:
    recs = []
    corpora = [corpus_filter] if corpus_filter else [p.name for p in root.iterdir() if p.is_dir()]
    for corpus in corpora:
        cdir = root / corpus
        if not cdir.exists():
            continue
        for jf in sorted(cdir.glob("*.jsonl")):
            if doc_filter and jf.stem != doc_filter:
                continue
            for line in jf.read_text().splitlines():
                if line.strip():
                    recs.append(json.loads(line))
    return recs


def _report(conn) -> None:
    print("[stage7_index] post-index report:")
    for label, q in [
        ("by corpus", "SELECT corpus, count(*) FROM rag_chunks GROUP BY corpus ORDER BY corpus"),
        ("by access_tier", "SELECT access_tier, count(*) FROM rag_chunks GROUP BY access_tier ORDER BY access_tier"),
        ("needs_review", "SELECT needs_review, count(*) FROM rag_chunks GROUP BY needs_review ORDER BY needs_review"),
    ]:
        rows = conn.execute(q).fetchall()
        print(f"  {label}: " + ", ".join(f"{a}={b}" for a, b in rows))
    exp = conn.execute(
        "SELECT count(*) FILTER (WHERE expiry_date < current_date), "
        "       count(*) FILTER (WHERE expiry_date >= current_date AND expiry_date <= current_date + 30) "
        "FROM rag_chunks").fetchone()
    print(f"  freshness: {exp[0]} already-expired, {exp[1]} expiring within 30 days")
    if exp[0]:
        stale = conn.execute(
            "SELECT DISTINCT doc_id, version, expiry_date FROM rag_chunks "
            "WHERE expiry_date < current_date ORDER BY doc_id").fetchall()
        for doc_id, ver, expd in stale:
            print(f"    EXPIRED: {doc_id} v{ver} (expired {expd}) — will not be retrieved; refresh or confirm with BMMB")


def run(args) -> int:
    settings = get_settings()
    embedded_root = _ROOT / settings.data_dir / "06_embedded"
    if not embedded_root.exists():
        print("[stage7_index] no embeddings — run stage6 first.")
        return 1

    recs = _load_records(embedded_root, getattr(args, "corpus", None), args.doc)
    if not recs:
        print("[stage7_index] no records match the filter.")
        return 1
    dry = getattr(args, "dry_run", False)

    try:
        conn = _connect(settings)
    except Exception as e:
        print(f"[stage7_index] DB connection failed: {type(e).__name__}: {e}")
        return 1

    with conn:
        _apply_schema(conn)
        ids = [r["chunk_id"] for r in recs]
        existing = {r[0] for r in conn.execute(
            "SELECT chunk_id FROM rag_chunks WHERE chunk_id = ANY(%s)", (ids,)).fetchall()}
        inserts = sum(1 for i in ids if i not in existing)
        updates = len(ids) - inserts

        if dry:
            print(f"[stage7_index] DRY-RUN: {len(recs)} chunks → {inserts} insert, {updates} update "
                  f"(db={settings.db_name}). No writes.")
            conn.rollback()
            return 0

        if getattr(args, "supersede", False):
            superseded = 0
            for doc_id, version, eff in {(r["doc_id"], r["version"], r["effective_date"]) for r in recs}:
                if not eff:
                    continue
                cur = conn.execute(
                    "UPDATE rag_chunks SET expiry_date = %s "
                    "WHERE doc_id = %s AND version <> %s AND (expiry_date IS NULL OR expiry_date > %s)",
                    (eff, doc_id, version, eff))
                superseded += cur.rowcount
            print(f"[stage7_index] supersede: expired {superseded} prior-version chunks.")

        for r in recs:
            conn.execute(_UPSERT, {**r, "embedding": _vec_literal(r["embedding"])})
        conn.commit()
        flagged = sum(1 for r in recs if r.get("needs_review"))
        excl = (f" · {flagged} flagged (excluded from customer retrieval) · see review.html"
                if flagged else "")
        print(f"[stage7_index] indexed {len(recs)} chunks → {inserts} inserted, "
              f"{updates} updated (db={settings.db_name}){excl}.")
        _report(conn)
    return 0
