"""
Tests for the embedding layer (RAG_PLAN phase 2).

Offline throughout: the Vertex and Hugging Face providers are exercised against
injected fakes, so the batching, normalisation, task-type, and prefix logic is
covered without credentials or a 2GB torch install.
"""
from __future__ import annotations

import sys
import types as pytypes

import pytest

from app.agents.rag import embeddings
from app.agents.rag.embeddings import (
    EmbeddingDimensionMismatch,
    HashEmbedder,
    HuggingFaceEmbedder,
    VertexEmbedder,
    get_embedder,
    l2_normalise,
)
from app.config.settings import Settings


def _settings(**kwargs) -> Settings:
    # Every field these tests depend on is passed explicitly. Settings' defaults
    # are bound at import time from os.environ, so relying on them would make
    # results depend on the developer's .env and on test ordering.
    base = {
        "rag_embedding_dim": 8,
        "rag_embedding_model": "",
        "rag_embedding_provider": "hash",
        "gcp_project_id": "test-project",
    }
    return Settings(**{**base, **kwargs})


def norm(vector) -> float:
    return sum(v * v for v in vector) ** 0.5


# ── normalisation ────────────────────────────────────────────────────────────

def test_l2_normalise_produces_a_unit_vector():
    assert norm(l2_normalise([3.0, 4.0])) == pytest.approx(1.0)


def test_l2_normalise_preserves_direction():
    assert l2_normalise([3.0, 4.0]) == pytest.approx([0.6, 0.8])


def test_l2_normalise_leaves_a_zero_vector_alone():
    # Guards against a divide-by-zero on degenerate input.
    assert l2_normalise([0.0, 0.0]) == [0.0, 0.0]


# ── hash provider ────────────────────────────────────────────────────────────

def test_hash_embedder_is_deterministic():
    embedder = HashEmbedder(_settings())
    assert embedder.embed_documents(["abc"]) == embedder.embed_documents(["abc"])


def test_hash_embedder_returns_the_configured_width():
    for dim in (8, 768, 1024):
        embedder = HashEmbedder(_settings(rag_embedding_dim=dim))
        assert len(embedder.embed_query("x")) == dim


def test_hash_embedder_emits_unit_vectors():
    vectors = HashEmbedder(_settings(rag_embedding_dim=768)).embed_documents(["a", "b"])
    assert all(norm(v) == pytest.approx(1.0) for v in vectors)


def test_hash_embedder_distinguishes_documents_from_queries():
    # The document/query asymmetry of the real providers is mirrored offline.
    embedder = HashEmbedder(_settings())
    assert embedder.embed_documents(["same text"])[0] != embedder.embed_query("same text")


def test_hash_embedder_maps_different_texts_to_different_vectors():
    embedder = HashEmbedder(_settings())
    assert embedder.embed_documents(["a"])[0] != embedder.embed_documents(["b"])[0]


# ── Vertex provider (fake client) ────────────────────────────────────────────

class _FakeEmbedding:
    def __init__(self, values):
        self.values = values


class _FakeResponse:
    def __init__(self, embeddings_):
        self.embeddings = embeddings_


class _FakeModels:
    def __init__(self, dim, scale=1.0, fail_times=0):
        self.dim, self.scale, self.fail_times = dim, scale, fail_times
        self.calls = []

    def embed_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient vertex error")
        # Each vector is a distinct constant direction, scaled off unit length.
        return _FakeResponse([
            _FakeEmbedding([self.scale * (index + 1)] * self.dim)
            for index in range(len(contents))
        ])


class _FakeClient:
    def __init__(self, models):
        self.models = models


@pytest.fixture
def vertex(monkeypatch):
    """Build a VertexEmbedder wired to a fake genai client."""
    def build(dim=8, scale=1.0, fail_times=0, **settings_kwargs):
        fake_models = _FakeModels(dim, scale, fail_times)
        from google import genai
        monkeypatch.setattr(genai, "Client", lambda **kwargs: _FakeClient(fake_models))
        monkeypatch.setattr(embeddings.time, "sleep", lambda seconds: None)
        embedder = VertexEmbedder(_settings(rag_embedding_dim=dim, **settings_kwargs))
        return embedder, fake_models
    return build


def test_vertex_requires_a_project():
    with pytest.raises(RuntimeError, match="GCP_PROJECT_ID"):
        VertexEmbedder(Settings(gcp_project_id=None))


