# BMMB AI Service — Extraction

Repo: [`driandy88/bmmb-ai-service`](https://github.com/driandy88/bmmb-ai-service) · GCP project: `prototype-bmmb-1b62`

Standalone document extraction API. Upload a PDF or image, pick a template, get
back structured JSON — powered by Gemini (via Vertex AI). No database, no
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
│   ├── gemini_client.py      # Vertex AI call (ADC / service-account auth)
│   ├── schemas.py            # Pydantic response models
│   └── templates_config.json # the 6 SME-financing document templates
├── tests/
│   ├── test_schema_builder.py
│   └── test_api.py           # FastAPI TestClient, Gemini call mocked
├── notebooks/
│   ├── test_extraction.ipynb # manual test — local URL + deployed URL
│   └── make_sample_docs.py   # generates the two synthetic sample docs
├── sample_docs/
│   └── private/               # git-ignored — put your own test documents here
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

(A `libs/bmmb_common/` was actually created and then folded back into
`services/validation/` — see that section below — once it turned out to have
exactly one real consumer. Re-extract it once a second service genuinely
needs the same code, not before.)

---

## Validation Agent (`services/validation/`)

A second, independent service in this monorepo: deterministic + agentic
validation of an already-structured BMMB document bundle against BMMB's
compliance rules — SSM completeness, financial statement freshness, bank
statement continuity/coverage, IC completeness, consent-form signatures,
entity/IC cross-matching, and Form D expiry. See
`services/validation/__init__.py` for the full module map.

Two ways to validate a bundle:
- **Deterministic only** — `ValidationEngine` runs every rule in
  `services/validation/rules/` directly in Python. Fast, free, no LLM, no
  GCP credentials needed (`enable_ai_review=False`).
- **Agentic** (the default) — runs the deterministic engine first, then one
  Gemini call (via Vertex AI) reviews that report against the raw
  pre-adapter extraction, if available, to catch adapter-mapping bugs the
  deterministic engine can't see on its own. The AI review can never
  override a deterministic pass/fail — see `services/validation/agent.py`'s
  module docstring.

**Integrated with extraction** via `services/validation/extraction_adapter.py`:
- Maps `services.extraction`'s actual `POST /extract` output (per-template
  `{attribute_name: value}` dicts, one call per document) into a
  `ValidationBundle` (`bundle.py`). Handles the real gaps between the two
  services (field names that don't line up, values extraction doesn't
  capture at all, nulls, misaligned arrays) by degrading gracefully — never
  crashes, records an `AdapterWarning` for anything it had to default or
  couldn't determine, and feeds those warnings into the AI review step. See
  its module docstring for the full list of known gaps.
- `agent.run_agentic_validation_from_extraction()` / `POST
  /validate/from-extraction` — the entry points that take raw extraction
  results directly and call the adapter internally; only the extraction
  results are required, everything else is auto-derived or defaulted.
- Lives inside `services/validation/` rather than a shared `libs/` package
  since extraction never needs to import it — see the note in "Restructuring
  for the next two services" above.

`services/validation/examples/buggy_adapter_demo.py` is unrelated to the
above — a deliberately-bugged teaching fixture (different, made-up input
shape) used only by `examples/test_conflict_example.py` to demonstrate a
blind spot in the deterministic engine. Don't confuse it with the real
adapter.

### Structure

```
services/validation/
├── bundle.py                 # The canonical ValidationBundle schema (pydantic)
├── extraction_adapter.py     # Maps extraction's real POST /extract output -> ValidationBundle
├── rules/                    # Individual BMMB rule functions (date logic, completeness, matching)
├── engine.py                 # ValidationEngine -- runs every applicable rule against a bundle
├── schemas.py                 # AgenticValidationReport / AIFinding / AIReview output models
├── agent.py                   # run_agentic_validation() / run_agentic_validation_from_extraction()
├── api.py                     # FastAPI router/app -- /health, /validate, /validate/from-extraction
├── examples/                  # Sample bundles, extraction_results_example.json, curl requests, and
│                              # buggy_adapter_demo.py (a deliberately-bugged teaching fixture --
│                              # NOT the real adapter, see extraction_adapter.py for that)
├── tests/                     # pytest -- rules, engine, adapter, adapter demo, and API tests (Gemini call mocked)
├── requirements.txt           # runtime deps only
└── requirements-dev.txt       # + pytest
```

