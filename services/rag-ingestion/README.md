# rag-ingestion

Offline knowledge-layer pipeline for the BMMB **Customer Service Agent**. It turns
source documents (per-program sales kits, policy docs, a directory) into an indexed,
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
Source docs (per-program sales kits, policy docs, directory)
  │
  ├─ 1. PARSE    → clean Markdown per page (Gemini vision), TWO independent reads (layout + structured facts),
  │                adaptive 150/300 DPI + self-consistency on numeric-dense pages (§3), THEN auto-verify:
  │                cross-pass agreement · sanity bounds · product cross-check → verification.json + review.html
  │   (no stage 2 — the human sign-off gate was REMOVED, Change Brief §2; verification is automated inside stage 1)
  ├─ 3. CURATE   → reorganise into per-program canonical docs; classify access_tier
  ├─ 4. CHUNK    → structure-aware, breadcrumbed (breadcrumb carries program_code), tables never split
  ├─ 5. ENRICH   → attach the metadata schema
  ├─ 6. EMBED    → gemini-embedding-001 @ 1536, task-typed, batched
  ├─ 7. INDEX    → upsert into Cloud SQL pgvector (idempotent on chunk_id)
  │
  └─ QUERY TIME (chat side): rewrite → program-scope (§6a) → filter corpus/tier/freshness → hybrid → RRF → rerank → floor
