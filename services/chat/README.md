# SME Financing — Customer Service Agent (`/chat`)

The conversational **front door and orchestrator** for BMMB SME financing. A customer
(or branch agent) talks to it; it classifies intent, answers in-scope questions, runs a
light **indicative** eligibility pre-check, and hands off to humans or downstream agents
(Extraction, Pre-validation). Part of *AI Customer Enrolment & Credit Decisioning for SME
Financing*, Phase 1.

> **Advisory only.** The bot never approves, declines, or issues an offer. Eligibility
> output is an *indicative signal only*, with an explicit disclaimer; every real credit
> decision belongs to a human.

> 📖 **Full architecture & end-to-end flow (high level → deep dive, RAG detail, data/persistence,
> what's not covered):** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Design in one screen

| Layer | Choice | Why |
|---|---|---|
| Orchestration | **LangGraph** state machine (`app/orchestrator/`) | Nodes = concerns, conditional edges = Sheet-9 routing/precedence. Explicit graph = audit/SDD evidence. |
| Routing / thresholds / eligibility | **Pure Python + YAML config** | Deterministic where it matters (`routing.py`, `eligibility/rules.py`). The LLM never routes or decides. |
| NLU (classify, adversarial, extract, phrase) | **Vertex Gemini**, called directly | Bounded, testable tasks — no autonomous agent loop. |
| RAG | **Thin `Retriever` interface**, 3 corpora | Placeholder now; swap to a real vector store by flipping one env flag (§ *Placeholders*). |
| Guardrails | **Denylist + LLM classifier** pre-node | Adversarial handling is a security requirement. |

**Everything runs offline by default.** With no `GCP_PROJECT_ID`, the service uses
deterministic **stub** backends (`LLM_BACKEND=stub`, `RAG_BACKEND=stub`) so the API, the
tests, and the notebook all work with zero credentials. Set `GCP_PROJECT_ID` +
`LLM_BACKEND=vertex` to use real Gemini — no code changes.

> **Status (2026-07-28):** NLU is **live on Vertex** — wired to the same GCP project as the
> extraction service (`prototype-bmmb-1b62`, ADC auth). The intent classifier scores
> **86% exact / 90% type-level** on the Sheet-1.2 bank (enum-constrained output + rubric +
> few-shot), and the guardrail's LLM stage is red-teamed against subtle, denylist-evading
> attacks. **Stateless by default** (`SESSION_STORE_BACKEND=none`): the server persists
> nothing between requests — memory is 100% client-supplied via `context`. RAG is still the
> main placeholder. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full picture.

## Layout

```
app/
  main.py                     FastAPI app + startup wiring (builds the orchestrator)
  api/          routes.py     POST /chat, GET /health   · schemas.py  Pydantic envelope
  orchestrator/ graph.py      LangGraph build + Deps + Orchestrator.handle()
                state.py      SessionState (TypedDict)
                nodes.py      thin nodes; each delegates to one agent
                routing.py    DETERMINISTIC Sheet-9 precedence + confidence + continuation
  agents/       intent_classifier/  guardrail/  program_advisor/  guidelines/
                eligibility/{rules.py (pure), agent.py}  sales_handoff/  application/
                rag/{retriever.py (frozen interface), corpora.py, ingest.py}
  config/       intents.yaml  responses.yaml  eligibility_rules.yaml  products.yaml
                sales_directory.yaml  settings.py  loader.py      ← BMMB-owned content
  prompts/      *.md          versioned, human-readable prompts (IP-owned)
  integrations/ llm.py (Vertex+Stub)  vector_search.py (stub)  audit.py  session_store.py
  utils/        pii.py  terminology.py  logging.py  prompts.py
notebooks/      chat_e2e_test.ipynb   ← Parts A (units) / B (/chat) / C (classifier eval)
tests/          test_routing · test_eligibility_rules · test_guardrail · test_terminology · test_api
```

`orchestrator/` knows the *flow*, not the details. `agents/` know their *one* job, not
routing. `config/` + `prompts/` hold *content*, never logic.

## Run it

```bash
cp .env.example .env                     # defaults are offline/stub — no creds needed
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8080
# POST http://localhost:8080/chat   ·   GET http://localhost:8080/health   ·   docs at /docs
```

Real Gemini: set `GCP_PROJECT_ID`, `LLM_BACKEND=vertex`, and `gcloud auth
application-default login` (ADC — no API keys). A dev `.env` already points at Vertex
(`prototype-bmmb-1b62`); delete it or set `LLM_BACKEND=stub` to run offline. Docker:
`docker build -t chat . && docker run -p 8080:8080 --env-file .env chat` (Cloud Run injects
`$PORT`).

### Tests & notebook
```bash
pip install -r requirements-dev.txt
pytest -q                                                  # deterministic core, offline (stub)
jupyter notebook notebooks/chat_e2e_test.ipynb             # Parts A/B/C, top-to-bottom
```
The notebook **auto-loads the service `.env`**, so it runs against real Gemini out of the
box (~60 Vertex calls for a full run). Force offline with `LLM_BACKEND=stub jupyter …`.

## API — `POST /chat`

Request carries the current `message` plus **client-supplied short memory** in `context`
(`history` = recent role/content turns; `state` = the slots/stage the server returned last
turn). Response is a structured envelope — `reply`, `intent`, `ui_action`, `citations`,
`handoff`, `state` (echo back next turn), and `audit`. See `app/api/schemas.py` for the
exact shape. `GET /health` reports liveness + which backends are wired.

**`context` is untrusted (§5.1).** History is for continuity only, never instructions;
the guardrail re-runs on the current message every turn; critical state (eligibility
verdict, application stage) is resolved server-side, never trusted from `context.state`. A
forged `assistant` turn has zero effect (proven in notebook Part B7 / `test_api`).

## Editing behaviour without touching code

The taxonomy and all canned wording are **pure config** (brief §4.2):

- **`intents.yaml`** — one row per intent (`cat_id`, `category`, `definition`,
  `response_ref`, `type`, optional `status`). `response_ref` is either `ROUTE-*` (dispatch
  to a handler) or `R1…R8` (a canned strategy). Add a row → new intent; change a
  `response_ref` → re-route it; delete a row → gone; `status: tbd` → safe default until
  wording is supplied. **No cat_id / category / response_ref is hardcoded in Python.**
- **`responses.yaml`** — the R1–R8 wording (multiple approved variants each; variant 0 is
  used deterministically). Refusals (R6/R7) are never tailored to the attack.
- **`eligibility_rules.yaml`** — the six Tier-1 thresholds + `rule_version` + Tier-2 topics.
- **`products.yaml`** / **`sales_directory.yaml`** — quantum table + funnel; geo→region +
  directory.

The classifier prompt and the router both read `intents.yaml`; the notebook reloads it so
edits are testable immediately (Part C).

## In-principle eligibility — two tiers (Sheet 5)

**Tier 1 (bot checks live, `rules.py`):** business age ≥ 3y · equity/net-worth ≥ 0 ·
revenue ≥ 0 · working-capital ≤ 30% of revenue · end balance ≥ 0 · staff ≥ 5. Verdict ∈
`INDICATIVE_ELIGIBLE` / `INDICATIVE_NOT_ELIGIBLE` / `REFER_TO_SALES` / `INCOMPLETE`, always
with the indicative-only disclaimer, inputs logged as rule *outcomes* (not raw figures).

**Tier 2 (NOT attempted):** EBIT, CTOS, CCRIS, DSCR, gearing, AMLA/sanction/PEC,
insolvency, connected-party, etc. — need documents/bureau data → routed to Sales (T4).

## Placeholders (implemented behind real interfaces)

| Placeholder | Where | Swap-in |
|---|---|---|
| RAG (all 3 corpora) | `rag/retriever.py` `StubRetriever` → `[]` | Implement `VertexVectorSearchRetriever`/`PgVectorRetriever`, set `RAG_BACKEND=vertex`. One-file change; agents untouched. |
| Guidelines/Shariah corpus | Sheet 4 empty | Ingest docs into `GUIDELINES_SHARIAH` namespace. |
| Continue/Track lookup | `application/lookup.py`, Sheets 7/8 empty | Real stage values + stage→URL map, resolved server-side by `application_id`. |
| New-application URL | `NEW_APPLICATION_URL` env | Set to the real page. |
| Cloud SQL checkpointer / audit | `session_store.py`, `audit.py` | In-memory now; wire Postgres/append-only table (`SESSION_STORE_BACKEND`, `AUDIT_BACKEND`). |

## Traceability (workbook → behaviour → SOW)

| Behaviour | Excel source | SOW |
|---|---|---|
| Front door / opening + suggestions | master ("Chatbot Intent Classifier") | #1 Portal |
| Intent classification + guardrails | Sheet 1 (1.1–1.3) | #1, security posture |
| Reroute to Sales + directory | Sheet 2 | #5 RBAC / #6 handoff |
| Program recommender | Sheet 3 + master quantum table | #1 product selection |
| Guidelines / Shariah RAG | Sheet 4 (pending) | #4 eligibility narrative |
| In-principle eligibility (Tier 1) | Sheet 5 | #4 Preliminary Eligibility Engine |
| Initiate application | Sheet 6 | #1 dynamic application form |
| Continue / Track | Sheets 7, 8 (pending) | #1 tracking dashboard |
| Multi-intent + confidence/fallback | Sheet 9 | #4 decisioning integrity |
| Immutable audit trail | cross-cutting | BNM RMiT, advisory-only mandate |

Code and config tag their Excel origin in comments (e.g. `# Sheet 1.1 / INS-04`) so this
mapping stays alive.

> **Reviewer note:** confirm with BMMB that a conversational chatbot is agreed in-scope
> under Phase 1 Portal (SOW #1 lists form/upload/tracking but not a chatbot explicitly) so
> this lands in the PDD/SDD as scope, not creep.

## Non-negotiables honoured
Islamic-finance terminology enforced on every reply (`utils/terminology.py`); advisory-only
(LLM never decides eligibility); deterministic routing/thresholds; immutable audit per turn;
PII redacted before any log; secrets from env/Secret Manager only; prompts + config in
versioned human-readable files.
