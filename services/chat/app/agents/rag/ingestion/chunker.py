"""
Stage 3 — cut a parsed document into retrievable chunks (RAG_PLAN phase 1).

Chunking is the single highest-leverage knob in a RAG system: chunks are what
gets embedded, what gets scored, and what the LLM is eventually shown. Two
rules drive the design here.

1. **Never merge across a section boundary.** A chunk that straddles "3.2
   Eligibility" and "3.3 Fees" cannot be cited honestly, and it dilutes the
   embedding with two unrelated topics. Sections are packed independently.
2. **Overlap within a section only.** A sentence at a chunk boundary would
   otherwise lose the context that makes it answerable, so each chunk after
   the first repeats the tail of its predecessor.

Token counts are estimated, not tokenised: the embedding provider isn't known
at this stage (and swapping providers must not silently re-cut the corpus).
The estimate is deliberately conservative — see CHARS_PER_TOKEN.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Optional

from app.agents.rag.ingestion.parser import ParsedDoc, TextBlock
from app.config.settings import get_settings
from app.utils.logging import get_logger

log = get_logger("rag.chunker")

# Mixed Bahasa Malaysia + English runs ~3.5 characters per token on the
# Gemini/e5 tokenisers (Malay's longer affixed words push it below plain
# English's ~4). Under-estimating chars-per-token over-estimates the token
# count, which yields slightly smaller chunks — the safe direction to err.
CHARS_PER_TOKEN = 3.5

_SENTENCE_END = re.compile(r"(?<=[.!?:])\s+")


@dataclass(frozen=True)
class Chunk:
    text: str
    ref: str                 # human-readable citation -> RetrievalChunk.ref
    chunk_index: int         # position within its document, 0-based
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN))


# ── section grouping ─────────────────────────────────────────────────────────

def _sections(blocks: list[TextBlock]) -> Iterator[tuple[tuple[str, ...], list[TextBlock]]]:
    """Yield (heading path, consecutive blocks sharing it)."""
    current_headings: Optional[tuple[str, ...]] = None
    batch: list[TextBlock] = []
    for block in blocks:
        if current_headings is None or block.headings != current_headings:
            if batch:
                yield current_headings or (), batch
            current_headings, batch = block.headings, []
        batch.append(block)
    if batch:
        yield current_headings or (), batch


# ── splitting oversized blocks ───────────────────────────────────────────────

@dataclass(frozen=True)
class _Piece:
    text: str
    page: Optional[int]
    block_id: int    # pieces from the same block rejoin with a space, not a blank line


def _split_oversized(text: str, max_tokens: int) -> list[str]:
    """Break a single over-long block into pieces that fit.

    Tables (multi-line blocks) split by row; prose splits by sentence. A single
    sentence longer than the budget is left intact rather than cut mid-clause —
    one oversized chunk beats one meaningless one.
    """
    if estimate_tokens(text) <= max_tokens:
        return [text]

    units = text.split("\n") if "\n" in text else _SENTENCE_END.split(text)
    units = [u.strip() for u in units if u.strip()]

    pieces, current, current_tokens = [], [], 0
    for unit in units:
        unit_tokens = estimate_tokens(unit)
        if current and current_tokens + unit_tokens > max_tokens:
            pieces.append(" ".join(current))
            current, current_tokens = [], 0
        current.append(unit)
        current_tokens += unit_tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def _overlap_tail(pieces: list[_Piece], overlap_tokens: int) -> list[_Piece]:
    """The trailing pieces of a chunk, up to the overlap budget."""
    if overlap_tokens <= 0:
        return []
    tail: list[_Piece] = []
    total = 0
    for piece in reversed(pieces):
        tokens = estimate_tokens(piece.text)
        if tail and total + tokens > overlap_tokens:
            break
        tail.insert(0, piece)
        total += tokens
    # Never let the overlap be the entire chunk — that would stall the packer.
    return tail if len(tail) < len(pieces) else tail[1:]


# ── citation refs ────────────────────────────────────────────────────────────

def heading_header(headings: tuple[str, ...]) -> str:
    """The section path, as the line prepended to a chunk's searchable text."""
    return " › ".join(headings)


def build_ref(title: str, headings: tuple[str, ...], page: Optional[int]) -> str:
    """Compose the citation string shown to the customer.

    Deepest two heading levels only: the full path gets unreadably long in
    nested policy documents, and the leaf is what identifies the clause. A
    heading identical to the document title is dropped — a Markdown H1 is
    usually both, and "Title — Title › 1. Scope" reads like a bug.
    """
    parts = [title] if title else []
    path = [h for h in headings if h != title]
    if path:
        parts.append(" › ".join(path[-2:]))
    ref = " — ".join(parts) or "(untitled)"
    return f"{ref} (p.{page})" if page else ref


# ── folding away fragments ───────────────────────────────────────────────────

def _headings_of(chunk: Chunk) -> list[str]:
    return list(chunk.metadata.get("headings") or [])


def _related(a: Chunk, b: Chunk) -> bool:
    """Whether two chunks are close enough in the document to share one chunk.

    Same top-level section, or one of them is unsectioned preamble (a cover
    title, an intro line before the first heading) that belongs with whatever
    it introduces.
    """
    ha, hb = _headings_of(a), _headings_of(b)
    if not ha or not hb:
        return True
    return ha[0] == hb[0]


