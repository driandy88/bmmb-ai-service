"""
Minimal shared-secret guard for the write endpoints (create/update/delete
templates and attributes). Read endpoints (GET /templates, GET /attributes)
and /extract stay fully public, matching the service's existing behaviour --
only the newly-added mutating routes are gated behind this.

Checked lazily inside the dependency (not at import time like config.py's
DB_* vars) so a deployment without ADMIN_API_KEY set still serves reads and
extraction; it just can't accept writes until the key is configured.
"""
import os
import secrets

from fastapi import Header, HTTPException, status

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY")


def require_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    if not ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Service misconfigured: ADMIN_API_KEY is not set.",
        )
    if not x_admin_key or not secrets.compare_digest(x_admin_key, ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Admin-Key header.",
        )
