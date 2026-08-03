"""Test config — force the deterministic offline backends and make `app`
importable when pytest is run from the service root."""
import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("LLM_BACKEND", "stub")
os.environ.setdefault("RAG_BACKEND", "stub")
os.environ.setdefault("AUDIT_BACKEND", "memory")

# Pin the RAG knobs too. `app.main` calls load_dotenv() at import time, so once
# any test imports it the developer's own .env leaks into every Settings()
# built afterwards — which made results depend on test order and on whose
# machine the suite ran. Setting these first wins: load_dotenv() does not
# override variables that are already present.
os.environ.setdefault("RAG_EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("RAG_EMBEDDING_MODEL", "")
os.environ.setdefault("RAG_EMBEDDING_DIM", "1024")
os.environ.setdefault("RAG_FTS_CONFIG", "simple")
os.environ.setdefault("RAG_CHUNK_TOKENS", "400")
os.environ.setdefault("RAG_CHUNK_OVERLAP_TOKENS", "60")
os.environ.setdefault("RAG_CHUNK_MIN_TOKENS", "40")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
