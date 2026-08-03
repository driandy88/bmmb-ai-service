"""
Tests for the RAG ingestion stages: loader -> parser -> chunker (RAG_PLAN phase 1).

All offline: no database, no GCP, no network. The PDF cases run against the
committed fixture in `app/agents/rag/corpus/guidelines_shariah/`, so PDF
structure recovery is covered by a real PDF rather than a mock.
"""
from __future__ import annotations

import pytest

from app.agents.rag import db
from app.agents.rag.ingestion import chunker, loader, parser
from app.agents.rag.ingestion.chunker import Chunk, build_ref, estimate_tokens
from app.agents.rag.retriever import Corpus

MARKDOWN = """\
# Financing Handbook

Intro line before any section.

## 1. Eligibility

> A quoted note.

The business must be registered in Malaysia.

| Item | Amount |
| --- | --- |
| Fee | RM500 |

### 1.1 Documents

- SSM registration
- Bank statements
- MyKad

## 2. Fees

Processing fee applies.
"""


@pytest.fixture
def md_file(tmp_path):
    path = tmp_path / "program" / "handbook.md"
    path.parent.mkdir(parents=True)
    path.write_text(MARKDOWN, encoding="utf-8")
    return loader.load_file(path, Corpus.PROGRAM, root=tmp_path)


# ── loader ───────────────────────────────────────────────────────────────────

def test_discover_finds_supported_files_and_skips_others(tmp_path):
    corpus_dir = tmp_path / "program"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "a.md").write_text("# A", encoding="utf-8")
    (corpus_dir / "b.txt").write_text("plain", encoding="utf-8")
    (corpus_dir / "notes.xlsx").write_bytes(b"binary")
    (corpus_dir / ".hidden.md").write_text("# hidden", encoding="utf-8")

    found = loader.discover(Corpus.PROGRAM, root=tmp_path)

    assert [f.source_uri for f in found] == ["a.md", "b.txt"]


def test_discover_on_missing_corpus_dir_is_empty_not_an_error(tmp_path):
    assert loader.discover(Corpus.GUIDELINES_SHARIAH, root=tmp_path) == []


def test_content_hash_is_stable_and_content_dependent(tmp_path):
    path = tmp_path / "program" / "a.md"
    path.parent.mkdir(parents=True)
    path.write_text("same", encoding="utf-8")

    first = loader.load_file(path, Corpus.PROGRAM, root=tmp_path)
    second = loader.load_file(path, Corpus.PROGRAM, root=tmp_path)
    assert first.content_hash == second.content_hash

    path.write_text("different", encoding="utf-8")
    assert loader.load_file(path, Corpus.PROGRAM, root=tmp_path).content_hash != first.content_hash


def test_source_uri_is_relative_to_the_corpus_root(tmp_path):
    path = tmp_path / "program" / "nested" / "deep.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Deep", encoding="utf-8")

    source = loader.load_file(path, Corpus.PROGRAM, root=tmp_path)

    assert source.source_uri == "nested/deep.md"


# ── markdown parsing ─────────────────────────────────────────────────────────

def test_markdown_title_comes_from_the_h1(md_file):
    assert parser.parse(md_file).title == "Financing Handbook"


def test_markdown_heading_path_nests_and_pops(md_file):
    doc = parser.parse(md_file)
    by_text = {b.text: b.headings for b in doc.blocks}

    assert by_text["The business must be registered in Malaysia."] == (
        "Financing Handbook", "1. Eligibility",
    )
    # 1.1 nests under 1; "2. Fees" pops both back to the H1.
    assert by_text["Processing fee applies."] == ("Financing Handbook", "2. Fees")


def test_markdown_keeps_tables_and_lists_line_separated(md_file):
    doc = parser.parse(md_file)
    table = next(b.text for b in doc.blocks if b.text.startswith("| Item"))
    listing = next(b.text for b in doc.blocks if b.text.startswith("- SSM"))

    assert table.splitlines() == ["| Item | Amount |", "| --- | --- |", "| Fee | RM500 |"]
    assert listing.splitlines() == ["- SSM registration", "- Bank statements", "- MyKad"]


def test_markdown_strips_blockquote_markers(md_file):
    doc = parser.parse(md_file)
    assert any(b.text == "A quoted note." for b in doc.blocks)


def test_markdown_prose_lines_are_rejoined(tmp_path):
    path = tmp_path / "program" / "wrapped.md"
    path.parent.mkdir(parents=True)
    path.write_text("# T\n\nA sentence that was\nwrapped across lines.\n", encoding="utf-8")

    doc = parser.parse(loader.load_file(path, Corpus.PROGRAM, root=tmp_path))

    assert "A sentence that was wrapped across lines." in doc.text


def test_unsupported_extension_raises(tmp_path):
    path = tmp_path / "program" / "sheet.xlsx"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"binary")

    with pytest.raises(parser.UnsupportedFormat):
        parser.parse(loader.load_file(path, Corpus.PROGRAM, root=tmp_path))


# ── PDF parsing (against the committed fixture) ──────────────────────────────

