# RAG development plan — ingestion + retrieval over Cloud SQL `bmmb`

Status: **Phases 0-3 implemented; the sample corpus is ingested into `bmmb`** (2 docs, 25 chunks). Phases 4-8 pending.

This is the working plan for turning `app/agents/rag/` from a placeholder into a
real RAG system. It is written to be built **one layer at a time** — every phase
ends with something you can run and inspect on its own, without the next phase
existing.

---

## 1. What already exists (and what it constrains)

The interface is already drawn in the right place, so almost nothing outside
`app/agents/rag/` needs to change:

- `rag/retriever.py` freezes `retrieve(query, corpus, top_k) -> list[RetrievalChunk]`.
  Both consumers — `program_advisor/advisor.py:121` and `guidelines/guidelines.py:34` —
  depend only on that. **The entire retrieval pipeline (rewrite → hybrid search →
  rerank → context) is built *behind* this one method.**
- `rag/corpora.py` already has a `pgvector` branch constructing `PgVectorRetriever`,
  and `main.py` injects the retriever into the agents at startup. Turning RAG on is
  `RAG_BACKEND=pgvector` — no agent, orchestrator, prompt, or schema edits.
- `cloud-sql-python-connector[pg8000]` and `SQLAlchemy` are already in
  `requirements.txt` (for the audit/checkpointer paths), so there is no new
  connection machinery to invent — `services/extraction/app/config.py` is the
  reference implementation.
- `tests/conftest.py` forces `RAG_BACKEND=stub`. **Every layer must have a
  deterministic offline sibling** (hash embedder, no-DB dry run) or the suite stops
  running without credentials.

### Target database

Confirmed live (not assumed):

| | |
|---|---|
| Instance | `prototype-bmmb-1b62:asia-southeast1:bmmb` (Postgres 18.4) |
| Databases on it | `postgres`, `bmmb`, `bmmb_dev`, `bmmb_prod`, `bmmb_uat` |
| **RAG target** | **`bmmb`** |
| Auth | `postgres` user; password in Secret Manager `extraction-db-pass`. IAM auth also enabled on the instance. |

The `bmmb` database already contains the extraction service's tables
(`templates`, `attributes`, `template_attributes`). RAG tables are prefixed
`rag_` so the two never collide.

---

## 2. Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Source formats | PDF, DOCX, Markdown/text | Parser is a registry keyed by extension; adding a format is one function. |
| Sample corpus | Synthetic MD (program) + PDF (guidelines) | Exercises both parser paths end-to-end before real BMMB docs land. Clearly banner-marked as synthetic. |
| Embeddings | `gemini-embedding-001` **and** HF Inference API `intfloat/multilingual-e5-large`, both at 1024d | Both handle Bahasa Malaysia + English, and share one column so they are swappable with no migration. See §7. |
| Vector column dim | **From config**, not hardcoded | DDL is parameterised by `RAG_EMBEDDING_DIM`; currently 1024 (see §7 for why). |
| Lexical arm (BM25) | **Deferred to Phase 5** | Build and measure phases 0–4 first, then pick Postgres FTS+RRF vs. true BM25 on real numbers. |
| Language / FTS config | `simple` | Documents and queries are mixed BM + English; the `english` stemmer would mangle Malay. A second English-stemmed tsvector can be added later if it measures better. |

### The embedding-dimension trap

`vector(N)` is a fixed-width column — you cannot mix 768d and 1024d vectors in
it, and cosine distance between different dimensionalities is undefined. So:

- `RAG_EMBEDDING_DIM` drives the DDL, and `rag_chunks` records
  `embedding_model` per row. Retrieval filters on the active model and refuses
  to run on a mismatch, rather than silently returning garbage neighbours.
- **Switching provider across dimensionalities means re-applying the DDL and
  re-ingesting.** One command, but not free — pick before bulk ingest.
- This is why both providers are pinned to **1024**: `gemini-embedding-001` is
  Matryoshka-trained and truncates to any width, so it follows e5-large's native
  1024 rather than the reverse. Provider choice then stays open at zero cost.
  See §7 for the measurement behind that.

