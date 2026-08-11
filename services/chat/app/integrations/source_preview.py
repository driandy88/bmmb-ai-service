"""
Citation source preview (Tier 1) — turn a cited chunk's `doc_id` into a URL the browser can
open at the right page, so "see exactly where this came from" works from a citation click.

Security: ONLY documents in `config/source_docs.yaml` are servable (an explicit allowlist), and
`access_tier` gates the channel — an `internal` doc is NEVER served to the `customer` channel, even
though the whole PDF (not just the cited chunk) becomes visible once opened.

Modes (SOURCE_PREVIEW_MODE):
  * signed — return a short-lived v4 GCS **signed URL**; the browser fetches GCS directly (no load
             on this service). Falls back to `proxy` automatically if signing isn't permitted.
  * proxy  — return this service's `/chat/source/raw` path; the service streams the bytes (needs
             only GCS read, not signBlob).
  * off    — disabled (stub / offline): the endpoint returns 503.
Default: `off` when there's no GCP project, else `signed`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import yaml

log = logging.getLogger(__name__)

_MANIFEST = Path(__file__).resolve().parent.parent / "config" / "source_docs.yaml"


@dataclass(frozen=True)
class SourceDoc:
    doc_id: str
    source_uri: str          # gs://bucket/object.pdf
    title: str
    access_tier: str         # "customer" | "internal"


def _parse_gs(uri: str) -> tuple[str, str]:
    """gs://bucket/a/b.pdf -> ("bucket", "a/b.pdf")."""
    rest = uri[len("gs://"):]
    bucket, _, obj = rest.partition("/")
    return bucket, obj


class SourcePreview:
    def __init__(self, settings, manifest: Optional[dict] = None):
        self._mode = getattr(settings, "source_preview_mode", "off")
        self._ttl = int(getattr(settings, "source_url_ttl_seconds", 900))
        data = manifest if manifest is not None else (yaml.safe_load(_MANIFEST.read_text()) or {})
        self._docs: dict[str, SourceDoc] = {
            doc_id: SourceDoc(doc_id=doc_id, source_uri=v["source_uri"],
                              title=v.get("title", doc_id), access_tier=v.get("access_tier", "customer"))
            for doc_id, v in (data.get("docs") or {}).items()
        }

    # ── allowlist + access-tier gate ────────────────────────────────────────
    def resolve(self, doc_id: str) -> Optional[SourceDoc]:
        return self._docs.get((doc_id or "").strip())

    @staticmethod
    def allowed(doc: SourceDoc, channel: str) -> bool:
        """Internal docs are only ever served to the internal channel."""
        return doc.access_tier != "internal" or channel == "internal"

    # ── URL the browser opens (page fragment is appended client-side) ────────
    def url_for(self, doc: SourceDoc, channel: str) -> Optional[str]:
        if self._mode == "off":
            return None
        if self._mode == "proxy":
            return self._proxy_path(doc, channel)
        # signed (default): direct GCS URL; degrade to proxy if signing isn't permitted.
        try:
            return self._signed_url(doc.source_uri)
        except Exception as exc:  # noqa: BLE001 — never 500 the turn; fall back to proxy
            log.warning("Signed-URL failed for %s (%s); falling back to proxy stream.", doc.doc_id, exc)
            return self._proxy_path(doc, channel)

    @staticmethod
    def _proxy_path(doc: SourceDoc, channel: str) -> str:
        return "/chat/source/raw?" + urlencode({"doc_id": doc.doc_id, "channel": channel})

    def _signed_url(self, source_uri: str) -> str:
        from datetime import timedelta

        import google.auth
        from google.auth import compute_engine
        from google.auth.transport import requests as gauth_requests
        from google.cloud import storage  # lazy: not needed offline / in stub mode

        bucket, obj = _parse_gs(source_uri)
        blob = storage.Client().bucket(bucket).blob(obj)
        kwargs = dict(
            version="v4", expiration=timedelta(seconds=self._ttl), method="GET",
            response_type="application/pdf", response_disposition="inline",  # render in the iframe, don't download
        )
        # On Cloud Run the runtime credentials come from the metadata server and carry ONLY a token, so
        # generate_signed_url() can't sign a v4 URL locally ("you need a private key to sign
        # credentials"). Detect those creds and sign through the IAM SignBlob API instead — hand it the
        # SA email + a fresh access token (the runtime SA holds serviceAccountTokenCreator on itself).
        # A local key-based ADC still has a usable signer and signs directly, so leave it untouched.
        creds, _ = google.auth.default()
        if isinstance(creds, compute_engine.Credentials):
            creds.refresh(gauth_requests.Request())
            kwargs.update(service_account_email=creds.service_account_email, access_token=creds.token)
        return blob.generate_signed_url(**kwargs)

    # ── proxy-mode bytes (used only by /chat/source/raw) ────────────────────
    def download(self, doc: SourceDoc) -> Optional[bytes]:
        try:
            from google.cloud import storage

            bucket, obj = _parse_gs(doc.source_uri)
            return storage.Client().bucket(bucket).blob(obj).download_as_bytes()
        except Exception as exc:  # noqa: BLE001
            log.warning("Source stream failed for %s (%s).", doc.doc_id, exc)
            return None
