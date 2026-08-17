# chat-v2 — the agentic variant

An **experiment** running beside `services/chat`, not a replacement. Same policy, same corpus, same
approved wording; the difference is that a tool-calling agent plans each turn instead of a fixed
graph routing it.

Deploys to its own Cloud Run service (`chat-service-v2`). Deleting this folder, its workflow, and
`CHAT_V2_SERVICE_URL` in the gateway removes the experiment entirely.

## Why

v1's rigidity is not really its graph — it is the keyword matching in its handlers (`_APPLY_KEYWORDS`,
`_PURPOSE_KEYWORDS`, `detect_triggers`, `_tier2_signal`) and the single-label classifier ceiling. A
message that is three things at once cannot be represented, and "yeah go on lah" does not match "ok".

## Shape

```
guardrail (regex) → agent(tools) → terminology lint → audit
```

The bookends are deterministic on every path, exactly as in v1. What changed is the middle.

| Stays code | Is the agent's job |
|---|---|
| terminology lint (EN + BM) | which tools to call, in what order |
| eligibility rules engine | how to phrase things |
| programme selection by quantum range | when to clarify vs hand off |
| approved refusal/redirect wording | reading purpose against programme names |
| PII redaction + audit | resolving "JB" → Johor |
| stripping links no tool produced | |

The model decides *whether* to refuse; `responses.yaml` decides *what the refusal says*.

## Vendored from v1

17 files under `VENDORED.txt` are byte-identical copies of `services/chat`, kept at the same relative
paths so their imports work unchanged. `tests/test_no_drift.py` fails CI the moment one diverges.

Do not edit a vendored file. Re-copy from v1, or add a new module (that is what
`runtime/terminology_ms.py` is — v1's lint is English-only, and v2 writes far more Malay).

## Endpoints

| Path | |
|---|---|
| `POST /v2/chat` | same `ChatRequest` / `ChatResponse` as v1 |
| `POST /v2/chat/stream` | same SSE frames (start / token / done) |
| `GET /try` | minimal chat UI for testing this service directly |
| `GET /v2/policy` | the assembled system prompt — v2's equivalent of reading `routing.py` |
| `GET /health` | |

## Policy

Behaviour lives in `app/policy/`, not in Python. `prompts/*.md` are assembled in order into one
system prompt; the intent taxonomy is injected live from `intents.yaml` so scope stays single-sourced
with v1.

## Local

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q          # includes the v1 drift guard
PYTHONPATH=. .venv/bin/uvicorn app.main:app --reload
```

`RAG_BACKEND=stub` runs without a database; the knowledge tool then returns no results, which also
exercises the "say you don't know rather than guess" path.

## Known gaps

- Citation preview (`/chat/source`) is not implemented, so the gateway's v2 router omits it.
- `lookup_application` is stubbed exactly as in v1 — pending the real stage list.
- Malay terminology is two-tier: unambiguous riba terms are rewritten, ambiguous ones (`faedah` also
  means "benefit", `bunga` also means "flower") are flagged for review rather than mangled.