### Running it

```bash
# from the repo root -- imports are `services.validation.*`, so this must
# run from the repo root, not from inside services/validation/
python3 -m venv .venv && source .venv/bin/activate
pip install -r services/validation/requirements-dev.txt

uvicorn services.validation.api:app --reload
```

Open `http://localhost:8000/docs` for `GET /health` and `POST /validate`.
`POST /validate` takes `{"bundle": {...}, "raw_extraction": null, "enable_ai_review": true}`;
set `enable_ai_review: false` to skip the Gemini call entirely (no
`GCP_PROJECT_ID` / Vertex AI credentials needed in that case).

### Testing

```bash
# from the repo root
pytest services/validation/tests/ -v
```

Never calls the real Gemini/Vertex AI API — the agentic-path tests
monkeypatch `genai.Client`, same convention as
`services/extraction/tests/test_api.py`. No credentials or network access
required.

### Deployment (Cloud Run)

Same pipeline shape as extraction (`.github/workflows/deploy-validation.yml`,
path-filtered to `services/validation/**`): every PR runs `pytest`; every
push to `main` runs tests, then builds `services/validation/Dockerfile` and
deploys to Cloud Run as `validation-service`, reusing the same shared
Artifact Registry repo and `GCP_PROJECT_ID`/`GCP_SA_KEY` secrets as
extraction — no new GitHub secrets needed.

**One-time GCP setup still required before the first deploy succeeds** — a
dedicated runtime service account, same pattern as `extraction-service-sa`:
```bash
export PROJECT_ID="prototype-bmmb-1b62"

gcloud iam service-accounts create validation-service-sa \
  --display-name="Validation Service runtime" --project=$PROJECT_ID

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:validation-service-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```
`github-deployer` already has the project-level roles (`run.admin`,
`artifactregistry.writer`, `iam.serviceAccountUser`) needed to deploy this
service too — see "Deployment (Cloud Run)" → "One-time GCP setup" below,
which was set up for extraction but isn't extraction-specific.

---

## Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (recommended — commands below use it; plain `venv`+`pip` works too, see the fallback note)
- `gcloud` CLI installed
- **No API key needed.** Auth is via Google Cloud instead — see "Auth" below.
  Testing the **deployed** URL needs no setup at all. Running it **locally**
  needs a one-time Vertex AI IAM grant on your Google account from a project
  owner (not automatic, not inherited from anyone else) — see "Auth" below.

---

## Local setup

```bash
git clone https://github.com/driandy88/bmmb-ai-service.git
cd bmmb-ai-service

uv venv                            # creates .venv/
uv pip install -r requirements-dev.txt   # installs runtime + dev deps into it
# no uv? fallback: python3 -m venv venv && source venv/bin/activate && pip install -r requirements-dev.txt

cp .env.example .env              # then check GCP_PROJECT_ID matches prototype-bmmb-1b62
```

### Auth (one-time)

This service authenticates to Gemini through **Vertex AI**, not an API key —
and the setup differs depending on whether you're testing locally or against
the deployed service.

**Testing the deployed (Prod) URL needs nothing from you.** Auth there is the
Cloud Run service's own attached service account (`extraction-service-sa`),
already configured — anyone can hit the deployed URL and it just works.

**Running it locally is per-person and needs a one-time grant from a project
owner first.** `gcloud auth application-default login` authenticates
Application Default Credentials (ADC) as **you specifically** — it does not
inherit anyone else's access. Your local calls will fail with a permission
error until your Google account has been granted Vertex AI access on
`prototype-bmmb-1b62`.

