# BMMB AI Service — Aggregation / Normalization

The **middle stage** of the pipeline:

```
extraction (LLM, per document)  →  AGGREGATION (this service, deterministic Python)  →  validation (rules + optional LLM)
```

Extraction transcribes what is *printed* in each document. This service does the
**maths and merging that spans documents** — pure arithmetic, no LLM, no GCP, no
database — so results are exact, reproducible and unit-testable. The backend
calls it **once per application** with the raw extraction bundle and gets back
the canonical, aggregated dataset the frontend and validation consume.

Only document types that need a transform reach this service; the rest pass
through the backend untouched.

## Handlers

| Endpoint | Status | Does |
|---|---|---|
| `POST /aggregate/bank` | **implemented** | daily transaction rows → monthly totals → yearly averages, per (bank, account), + a balance-continuity integrity check |
| financial-statement normalization | planned | pick reporting year(s), derive EBITDA / gross profit / D&A from printed components |
| SSM merge (Form 24 / 44 / 49 / 9&28) | planned | merge into one entity + director + shareholder set, deduped across MyKad / Consent / CIF |

### `POST /aggregate/bank`

Request body is the raw extraction of one or more bank-statement documents
(see `examples/bank_extraction_example.json`):

```json
{ "documents": [ { "source_document": "...", "bank_name": "...",
                   "account_number_masked": "...",
                   "transactions": [ {"date": "2026-01-10", "credit": 12000, "balance": 12000}, ... ] } ] }
```

Response groups by `(bank_name, account_number_masked)` and returns `monthly[]`,
`yearly[]` (with `avg_monthly_deposit` / `avg_monthly_withdrawal` /
`avg_monthly_end_balance`), the `source_documents` each account drew from, and
`integrity_warnings[]`. A warning is raised whenever a running balance doesn't
equal `previous − debit + credit`, which flags a transaction row extraction
probably missed or misread — so a wrong figure surfaces instead of silently
skewing an average.

> **Why daily, not a "monthly summary"?** Real Malaysian statements (e.g.
> Standard Chartered) often have **no** monthly summary block — only daily
> transactions. Extracting the summary block returns nulls on those. So
> extraction pulls the daily rows and this service sums them. See the notebook
> `services/extraction/manual_extraction_test.ipynb` §6 for the head-to-head.

## Run it

```bash
# from the repo root -- imports are `services.aggregation.*`, so run from the root
python3 -m venv .venv && source .venv/bin/activate
pip install -r services/aggregation/requirements-dev.txt

uvicorn services.aggregation.api:app --reload
# open http://localhost:8000/docs, or:
curl -X POST http://localhost:8000/aggregate/bank \
  -H "Content-Type: application/json" \
  -d @services/aggregation/examples/bank_extraction_example.json
```

## Testing

```bash
# from the repo root
pytest services/aggregation/tests/ -v
```

No credentials or network access required — the service is pure Python, so the
tests (including the FastAPI `TestClient` ones) run fully offline. Same in CI.

## Deployment (Cloud Run)

`.github/workflows/deploy-aggregation.yml`, path-filtered to
`services/aggregation/**`: every PR runs `pytest`; every push to `main` runs
tests, then builds `services/aggregation/Dockerfile` and deploys to Cloud Run as
`aggregation-service`, reusing the shared `bmmb-ai-service` Artifact Registry
repo and the same `GCP_PROJECT_ID` / `GCP_SA_KEY` secrets as the other services.

**One-time GCP setup** — a dedicated runtime service account **with zero role
bindings** (this service calls no Google APIs, so least-privilege is literally
"no roles"):

```bash
export PROJECT_ID="prototype-bmmb-1b62"
gcloud iam service-accounts create aggregation-service-sa \
  --display-name="Aggregation Service runtime" --project=$PROJECT_ID
```

`github-deployer` already has the project-level roles needed to build and deploy
this service (see the extraction README's "Deployment" section) — no new secrets.

## Structure

```
services/aggregation/
├── api.py         # FastAPI app/router: /health, /aggregate/bank
├── bank.py        # aggregate_bank() -- the deterministic daily->monthly->yearly logic
├── schemas.py     # pydantic request/response models
├── examples/      # sample request body
├── tests/         # pytest -- aggregation maths, multi-doc pooling, continuity check, API
├── Dockerfile
├── requirements.txt
└── requirements-dev.txt
```
