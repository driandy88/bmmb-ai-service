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
- **`services/rag-ingestion/` is not an HTTP service** — it's an offline CLI pipeline
  (`cli.py stage1..stage7`), no Dockerfile, no deploy workflow, run manually/as a job.
  It doesn't fit "mount as a router" and should stay **out of scope**. Despite the
  name overlap, it is **not** touched by open PR #42 (`feat/rag`) — verified via
  `gh pr diff 42 --name-only`: all 23 changed files live under
  `services/chat/app/agents/rag/` (a same-named but separate in-app RAG feature of the
  `chat` service) plus `services/chat/{requirements.txt,.env.example,tests/,scripts/,
  docs/RAG_PLAN.md}`. The real coordination risk is therefore with Phase 2's `chat`
  namespacing (which rewrites `services/chat/app`'s internal imports), not with this
  directory — `rag-ingestion` needs no coordination at all and stays out of scope
  regardless of #42's fate.
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

Prefixing routes (`/extraction/extract`, `/chat/chat`, `/validation/validate`, ...) is
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
- Exclude `services/rag-ingestion/` from this migration (stays a standalone offline
  pipeline).
- Do this work on a branch based on `main` after PR #42 (`feat/rag`) lands, or rebase
  onto it before merging, to avoid fighting an in-flight PR that touches `chat`/RAG.
- **Status (2026-08-10):** PR #42 is still open/unmerged. This branch is based on
  current `main` tip (merge-base = `main` HEAD), so it's current for now with nothing
  to rebase yet — rebase onto `main` again right before merging this work, once #42
  has landed, to pick up its changes under `services/chat/app/agents/rag/`.

**Phase 1 — Dependency & env consolidation**
- Pick one shared `fastapi`/`uvicorn` pin — mcp's (`0.139.2`/`0.51.0`) is the floor
  since fastmcp forces it; re-run each service's existing `pytest` suite against that
  bump (their route code is simple, but this must be verified, not assumed).
- Pick one `google-genai` pin (`1.47.0`, matching bbox/validation) and re-run the
  Gemini-touching tests for `extraction` and `chat` after bumping from `0.3.*`.
- Pin `pydantic` and `python-dotenv` to one version each; re-run tests.
- Merge `.env.example` + `env.example` into a single root `.env.example` covering the
  union of vars already collected above — no renames needed, no real collisions found.
  Document mcp's `GOOGLE_API_KEY` path alongside the ADC-based vars.

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
- Create root `requirements.txt` (union of the Phase-1-resolved pins) and root
  `Dockerfile` (installs both `tesseract-ocr` and `libimage-exiftool-perl`).
- Rename the 6 existing per-service Dockerfiles to `Dockerfile.old`.

**Phase 3 — Code refactoring**
- `aggregation`/`bbox_generator`/`validation`/`mcp`: no structural change — already
  routers; leave their standalone `app` objects in place (tests import them directly).
- `extraction`/`chat`: do the router conversion from Phase 2.
- Fix the ~21 test files under `services/extraction/tests` and `services/chat/tests`
  that `import from app...` → `from services.extraction.app...` /
  `from services.chat.app...` (mechanical rename).
- Each service keeps its own `/health` under its prefix (e.g. `/extraction/health`);
  add one root-level `/health` as an aggregate liveness check for the whole monolith.

**Phase 4 — Local testing**
- `pip install -r requirements.txt` at root; run `pytest services/` (excluding
  `rag-ingestion`) to catch dependency-bump breakage before touching Docker.
- `docker build -t unified-agents .` / `docker run -p 8080:8080 --env-file .env
  unified-agents`; hit `/docs` and exercise one real endpoint per mounted router
  (`/extraction/extract`, `/chat/chat`, `/validation/validate`,
  `/aggregation/aggregate/bank`, `/bbox/align`, `/mcp/mcp/servers`).
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
  `services/**` change except `services/rag-ingestion/**`.

**Phase 6 — Clean up**
- Once traffic is verified stable, turn off the 6 original Cloud Run services
  (`extraction-service`, `aggregation-service`, `bbox-generator-service`,
  `validation-service`, `mcp-service`, `chat-service`).
- Delete the `Dockerfile.old` files and the now-redundant per-service workflows.
- Document the router-mount pattern for a 7th service, pointing at
  `aggregation/api.py` / `validation/api.py`'s existing docstrings as the canonical
  example to copy (they already explain it).

## Verification

- `pytest services/` (all 6, excluding rag-ingestion) green at each dependency pin
  change, and again after the namespace move.
- `docker build` + `docker run`, manual hit on every route listed in Phase 4.
- Confirm `chat`'s lifespan-built orchestrator still initializes correctly as one of
  several routers rather than the sole app — checked `app.state.orchestrator` /
  `app.state.source_preview` usage in `services/chat/app/api/routes.py`; no other
  service touches `app.state`, so no collision expected, but re-verify after the merge.
- Diff old vs. new `/openapi.json` per service prefix to catch any accidental
  path/shape drift beyond the intended prefix change.