@pytest.fixture(scope="module")
def pdf_doc():
    sources = loader.discover(Corpus.GUIDELINES_SHARIAH)
    pdfs = [s for s in sources if s.suffix == ".pdf"]
    if not pdfs:
        pytest.skip("no PDF fixture in corpus/guidelines_shariah/")
    return parser.parse(pdfs[0])


def test_pdf_reports_page_count_and_tags_blocks_with_pages(pdf_doc):
    assert pdf_doc.page_count and pdf_doc.page_count >= 2
    pages = {b.page for b in pdf_doc.blocks}
    assert pages == set(range(1, pdf_doc.page_count + 1))


def test_pdf_recovers_numbered_heading_structure(pdf_doc):
    paths = {b.headings for b in pdf_doc.blocks if b.headings}

    # A numbered subsection nests under its parent section...
    assert any(len(p) == 2 and p[0].startswith("2.") and p[1].startswith("2.1")
               for p in paths), paths
    # ...and the next top-level heading pops the subsection back off.
    assert any(p == ("3. PROHIBITED ACTIVITIES",) for p in paths), paths


def test_pdf_content_survives_extraction(pdf_doc):
    assert "Tawarruq" in pdf_doc.text
    assert "takaful" in pdf_doc.text.lower()


# ── refs ─────────────────────────────────────────────────────────────────────

def test_build_ref_composes_title_headings_and_page():
    assert build_ref("Handbook", ("1. Scope", "1.2 Limits"), 4) == \
        "Handbook — 1. Scope › 1.2 Limits (p.4)"


def test_build_ref_drops_a_heading_identical_to_the_title():
    assert build_ref("Handbook", ("Handbook", "1. Scope"), None) == "Handbook — 1. Scope"


def test_build_ref_keeps_only_the_two_deepest_levels():
    ref = build_ref("H", ("A", "B", "C", "D"), None)
    assert ref == "H — C › D"


def test_build_ref_without_headings_or_page():
    assert build_ref("Handbook", (), None) == "Handbook"


# ── chunking ─────────────────────────────────────────────────────────────────

def _chunk(md_file, **kwargs):
    return chunker.chunk_document(parser.parse(md_file), **kwargs)


def test_chunk_text_carries_its_section_path(md_file):
    # Without this, heading-only terms are unsearchable: "Kelayakan" appears
    # nowhere but a heading in the real corpus, so a lexical search for it
    # matched zero chunks, and the embedding lost the topic of the section.
    # min_tokens=0 so sections are not folded together — this asserts about one
    # section's own header, not about a merged chunk carrying several.
    chunks = _chunk(md_file, max_tokens=400, overlap_tokens=0, min_tokens=0)
    eligibility = next(c for c in chunks if "registered in Malaysia" in c.text)

    assert eligibility.text.startswith("Financing Handbook › 1. Eligibility")
    assert "The business must be registered in Malaysia." in eligibility.text


def test_heading_path_is_not_prepended_when_there_is_none(tmp_path):
    path = tmp_path / "program" / "flat.txt"
    path.parent.mkdir(parents=True)
    path.write_text("Just a paragraph with no headings at all.\n", encoding="utf-8")

    chunks = chunker.chunk_document(
        parser.parse(loader.load_file(path, Corpus.PROGRAM, root=tmp_path)),
        max_tokens=400, overlap_tokens=0,
    )
    assert chunks[0].text == "Just a paragraph with no headings at all."


def test_the_heading_header_counts_against_the_token_budget(tmp_path):
    # The header is real tokens; the budget must absorb it rather than let
    # chunks silently overshoot by the length of their heading path.
    path = tmp_path / "program" / "deep.md"
    path.parent.mkdir(parents=True)
    heading = "A Very Long Section Heading That Eats Into The Budget"
    body = " ".join(f"Filler sentence {i} here." for i in range(60))
    path.write_text(f"# T\n\n## {heading}\n\n{body}\n", encoding="utf-8")

    chunks = chunker.chunk_document(
        parser.parse(loader.load_file(path, Corpus.PROGRAM, root=tmp_path)),
        max_tokens=120, overlap_tokens=0, min_tokens=0,
    )
    assert chunks
    assert all(c.token_count <= 120 for c in chunks), [c.token_count for c in chunks]


def test_chunks_respect_the_token_budget(md_file):
    chunks = _chunk(md_file, max_tokens=60, overlap_tokens=10, min_tokens=0)
    oversized = [c for c in chunks if c.token_count > 60]
    # Only an indivisible unit (a single long sentence) may exceed the budget.
    assert all(len(c.text.split()) < 40 for c in oversized), oversized


def test_chunks_never_span_two_sections(md_file):
    for chunk in _chunk(md_file, max_tokens=4000, overlap_tokens=0, min_tokens=0):
        assert "Processing fee applies." not in chunk.text or \
            "The business must be registered" not in chunk.text