### Hugging Face provider

Runs over the **hosted Inference API** (`huggingface_hub`, `HF_TOKEN`), not
local weights — so no torch, no ~2GB dependency, and nothing extra in the Cloud
Run image. The trade-off is that it needs network and a token, and that model
availability depends on what the inference providers currently serve.

- **Deployed** → Vertex provider (ADC, no token to rotate, ~1.4× faster here).
- **Alternative / second opinion** → HF provider.

e5 models require `"query: "` / `"passage: "` prefixes and L2-normalised output;
the provider applies both internally so callers never think about it. The API's
own `normalize` flag is not used, since not every inference backend honours it.

---

## 3. Target structure

```
app/agents/rag/
  retriever.py            # frozen interface — unchanged
  corpora.py              # factory; wire the pgvector branch (phase 4)
  schema.sql              # rag_documents + rag_chunks DDL           [phase 0]
  db.py                   # Cloud SQL engine, shared by both modules [phase 0]
  embeddings.py           # Embedder: vertex | hf | hash             [phase 2]
  corpus/<corpus>/…       # source documents on disk
  ingestion/
    loader.py             # discover files, hash, classify           [phase 1]
    parser.py             # bytes -> ParsedDoc(blocks w/ page+heading) [phase 1]
    chunker.py            # blocks -> Chunk[] w/ citation refs        [phase 1]
    store.py              # upsert documents + chunks                 [phase 3]
    pipeline.py           # load→parse→chunk→embed→store              [phase 3]
  retrieval/
    rewriter.py           # query rewrite / expansion                 [phase 6]
    search.py             # vector KNN + lexical arms                 [phase 4,5]
    fusion.py             # RRF + rerank                              [phase 5,6]
    context.py            # dedupe, token budget, citation refs       [phase 7]
    pipeline.py           # PgVectorRetriever.retrieve()              [phase 4]
scripts/
  rag_db.py               # apply/inspect/drop schema                 [phase 0]
  rag_ingest.py           # run ingestion, --dry-run                  [phase 1,3]
  rag_search.py           # query the pipeline, show each stage       [phase 4+]
```

## 4. Schema

Two tables, not one. Document-level provenance is what makes citations
meaningful and re-ingest idempotent (skip unchanged files by content hash).

```
rag_documents(id, corpus, source_uri UNIQUE per corpus, title, content_hash,
              byte_size, page_count, metadata jsonb, ingested_at)

rag_chunks(id, document_id FK CASCADE, corpus, chunk_index, ref, text,
           token_count, embedding vector(<RAG_EMBEDDING_DIM>),
           embedding_model, tsv tsvector GENERATED STORED, metadata jsonb)
```

Indexes: HNSW `vector_cosine_ops` on `embedding`, GIN on `tsv`, btree on
`(corpus)`. `ref` is the human-readable citation (`"Program T&C — §3.2 (p.4)"`)
that surfaces as `RetrievalChunk.ref` in the API response.

---

## 5. Phases

Each row is independently runnable. Phase 1 needs **zero credentials**, and so
does phase 2 with `RAG_EMBEDDING_PROVIDER=hash`; phase 3 is the first that
writes to `bmmb`.

| # | Layer | Deliverable | Verify with |
|---|---|---|---|
| **0** ✅ | Foundations | `schema.sql`, `db.py`, RAG settings block — **applied to `bmmb`** | `scripts/rag_db.py inspect` |
| **1** ✅ | Parse + chunk | `loader`, `parser`, `chunker`, sample MD + PDF corpus | `scripts/rag_ingest.py --dry-run` |
| **2** ✅ | Embeddings | `embeddings.py` — vertex / hf / hash behind one interface | `scripts/rag_embed.py` |
| **3** ✅ | Ingestion complete | `store.py`, `pipeline.py`, real `ingest()` | `scripts/rag_ingest.py` (+`--force`, `--prune`) |
| **4** | Vector retrieval | `PgVectorRetriever.retrieve()`, KNN only | `RAG_BACKEND=pgvector` → real citations in `/chat` |
| **5** | Hybrid + BM25 decision | lexical arm, RRF fusion, benchmark | Compare vector / lexical / fused on golden queries |
| **6** | Rewrite + rerank | `rewriter.py`, reranker, per-stage flags | A/B each stage on/off |
| **7** | Context + integration | `context.py`; grounded guidelines prompt (replaces TODO at `guidelines.py:38`) | End-to-end grounded, cited answers |
| **8** | Eval + ops | Golden query set, deploy env, docs | Recall/precision per config |

