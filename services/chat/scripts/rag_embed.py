"""Inspect the embedding layer (docs/RAG_PLAN.md phase 2).

    python scripts/rag_embed.py                      # configured provider
    python scripts/rag_embed.py --provider hash      # offline, no credentials
    python scripts/rag_embed.py --provider hf        # HF Inference API (needs HF_TOKEN)
    python scripts/rag_embed.py --corpus program     # embed real chunks, time it

Prints the vector width, the L2 norm (should be 1.0 for every provider), and a
similarity matrix over a fixed probe set. The probe set is built so the matrix
is readable as a quality check rather than a smoke test:

  * pairs 1-2 are the same question in Bahasa Malaysia and English — a
    multilingual model should score these high;
  * pair 3 is a related-but-different topic — should be mid;
  * pair 4 is unrelated — should be low.

If cross-lingual pairs do not clearly outscore the unrelated pair, the model or
its task-type/prefix handling is wrong, and no amount of tuning further down the
pipeline will fix it.
"""
import argparse
import sys
import time
from pathlib import Path

_CHAT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CHAT_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_CHAT_DIR / ".env")

from app.agents.rag import embeddings  # noqa: E402
from app.agents.rag.ingestion import chunker, loader, parser  # noqa: E402
from app.agents.rag.retriever import Corpus  # noqa: E402
from app.config.settings import Settings, get_settings  # noqa: E402

PROBES = [
    "Apakah syarat kelayakan pembiayaan PKS?",          # BM: SME eligibility
    "What are the eligibility criteria for SME financing?",
    "How long does the application process take?",       # related, different topic
    "What is the weather in Kuala Lumpur today?",        # unrelated
]

QUERY = "Bolehkah saya selesaikan pembiayaan lebih awal?"   # BM: early settlement


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))     # both are unit vectors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["vertex", "hf", "hash"],
                    help="override RAG_EMBEDDING_PROVIDER")
    ap.add_argument("--model", help="override RAG_EMBEDDING_MODEL")
    ap.add_argument("--corpus", choices=[c.value for c in Corpus],
                    help="also embed this corpus's real chunks and time it")
    ap.add_argument("--limit", type=int, default=25,
                    help="max chunks to embed with --corpus (default 25)")
    args = ap.parse_args()

    settings = get_settings()
    if args.provider or args.model:
        from dataclasses import replace
        settings = replace(
            settings,
            rag_embedding_provider=args.provider or settings.rag_embedding_provider,
            rag_embedding_model=args.model if args.model is not None
            else ("" if args.provider else settings.rag_embedding_model),
        )

    began = time.time()
    embedder = embeddings.get_embedder(settings)
    print(f"provider : {settings.rag_embedding_provider}")
    print(f"model    : {embedder.model}")
    print(f"dim      : {embedder.dim}  (init {(time.time() - began) * 1000:.0f}ms)\n")

    began = time.time()
    vectors = embedder.embed_documents(PROBES)
    doc_ms = (time.time() - began) * 1000

    began = time.time()
    query_vector = embedder.embed_query(QUERY)
    query_ms = (time.time() - began) * 1000

    norms = [round(sum(v * v for v in vec) ** 0.5, 6) for vec in vectors]
    print(f"embed_documents({len(PROBES)}) : {doc_ms:.0f}ms   norms={set(norms)}")
    print(f"embed_query(1)     : {query_ms:.0f}ms\n")

    print("similarity matrix (documents):")
    print("      " + "".join(f"{i:>8}" for i in range(len(PROBES))))
    for i, row in enumerate(vectors):
        cells = "".join(f"{cosine(row, col):>8.3f}" for col in vectors)
        print(f"  [{i}] {cells}   {PROBES[i][:44]}")

    cross_lingual = cosine(vectors[0], vectors[1])
    related = cosine(vectors[0], vectors[2])
    unrelated = cosine(vectors[0], vectors[3])
    print(f"\ncross-lingual (BM↔EN, same question) : {cross_lingual:.3f}")
    print(f"related topic                        : {related:.3f}")
    print(f"unrelated topic                      : {unrelated:.3f}")
    print(f"spread (cross-lingual − unrelated)   : {cross_lingual - unrelated:.3f}")

    # Judged on ORDERING, not on an absolute margin. Different model families
    # use wildly different slices of the cosine range — e5 packs everything
    # into ~0.75-0.90 while gemini spreads wider — so any fixed threshold would
    # just measure which family the model belongs to. A narrow spread does mean
    # score cutoffs are a bad idea downstream; relative ranking is what counts.
    if settings.rag_embedding_provider == "hash":
        print("verdict: n/a — the hash provider has no semantics by design")
    elif cross_lingual > related > unrelated:
        print("verdict: PASS — same-meaning > related > unrelated, correctly ordered")
    elif cross_lingual > unrelated:
        print("verdict: WEAK — same-meaning still beats unrelated, but the "
              "middle pair is out of order")
    else:
        print("verdict: FAIL — check task types / prefixes for this model")

    print(f"\nquery vs documents ({QUERY[:40]}…):")
    for i, vec in enumerate(vectors):
        print(f"  [{i}] {cosine(query_vector, vec):>6.3f}  {PROBES[i][:52]}")

    if args.corpus:
        sources = loader.discover(Corpus(args.corpus))
        chunks = []
        for source in sources:
            chunks.extend(chunker.chunk_document(parser.parse(source)))
        chunks = chunks[: args.limit]
        if not chunks:
            print(f"\nNo chunks in corpus {args.corpus}.")
            return 0
        began = time.time()
        embedder.embed_documents([c.text for c in chunks])
        elapsed = (time.time() - began) * 1000
        print(f"\ncorpus {args.corpus}: embedded {len(chunks)} real chunks in "
              f"{elapsed:.0f}ms ({elapsed / len(chunks):.0f}ms/chunk)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, embeddings.EmbeddingDimensionMismatch) as exc:
        print(f"\n{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
