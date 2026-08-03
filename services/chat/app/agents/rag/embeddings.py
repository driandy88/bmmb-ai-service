"""
The embedding boundary (RAG_PLAN phase 2) — text in, vectors out.

Both RAG modules depend on this and nothing else about embeddings: ingestion
embeds chunks, retrieval embeds the query, and neither knows which provider is
behind the interface. Three implementations ship:

    vertex  gemini-embedding-001 on Vertex AI (ADC auth) — the deployed path
    hf      Hugging Face Inference API, default intfloat/multilingual-e5-large
            (HF_TOKEN auth) — the alternative provider; no torch, no weights
    hash    deterministic pseudo-vectors, no credentials — tests and CI

Two invariants hold across all three, and the rest of the system leans on them:

1. **Same dimensionality.** Every provider emits `RAG_EMBEDDING_DIM` floats, so
   they share one `vector(N)` column and can be swapped without a migration.
   The default pairing is **1024**: multilingual-e5-large, which is natively
   1024, and gemini-embedding-001 truncated to match. 1024 rather than gemini's
   more usual 768 because that is the width of the multilingual model Hugging
   Face actually serves for feature-extraction — the interchangeability
   requirement is what fixes the number, not the other way round.
2. **Unit length.** Every vector is L2-normalised, which makes cosine distance
   and inner product agree and keeps scores comparable across providers.

Queries and documents are embedded *asymmetrically* — different task types on
Vertex, different text prefixes on e5 — because both model families are trained
that way and lose real accuracy without it. That asymmetry is the main thing
this interface exists to hide: callers pick `embed_documents` vs `embed_query`
and never think about it again.
"""
from __future__ import annotations

import hashlib
import math
import struct
import time
from abc import ABC, abstractmethod
from typing import Optional, Sequence

from app.config.settings import Settings, get_settings
from app.utils.logging import get_logger

log = get_logger("rag.embeddings")

# Vertex accepts up to 250 instances per embed request (verified against
# gemini-embedding-001 in asia-southeast1: 250 texts in ~3.7s).
VERTEX_MAX_BATCH = 250

# Kept well below Vertex's: the hosted Inference API has tighter payload limits
# and rate limits, and a rejected batch costs a full retry cycle.
HF_MAX_BATCH = 32

# gemini-embedding-001 is natively 3072-dimensional and Matryoshka-trained, so
# a shorter prefix of the vector is still a valid embedding.
GEMINI_NATIVE_DIM = 3072

DEFAULT_MODELS = {
    "vertex": "gemini-embedding-001",
    "hf": "intfloat/multilingual-e5-large",   # 1024 — matches the Vertex default
    "hash": "hash-deterministic",
}

# Native output widths, used to fail fast with a useful message instead of
# discovering the mismatch on the first insert.
#
# Note which of these HF actually *serves*: the hosted Inference API registers
# each model against specific tasks, and the smaller e5 sizes are published for
# `sentence-similarity` only, so `feature_extraction` on them fails outright.
# e5-large is the multilingual model served for `feature-extraction`, which is
# why the shared width is 1024 rather than gemini's more usual 768.
HF_NATIVE_DIMS = {
    "intfloat/multilingual-e5-large": 1024,           # served: feature-extraction
    "intfloat/multilingual-e5-large-instruct": 1024,  # served: feature-extraction
    "BAAI/bge-large-en-v1.5": 1024,                   # served, English only
    "mixedbread-ai/mxbai-embed-large-v1": 1024,       # served, English only
    "intfloat/multilingual-e5-base": 768,             # NOT served for feature-extraction
    "intfloat/multilingual-e5-small": 384,            # NOT served for feature-extraction
    "BAAI/bge-m3": 1024,
}


class EmbeddingDimensionMismatch(RuntimeError):
    """Raised when a provider's output width != RAG_EMBEDDING_DIM.

    Loud on purpose: a mismatch cannot be papered over. The `vector(N)` column
    is fixed-width, so the options are to change RAG_EMBEDDING_DIM (and
    re-apply the DDL and re-ingest) or to pick a model that matches.
    """


