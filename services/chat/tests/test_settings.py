"""
Tests for environment parsing in app.config.settings.

The `_optional` cases are a regression guard: python-dotenv returns the inline
comment as the value when the value itself is blank (`FOO=   # note` parses as
`'# note'`), which silently turns "deliberately unset" into a truthy string.
That surfaced as the RAG store trying to connect to a comment as if it were a
database URL, and would equally have broken EXTRACTION_BACKEND=http.
"""
from __future__ import annotations

import pytest

from app.config.settings import _flag, _optional


def test_optional_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_URL", raising=False)
    assert _optional("SOME_URL") is None


def test_optional_returns_none_for_a_blank_value(monkeypatch):
    monkeypatch.setenv("SOME_URL", "   ")
    assert _optional("SOME_URL") is None


def test_optional_treats_a_comment_only_value_as_unset(monkeypatch):
    monkeypatch.setenv("SOME_URL", "# e.g. https://service.example")
    assert _optional("SOME_URL") is None


def test_optional_strips_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv("SOME_URL", "  postgresql://host/db  ")
    assert _optional("SOME_URL") == "postgresql://host/db"


def test_optional_keeps_a_value_containing_a_hash(monkeypatch):
    monkeypatch.setenv("SOME_PASS", "p#ssw0rd")
    assert _optional("SOME_PASS") == "p#ssw0rd"


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("", False), ("maybe", False),
])
def test_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("SOME_FLAG", value)
    assert _flag("SOME_FLAG", False) is expected


def test_flag_uses_the_default_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert _flag("SOME_FLAG", True) is True
