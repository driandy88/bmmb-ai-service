"""Test config — force the deterministic offline backends and make `app`
importable when pytest is run from the service root."""
import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("LLM_BACKEND", "stub")
os.environ.setdefault("RAG_BACKEND", "stub")
os.environ.setdefault("AUDIT_BACKEND", "memory")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