**If you're a new collaborator:** ask a project owner to run this once for
your account (they'll need your Google account email):
```bash
gcloud projects add-iam-policy-binding prototype-bmmb-1b62 \
  --member="user:YOUR_EMAIL@example.com" \
  --role="roles/aiplatform.user"
```

Then, on your own machine:
```bash
gcloud auth application-default login
```

This authenticates ADC as you. On Cloud Run, the equivalent call is
authenticated automatically by the attached service account — nothing to
configure there beyond the IAM role it already has.

### Run it

```bash
uv run uvicorn app.main:app --reload
# no uv? fallback: uvicorn app.main:app --reload   (with venv activated)
```

Open `http://localhost:8000/docs` for the interactive Swagger UI. Run it
**from the repo root** — `.env` is loaded relative to the current working
directory, so running from inside `app/` or `notebooks/` won't find it.

---

## Using the API

```bash
curl -X POST http://localhost:8000/extract \
  -F "template=business_registration_ssm" \
  -F "file=@sample_docs/sample_ssm_certificate.pdf;type=application/pdf"
```

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Liveness check |
| `/templates` | GET | List all templates (key, kind, field count) |
| `/templates/{key}` | GET | Full field definitions for one template |
| `/extract` | POST | `template`, `file` (form-data) → extracted JSON |

Supported file types: PDF, JPEG, PNG, WEBP, HEIC, HEIF. Max 20 MB.

Every template is either:
- **`kind: single`** — one JSON object per document (e.g. `business_registration_ssm`)
- **`kind: array`** — a JSON array of objects (e.g. `bank_statements`, one entry per month; `ic_photocopies`, one entry per director)

---

## For collaborators: step-by-step

**Only want to test the deployed service? Skip straight to step 4** — get the
URL with `gcloud run services describe extraction-service --region=asia-southeast1 --format="value(status.url)"`,
drop a file in `sample_docs/private/`, and hit it with the notebook's Prod
cell or a `curl` (see "Using the API" above). No GCP setup, no IAM grant, no
local server needed — the deployed service is already fully configured.

### A. "I just want to test it" (including running it locally)

1. Do the **Local setup** above (clone, `uv venv` + `uv pip install`, `.env`).
   Then, for local runs specifically: confirm a project owner has granted
   your Google account Vertex AI access (see "Auth" above — this is a
   one-time grant tied to *your* account, not something you inherit
   automatically), then run `gcloud auth application-default login`.
2. Run it: `uv run uvicorn app.main:app --reload` (leave this running in a terminal).
3. Drop your own test document into `sample_docs/private/` — this folder is
   **git-ignored** (except its README), so real/sensitive test documents
   never risk getting committed. Never put real documents anywhere else in the repo.
4. Open `notebooks/test_extraction.ipynb`:
   - Cell 1: set `FILE_PATH` to your file in `sample_docs/private/...`, and
     `TEMPLATE` to match its document type (see the table in `/templates` or
     §"Using the API" above — e.g. `ic_photocopies` for an IC, `business_registration_ssm` for an SSM cert)
   - Run the **Local** cell → should return `200` with extracted fields
   - Run the **Prod** cell → same file, hitting the deployed Cloud Run service. Get the URL with:
     ```bash
     gcloud run services describe extraction-service --region=asia-southeast1 --format="value(status.url)"
     ```
5. If a cell ran on a real document, clear the notebook's output before
   closing it (see "Before committing" below) — you don't need to commit
   anything for just testing, but it's good habit.

### B. "I want to update the extraction function, a template, or a prompt"

1. Sync and branch:
   ```bash
   git checkout main && git pull
   git checkout -b feature/<short-description>
   ```
2. Make your change:
   - New/changed template field → edit `app/templates_config.json` (see "Adding or changing a template" below)
   - New field type or extraction logic → edit `app/schema_builder.py`
   - Prompt wording → edit `SYSTEM_INSTRUCTION` or `generate_extraction_prompt()` in `app/schema_builder.py` / `app/gemini_client.py`
3. Test locally, in this order:
   ```bash
   pytest tests/ -v                          # fast, no credentials needed — must pass
   uv run uvicorn app.main:app --reload       # then re-run the notebook's Local cell
   ```
   Use your own test document in `sample_docs/private/` if the change affects
   a specific document type, so you're testing against something realistic.
4. **Before committing:** if any notebook cell ran on a real document, clear
   its output first — the git-ignore protects the source file, not the
   notebook's saved output cells:
   ```bash
   jupyter nbconvert --clear-output --inplace notebooks/test_extraction.ipynb
   ```
5. Commit, push, open a PR:
   ```bash
   git add -A && git commit -m "Describe your change"
   git push -u origin feature/<short-description>
   ```
   Open the PR on GitHub → CI runs `pytest` automatically → request review.
6. Once merged, `deploy` runs automatically (build → push → Cloud Run). Re-run
   the notebook's **Prod** cell against the same file to confirm the deployed
   version behaves the same as what you tested locally.

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

## Testing (reference)

```bash
pytest tests/ -v
```

Tests never call the real Gemini/Vertex AI API — `app.extraction.run_extraction`
is monkeypatched in `tests/test_api.py`, so CI runs with no credentials and no
network access.

For a real end-to-end test (actual Gemini call, both local and deployed), see
**"For collaborators" → A** above.

---

## Deployment (Cloud Run)

### One-time GCP setup

```bash
export PROJECT_ID="prototype-bmmb-1b62"
export REGION="asia-southeast1"

gcloud services enable run.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com \
  --project=$PROJECT_ID

# Artifact Registry repo for Docker images — shared across all services in this
# monorepo (extraction-service now; judgement-rag-service, memo-lo-service later),
# so it's named after the repo, not the individual service.
gcloud artifacts repositories create bmmb-ai-service \
  --repository-format=docker --location=$REGION --project=$PROJECT_ID

# Dedicated service account for the running service
gcloud iam service-accounts create extraction-service-sa \
  --display-name="Extraction Service runtime" --project=$PROJECT_ID

# Needed for Vertex AI (Gemini) calls
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:extraction-service-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

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
  push to Artifact Registry → deploy to Cloud Run (env vars `GCP_PROJECT_ID`
  and `ALLOWED_ORIGINS` set automatically), tagged with the commit SHA for
  easy rollback (`gcloud run services update-traffic ... --to-revisions=<sha>=100`).

---

## Collaboration guide

**Branching**
- `main` is protected — no direct pushes. All changes via PR.
- Branch names: `feature/<short-description>`, `fix/<short-description>`.

**PR checklist**
- [ ] `pytest tests/` passes locally
- [ ] New template? Added a case to `test_schema_builder.py`
- [ ] Tested against a real document locally (see "For collaborators" → A/B above)
- [ ] Notebook output cleared if it contains real extracted data
- [ ] Updated `README.md` if endpoints or setup steps changed

**Commit messages:** short imperative summary (`Add X`, `Fix Y`), body if the
"why" isn't obvious from the diff.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: app` | Run commands from the repo root, not from inside `app/` |
| `500 Service misconfigured: GCP_PROJECT_ID is not set` | `.env` is missing, misnamed (must be exactly `.env`, not `.env.example`), in the wrong folder (must be repo root), or uvicorn was started before `.env` existed — stop it fully (Ctrl+C) and restart; `--reload` does not re-read `.env` |
| `.env` seems right but still not picked up | Sanity-check in isolation: `python3 -c "from dotenv import load_dotenv; import os; load_dotenv(); print(os.getenv('GCP_PROJECT_ID'))"` from the repo root — should print `prototype-bmmb-1b62` |
| `502 Gemini API error` (local) | Run `gcloud auth application-default login`; if the error mentions `PERMISSION_DENIED`, your Google account hasn't been granted Vertex AI access on `prototype-bmmb-1b62` yet — ask a project owner to run the `add-iam-policy-binding ... roles/aiplatform.user` command in "Auth" above for your account |
| `422` asking for `api_key` on the **deployed** URL | The live Cloud Run revision is older than your local code — push your branch, merge the PR, wait for `deploy` to finish, then retry |
| `404 Not Found` on the deployed URL | Check `DEPLOYED_URL` has no trailing slash and isn't still the placeholder value |
| Notebook shows an old/stale error after you fixed something | Check the cell's execution-count number (`[3]`, `[5]`...) — Jupyter only updates the cell you actually re-ran; re-run it again to get current output |
| Port 8000 already in use | `lsof -ti:8000 \| xargs kill` |
| Notebook can't find sample docs | Run `python notebooks/make_sample_docs.py` first (from the repo root or `notebooks/`) |