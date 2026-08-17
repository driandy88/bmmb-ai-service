"""
The deterministic bookend: citation extraction and the fabricated-link guard.

The link cases are a regression pin. During testing the agent answered a Malay
"how do I apply?" by writing an application URL from memory instead of calling
start_application — a plausible-looking link that does not come from config.
Prompt rules reduce that; this makes it structurally impossible.
"""
from __future__ import annotations

import pytest

from app.runtime.postprocess import split_cited_sentences, strip_unapproved_links

APPROVED = "https://apply.muamalat.example/sme/new"


# ── fabricated links ─────────────────────────────────────────────────────────

def test_removes_a_link_no_tool_produced():
    reply = "Untuk memulakan permohonan, anda boleh klik pautan ini: https://www.muamalat.com.my/sme-financing/apply-now/."
    cleaned, removed = strip_unapproved_links(reply, set())
    assert removed == ["https://www.muamalat.com.my/sme-financing/apply-now/"]
    assert "http" not in cleaned
    assert cleaned.endswith(".")


def test_keeps_a_link_a_tool_produced():
    reply = f"Your application form is here: {APPROVED}"
    cleaned, removed = strip_unapproved_links(reply, {APPROVED})
    assert removed == []
    assert APPROVED in cleaned


def test_keeps_approved_and_strips_fabricated_in_one_reply():
    reply = f"Apply here {APPROVED} or read more at https://evil.example/phish"
    cleaned, removed = strip_unapproved_links(reply, {APPROVED})
    assert removed == ["https://evil.example/phish"]
    assert APPROVED in cleaned
    assert "evil.example" not in cleaned


def test_trailing_punctuation_is_not_part_of_the_url():
    cleaned, removed = strip_unapproved_links(f"See {APPROVED}, then sign in.", {APPROVED})
    assert removed == []
    assert APPROVED in cleaned


def test_no_links_is_a_noop():
    reply = "Our SME financing team can help with that."
    cleaned, removed = strip_unapproved_links(reply, set())
    assert (cleaned, removed) == (reply, [])


# ── citation extraction ──────────────────────────────────────────────────────

def test_inline_markers_become_sentence_cites():
    reply = "GGSM covers up to RM 1 million [1]. Tenure runs to seven years [2]."
    clean, sentences = split_cited_sentences(reply, max_n=2)
    assert "[1]" not in clean and "[2]" not in clean
    assert [s["cites"] for s in sentences] == [[1], [2]]
    assert sentences[0]["text"] == "GGSM covers up to RM 1 million."


def test_multi_cite_marker():
    _, sentences = split_cited_sentences("Both apply here [1, 2].", max_n=2)
    assert sentences[0]["cites"] == [1, 2]


def test_marker_beyond_retrieved_count_is_dropped():
    """A hallucinated citation must not reach the UI as a dead chip."""
    _, sentences = split_cited_sentences("Something [7].", max_n=2)
    assert sentences[0]["cites"] == []


def test_uncited_reply_reports_no_sentences():
    """No markers means not a grounded answer — the caller uses this to decide
    whether to set `grounded`."""
    clean, sentences = split_cited_sentences("Happy to help with SME financing.", max_n=0)
    assert sentences is None
    assert clean == "Happy to help with SME financing."