"""
LangGraph checkpointer (brief §3) — optional durability / resume-across-handoff.

Memory of record is the client-supplied context (§5.1); this checkpointer is a
durability + resume affordance, not the conversational memory. It's kept behind
a factory so the in-memory saver used now can be swapped for a Cloud SQL
(Postgres) saver later with no orchestrator change.
"""
from __future__ import annotations

from typing import Optional

from langgraph.checkpoint.memory import MemorySaver

from app.config.settings import Settings, get_settings
from app.utils.logging import get_logger

log = get_logger("session_store")


def get_checkpointer(settings: Optional[Settings] = None):
    """Return a LangGraph checkpointer, or None for a fully stateless service.

      none    (default) -> None. Nothing is retained between requests; memory is
                           entirely client-supplied (§5.1). Recommended default.
      memory            -> in-process MemorySaver (state kept in RAM per
                           session_id; lost on restart; never written anywhere).
      postgres          -> TODO (Cloud SQL durability); falls back to MemorySaver.
    """
    settings = settings or get_settings()
    backend = settings.session_store_backend
    if backend == "none":
        return None
    if backend == "postgres":
        # TODO: build langgraph PostgresSaver against Cloud SQL (see settings DB_*).
        log.warning("SESSION_STORE_BACKEND=postgres not yet wired; using in-memory checkpointer.")
    return MemorySaver()
