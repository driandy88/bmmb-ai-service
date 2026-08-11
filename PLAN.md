# Plan: Migrate `bmmb-ai-service` Cloud Run agents to a modular FastAPI monolith

## Context

The starting point was a generic 6-phase "5 microservices → modular monolith"
playbook. Before adapting it, the actual repo (`services/*`, Dockerfiles,
requirements, CI workflows, env usage, route tables) was audited, because several
of the template's assumptions ("5 agents", "version conflicts need resolving from
scratch", "env vars need prefixing", "routers don't exist yet") turned out to be
wrong in specific, consequential ways. This plan reflects what's actually there,
not the generic template.

**Why this matters:** the repo's own `README.md` already anticipated this move ("once
`judgement-rag` and `memo-lo` are ready... services/ layout... share common code")
and four of the six services were *already* built router-first specifically so they
could be mounted into a shared app later. Treating this as a from-scratch merge would
redo work that's done and miss the two services that actually still need it.

## Audit findings (what's really in the repo)

- **6 independently-deployed Cloud Run FastAPI services**, not 5: `extraction`,
  `aggregation`, `bbox_generator`, `validation`, `mcp`, `chat`. Each has its own
  `Dockerfile` and a path-filtered GitHub Actions workflow
  (`deploy.yml`, `deploy-aggregation.yml`, `deploy-bbox.yml`, `deploy-validation.yml`,
  `deploy-mcp.yml`, `deploy-chat.yml`).
