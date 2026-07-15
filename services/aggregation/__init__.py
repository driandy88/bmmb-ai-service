"""
Aggregation / normalization service.

Deterministic post-extraction transforms — no LLM, no GCP, no database. Turns
the raw, per-document output of services.extraction into the canonical,
aggregated dataset the frontend and services.validation consume.

Module map:
  bank.py     -- daily transaction rows -> monthly totals -> yearly averages,
                 per (bank, account), with a balance-continuity integrity check.
  schemas.py  -- pydantic request/response models for each handler.
  api.py      -- FastAPI app/router: /health, /aggregate/bank.

Handlers for the other document types that need normalization (financial
statements: pick reporting year + derive EBITDA/etc.; SSM 24/44/49/9&28: merge
into one entity/director/shareholder set) will land here as sibling modules —
see the repo's data-fields spec. Document types that need no transform never
reach this service; the backend only routes the ones that do.
"""