## 6. Phase 1 results

Against the two synthetic fixtures, with the default 400/60/40 token settings:

| Corpus | Source | Blocks | Chunks | Avg | Range |
|---|---|---|---|---|---|
| `program` | `sme_financing_programmes.md` (7 KB) | 29 | 12 | 154 tok | 71–259 |
| `guidelines_shariah` | `shariah_guidelines.pdf` (3 pages) | 14 | 12 | 113 tok | 57–168 |

Refs come out citable, e.g.
`SME Financing — Shariah Guidelines — 2. SHARIAH CONTRACTS USED › 2.3 Ijarah Muntahia Bittamleek (p.2)`.

Three things the fixtures caught that are worth remembering when real documents
arrive:

- **Tables and lists must keep their line breaks.** Joining a bullet list into
  one line destroys item boundaries; splitting a table across chunks strips the
  header row from the tail half. Both are handled in `parse_markdown`, and both
  will need re-checking against real PDFs, where tables extract far less cleanly.
- **PDF heading recovery is heuristic.** There is no semantic heading in a PDF,
  so structure is inferred from numbering (`3.2 …`) and short all-caps lines. It
  works on the fixture; a real document with a different heading convention will
  degrade to shallower refs, not to lost text.
- **Fragments need folding.** A cover page produced a 10-token title-only chunk.
  Anything under `RAG_CHUNK_MIN_TOKENS` is now merged into a related neighbour.

## 7. Phase 2 results

Both providers verified live, at the same width, on the same probe set:

| | vertex | hf |
|---|---|---|
| Model | `gemini-embedding-001` | `intfloat/multilingual-e5-large` |
| Auth | ADC | `HF_TOKEN` |
| Width | 1024 (truncated from 3072) | 1024 (native) |
| 4 docs | 828 ms | 1191 ms |
| 1 query | 204 ms | 346 ms |
| Cross-lingual BM↔EN | 0.853 | 0.869 |
| Unrelated topic | 0.688 | 0.771 |
| Real corpus | 36 ms/chunk | — |

### Why the shared width is 1024, not 768

The original plan said 768. That does not survive contact with the requirement
that both providers be interchangeable:

- Hugging Face's inference providers register each model against **specific
  tasks**. `multilingual-e5-base` (768) is published for `sentence-similarity`
  only, so `feature_extraction` on it fails outright — it is not usable as an
  embedding backend at all, regardless of its width.
- Of the multilingual models actually served for `feature-extraction`,
  `multilingual-e5-large` is the practical choice, and it is **natively 1024**.
- `gemini-embedding-001` is Matryoshka-trained, so it truncates to 1024 as
  happily as to 768.

So the HF side fixes the number and Vertex follows. The schema was re-applied at
`vector(1024)` while both tables were still empty, costing nothing.

### Two things measured, not assumed

- **Truncated Vertex vectors are not unit length.** The full 3072-d output has
  |v| = 1.0, but MRL truncation returns |v| ≈ 0.59 at 768 and ≈ 0.62 at 1024.
  Cosine distance is unaffected, but inner product silently is, and vectors
  would not be comparable across providers. `VertexEmbedder` re-normalises.
