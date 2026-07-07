# BMMB AI Service — Extraction

Repo: [`driandy88/bmmb-ai-service`](https://github.com/driandy88/bmmb-ai-service) · GCP project: `prototype-bmmb-1b62`

Standalone document extraction API. Upload a PDF or image, pick a template, get
back structured JSON — powered by Gemini structured outputs. No database, no
CRUD UI: templates are defined declaratively in `app/templates_config.json`.

This is a deliberately narrow extraction of the original Universal Data
Extractor project (Phase 3 of `dev_plans.md`) — the CRUD-based
Attribute/Template management UI is out of scope here. If you need to define
a new template, edit `app/templates_config.json` directly (see below).

> This repo currently holds only the extraction service. The judgement-RAG and
> memo/LO services are planned to land here later — see the note at the
> bottom of this section on restructuring into `services/extraction/` before
> that happens.

---

## Project structure

```
bmmb-ai-service/            # currently flat — see restructuring note below
├── app/
│   ├── main.py               # FastAPI app entrypoint
│   ├── extraction.py         # the /extract, /templates, /health routes
│   ├── config.py             # loads + normalises templates_config.json
│   ├── schema_builder.py     # config -> Gemini response_schema + prompt
│   ├── gemini_client.py      # thin wrapper around the google-genai SDK call
│   ├── schemas.py            # Pydantic response models
│   └── templates_config.json # the 6 SME-financing document templates
├── tests/
│   ├── test_schema_builder.py
│   └── test_api.py           # FastAPI TestClient, Gemini call mocked
├── notebooks/
│   ├── test_extraction.ipynb # manual smoke test — PDF + image
│   └── make_sample_docs.py   # generates the two synthetic sample docs
├── sample_docs/               # synthetic PDF + PNG for testing (not real documents)
├── .github/workflows/deploy.yml
├── Dockerfile
├── requirements.txt           # runtime deps only
└── requirements-dev.txt       # + pytest, notebook, sample-doc generation
```

**Restructuring for the next two services:** once `judgement-rag` and
`memo-lo` are ready to start, the recommended layout is:

```
bmmb-ai-service/
├── services/
│   ├── extraction/    # everything currently at repo root moves here
│   ├── judgement-rag/
│   └── memo-lo/
├── libs/bmmb_common/   # shared audit logging, schemas, GCP clients
└── .github/workflows/  # one workflow per service, path-filtered
```

This keeps each service's Dockerfile/tests/deploy independent while sharing
common code once, so a schema or audit-logging change can't be updated in one
service's copy and forgotten in another's. Worth doing this move **before**
`judgement-rag` lands, not after — it's a bigger diff once there are two
services to untangle from a flat layout.

---

## Prerequisites

- Python 3.12+
- A [Gemini API key](https://aistudio.google.com/app/apikey)
- Docker (only needed for local container testing / deployment)

---

## Local setup

```bash
git clone https://github.com/driandy88/bmmb-ai-service.git
cd bmmb-ai-service

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements-dev.txt   # includes runtime deps + test/notebook tools
cp .env.example .env                  # optional — only used by the notebook
```

Run the API:

```bash
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for the interactive Swagger UI.

---

## Using the API

The Gemini API key is passed **per request**, not stored server-side — the
service never persists your key.

```bash
curl -X POST http://localhost:8000/extract \
  -F "template=business_registration_ssm" \
  -F "api_key=YOUR_GEMINI_API_KEY" \
  -F "file=@sample_docs/sample_ssm_certificate.pdf;type=application/pdf"
```

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Liveness check |
| `/templates` | GET | List all templates (key, kind, field count) |
| `/templates/{key}` | GET | Full field definitions for one template |
| `/extract` | POST | `template`, `api_key`, `file` (form-data) → extracted JSON |

Supported file types: PDF, JPEG, PNG, WEBP, HEIC, HEIF. Max 20 MB.

Every template is either:
- **`kind: single`** — one JSON object per document (e.g. `business_registration_ssm`)
- **`kind: array`** — a JSON array of objects (e.g. `bank_statements`, one entry per month; `ic_photocopies`, one entry per director)

---

## Testing

```bash
pytest tests/ -v
```

Tests never call the real Gemini API — `app.extraction.run_extraction` is
monkeypatched in `tests/test_api.py`, so CI runs with no API key and no
network access.

**Manual end-to-end test (real Gemini call, both file types):**

```bash
python notebooks/make_sample_docs.py     # generates sample_docs/*.pdf and *.png
jupyter notebook notebooks/test_extraction.ipynb
```

Paste your API key into the second cell and run all — it exercises the PDF
path (`business_registration_ssm`) and the image path (`ic_photocopies`)
against the two synthetic sample documents included in the repo.

---

## Adding or changing a template

Templates live entirely in `app/templates_config.json` — no code change
needed for a new field or template, only for a new field **type**.

1. Add a new top-level key with either a `fields` object (single-object
   template) or a `<something>_object_fields` object (array template).
2. Each field needs `field_name`, `description`, `example`, `data_type`
   (`string`, `float`, `date`, or `list[string]`).
3. Run `pytest tests/test_schema_builder.py` — add a case if it's a new
   template you want permanently covered.

To add a new `data_type`, extend `_TYPE_MAP` in `app/schema_builder.py`.

---

## Deployment (Cloud Run)

### One-time GCP setup

```bash
export PROJECT_ID="prototype-bmmb-1b62"
export REGION="asia-southeast1"

gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  --project=$PROJECT_ID

# Artifact Registry repo for Docker images — shared across all services in this
# monorepo (extraction-service now; judgement-rag-service, memo-lo-service later),
# so it's named after the repo, not the individual service.
gcloud artifacts repositories create bmmb-ai-service \
  --repository-format=docker --location=$REGION --project=$PROJECT_ID

# Dedicated service account for the running service (least privilege —
# this API doesn't touch Cloud SQL or GCS, so it needs no extra roles)
gcloud iam service-accounts create extraction-service-sa \
  --display-name="Extraction Service runtime" --project=$PROJECT_ID

# Service account for GitHub Actions to deploy with
gcloud iam service-accounts create github-deployer \
  --display-name="GitHub Actions deployer" --project=$PROJECT_ID

for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:github-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="$role"
done

# Key for the deployer account -- see the security note below before using this
gcloud iam service-accounts keys create github-deployer-key.json \
  --iam-account=github-deployer@${PROJECT_ID}.iam.gserviceaccount.com
```

> **Security note:** a downloaded JSON key is a long-lived credential — treat
> `github-deployer-key.json` like a password (never commit it) and rotate it
> periodically. **Workload Identity Federation** removes the key entirely by
> letting GitHub Actions authenticate directly with short-lived tokens; it's
> the recommended hardening step once the pipeline is working end to end —
> see [google-github-actions/auth](https://github.com/google-github-actions/auth#setting-up-workload-identity-federation).

### GitHub repo secrets

Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|---|---|
| `GCP_PROJECT_ID` | `prototype-bmmb-1b62` |
| `GCP_SA_KEY` | the full contents of `github-deployer-key.json` |
| `ALLOWED_ORIGINS` | comma-separated frontend origins, or `*` for now |

Delete the local `github-deployer-key.json` file once it's pasted into the secret.

### How the pipeline works (`.github/workflows/deploy.yml`)

- **Every PR** → runs `pytest` only. Nothing gets deployed from a branch.
- **Every push to `main`** (i.e. a merged PR) → tests run again, then build →
  push to Artifact Registry → deploy to Cloud Run, tagged with the commit SHA
  for easy rollback (`gcloud run services update-traffic ... --to-revisions=<sha>=100`).

---

## Collaboration guide

**Branching**
- `main` is protected — no direct pushes. All changes via PR.
- Branch names: `feature/<short-description>`, `fix/<short-description>`.

**Workflow**
```bash
git checkout main && git pull
git checkout -b feature/add-tax-form-template
# ... make changes ...
pytest tests/ -v                 # must pass before pushing
git add -A && git commit -m "Add tax_form_b template"
git push -u origin feature/add-tax-form-template
# Open a PR on GitHub -> CI runs pytest automatically -> request review
```

**PR checklist**
- [ ] `pytest tests/` passes locally
- [ ] New template? Added a case to `test_schema_builder.py`
- [ ] Updated `README.md` if endpoints or setup steps changed

**Commit messages:** short imperative summary (`Add X`, `Fix Y`), body if the
"why" isn't obvious from the diff.

---

## Cloning (for teammates joining later)

```bash
git clone https://github.com/driandy88/bmmb-ai-service.git
cd bmmb-ai-service
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Then open `http://localhost:8000/docs` to confirm it's running, and run
`pytest tests/ -v` to confirm the test suite passes in your environment.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: app` | Run commands from the repo root, not from inside `app/` |
| `422 Unprocessable Entity` on `/extract` | `template`, `api_key`, and `file` are all required form fields |
| `502 Gemini API error` | Check the API key is valid and has access to the requested model |
| Port 8000 already in use | `lsof -ti:8000 \| xargs kill` |
| Notebook can't find sample docs | Run `python notebooks/make_sample_docs.py` first (from the repo root or `notebooks/`) |