- **`services/rag-ingestion/` is in scope, but it's not an HTTP service today** — it's
  an offline CLI pipeline (`cli.py stage1..stage7`), no Dockerfile, no deploy workflow,
  no `fastapi`/`uvicorn` in its `requirements.txt` at all, run manually/as a job.
  It's being pulled into this migration because it may become an online service later;
  concretely that means Phase 1/2 fold its deps, env, and Docker image into the shared
  ones now, but it is **not** given a router or mounted into the unified app in this
  pass — see "rag-ingestion online conversion" under Phase 2 for why that's split out.
  Despite the name overlap, it is **not** touched by open PR #42 (`feat/rag`) — verified
  via `gh pr diff 42 --name-only`: all 23 changed files live under
  `services/chat/app/agents/rag/` (a same-named but separate in-app RAG feature of the
  `chat` service) plus `services/chat/{requirements.txt,.env.example,tests/,scripts/,
  docs/RAG_PLAN.md}`. The real coordination risk from #42 is therefore with Phase 2's
  `chat` namespacing (which rewrites `services/chat/app`'s internal imports), not with
  `rag-ingestion` — folding `rag-ingestion` in is unaffected by #42's fate.
- **`rag-ingestion`'s deps are a clean superset, no version conflicts**: its
  `requirements.txt` pins nothing yet (`pyyaml`, `python-dotenv`, `pymupdf`, `pillow`,
  `google-genai`, `tiktoken`, `google-cloud-aiplatform`, `psycopg[binary]`, `pgvector`,
  `cloud-sql-python-connector`, plus eval-only `ragas`/`datasets`/
  `langchain-google-vertexai`). Its two Gemini call sites (`pipeline/stage1_parse.py`,
  `pipeline/stage6_embed.py`) use the same `genai.Client(vertexai=True, ...)` pattern
  as the other services, so it adopts the Phase 1 `google-genai==1.47.0` pin like they
  do — needs the same "re-run tests after bump" treatment, nothing structurally new.
  `psycopg`/`pgvector`/`cloud-sql-python-connector` are new to the unified image (no
  other service uses them), and `ragas`/`datasets`/`langchain-google-vertexai` are
  eval-only, heavy, and never imported by any HTTP path — keep those as a separate
  `requirements-eval.txt` rather than baking them into the always-on runtime image.
  Note for later: `chat`'s new RAG feature (PR #42) talks to the *same* Cloud SQL
  `pgvector` store via `pg8000` (`CAST(:v AS vector)`, no native adapter), while
  `rag-ingestion` uses `psycopg`'s native pgvector adapter — two drivers, one database;
  not a migration blocker, just worth reconciling whenever both are actively worked.
- **`rag-ingestion` has no `services/rag-ingestion/__init__.py`** (every other service
  does, alongside the existing `services/__init__.py`), and `cli.py` loads its stage
  modules via `importlib`, not package-relative imports — Phase 2 adds the missing
  `__init__.py` for consistency, but there's no import rewrite needed like
  `extraction`/`chat` require, since nothing in it does `from app.x import y`.
- **4 of 6 services are already router-based and merge-ready**: `aggregation`,
  `bbox_generator`, `validation`, `mcp` each expose `router = APIRouter(...)` in their
  `api.py`, live under the `services.<name>` package (there's already a
  `services/__init__.py`), and their own docstrings literally say:
  `from services.X.api import router; app.include_router(router)`. Their standalone
  `app = FastAPI()` in the same file is only for solo `uvicorn`/tests — safe to leave.
- **2 of 6 (`extraction`, `chat`) are not converted yet.** Both still use a flat
  top-level `app/` package with `app = FastAPI()` built directly in `app/main.py`, no
  exported router, and absolute imports like `from app.extraction import router`.
  Both packages are literally named `app` — copying them under one root as-is
  collides directly. This is the real Phase-3 work.
- **Every service defines `GET /health` at its root**, and several others collide too
  (`/`, `/extract`, `/align`, `/validate`, `/aggregate/bank`, `/mcp/servers`,
  `/chat`, `/templates/*`, `/attributes/*`). Mounting all 6 under one app **requires
  per-service path prefixes**, which changes every currently-public URL. This is a
  breaking change for whatever calls these URLs today, not just a code detail — see
  "Open decision" below.
- **Real inter-service coupling today**: `chat` calls `extraction` over plain HTTP
  (`EXTRACTION_BACKEND=http` → `POST {EXTRACTION_SERVICE_URL}/extract`). After the
  merge this can become an in-process call — worth doing, not required for
  correctness (updating the URL to the new prefixed path also works).
- **Dependency versions genuinely differ, but not randomly:**
  - `fastapi`/`uvicorn`: `0.115.*` (extraction, chat) vs `0.121.0` (aggregation, bbox,
    validation) vs `0.139.2`/`0.51.0` (mcp) — mcp's requirements.txt explains this is
    deliberate: `fastmcp` 3.x needs a newer `starlette` that older FastAPI rejects.
    Any unified pin has to satisfy mcp's floor.
  - `google-genai`: `0.3.*` (extraction, chat) vs `1.47.0` (bbox, validation) — a
    major-version jump. All call sites use the same `genai.Client(vertexai=True, ...)`
    pattern (checked `gemini_client.py`, `llm_bbox.py`, `integrations/llm.py`), so the
    surface looks compatible, but each service's actual Gemini calls need a real test
    pass after bumping, not just an import check.
  - `pydantic`/`python-dotenv` also differ by patch/minor across services — low risk,
    just need one pinned version and a test run.
- **No real env-var collisions.** `DB_USER`/`DB_PASS`/`DB_NAME`/`INSTANCE_CONNECTION_NAME`
  are used by both `extraction` and `chat` but are *meant* to be identical — both
  comments confirm they point at the same Cloud SQL instance/tables by design.
  `GCP_PROJECT_ID`/`VERTEX_LOCATION`/`ALLOWED_ORIGINS` are shared conventions across
  all services. The one real difference: `mcp` authenticates to Gemini via a raw
  `GOOGLE_API_KEY` (langchain-google-genai) instead of Vertex ADC like everyone else —
  additive, not colliding, but the unified service account needs both ADC roles and a
  `GOOGLE_API_KEY` secret.
- Two near-duplicate root files already exist, `.env.example` and `env.example` —
  pre-existing clutter worth folding into one while touching this area anyway.
- `python:3.12-slim` is already uniform across all 6 Dockerfiles — no conflict there.
  OS-level deps do differ: `bbox_generator` needs `tesseract-ocr`, `extraction` needs
  `libimage-exiftool-perl`; both need to land in one root Dockerfile.
- Checked `mcp`'s subprocess spawn of `servers/email_server.py`
  (`agent.py`: `Path(__file__).resolve().parent / "servers" / "email_server.py"`) —
  it's already `__file__`-relative, so it keeps working unmodified once merged; no
  fix needed there.

## Open decision — flag before Phase 5

Prefixing routes (`/extraction/extract`, `/chatbot/chat`, `/validation/validate`, ...) is
mandatory (six services collide on `/health` alone) and **breaks every current public
path**. Two ways to handle it:
1. **Update all known callers** (chat's own `EXTRACTION_SERVICE_URL`, any frontend,
   `services/validation/examples/curl_requests.sh`, Postman collections) to the new
   prefixed paths as part of cutover. Simplest, most explicit — recommended given this
   looks like an internal system with few consumers.
2. Put a path-rewriting proxy/load-balancer URL map in front to preserve old paths.
   Real infra for a system this size — likely not worth it.

This plan assumes **option 1**. Confirm before starting Phase 5 (deployment/cutover).

## Plan

**Phase 0 — Scope lock**
- Include `services/rag-ingestion/` in this migration's dependency/env/Docker
  consolidation (Phases 1–2), since it may become an online service later and doing
  that consolidation once now avoids a second pass. It does **not** get a router or
  get mounted into the unified app in this migration — turning its stage pipeline
  into HTTP endpoints is a separate, unscoped design question (sync vs. async
  execution, since Vertex-bound stages can run minutes; job-status tracking) and is
  explicitly deferred, not decided here.
- Integration branch: all work happens on `refactor/agents-services`, cut from `main`
  and **not merged back into `main`** — it gets deployed directly from this branch to
  verify the migration before any merge decision is made. Individual tickets branch
  off `refactor/agents-services` (not `main`) and open PRs with
  `refactor/agents-services` as the base.
- PR #42 (`feat/rag`, still open) doesn't touch `rag-ingestion` at all — see the audit
  finding above — so it has no bearing on the rag-ingestion inclusion. It does add
  files under `services/chat/app/agents/rag/`, which Phase 2's `chat` namespacing
  will conflict with if both land out of order; rebase this work onto `main` once #42
  merges, before it's eventually considered for merge.

**Phase 1 — Dependency & env consolidation**
- Pick one shared `fastapi`/`uvicorn` pin — mcp's (`0.139.2`/`0.51.0`) is the floor
  since fastmcp forces it; re-run each service's existing `pytest` suite against that
  bump (their route code is simple, but this must be verified, not assumed).
- Pick one `google-genai` pin (`1.47.0`, matching bbox/validation) and re-run the
  Gemini-touching tests for `extraction`, `chat`, and `rag-ingestion` after bumping
  from `0.3.*` (`rag-ingestion` was unpinned, so this is a first pin for it, not a
  bump — still needs the same test-after-pin treatment).
- Pin `pydantic` and `python-dotenv` to one version each; re-run tests.
- Add `rag-ingestion`'s runtime deps (`pymupdf`, `pillow`, `tiktoken`,
  `google-cloud-aiplatform`, `psycopg[binary]`, `pgvector`,
  `cloud-sql-python-connector`) to the union. Keep `ragas`/`datasets`/
  `langchain-google-vertexai` (eval-only, Phase 7 of its own plan) out of the shared
  runtime `requirements.txt` — put them in `requirements-eval.txt` instead so the
  always-on image doesn't pay for them.