- **e5 compresses its similarity range.** Unrelated text still scores 0.771,
  versus 0.688 on gemini — a 0.098 spread against gemini's 0.165. Neither is
  wrong, but it means **absolute score thresholds are not portable** between
  providers. Relative ranking is the only thing worth acting on, which argues
  against a naive `RAG_MIN_SCORE` cutoff and in favour of the phase 6 reranker.

## 8. Phase 3 results

The corpus is live in `bmmb`: **2 documents, 25 chunks, all embedded** with
`gemini-embedding-001` at 1024-d. Verified in the database, not just in the
CLI's own output — every chunk has `vector_dims = 1024`, `vector_norm ≈ 1.0`, a
populated `tsv`, and there are zero duplicate `(document_id, chunk_index)` rows
after repeated runs.

The four paths, each exercised against the real database:

| Command | Result |
|---|---|
| `rag_ingest.py --corpus program` (first run) | `+ added   12 chunks` |
| same, re-run | `= skipped` — no parse, no embed, no write |
| `--force` | `~ updated  12 chunks` — replaced, not appended |
| `--prune` after deleting a file | `- pruned` — rows removed, chunks cascade |

### The skip is about cost, not just correctness

An unchanged document short-circuits on its sha256 **before parsing and before
embedding**. That ordering is the point: embedding is the billed, slow stage,
so re-running the job is genuinely free rather than merely idempotent. A test
asserts the embedder is not called on the second run, because a refactor could
easily preserve the "skipped" label while quietly still embedding.

`--force` skips the hash *comparison* but still does the existence lookup — one
indexed query, and without it every forced re-run would report documents it
replaced as newly "added".

Unchanged bytes alone are **not** enough to skip. The check also compares the
stored `embedding_model` against the active embedder, because swapping
`RAG_EMBEDDING_PROVIDER` leaves every source file untouched while making every
stored vector incomparable with the queries the retriever will now issue —
cosine distance between two models' vectors is noise, and it degrades results
silently rather than raising. Verified against the live database: swapping to
`hf` logs `embedded by gemini-embedding-001, now using
intfloat/multilingual-e5-large` and re-embeds. A document with zero stored
chunks (an interrupted run) or chunks from more than one model is treated the
same way.

`--prune` is opt-in and never implied. A file that is missing because someone
is mid-edit must not silently delete its chunks.

### A phase-1 bug that only ingestion could reveal

Querying the stored `tsv` for `kelayakan` returned **zero** chunks, even though
the corpus is explicitly about eligibility. The word appears exactly once in
the source — in the heading `## 3. Eligibility / Kelayakan` — and the chunker
routed heading text into `ref` and metadata but never into the chunk body.

Both retrieval arms were degraded by this:

- the **lexical** arm could not match any heading-only term at all;
- the **vector** arm embedded a clause about "minimum annual sales turnover of
  RM100,000" with nothing anywhere in its text indicating the clause was *about*
  eligibility.

Fixed by prepending the section path to each chunk's text, with the header's
tokens deducted from that section's budget so chunks cannot overshoot
`RAG_CHUNK_TOKENS` by the length of their heading path. After re-ingesting,
`kelayakan` matches 3 chunks, `yuran` 1, `takaful` 2.

Worth noting for when the real BMMB documents arrive: this class of bug is
invisible in a dry run. The chunks looked perfectly reasonable in
`--dry-run --show`; it only surfaced by querying what had actually been stored.

### Where the parser dependencies live

`pypdf`, `python-docx`, and `huggingface-hub` are in `requirements.txt`, all
imported lazily — a Markdown-only corpus loads no PDF parser, and the Vertex
provider never loads the HF client. Nothing else was added: the PDF fixture is
committed as a binary rather than generated, so no PDF-authoring library is a
dependency of this service.

### Phase gates worth knowing about

- **Phase 3** is where an accidental re-ingest could duplicate rows — hence
  content-hash idempotency and `--dry-run` land before it, in phase 1.
- **Phase 4** flips `RAG_BACKEND`. Until then `stub` stays the default and
  nothing in the live service changes.
- **Phase 7** is the only phase that edits code outside `app/agents/rag/`.
