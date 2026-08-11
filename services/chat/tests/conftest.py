"""Test config — force the deterministic offline backends, and put the repo
root on sys.path so tests can `from services.chat... import` regardless of the
directory pytest is invoked from — same convention as
services/validation/tests/conftest.py."""
import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("LLM_BACKEND", "stub")
os.environ.setdefault("RAG_BACKEND", "stub")
os.environ.setdefault("AUDIT_BACKEND", "memory")

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
