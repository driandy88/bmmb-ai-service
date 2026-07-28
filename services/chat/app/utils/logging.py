"""
Structured logging helper.

Thin wrapper over the stdlib logger with one rule baked in: callers pass
already-redacted values. Raw prompts / customer data must be run through
`utils.pii.redact` BEFORE they reach any log call (brief §2.5, §12). This
module deliberately offers no "log the raw request" convenience so that rule
is hard to break by accident.
"""
from __future__ import annotations

import logging
import os

_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"chat.{name}")
