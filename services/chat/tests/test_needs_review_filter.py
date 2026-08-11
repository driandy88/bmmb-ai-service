"""A chunk flagged by automated verification (needs_review=true) must never reach
customer-channel retrieval (rag-ingestion Change Brief §4, §7).

The exclusion is enforced in SQL by PgVectorRetriever._filters, exactly like the
access_tier boundary. We assert the WHERE clause it builds (no DB needed): the
customer channel filters `needs_review = false`; the internal channel does not,
so operators can still see flagged content.
"""
from services.chat.app.config.settings import get_settings
from services.chat.app.integrations.vector_search import PgVectorRetriever


def _where(channel: str) -> str:
    retriever = PgVectorRetriever(get_settings(), namespaces={})
    where, _params = retriever._filters(channel, program_code=None)
    return where


def test_customer_channel_excludes_flagged_chunks():
    where = _where("customer")
    assert "needs_review = false" in where
    assert "access_tier = 'customer'" in where


def test_internal_channel_can_see_flagged_chunks():
    where = _where("internal")
    assert "needs_review" not in where