- Merge `.env.example` + `env.example` into a single root `.env.example` covering the
  union of vars already collected above (now including `rag-ingestion`'s Cloud SQL /
  pgvector settings) — no renames needed, no real collisions found. Document mcp's
  `GOOGLE_API_KEY` path alongside the ADC-based vars.

**Phase 2 — Project restructuring**
- `services/` and `services/__init__.py` already exist — no "move folders" step needed.
- Namespace `extraction` and `chat` to match the other four: move their `app/` folders
  under `services/extraction/app/` and `services/chat/app/`, fix internal imports from
  `from app.x import y` to relative imports (`from .x import y`), same pattern already
  used inside `aggregation`/`bbox_generator`/`validation`/`mcp`.
- Give both a `services/extraction/api.py` and `services/chat/api.py` exporting
  `router = APIRouter(...)`, mirroring `validation/api.py`'s and `aggregation/api.py`'s
  existing convention (including their "mount elsewhere" docstring).
- Create root `main.py`: one `app = FastAPI(title="BMMB Unified AI Service")`, one
  merged `CORSMiddleware` (union of each service's allowed origins), then
  `app.include_router(x_router, prefix="/<name>", tags=[...])` for all 6. FastAPI
  allows only one `lifespan` — wrap chat's existing orchestrator-build lifespan (the
  only one of the 6 that needs startup wiring) as the root app's lifespan.
- Create root `requirements.txt` (union of the Phase-1-resolved pins, including
  `rag-ingestion`'s) and root `Dockerfile` (installs both `tesseract-ocr` and
  `libimage-exiftool-perl`).