def test_vertex_renormalises_truncated_vectors(vertex):
    # gemini-embedding-001 does NOT re-normalise after Matryoshka truncation —
    # measured |v| ~= 0.59 at 768. Vectors must still come back unit length.
    embedder, _ = vertex(dim=8, scale=0.25)
    assert all(norm(v) == pytest.approx(1.0) for v in embedder.embed_documents(["a", "b"]))


def test_vertex_leaves_full_width_vectors_untouched(vertex):
    embedder, _ = vertex(dim=embeddings.GEMINI_NATIVE_DIM, scale=1.0)
    vector = embedder.embed_query("a")
    assert len(vector) == embeddings.GEMINI_NATIVE_DIM
    assert vector[0] == 1.0        # passed through, not normalised


def test_vertex_uses_asymmetric_task_types(vertex):
    embedder, models = vertex()
    embedder.embed_documents(["a"])
    embedder.embed_query("b")

    assert models.calls[0]["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert models.calls[1]["config"].task_type == "RETRIEVAL_QUERY"


def test_vertex_requests_the_configured_dimensionality(vertex):
    embedder, models = vertex(dim=8)
    embedder.embed_documents(["a"])
    assert models.calls[0]["config"].output_dimensionality == 8


def test_vertex_batches_large_inputs(vertex):
    embedder, models = vertex()
    texts = [f"text {i}" for i in range(embeddings.VERTEX_MAX_BATCH * 2 + 10)]

    vectors = embedder.embed_documents(texts)

    assert len(vectors) == len(texts)
    assert [len(call["contents"]) for call in models.calls] == [
        embeddings.VERTEX_MAX_BATCH, embeddings.VERTEX_MAX_BATCH, 10,
    ]
    # Batching must not reorder or drop anything.
    assert [c for call in models.calls for c in call["contents"]] == texts


def test_vertex_embedding_empty_list_makes_no_call(vertex):
    embedder, models = vertex()
    assert embedder.embed_documents([]) == []
    assert models.calls == []


def test_vertex_retries_transient_failures(vertex):
    embedder, models = vertex(fail_times=2)
    assert len(embedder.embed_query("a")) == 8
    assert len(models.calls) == 3


def test_vertex_raises_after_exhausting_retries(vertex):
    embedder, _ = vertex(fail_times=99)
    # No silent fallback: a vector from another model is not comparable with
    # what is already stored, so failing loudly is the only safe option.
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        embedder.embed_query("a")


def test_vertex_rejects_a_width_mismatch(vertex):
    embedder, _ = vertex(dim=8)
    object.__setattr__(embedder, "_dim", 16)      # column says 16, model gives 8
    with pytest.raises(EmbeddingDimensionMismatch, match="RAG_EMBEDDING_DIM"):
        embedder.embed_documents(["a"])


# ── Hugging Face provider (fake Inference API client) ────────────────────────

class _FakeInferenceClient:
    instances: list = []

    def __init__(self, model=None, token=None, **kwargs):
        self.model, self.token = model, token
        self.dim = 1024
        self.calls: list[list[str]] = []
        self.result = None          # set per-test to control the response shape
        self.fail_times = 0
        _FakeInferenceClient.instances.append(self)

    def feature_extraction(self, text, **kwargs):
        self.calls.append(list(text))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient hf error")
        if self.result is not None:
            return self.result
        # Non-unit rows, so client-side normalisation is actually exercised.
        return [[3.0] + [0.0] * (self.dim - 1) for _ in text]


@pytest.fixture
def fake_hf(monkeypatch):
    module = pytypes.ModuleType("huggingface_hub")
    module.InferenceClient = _FakeInferenceClient
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)
    monkeypatch.setattr(embeddings.time, "sleep", lambda seconds: None)
    _FakeInferenceClient.instances = []
    return module


def _hf(dim=1024, **kwargs):
    return HuggingFaceEmbedder(_settings(rag_embedding_dim=dim, hf_token="hf_test", **kwargs))


def test_hf_defaults_to_the_1024_dimensional_e5_large(fake_hf):
    assert _hf().model == "intfloat/multilingual-e5-large"


def test_hf_requires_a_token(fake_hf):
    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        HuggingFaceEmbedder(_settings(rag_embedding_dim=1024, hf_token=None))


def test_hf_passes_the_token_and_model_to_the_client(fake_hf):
    _hf()
    client = _FakeInferenceClient.instances[-1]
    assert client.token == "hf_test"
    assert client.model == "intfloat/multilingual-e5-large"


