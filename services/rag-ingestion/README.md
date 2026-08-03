# rag-ingestion

Offline knowledge-layer pipeline for the BMMB **Customer Service Agent**. It turns
source documents (designed slide decks, policy docs, a directory) into an indexed,
governed corpus in Cloud SQL `pgvector`, which the chat service's `PgVectorRetriever`
then queries behind the **frozen `Retriever` interface** — no agent, orchestrator,
prompt, or schema changes.

> **Two deliverables, cleanly separated.** This service is deliverable #1 (the
> offline pipeline + evaluation). Deliverable #2 is the real retriever inside
> `services/chat/app/agents/rag/`, swapped in via `RAG_BACKEND=pgvector`.
> Structure and sequence come from the Build Brief; rationale from the *RAG
> Design Document*.

## Pipeline

```
Source docs (PDF decks, policy docs, directory)
  │
  ├─ 1. PARSE    → clean Markdown per page (Gemini vision — text extraction fails on designed slides)
  ├─ 2. VERIFY   → SME signs off the extracted figures                       ← human gate
  ├─ 3. CURATE   → reorganise into per-program canonical docs; classify access_tier
  ├─ 4. CHUNK    → structure-aware, breadcrumbed, tables never split
  ├─ 5. ENRICH   → attach the metadata schema
  ├─ 6. EMBED    → gemini-embedding-001 @ 1536, task-typed, batched
  ├─ 7. INDEX    → upsert into Cloud SQL pgvector (idempotent on chunk_id)
  │
  └─ QUERY TIME (chat side): rewrite → filter by corpus/tier/freshness → hybrid search → RRF → rerank → floor
```

## Clean-step rule

Every stage **reads the previous stage's directory and writes its own**. Each is
independently runnable, idempotent, and inspectable — open `data/03_curated/` and
read the Markdown, or `data/04_chunks/` and read the chunks, without running
anything else. No stage reaches back more than one step; a failed stage never
corrupts an earlier one.

```
data/00_raw/ → 01_parsed/ → 02_verified/ → 03_curated/ → 04_chunks/ → 05_enriched/
```

`data/` is git-ignored (only the empty structure is tracked); artifacts stay local.

## Layout

```
cli.py                 single entrypoint — `python cli.py <stage> [opts]`
config/                settings.py (env), documents.yaml (source manifest), corpora.yaml
prompts/               extraction.md (vision→Markdown), query_rewrite.md, …  (added per phase)
pipeline/              stage1_parse … stage7_index
db/                    schema.sql (rag_chunks DDL + HNSW/GIN indexes), migrations/
eval/                  golden_set.csv, run_ragas.py, run_deterministic.py, runs/
notebooks/             rag_pipeline_test.ipynb (per-stage + retrieval + end-to-end)
tests/                 test_chunking / test_metadata / test_retrieval_filters
data/                  git-ignored intermediate artifacts (00_raw … 05_enriched)
```

## Usage

Run from this directory so `config` and `pipeline` import as top-level packages:

```bash
python cli.py --help                 # lists every stage
python cli.py stage1 --doc talk_pp_commercial_financing
python cli.py stage4 --corpus program
python cli.py stage7 --dry-run
```

Apply the schema to the Cloud SQL instance (needs the `vector` extension available):

```bash
psql "$DATABASE_URL" -f db/schema.sql
```

Configuration is entirely environment-driven — see `config/settings.py`; secrets
come from Secret Manager → env, never source. `RAG_BACKEND`, model ids, dimensions,
thresholds, and corpus names live in config, never hardcoded in `.py`.

## Build order (build one phase at a time; commit + report after each)

| Phase | Work | Status |
|---|---|---|
| **0** | Scaffold, `settings.py`, `documents.yaml`, `corpora.yaml`, `schema.sql`, README, CLI | ✅ this commit |
| 1 | Stage 1 parse + `prompts/extraction.md`, on one document | ⬜ |
| 2 | Stage 2 verify report + sign-off gate | ⬜ |
| 3 | Stage 3 curate + Stage 4 chunk | ⬜ |
| 4 | Stage 5 enrich + Stage 6 embed + Stage 7 index | ⬜ |
| 5 | `PgVectorRetriever`: filters → hybrid → RRF → rerank → floor | ⬜ |
| 6 | Swap into chat via `RAG_BACKEND=pgvector`; query rewriting | ⬜ |
| 7 | `golden_set.csv`, `run_ragas.py`, `run_deterministic.py` | ⬜ |

## Non-negotiables (violating these is a defect — brief §2, §11)

- **Islamic finance terminology** — *financing* not loan, *profit rate* not interest; handle "loan"/"interest" at query rewriting, never in output.
- **Access tiers are a SQL security control** — `access_tier: internal` content (credit-filtering criteria) must never reach the customer channel. Filtering it in a prompt is not enough; it defeats the ADV-05 threshold-probing guardrail from the knowledge side.
- **Chunks are data, not instructions** — retrieved text is untrusted; "ignore previous instructions" in a document must be inert.
- **Honest abstention** — below the relevance floor, return no context; the agent declines and offers a Sales handoff. A fluent guess is a compliance incident.
- **Never mix embedding models/dimensions** in one index — that is a versioned re-embed.
- **GCP only, in-region** — Vertex for embeddings/LLM, Cloud SQL, GCS, Secret Manager. No third-party vector DB, no OpenAI (including as a RAGAS judge).
- **Retrieval informs, never decides** — RAG output never feeds the eligibility rules function.
- Human sign-off (Stage 2) gates indexing; **Stage 3 refuses to run without it**.

## Known gaps (surfaced, not silently filled — brief §12)

- **`guidelines_shariah` has no source content yet** (workbook Sheet 4 empty). Plumbing is built; the corpus stays empty and this blocks INS-03 / AMB-05. Escalate to BMMB as a dated dependency.
- **`sales_dir`** is a deterministic state→region→contact lookup — better served by a plain SQL table than by vector search; kept in the enum for interface consistency.
- **Interface question for Phase 5:** the frozen `Retriever.retrieve(query, corpus: Corpus, top_k)` takes a *single* `Corpus`, but AMB-05 needs `program` + `guidelines_shariah` **together**. Resolve *how* multi-corpus is expressed without changing call-sites before starting Phase 5 (see the phase report).
- Confirm current Vertex model ids at build time rather than assuming.
