"""
Immutable audit trail (brief §2.4, §7) — regulatory evidence (BNM RMiT).

Every turn writes ONE append-only record: trace_id, route, rule_version, the
guardrail verdict, PII-redacted decision inputs, and a timestamp. This is
treated as evidence: the writer never mutates or deletes, and it re-redacts
every record defensively (PII discipline, §2.5) so raw customer data can't leak
into the audit sink even if a caller slips.

InMemoryAuditWriter ships now (acceptable until GCP wiring, §11). The Cloud SQL
writer is a placeholder that appends to memory and warns, so audit never breaks a
turn before the append-only table is provisioned.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.config.settings import Settings, get_settings
from app.utils import pii
from app.utils.logging import get_logger

log = get_logger("audit")


class AuditWriter(ABC):
    @abstractmethod
    def write(self, record: dict) -> None:
        """Append one immutable, PII-redacted audit record."""

    def records(self) -> list[dict]:
        """Best-effort read-back (in-memory writers only) for tests/notebook."""
        return []


class InMemoryAuditWriter(AuditWriter):
    def __init__(self) -> None:
        self._records: list[dict] = []

    def write(self, record: dict) -> None:
        self._records.append(pii.redact_obj(dict(record)))   # redact defensively; append-only

    def records(self) -> list[dict]:
        return list(self._records)


class NullAuditWriter(AuditWriter):
    """Retains NOTHING — for running the service with zero data retention.
    NOTE: this drops the immutable audit trail the brief requires for BNM RMiT,
    so it's opt-in, not the default."""

    def write(self, record: dict) -> None:
        pass


class CloudSQLAuditWriter(InMemoryAuditWriter):
    """PLACEHOLDER — TODO: INSERT into an append-only Cloud SQL table (no UPDATE/
    DELETE grants on it). Until provisioned, behaves as in-memory + warns once."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        log.warning("AUDIT_BACKEND=cloudsql not yet wired; using in-memory audit (append-only).")


def get_audit_writer(settings: Optional[Settings] = None) -> AuditWriter:
    settings = settings or get_settings()
    backend = settings.audit_backend
    if backend == "none":
        return NullAuditWriter()          # zero retention (drops the compliance trail)
    if backend == "cloudsql":
        return CloudSQLAuditWriter(settings)
    return InMemoryAuditWriter()          # default: redacted records held in RAM only