def l2_normalise(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return list(vector)
    return [value / norm for value in vector]


class Embedder(ABC):
    """Frozen interface. Ingestion and retrieval depend only on this."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier — recorded in rag_chunks.embedding_model."""

    @property
    @abstractmethod
    def dim(self) -> int:
        ...

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed corpus chunks for storage."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a user query for search."""

    def _validate(self, vectors: list[list[float]]) -> list[list[float]]:
        for vector in vectors:
            if len(vector) != self.dim:
                raise EmbeddingDimensionMismatch(
                    f"{self.model} returned {len(vector)} dimensions but "
                    f"RAG_EMBEDDING_DIM is {self.dim}. Either set "
                    f"RAG_EMBEDDING_DIM={len(vector)} and re-apply the schema "
                    f"(scripts/rag_db.py apply) and re-ingest, or choose a "
                    f"model whose width matches."
                )
        return vectors


# ── Vertex AI ────────────────────────────────────────────────────────────────

class VertexEmbedder(Embedder):
    """gemini-embedding-001 on Vertex AI. ADC auth — no API keys.

    Truncating the native 3072-d output down to `RAG_EMBEDDING_DIM` is a
    supported Matryoshka operation, but the API does **not** re-normalise
    afterwards: a 768-d truncation comes back with |v| ≈ 0.59, not 1.0 (measured,
    not assumed). Left alone that breaks the unit-length invariant this module
    promises — inner-product scores would silently shrink, and vectors would not
    be comparable with the other providers. So truncated output is re-normalised
    here, which is also what Google's own guidance says to do.
    """

    def __init__(self, settings: Settings):
        if not settings.gcp_project_id:
            raise RuntimeError("RAG_EMBEDDING_PROVIDER=vertex requires GCP_PROJECT_ID.")
        from google import genai  # lazy — only this provider needs it

        self._settings = settings
        self._model = settings.rag_embedding_model or DEFAULT_MODELS["vertex"]
        self._dim = settings.rag_embedding_dim
        self._client = genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.vertex_location,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def _embed(self, texts: Sequence[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        config = types.EmbedContentConfig(
            task_type=task_type, output_dimensionality=self._dim,
        )
        vectors: list[list[float]] = []

        for start in range(0, len(texts), VERTEX_MAX_BATCH):
            batch = list(texts[start:start + VERTEX_MAX_BATCH])
            began = time.time()
            response = self._retry(model=self._model, contents=batch, config=config)
            vectors.extend(
                # Truncation drops the normalisation; restore it.
                l2_normalise(embedding.values) if self._dim < GEMINI_NATIVE_DIM
                else list(embedding.values)
                for embedding in response.embeddings
            )
            log.info("Embedded %d text(s) [%s] in %.0fms",
                     len(batch), task_type, (time.time() - began) * 1000)

        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Vertex returned {len(vectors)} embeddings for {len(texts)} texts."
            )
        return self._validate(vectors)

    def _retry(self, **kwargs):
        """Retry transient Vertex failures.

        Unlike the LLM client, there is no degraded fallback available: an
        embedding produced by a different model is not comparable with the
        stored vectors, so a wrong answer is worse than an error.
        """
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                return self._client.models.embed_content(**kwargs)
            except Exception as exc:  # noqa: BLE001 — retried, then re-raised
                last = exc
                if attempt < 2:
                    backoff = 2 ** attempt
                    log.warning("Vertex embed failed (%s); retrying in %ss.", exc, backoff)
                    time.sleep(backoff)
        raise RuntimeError(f"Vertex embedding failed after 3 attempts: {last}") from last

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


# ── Hugging Face (hosted Inference API) ──────────────────────────────────────

class HuggingFaceEmbedder(Embedder):
    """Hugging Face Inference API, defaulting to intfloat/multilingual-e5-large.

    e5 chosen for parity with the Vertex path: multilingual (Bahasa Malaysia +
    English) and natively 1024-d, which is the width gemini-embedding-001
    truncates to, so both providers share one column. The *large* size
    specifically: HF serves it for `feature-extraction`, while the smaller e5
    sizes are published for `sentence-similarity` only and cannot be used here
    at all. The family requires `query:` / `passage:` prefixes — without them
    retrieval quality drops sharply, and the prefix is easy to forget, so it is
    applied here rather than left to callers.

    Runs over the hosted Inference API rather than local weights: authentication
    is `HF_TOKEN`, and the only dependency is `huggingface_hub` (no torch, no
    model download). The trade-off is that this provider now needs network and
    a token, and that model availability depends on what the Inference
    providers currently serve.
    """

    QUERY_PREFIX = "query: "
    PASSAGE_PREFIX = "passage: "

    def __init__(self, settings: Settings):
        self._model_name = settings.rag_embedding_model or DEFAULT_MODELS["hf"]
        self._dim = settings.rag_embedding_dim

        native = HF_NATIVE_DIMS.get(self._model_name)
        if native is not None and native != self._dim:
            raise EmbeddingDimensionMismatch(
                f"{self._model_name} is natively {native}-dimensional but "
                f"RAG_EMBEDDING_DIM is {self._dim}. The default pairing is "
                f"RAG_EMBEDDING_DIM=1024 with intfloat/multilingual-e5-large, "
                f"which is the multilingual model Hugging Face serves for "
                f"feature-extraction. Changing the width means re-applying the "
                f"schema (scripts/rag_db.py apply) and re-ingesting."
            )

        if not settings.hf_token:
            raise RuntimeError(
                "RAG_EMBEDDING_PROVIDER=hf requires HF_TOKEN (a Hugging Face "
                "access token with inference permission). Create one at "
                "https://huggingface.co/settings/tokens and put it in .env."
            )

        try:
            from huggingface_hub import InferenceClient  # lazy
        except ImportError as exc:  # pragma: no cover - depends on local install
            raise RuntimeError(
                "RAG_EMBEDDING_PROVIDER=hf needs huggingface_hub. Install it "
                "with: pip install -r requirements.txt"
            ) from exc

        self._client = InferenceClient(model=self._model_name, token=settings.hf_token)

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    @staticmethod
    def _as_matrix(result, expected_rows: int) -> list[list[float]]:
        """Coerce the API's response into rows of floats.

        `feature_extraction` returns a numpy array whose shape depends on the
        model: a sentence-transformers model gives (n, dim) — or (dim,) for a
        single input — while a raw encoder gives (n, tokens, dim) of per-token
        vectors. Storing the latter would silently corrupt the index, so it is
        rejected rather than pooled: picking a pooling strategy here would not
        match how the model was trained.
        """
        data = result.tolist() if hasattr(result, "tolist") else list(result)
        if data and not isinstance(data[0], (list, tuple)):
            data = [data]                                  # (dim,) -> one row
        if data and data[0] and isinstance(data[0][0], (list, tuple)):
            raise RuntimeError(
                "The Inference API returned token-level embeddings, not one "
                "vector per text. Use a sentence-transformers model served for "
                "feature-extraction (such as intfloat/multilingual-e5-large) "
                "for RAG_EMBEDDING_MODEL."
            )
        if len(data) != expected_rows:
            raise RuntimeError(
                f"Hugging Face returned {len(data)} embeddings for {expected_rows} texts."
            )
        return [[float(value) for value in row] for row in data]

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []

        for start in range(0, len(texts), HF_MAX_BATCH):
            batch = list(texts[start:start + HF_MAX_BATCH])
            began = time.time()
            result = self._retry(batch)
            # Normalised here rather than via the API's `normalize` flag, which
            # not every inference backend honours — the unit-length invariant
            # has to hold regardless of who serves the model.
            vectors.extend(l2_normalise(row) for row in self._as_matrix(result, len(batch)))
            log.info("Embedded %d text(s) via HF in %.0fms",
                     len(batch), (time.time() - began) * 1000)

        return self._validate(vectors)

    def _retry(self, batch: list[str]):
        """Retry transient API failures. Same no-fallback rule as Vertex."""
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                return self._client.feature_extraction(batch)
            except Exception as exc:  # noqa: BLE001 — retried, then re-raised
                # An unsupported task is a permanent routing fact about the
                # model, not a blip: HF registers each model against specific
                # tasks, and the smaller e5 sizes are published for
                # `sentence-similarity` only. Retrying cannot help.
                if "doesn't support task" in str(exc):
                    raise RuntimeError(
                        f"{self._model_name} is not served for feature-extraction "
                        f"by any Hugging Face inference provider ({exc}). Use "
                        f"intfloat/multilingual-e5-large, which is."
                    ) from exc
                last = exc
                if attempt < 2:
                    backoff = 2 ** attempt
                    log.warning("HF embed failed (%s); retrying in %ss.", exc, backoff)
                    time.sleep(backoff)
        raise RuntimeError(f"Hugging Face embedding failed after 3 attempts: {last}") from last

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed([f"{self.PASSAGE_PREFIX}{text}" for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"{self.QUERY_PREFIX}{text}"])[0]


# ── Offline deterministic ────────────────────────────────────────────────────

class HashEmbedder(Embedder):
    """Deterministic pseudo-embeddings from a hash of the text. No network.

    Exists so the ingestion and retrieval paths are fully testable offline, the
    way StubLLMClient does for the LLM. It has no semantic meaning whatsoever —
    identical text embeds identically and everything else is noise, which is
    enough to exercise storage, SQL, and plumbing, and useless for judging
    retrieval quality.
    """

    def __init__(self, settings: Settings):
        self._dim = settings.rag_embedding_dim
        self._model = settings.rag_embedding_model or DEFAULT_MODELS["hash"]

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str, prefix: str) -> list[float]:
        values: list[float] = []
        counter = 0
        seed = f"{prefix}{text}".encode("utf-8")
        while len(values) < self._dim:
            digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            # 8 floats per 32-byte digest, mapped into [-1, 1).
            for offset in range(0, 32, 4):
                (raw,) = struct.unpack(">I", digest[offset:offset + 4])
                values.append(raw / 2**31 - 1.0)
            counter += 1
        return l2_normalise(values[: self._dim])

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._validate([self._vector(text, "passage:") for text in texts])

    def embed_query(self, text: str) -> list[float]:
        # Same prefix scheme as the real providers, so query/document asymmetry
        # is exercised offline too.
        return self._validate([self._vector(text, "query:")])[0]


# ── factory ──────────────────────────────────────────────────────────────────

def get_embedder(settings: Optional[Settings] = None) -> Embedder:
    """Build the configured embedder. Mirrors rag/corpora.py's retriever factory."""
    settings = settings or get_settings()
    provider = settings.rag_embedding_provider
    if provider == "vertex":
        return VertexEmbedder(settings)
    if provider == "hf":
        return HuggingFaceEmbedder(settings)
    if provider == "hash":
        return HashEmbedder(settings)
    raise RuntimeError(
        f"Unknown RAG_EMBEDDING_PROVIDER={provider!r}; expected vertex|hf|hash."
    )
