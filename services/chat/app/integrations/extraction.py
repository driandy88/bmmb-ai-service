"""
Extraction service client — document → structured fields (brief §4.4 / §7).

The chat agent does NOT parse documents itself. When a customer uploads an SME
document, the agent calls the separate `extraction` microservice
(services/extraction, `POST /extract`) and maps a few Tier-1 figures out of the
result (see agents/eligibility/document_map.py). Only Tier-1 figures are read;
Tier-2 fields in the same document are never touched (boundary stays intact).

Offline-first, same shape as the LLM / RAG backends:
  EXTRACTION_BACKEND=stub  → deterministic canned fields (no network) — the seam
                             the notebook and tests run against.
  EXTRACTION_BACKEND=http  → POST multipart to EXTRACTION_SERVICE_URL/extract.

`extract()` returns the extraction service's `data.extracted_data` object — the
raw per-template shape (scalars for SSM/customer-info, arrays for multi-year
AFS / multi-month bank statements). The mapper locates arrays by content, so it
does not depend on the DB's row_group key names.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from ..config.settings import Settings, get_settings

log = logging.getLogger(__name__)

# (filename, content_type, bytes)
FilePart = tuple[str, str, bytes]


class ExtractionClient(ABC):
    @abstractmethod
    def extract(self, template_id: str, files: list[FilePart]) -> dict:
        """Return the extraction service's `data.extracted_data` object."""


class StubExtractionClient(ExtractionClient):
    """Deterministic output shaped like a real /extract response, so the
    document-upload path is exercisable with no extraction service or GCP. The
    multi-period groups are deliberately keyed like the config reference; the
    mapper finds them by content regardless of key name."""

    _CANNED: dict[str, dict] = {
        "business_registration_ssm": {
            "document_type": "Certificate of Incorporation",
            "business_name": "Prisma Niaga Sdn. Bhd.",
            "business_registration_number": "202301045678 (1534291-W)",
            "incorporation_date": "15 Mac / March 2018",
        },
        "customer_information_details": {
            "names": ["Ahmad Faizal bin Mohd Noor"],
            "email": "faizal@prismaniaga.com.my",
            "phone_number": "03-2698 4521",
            "financing_request_volume": 300000.0,
        },
        "audited_financial_statements": {
            "audited_financial_statements": [
                {"financial_year": "FY2022", "revenue_turnover_gross_profit": 3_910_000.0,
                 "total_equity": 812_300.0, "net_worth": 812_300.0, "ebitda_net_profit": 610_000.0},
                {"financial_year": "FY2023", "revenue_turnover_gross_profit": 4_820_500.0,
                 "total_equity": 1_060_425.0, "net_worth": 1_060_425.0, "ebitda_net_profit": 803_800.0},
            ],
        },
        "bank_statements": {
            "bank_statements": [
                {"month": "June 2023", "monthly_end_balance": 210_400.0},
                {"month": "July 2023", "monthly_end_balance": 266_750.0},
            ],
        },
    }

    def extract(self, template_id: str, files: list[FilePart]) -> dict:
        return dict(self._CANNED.get(template_id, {}))


class HttpExtractionClient(ExtractionClient):
    """POSTs the uploaded files to the extraction service's /extract endpoint."""

    def __init__(self, settings: Settings):
        self._url = (settings.extraction_service_url or "").rstrip("/")
        self._model = settings.model_id

    def extract(self, template_id: str, files: list[FilePart]) -> dict:
        import httpx  # lazy — only needed on the http path

        resp = httpx.post(
            f"{self._url}/extract",
            data={"template_id": template_id, "model": self._model},
            files=[("files", (fn, data, ct)) for fn, ct, data in files],
            timeout=180.0,
        )
        resp.raise_for_status()
        return (resp.json().get("data") or {}).get("extracted_data", {}) or {}


def get_extraction_client(settings: Optional[Settings] = None) -> ExtractionClient:
    settings = settings or get_settings()
    if settings.extraction_backend == "http":
        if not settings.extraction_service_url:
            log.warning("EXTRACTION_BACKEND=http but EXTRACTION_SERVICE_URL missing → using stub.")
            return StubExtractionClient()
        return HttpExtractionClient(settings)
    return StubExtractionClient()