def test_hf_applies_the_e5_passage_and_query_prefixes(fake_hf):
    embedder = _hf()
    embedder.embed_documents(["eligibility rules"])
    embedder.embed_query("am I eligible?")

    client = _FakeInferenceClient.instances[-1]
    assert client.calls[0] == ["passage: eligibility rules"]
    assert client.calls[1] == ["query: am I eligible?"]


def test_hf_normalises_client_side(fake_hf):
    # The API's own `normalize` flag is not honoured by every backend, so the
    # unit-length invariant is enforced here regardless.
    vectors = _hf().embed_documents(["x", "y"])
    assert all(norm(v) == pytest.approx(1.0) for v in vectors)


def test_hf_accepts_a_single_flat_vector_response(fake_hf):
    embedder = _hf()
    _FakeInferenceClient.instances[-1].result = [3.0] + [0.0] * 1023  # (dim,) not (1, dim)
    assert norm(embedder.embed_query("x")) == pytest.approx(1.0)


def test_hf_rejects_token_level_embeddings(fake_hf):
    # A raw encoder returns (n, tokens, dim); pooling that here would not match
    # how the model was trained, so it is refused rather than guessed at.
    embedder = _hf()
    _FakeInferenceClient.instances[-1].result = [[[1.0] * 1024] * 5]
    with pytest.raises(RuntimeError, match="token-level"):
        embedder.embed_query("x")


def test_hf_rejects_a_row_count_mismatch(fake_hf):
    embedder = _hf()
    _FakeInferenceClient.instances[-1].result = [[1.0] * 1024]    # 1 row for 2 texts
    with pytest.raises(RuntimeError, match="2 texts"):
        embedder.embed_documents(["a", "b"])


def test_hf_batches_large_inputs(fake_hf):
    embedder = _hf()
    texts = [f"text {i}" for i in range(embeddings.HF_MAX_BATCH + 5)]

    assert len(embedder.embed_documents(texts)) == len(texts)
    assert [len(call) for call in _FakeInferenceClient.instances[-1].calls] == [
        embeddings.HF_MAX_BATCH, 5,
    ]


def test_hf_retries_transient_failures(fake_hf):
    embedder = _hf()
    _FakeInferenceClient.instances[-1].fail_times = 2
    assert len(embedder.embed_query("x")) == 1024


def test_hf_raises_after_exhausting_retries(fake_hf):
    embedder = _hf()
    _FakeInferenceClient.instances[-1].fail_times = 99
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        embedder.embed_query("x")


def test_hf_embedding_empty_list_makes_no_call(fake_hf):
    embedder = _hf()
    assert embedder.embed_documents([]) == []
    assert _FakeInferenceClient.instances[-1].calls == []


def test_hf_rejects_a_model_whose_native_width_mismatches(fake_hf):
    # e5-large is 1024 and cannot share a vector(768) column. Caught before any
    # network call rather than after.
    with pytest.raises(EmbeddingDimensionMismatch, match="e5-large"):
        _hf(dim=768, rag_embedding_model="intfloat/multilingual-e5-large")
    assert _FakeInferenceClient.instances == []


def test_hf_accepts_a_different_width_when_the_model_matches(fake_hf):
    # e5-base is 768; it is only usable if the column is 768 too (and it is not
    # served for feature-extraction, which the live CLI reports separately).
    assert _hf(dim=768, rag_embedding_model="intfloat/multilingual-e5-base").dim == 768


def test_hf_default_matches_the_vertex_default_width():
    # The interchangeability promise: both defaults fit the same column.
    assert embeddings.HF_NATIVE_DIMS[embeddings.DEFAULT_MODELS["hf"]] == 1024


# ── factory ──────────────────────────────────────────────────────────────────

def test_factory_selects_the_configured_provider(fake_hf, vertex, monkeypatch):
    assert isinstance(get_embedder(_settings(rag_embedding_provider="hash")), HashEmbedder)

    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **kwargs: _FakeClient(_FakeModels(8)))
    assert isinstance(get_embedder(_settings(rag_embedding_provider="vertex")), VertexEmbedder)

    assert isinstance(
        get_embedder(_settings(
            rag_embedding_provider="hf", rag_embedding_dim=1024, hf_token="hf_test",
        )),
        HuggingFaceEmbedder,
    )


def test_factory_rejects_an_unknown_provider():
    with pytest.raises(RuntimeError, match="Unknown RAG_EMBEDDING_PROVIDER"):
        get_embedder(_settings(rag_embedding_provider="word2vec"))


def test_every_provider_reports_a_model_name_for_the_audit_column(fake_hf):
    assert HashEmbedder(_settings()).model
    assert _hf().model
