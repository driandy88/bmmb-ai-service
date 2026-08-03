"""
Stage 2 — turn a source file into clean text that still knows where it came
from (RAG_PLAN phase 1).

The output is not one big string. Every `TextBlock` carries its **page number**
and its **heading path**, because that is what the chunker turns into a
citation `ref` — "SME Financing Programmes — 3.2 Eligibility (p.4)" is useful
to a customer; "chunk 47" is not. Losing structure here is unrecoverable
later, so structure is preserved even though it costs a little complexity.

Formats are a registry keyed by extension. Adding one (HTML, CSV, …) means
writing one function and adding one entry — no other stage changes.

Third-party parsers (pypdf, python-docx) are imported lazily inside their
handlers so a Markdown-only run needs neither installed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.agents.rag.ingestion.loader import SourceFile
from app.utils.logging import get_logger

log = get_logger("rag.parser")


@dataclass(frozen=True)
class TextBlock:
    """One paragraph-ish unit of text plus where it sits in the document."""
    text: str
    page: Optional[int] = None            # 1-based; None when the format has no pages
    headings: tuple[str, ...] = ()        # ("3. Eligibility", "3.2 SME criteria")


@dataclass
class ParsedDoc:
    title: str
    blocks: list[TextBlock]
    page_count: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Whole document as plain text — for eyeballing and debugging only."""
        return "\n\n".join(b.text for b in self.blocks)


class UnsupportedFormat(ValueError):
    pass


# ── shared text hygiene ──────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Collapse runs of whitespace but keep the text otherwise verbatim."""
    return re.sub(r"[ \t ]+", " ", text).strip()


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines; join wrapped lines back into one paragraph."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        joined = _clean(" ".join(line.strip() for line in para.splitlines()))
        if joined:
            out.append(joined)
    return out


def _title_from_filename(source: SourceFile) -> str:
    stem = source.path.stem.replace("_", " ").replace("-", " ")
    return _clean(stem).title()


# ── Markdown / plain text ────────────────────────────────────────────────────

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_BLOCKQUOTE = re.compile(r"^\s*>\s?")
# A paragraph whose first line opens a table row, a bullet, or a numbered item.
_STRUCTURED = re.compile(r"^\s*(\||[-*+]\s|\d+[.)]\s)")


def _is_structured(paragraph: str) -> bool:
    for line in paragraph.splitlines():
        if line.strip():
            return bool(_STRUCTURED.match(line))
    return False


def parse_markdown(source: SourceFile) -> ParsedDoc:
    """Headings define the section path; blank lines separate blocks.

    Tables, lists, and fenced code keep their line breaks; prose gets its
    wrapped lines joined back into one line. That distinction matters — a table
    split across chunks loses its header row, and a bullet list flattened into
    one line loses its item boundaries. Both read as noise to an embedding
    model and to whoever ends up reading the citation.
    """
    lines = source.read_bytes().decode("utf-8", errors="replace").splitlines()

    title: Optional[str] = None
    heading_stack: list[tuple[int, str]] = []   # (level, text)
    blocks: list[TextBlock] = []
    buffer: list[str] = []
    fence: list[str] = []

    def add(text: str) -> None:
        if text.strip():
            blocks.append(TextBlock(text=text, headings=tuple(h for _, h in heading_stack)))

    def flush() -> None:
        raw = "\n".join(buffer).strip()
        buffer.clear()
        if not raw:
            return
        for para in re.split(r"\n\s*\n", raw):
            # Blockquote markers are presentation, not content.
            para = "\n".join(_BLOCKQUOTE.sub("", line) for line in para.splitlines())
            if not para.strip():
                continue
            if _is_structured(para):
                add("\n".join(line.rstrip() for line in para.splitlines() if line.strip()))
            else:
                add(_clean(" ".join(line.strip() for line in para.splitlines())))

    for line in lines:
        if _FENCE.match(line):
            if fence:
                fence.append(line)
                add("\n".join(fence))     # closing fence — emit as one block
                fence.clear()
            else:
                flush()
                fence.append(line)
            continue
        if fence:
            fence.append(line)
            continue

        match = _MD_HEADING.match(line)
        if match:
            flush()
            level, text = len(match.group(1)), _clean(match.group(2))
            if level == 1 and title is None:
                title = text
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, text))
            continue

        buffer.append(line)

    if fence:                              # unterminated fence
        add("\n".join(fence))
    flush()
    return ParsedDoc(
        title=title or _title_from_filename(source),
        blocks=blocks,
        metadata={"format": "markdown"},
    )


def parse_text(source: SourceFile) -> ParsedDoc:
    raw = source.read_bytes().decode("utf-8", errors="replace")
    blocks = [TextBlock(text=p) for p in _split_paragraphs(raw)]
    return ParsedDoc(title=_title_from_filename(source), blocks=blocks,
                     metadata={"format": "text"})


# ── PDF ──────────────────────────────────────────────────────────────────────