def test_chunk_indices_are_contiguous_from_zero(md_file):
    chunks = _chunk(md_file, max_tokens=60, overlap_tokens=10)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_overlap_repeats_the_tail_of_the_previous_chunk(tmp_path):
    path = tmp_path / "program" / "long.md"
    path.parent.mkdir(parents=True)
    sentences = " ".join(f"Sentence number {i} carries some filler words." for i in range(40))
    path.write_text(f"# T\n\n## S\n\n{sentences}\n", encoding="utf-8")

    chunks = chunker.chunk_document(
        parser.parse(loader.load_file(path, Corpus.PROGRAM, root=tmp_path)),
        max_tokens=80, overlap_tokens=30, min_tokens=0,
    )

    assert len(chunks) > 2
    tail = chunks[0].text.split()[-4:]
    assert " ".join(tail) in chunks[1].text


def test_zero_overlap_produces_no_repetition(tmp_path):
    path = tmp_path / "program" / "long.md"
    path.parent.mkdir(parents=True)
    sentences = " ".join(f"Unique marker {i} appears exactly once here." for i in range(30))
    path.write_text(f"# T\n\n## S\n\n{sentences}\n", encoding="utf-8")

    chunks = chunker.chunk_document(
        parser.parse(loader.load_file(path, Corpus.PROGRAM, root=tmp_path)),
        max_tokens=80, overlap_tokens=0, min_tokens=0,
    )

    joined = " ".join(c.text for c in chunks)
    assert joined.count("Unique marker 7 ") == 1


def test_fragments_are_folded_into_a_related_neighbour(md_file):
    with_folding = _chunk(md_file, max_tokens=400, overlap_tokens=0, min_tokens=40)
    without = _chunk(md_file, max_tokens=400, overlap_tokens=0, min_tokens=0)

    assert len(with_folding) < len(without)
    assert all(len(c.text) for c in with_folding)


def test_folding_never_exceeds_the_token_budget(md_file):
    for chunk in _chunk(md_file, max_tokens=120, overlap_tokens=0, min_tokens=100):
        assert chunk.token_count <= 120 or "\n\n" not in chunk.text


def test_overlap_must_be_smaller_than_the_chunk(md_file):
    with pytest.raises(ValueError):
        _chunk(md_file, max_tokens=50, overlap_tokens=50)


def test_empty_document_yields_no_chunks(tmp_path):
    path = tmp_path / "program" / "empty.md"
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")

    assert _chunk(loader.load_file(path, Corpus.PROGRAM, root=tmp_path)) == []


def test_estimate_tokens_scales_with_length():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 350) == 100


def test_chunk_metadata_carries_page_and_headings(pdf_doc):
    chunks = chunker.chunk_document(pdf_doc, max_tokens=400, overlap_tokens=60)
    assert chunks
    assert all("headings" in c.metadata for c in chunks)
    assert any(c.metadata.get("page") for c in chunks)
    assert all(c.ref.startswith(pdf_doc.title) for c in chunks)


# ── schema rendering ─────────────────────────────────────────────────────────

def test_render_schema_substitutes_dimension_and_fts_config():
    from app.config.settings import Settings

    settings = Settings(rag_embedding_dim=1024, rag_fts_config="english")
    sql = db.render_schema(settings)

    assert "vector(1024)" in sql
    assert "to_tsvector('english', text)" in sql
    assert "${" not in sql


def test_render_schema_rejects_an_injectable_fts_config():
    from app.config.settings import Settings

    with pytest.raises(RuntimeError, match="plain identifier"):
        db.render_schema(Settings(rag_fts_config="simple'); DROP TABLE rag_chunks; --"))


def test_split_statements_splits_the_real_schema():
    from app.config.settings import Settings

    statements = db.split_statements(db.render_schema(Settings()))

    assert len(statements) == 7
    assert statements[0] == "CREATE EXTENSION IF NOT EXISTS vector"
    assert all("--" not in s for s in statements)          # comments stripped
    assert not any(s.endswith(";") for s in statements)


def test_split_statements_ignores_an_apostrophe_inside_a_comment():
    # Regression: `service's` looked like an open string literal, which swallowed
    # every following semicolon and collapsed the file into one statement.
    sql = """
    -- the extraction service's tables live here
    CREATE TABLE a (id INT);
    CREATE TABLE b (id INT);
    """
    assert db.split_statements(sql) == ["CREATE TABLE a (id INT)", "CREATE TABLE b (id INT)"]


def test_split_statements_keeps_a_semicolon_inside_a_string_literal():
    sql = "INSERT INTO t VALUES ('a;b'); SELECT 1;"
    assert db.split_statements(sql) == ["INSERT INTO t VALUES ('a;b')", "SELECT 1"]


def test_split_statements_handles_escaped_quotes():
    sql = "INSERT INTO t VALUES ('it''s fine; really'); SELECT 2;"
    assert db.split_statements(sql) == [
        "INSERT INTO t VALUES ('it''s fine; really')", "SELECT 2",
    ]


def test_split_statements_on_comments_only_yields_nothing():
    assert db.split_statements("-- just a note\n-- and another\n") == []


def test_chunk_is_immutable():
    chunk = Chunk(text="t", ref="r", chunk_index=0, token_count=1)
    with pytest.raises(Exception):
        chunk.text = "changed"