def _join(first: Chunk, second: Chunk, title: str) -> Chunk:
    text = f"{first.text}\n\n{second.text}"
    # Keep the identity of the substantive half — a cover title merged into a
    # clause should cite the clause, not the title.
    host = first if first.token_count >= second.token_count else second
    metadata = dict(host.metadata)
    pages = [c.metadata.get("page") for c in (first, second) if c.metadata.get("page")]
    if pages:
        metadata["page"] = pages[0]
    return Chunk(
        text=text,
        ref=build_ref(title, tuple(_headings_of(host)), metadata.get("page")),
        chunk_index=first.chunk_index,
        token_count=estimate_tokens(text),
        metadata=metadata,
    )


def _merge_undersized(
    chunks: list[Chunk], *, title: str, max_tokens: int, min_tokens: int,
) -> list[Chunk]:
    """Fold fragment chunks into a neighbour they belong with.

    Policy documents are full of one-line sections, and a cover page yields a
    title with nothing under it. On their own these embed to almost nothing and
    pollute the result list, so anything under `min_tokens` is merged backwards
    into the previous chunk, or forwards into the next, whenever the two are
    related and the result still fits the budget. A fragment with no related
    neighbour is kept as-is rather than forced somewhere it doesn't belong.
    """
    if min_tokens <= 0 or not chunks:
        return chunks

    out: list[Chunk] = []
    pending: Optional[Chunk] = None       # fragment waiting for the next chunk

    for chunk in chunks:
        if pending is not None:
            if _related(pending, chunk) and \
                    pending.token_count + chunk.token_count <= max_tokens:
                chunk = _join(pending, chunk, title)
            else:
                out.append(pending)
            pending = None

        if chunk.token_count >= min_tokens:
            out.append(chunk)
        elif out and _related(out[-1], chunk) and \
                out[-1].token_count + chunk.token_count <= max_tokens:
            out[-1] = _join(out[-1], chunk, title)
        else:
            pending = chunk

    if pending is not None:
        out.append(pending)

    return [replace(chunk, chunk_index=index) for index, chunk in enumerate(out)]


# ── the packer ───────────────────────────────────────────────────────────────

def chunk_document(
    doc: ParsedDoc,
    *,
    max_tokens: Optional[int] = None,
    overlap_tokens: Optional[int] = None,
    min_tokens: Optional[int] = None,
) -> list[Chunk]:
    """Pack a parsed document into overlapping, section-scoped chunks."""
    settings = get_settings()
    max_tokens = max_tokens if max_tokens is not None else settings.rag_chunk_tokens
    overlap_tokens = (
        overlap_tokens if overlap_tokens is not None else settings.rag_chunk_overlap_tokens
    )
    min_tokens = min_tokens if min_tokens is not None else settings.rag_chunk_min_tokens
    if overlap_tokens >= max_tokens:
        raise ValueError(f"overlap_tokens ({overlap_tokens}) must be < max_tokens ({max_tokens})")

    chunks: list[Chunk] = []

    def emit(headings: tuple[str, ...], pieces: list[_Piece]) -> None:
        if not pieces:
            return
        text = pieces[0].text
        for previous, piece in zip(pieces, pieces[1:]):
            text += (" " if piece.block_id == previous.block_id else "\n\n") + piece.text
        text = text.strip()
        if not text:
            return
        # Prepend the section path. Headings are otherwise lost from the
        # searchable text entirely: "3. Eligibility / Kelayakan" lives in the
        # ref and the metadata, so a lexical search for `kelayakan` matched
        # nothing, and the embedding of a clause about turnover thresholds
        # carried no signal that the clause was *about* eligibility. Both arms
        # improve from the chunk stating its own context.
        header = heading_header(headings)
        if header:
            text = f"{header}\n\n{text}"
        pages = [p.page for p in pieces if p.page is not None]
        start_page = pages[0] if pages else None
        metadata: dict[str, Any] = {"headings": list(headings)}
        if start_page is not None:
            metadata["page"] = start_page
            if pages[-1] != start_page:
                metadata["page_end"] = pages[-1]
        chunks.append(Chunk(
            text=text,
            ref=build_ref(doc.title, headings, start_page),
            chunk_index=len(chunks),
            token_count=estimate_tokens(text),
            metadata=metadata,
        ))

    for headings, blocks in _sections(doc.blocks):
        # The prepended section path costs tokens too, so the body budget is
        # reduced by it — otherwise chunks would quietly exceed max_tokens by
        # the length of their heading path.
        header_tokens = estimate_tokens(heading_header(headings)) if headings else 0
        budget = max(max_tokens - header_tokens, 1)

        pieces: list[_Piece] = []
        for block_id, block in enumerate(blocks):
            for piece_text in _split_oversized(block.text, budget):
                pieces.append(_Piece(text=piece_text, page=block.page, block_id=block_id))

        current: list[_Piece] = []
        current_tokens = 0
        for piece in pieces:
            tokens = estimate_tokens(piece.text)
            if current and current_tokens + tokens > budget:
                emit(headings, current)
                current = _overlap_tail(current, overlap_tokens)
                current_tokens = sum(estimate_tokens(p.text) for p in current)
            current.append(piece)
            current_tokens += tokens
        emit(headings, current)

    merged = _merge_undersized(
        chunks, title=doc.title, max_tokens=max_tokens, min_tokens=min_tokens,
    )
    log.info("Chunked %r -> %d chunk(s) (max_tokens=%d, overlap=%d, %d fragment(s) folded)",
             doc.title, len(merged), max_tokens, overlap_tokens, len(chunks) - len(merged))
    return merged
