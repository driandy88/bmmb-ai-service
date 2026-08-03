"""Run the RAG ingestion pipeline over a corpus (docs/RAG_PLAN.md).

    # parse + chunk only. No credentials, no database, no API calls:
    python scripts/rag_ingest.py --corpus program --dry-run

    # inspect the actual chunk text the retriever will be scoring:
    python scripts/rag_ingest.py --corpus guidelines_shariah --dry-run --show 3

    # the real thing — embed and write to `bmmb`:
    python scripts/rag_ingest.py --corpus program

    # re-embed even if the source bytes are unchanged (needed after changing
    # the embedding model or the chunk sizing):
    python scripts/rag_ingest.py --corpus program --force

    # also delete rows for documents no longer on disk:
    python scripts/rag_ingest.py --corpus program --prune

    # one ad-hoc file, and dump the chunks for diffing:
    python scripts/rag_ingest.py --corpus program --file /tmp/new_tnc.pdf \
        --dry-run --json /tmp/chunks.json

Re-running without --force is cheap by design: a document whose sha256 matches
what is already stored is skipped before parsing and before embedding.
"""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

_CHAT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CHAT_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_CHAT_DIR / ".env")

from app.agents.rag import db  # noqa: E402
from app.agents.rag.ingestion import chunker, loader, parser, pipeline  # noqa: E402
from app.agents.rag.retriever import Corpus  # noqa: E402
from app.config.settings import get_settings  # noqa: E402

_MARK = {
    pipeline.Outcome.ADDED: "+",
    pipeline.Outcome.UPDATED: "~",
    pipeline.Outcome.SKIPPED: "=",
    pipeline.Outcome.PRUNED: "-",
    pipeline.Outcome.FAILED: "!",
}


def _collect(args, corpus: Corpus) -> list[loader.SourceFile]:
    if args.file:
        path = Path(args.file).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"No such file: {path}")
        return [loader.load_file(path, corpus)]
    return loader.discover(corpus)


def _dry_run(args, corpus: Corpus, sources: list[loader.SourceFile]) -> int:
    """Parse + chunk and report, writing nothing."""
    print(f"corpus={corpus.value}  documents={len(sources)}  "
          f"max_tokens={args.max_tokens} overlap={args.overlap_tokens}  [DRY RUN]\n")

    payload, total_chunks, total_tokens = [], 0, 0

    for source in sources:
        doc = parser.parse(source)
        chunks = chunker.chunk_document(
            doc, max_tokens=args.max_tokens, overlap_tokens=args.overlap_tokens,
        )
        tokens = sum(c.token_count for c in chunks)
        total_chunks += len(chunks)
        total_tokens += tokens

        pages = f", {doc.page_count} pages" if doc.page_count else ""
        print(f"── {source.source_uri}")
        print(f"   title      : {doc.title}")
        print(f"   format     : {doc.metadata.get('format')}{pages}, {source.byte_size:,} bytes")
        print(f"   hash       : {source.content_hash[:16]}…")
        print(f"   blocks     : {len(doc.blocks)}")
        print(f"   chunks     : {len(chunks)}  (~{tokens:,} tokens, "
              f"avg {tokens // max(len(chunks), 1)}/chunk)")
        if chunks:
            widths = [c.token_count for c in chunks]
            print(f"   chunk size : min {min(widths)}, max {max(widths)} tokens")

        for chunk in chunks[: args.show]:
            print(f"\n   [{chunk.chunk_index}] ref: {chunk.ref}")
            print(f"       tokens: {chunk.token_count}  meta: {chunk.metadata}")
            for line in chunk.text.splitlines():
                print(f"       │ {line}")
        print()

        payload.extend(
            {"source_uri": source.source_uri, "corpus": corpus.value, **asdict(chunk)}
            for chunk in chunks
        )

    print(f"TOTAL: {len(sources)} document(s), {total_chunks} chunks, ~{total_tokens:,} tokens")
    print("Nothing was written (--dry-run). Drop the flag to embed and store.")

    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
        print(f"Wrote {len(payload)} chunks to {args.json}")
    return 0


def _ingest(args, corpus: Corpus, sources: list[loader.SourceFile]) -> int:
    settings = get_settings()
    store = pipeline.ChunkStore()
    before = store.stats(corpus)

    print(f"corpus={corpus.value}  documents={len(sources)}  "
          f"provider={settings.rag_embedding_provider} dim={settings.rag_embedding_dim}"
          f"{'  [FORCE]' if args.force else ''}")
    print(f"before: {before['documents']} docs, {before['chunks']} chunks\n")

    report = pipeline.ingest_sources(
        corpus, sources, store=store, force=args.force,
    )

    if args.prune and not args.file:
        on_disk = {source.source_uri for source in sources}
        for record in store.list_documents(corpus):
            if record.source_uri not in on_disk:
                store.delete_document(corpus, record.source_uri)
                report.documents.append(pipeline.DocumentOutcome(
                    record.source_uri, pipeline.Outcome.PRUNED,
                ))

    for outcome in report.documents:
        detail = f"{outcome.chunks} chunks, ~{outcome.tokens:,} tokens" if outcome.chunks \
            else (outcome.error or "")
        print(f"  {_MARK[outcome.outcome]} {outcome.source_uri:<40} "
              f"{outcome.outcome.value:<8} {detail}")

    after = store.stats(corpus)
    print(f"\n{report.summary()}")
    print(f"after : {after['documents']} docs, {after['chunks']} chunks, "
          f"{after['embedded']} embedded")

    if report.failed:
        print(f"\n{len(report.failed)} document(s) FAILED — see errors above.", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    settings = get_settings()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, choices=[c.value for c in Corpus])
    ap.add_argument("--file", help="ingest a single file instead of the whole corpus")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse + chunk and report; embed nothing, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="re-embed even when the source bytes are unchanged")
    ap.add_argument("--prune", action="store_true",
                    help="delete stored documents that no longer exist on disk")
    ap.add_argument("--show", type=int, default=0, metavar="N",
                    help="--dry-run only: print the first N chunks of each document")
    ap.add_argument("--json", metavar="PATH",
                    help="--dry-run only: write all chunks to a JSON file")
    ap.add_argument("--max-tokens", type=int, default=settings.rag_chunk_tokens)
    ap.add_argument("--overlap-tokens", type=int, default=settings.rag_chunk_overlap_tokens)
    args = ap.parse_args()

    corpus = Corpus(args.corpus)
    sources = _collect(args, corpus)
    if not sources:
        print(f"No documents found for corpus {args.corpus!r}.")
        return 0

    return _dry_run(args, corpus, sources) if args.dry_run else _ingest(args, corpus, sources)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except db.RagDbNotConfigured as exc:
        print(f"Not configured: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