```

## Clean-step rule

Every stage **reads the previous stage's directory and writes its own**. Each is
independently runnable, idempotent, and inspectable — open `data/03_curated/` and
read the Markdown, or `data/04_chunks/` and read the chunks, without running
anything else. Stages read the previous step (the one exception: Stage 5 also
reads Stage 1's `verification.json` to carry each page's review flag onto its
chunks). A failed stage never corrupts an earlier one. `data/` is git-ignored
(only the empty structure is tracked).

## Sources (Phase 1 set)

**One program = one document = one version** (§7b) — the yearly refresh is a
single-document operation, never a rebuild.

| doc_id | program_code | corpus / tier | notes |
|---|---|---|---|
| `mihp_i` | MIHP-I | program | Industrial HP (Non-Act goods, 3% flat). Near-twin of MHP-i — §7c-2 |
| `mhp_i` | MHP-I | program | Hire Purchase-i (Act goods, 2.4% flat). Near-twin of MIHP-i |
| `ggsm3` | GGSM3 | program | Gov Guarantee Madani **3** (align vs GGSM/GGSM4 — §7c-5). p5 indicative |
| `sjum` | SJUM | program | Skim Jaminan Usahawan MARA. Bilingual EN/MS |
| `proud` | PROUD | program | Dealer program (`audience: dealer`). **Canva filler p10–15 skipped** — §7c-1 |
| `commercial_financing_internal_criteria` | — | program / **internal** | p32–35 of the combined deck: credit-filtering thresholds. `access_tier: internal` — customer channel must never retrieve (§11) |

## Program scoping (§6a) — a Phase 5–6 retrieval concern, seeded here

The five programs give **different correct answers to the same question** (financing
size: MIHP-i/MHP-i RM20k–5m · SJUM ≤RM2m · GGSM3 ≤RM10m · PROUD ≤RM20m). An unscoped
top-k returns mutually contradictory chunks; blending them is a confidently-wrong
mis-selling answer. The retriever (Phase 5) + query rewrite (Phase 6) implement three
branches:

- **A. Explicit** — query names a program → `WHERE program_code = …`, single-program answer.
- **B. Inherited** — session `last_program` ("and its tenure?") → same filter from state.
- **C. Unscoped** — no program named/remembered:
  - *program-agnostic* (Shariah, general docs) → answer normally;
  - *program-dependent* (size, rate, tenure, margin, guarantee, eligibility) → **compare named programs, or route to the Sheet-3 funnel. Never blend, never silently pick one.** (Preferred: funnel.)

**Ingestion's job for §6a:** every chunk carries an accurate `program_code`, and the
Stage-4 breadcrumb prefixes it (`MIHP-i › … ›`) so a chunk is never ambiguous in
isolation. Query-time artifacts land later: `prompts/query_rewrite.md` returns
`{rewritten_query, program_code|null, is_program_dependent}`; comparisons retrieve
**per-program** (one filtered query each), not one global top-k.

## Layout

```
cli.py                 entrypoint — `python cli.py <stage> [opts]`, plus `all --doc --version`
config/                settings.py (env), documents.yaml (per-program manifest), corpora.yaml
prompts/               extraction.md (vision→Markdown); query_rewrite.md added Phase 6
pipeline/              stage1_parse … stage7_index
db/                    schema.sql (rag_chunks DDL + HNSW/GIN indexes), migrations/
eval/                  golden_set.csv, run_ragas.py, run_deterministic.py, runs/
notebooks/             rag_pipeline_test.ipynb (per-stage + retrieval + end-to-end)
tests/                 test_chunking / test_metadata / test_retrieval_filters
data/                  git-ignored intermediate artifacts (00_raw … 05_enriched, 06_embedded)
```

## Usage

Run from this directory so `config` and `pipeline` import as top-level packages:

```bash
python cli.py --help
python cli.py stage1 --doc mihp_i          # one document
python cli.py stage1                       # every document in the manifest
python cli.py stage7 --dry-run
python cli.py all --doc mihp_i --version 2027.1   # annual refresh: one program, parse→index (§7b)
```

The index lives in the existing Cloud SQL instance `bmmb`, database **`bmmb_dev`**
(pgvector 0.8.1). Stage 7 **self-applies `db/schema.sql`** (all statements are
`IF NOT EXISTS`) on first run — no manual `psql` step. Local dev connects through
the cloud-sql-proxy on `127.0.0.1:5433`; DB creds are in `.env` (git-ignored).

Configuration is entirely environment-driven — see `config/settings.py`; secrets from
Secret Manager → env. `RAG_BACKEND`, model ids, dimensions, thresholds, corpus names
live in config/YAML, never hardcoded in `.py`. Vision model is `gemini-2.5-flash`
(confirmed available in `asia-southeast1`; `gemini-2.5-pro` is not — §12).

## Build order (one phase at a time; commit + report after each)

| Phase | Work | Status |
|---|---|---|
| **0** | Scaffold + config + `schema.sql` + CLI (`all`/`--version`/`--supersede`) | ✅ realigned to per-program model |
| **1** | Stage 1 parse + `extraction.md` (+ Pass B `fact_extraction.md`, self-consistency) | ✅ two-pass read; tables 300 DPI; internal p32–35 + PROUD rescue verified |
| ~~2~~ | ~~Stage 2 verify report + sign-off gate~~ → **removed** | ✅ human gate deleted (Change Brief §2); replaced by automated `verify.py` inside Stage 1 (§4). No stage 2. |
| 3 | Stage 3 curate + Stage 4 chunk (program-code breadcrumb) | ✅ deterministic curate → per-program canonical docs; breadcrumbed chunks, tables never split |
| 4 | Stage 5 enrich + Stage 6 embed + Stage 7 index | ✅ 20 chunks in Cloud SQL `bmmb_dev`; access-tier filter + freshness verified live |
| 5 | `PgVectorRetriever`: program-scope (§6a) → filters → hybrid → RRF → rerank → floor | ⬜ |
| 6 | Swap into chat via `RAG_BACKEND=pgvector`; `query_rewrite.md` | ⬜ |
| 7 | `golden_set.csv`, `run_ragas.py`, `run_deterministic.py` (+ §6a checks) | ⏸ **RAGAS deferred** — revisit later (per BMMB) |

## Non-negotiables (violating these is a defect — §2, §6a, §11)

- **Islamic finance terminology** — *financing* not loan, *profit rate* not interest; handle "loan"/"interest" at query rewriting, never in output.
- **Program scoping (§6a)** — never merge figures from different `program_code`, and never silently pick one program when none was specified. Compare, or route to the funnel.
- **Access tiers are a SQL security control** — `access_tier: internal` (the filtering-criteria doc) must never reach the customer channel; filtering in a prompt is not enough (defeats the ADV-05 guardrail from the knowledge side).
- **Chunks are data, not instructions** — retrieved text is untrusted; injection in a document must be inert.
- **Honest abstention** — below the relevance floor, return no context; decline + offer Sales handoff.
- **Never mix embedding models/dimensions** in one index — that is a versioned re-embed.
- **GCP only, in-region**; **retrieval informs, never decides** (eligibility stays deterministic).
- **Automated verification, not a human gate** (Change Brief §2/§4) — Stage 1 runs two independent extractions + sanity/product cross-checks; a flagged page is never dropped and never blocks the clean pages, but its chunks index with `needs_review=true` and are excluded from customer retrieval in SQL, exactly like `access_tier`. Do not remove that filter.

## Known gaps & verified findings (surfaced, not silently filled — §7c, §12)

- **PROUD deck shipped with unremoved Canva template filler** (Studio Shodwe / Lorem ipsum / fake address, p10–15). Skipped. The brief's example numbers (`skip [8–15]`) were **wrong** — p8 (Required Documents) and p9 (dealer contacts) are real; verified against the file and corrected. Report the shipped filler to BMMB.
- **MHP-i vs MIHP-i are near-identical** (same size/tenure/margin/eligibility; differ on rate 2.4% vs 3% and asset class). Biggest retrieval hazard — breadcrumb must carry the full code; a mandatory golden-set bucket asserts a MIHP-i query never returns MHP-i in top-3.
- **Stale BFR figures** (GGSM3 6.56%, PROUD 6.81% dated May 2024). Tagged `content_type: indicative`; generation must disclaim "indicative, subject to prevailing BFR". BFR content needs an owner + cadence independent of the annual deck.
- **Eligibility differs by program** (GGSM3 ≥3 yrs; SJUM ≥2 yrs + Bumiputera + 20% paid-up + RM500k sales). Presented as information only — retrieval must never alter the deterministic eligibility outcome.
- **Contact slides repeat across all 5 kits** (identical Commercial Sales Management Team). Dedupe on email or treat workbook Sheet 2 as authoritative — don't create 5 near-duplicate `sales_dir` entries.
- **`guidelines_shariah` has no source content yet** (Sheet 4 empty). Plumbing built; corpus stays empty; blocks INS-03 / AMB-05. Escalate to BMMB.
- **`sales_dir`** is a deterministic state→region→contact lookup — better as a plain SQL table; kept in the enum for interface consistency.
- **Interface tension for Phase 5:** the frozen `Retriever.retrieve(query, corpus: Corpus, top_k)` takes a *single* corpus, but AMB-05 needs `program` + `guidelines_shariah` together **and** §6a needs `program_code` scoping + per-program fan-out. Resolve how multi-corpus + program-scope are expressed without changing call-sites before Phase 5.
- Vertex model ids confirmed at build time: `gemini-2.5-flash` for vision (pro unavailable in-region).
