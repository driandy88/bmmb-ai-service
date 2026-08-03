"""Test config — make `config` and `pipeline` importable when pytest runs from
the service root (services/rag-ingestion/), matching how cli.py is invoked."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
