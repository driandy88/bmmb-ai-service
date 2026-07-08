"""
Puts the repo root on sys.path so tests can `from services.bbox_generator...
import` regardless of the directory pytest is invoked from — same convention
already used by services/validation/tests/conftest.py.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