# "3.2 Eligibility criteria" / "3.2. Eligibility" — numbered headings are the
# reliable signal in policy/T&C documents. Depth of the number = heading level.
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+){0,3})[.)]?\s+(\S.{0,79})$")
# "SECTION 3 — ELIGIBILITY" — short all-caps lines are treated as top level.
_CAPS_HEADING = re.compile(r"^[A-Z0-9][A-Z0-9 &/'’\-—:().]{3,79}$")
# A word broken across a line end: "financ-\ning" -> "financing".
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")


def _looks_like_heading(line: str) -> Optional[tuple[int, str]]:
    stripped = line.strip()
    if not stripped or stripped.endswith((".", ",", ";")):
        return None
    match = _NUMBERED_HEADING.match(stripped)
    if match:
        return match.group(1).count(".") + 1, stripped
    if _CAPS_HEADING.match(stripped) and len(stripped.split()) <= 12:
        return 1, stripped
    return None


def parse_pdf(source: SourceFile) -> ParsedDoc:
    """Extract per-page text with pypdf, then recover section structure.

    PDFs carry no semantic headings, so structure is inferred from numbering
    and capitalisation. It is heuristic by nature: when a heading isn't
    detected the text is still indexed, it just gets a shallower `ref`.
    """
    from pypdf import PdfReader

    reader = PdfReader(source.path)
    heading_stack: list[tuple[int, str]] = []
    blocks: list[TextBlock] = []

    for page_number, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        raw = _HYPHEN_BREAK.sub(r"\1\2", raw)

        buffer: list[str] = []

        def flush(page_number: int = page_number) -> None:
            if not buffer:
                return
            text = _clean(" ".join(buffer))
            buffer.clear()
            if text:
                blocks.append(TextBlock(
                    text=text, page=page_number,
                    headings=tuple(h for _, h in heading_stack),
                ))

        for line in raw.splitlines():
            if not line.strip():
                flush()
                continue
            heading = _looks_like_heading(line)
            if heading:
                flush()
                level, text = heading
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, _clean(text)))
                continue
            buffer.append(line.strip())

        flush()

    meta_title = None
    try:
        meta_title = (reader.metadata or {}).get("/Title")
    except Exception:  # noqa: BLE001 — malformed metadata must not fail ingestion
        log.warning("Could not read PDF metadata for %s.", source.source_uri)

    return ParsedDoc(
        title=_clean(str(meta_title)) if meta_title else _title_from_filename(source),
        blocks=blocks,
        page_count=len(reader.pages),
        metadata={"format": "pdf"},
    )


# ── DOCX ─────────────────────────────────────────────────────────────────────

_DOCX_HEADING_STYLE = re.compile(r"^Heading (\d)", re.IGNORECASE)


def parse_docx(source: SourceFile) -> ParsedDoc:
    """Walk the document body in order so tables stay where they belong.

    python-docx exposes `paragraphs` and `tables` as separate collections,
    which loses their interleaving — a table would drift away from the clause
    that introduces it. Walking the underlying XML preserves reading order.
    """
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(source.path))
    body = document.element.body

    title: Optional[str] = None
    heading_stack: list[tuple[int, str]] = []
    blocks: list[TextBlock] = []

    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]

        if tag == "p":
            paragraph = Paragraph(child, document)
            text = _clean(paragraph.text)
            if not text:
                continue
            style = paragraph.style.name if paragraph.style is not None else ""
            match = _DOCX_HEADING_STYLE.match(style or "")
            if match:
                level = int(match.group(1))
                if level == 1 and title is None:
                    title = text
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, text))
                continue
            blocks.append(TextBlock(text=text, headings=tuple(h for _, h in heading_stack)))

        elif tag == "tbl":
            table = Table(child, document)
            rows = [
                " | ".join(_clean(cell.text) for cell in row.cells)
                for row in table.rows
            ]
            rendered = "\n".join(r for r in rows if r.replace("|", "").strip())
            if rendered:
                blocks.append(TextBlock(
                    text=rendered, headings=tuple(h for _, h in heading_stack),
                ))

    core_title = None
    try:
        core_title = document.core_properties.title
    except Exception:  # noqa: BLE001
        pass

    return ParsedDoc(
        title=title or _clean(core_title or "") or _title_from_filename(source),
        blocks=blocks,
        metadata={"format": "docx"},
    )


# ── registry ─────────────────────────────────────────────────────────────────

PARSERS: dict[str, Callable[[SourceFile], ParsedDoc]] = {
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".txt": parse_text,
    ".pdf": parse_pdf,
    ".docx": parse_docx,
}


def parse(source: SourceFile) -> ParsedDoc:
    parser = PARSERS.get(source.suffix)
    if parser is None:
        raise UnsupportedFormat(
            f"No parser for {source.suffix!r} ({source.source_uri}). "
            f"Supported: {', '.join(sorted(PARSERS))}"
        )
    doc = parser(source)
    log.info("Parsed %s -> %d block(s), title=%r", source.source_uri, len(doc.blocks), doc.title)
    return doc