- Rename the 6 existing per-service Dockerfiles to `Dockerfile.old`.
- Add `services/rag-ingestion/__init__.py` (missing today, unlike every other
  service) so it's a proper package under the `services.*` namespace. No import
  rewrite needed — `cli.py` loads stages via `importlib`, not `from app.x import y`.
- **rag-ingestion online conversion — explicitly deferred**: no `api.py`/router is
  created for it in this migration. Its stages are Vertex-call-bound (can run
  minutes), so a naive synchronous `POST /rag-ingestion/run` would need real
  job-status/async design, not a thin wrapper — that's follow-up work once there's an
  actual online-service requirement, not before.

**Phase 3 — Code refactoring**
- `aggregation`/`bbox_generator`/`validation`/`mcp`: no structural change — already
  routers; leave their standalone `app` objects in place (tests import them directly).
- `extraction`/`chat`: do the router conversion from Phase 2.
- `rag-ingestion`: no router conversion (deferred, see Phase 2) — only the new
  `__init__.py` and its dependency/env consolidation from Phases 1–2 land here.
- Fix the ~21 test files under `services/extraction/tests` and `services/chat/tests`
  that `import from app...` → `from services.extraction.app...` /
  `from services.chat.app...` (mechanical rename).
- Each service keeps its own `/health` under its prefix (e.g. `/extraction/health`);
  add one root-level `/health` as an aggregate liveness check for the whole monolith.

**Phase 4 — Local testing**
- `pip install -r requirements.txt` at root; run `pytest services/` (all 7, including
  `rag-ingestion`'s 3 `verify_*` test files) to catch dependency-bump breakage before
  touching Docker.
- `docker build -t unified-agents .` / `docker run -p 8080:8080 --env-file .env
  unified-agents`; hit `/docs` and exercise one real endpoint per mounted router
  (`/extraction/extract`, `/chatbot/chat`, `/validation/validate`,
  `/aggregation/aggregate/bank`, `/bbox/align`, `/mcp/servers`).
- Measure memory/cold-start explicitly: the merged image now always pays for
  `langchain`+`fastmcp`+`langchain-google-genai` (mcp), `langgraph` (chat), `PyMuPDF`
  (extraction), and `tesseract` (bbox) simultaneously — materially heavier than any
  single current service. Use this number, not a guess, to size Phase 5.

**Phase 5 — Deployment & cutover**
- Resolve the "Open decision" above before starting.
- Deploy as a new Cloud Run service (`bmmb-unified-service`) alongside the 6 existing
  ones, no traffic yet.
- Size CPU/memory from the Phase 4 measurement (start 2 vCPU / 2GiB, adjust from data).
- Grant the unified runtime service account the union of the 6 existing SAs' roles
  (`roles/aiplatform.user`, `roles/cloudsql.client`) plus the `GOOGLE_API_KEY` secret
  for mcp.
- Update `chat`'s extraction client to the new prefixed path (or switch it to an
  in-process call to the extraction router now that they're colocated — optional
  stretch goal, not required for correctness).
- Update any other known caller to the new prefixed paths.
- Collapse the 6 path-filtered workflows into one `deploy.yml` triggered on any
  `services/**` change, including `services/rag-ingestion/**` — it now shares the
  root `requirements.txt`/`Dockerfile`, so a change there can affect the built image
  even though it isn't mounted as a router.

**Phase 6 — Clean up**
- Once traffic is verified stable, turn off the 6 original Cloud Run services
  (`extraction-service`, `aggregation-service`, `bbox-generator-service`,
  `validation-service`, `mcp-service`, `chat-service`).
- Delete the `Dockerfile.old` files and the now-redundant per-service workflows.
- Document the router-mount pattern for a 7th service, pointing at
  `aggregation/api.py` / `validation/api.py`'s existing docstrings as the canonical
  example to copy (they already explain it).

## Verification

- `pytest services/` (all 7, including rag-ingestion) green at each dependency pin
  change, and again after the namespace move.
- `docker build` + `docker run`, manual hit on every route listed in Phase 4.
- Confirm `chat`'s lifespan-built orchestrator still initializes correctly as one of
  several routers rather than the sole app — checked `app.state.orchestrator` /
  `app.state.source_preview` usage in `services/chat/app/api/routes.py`; no other
  service touches `app.state`, so no collision expected, but re-verify after the merge.
- Diff old vs. new `/openapi.json` per service prefix to catch any accidental
  path/shape drift beyond the intended prefix change.
