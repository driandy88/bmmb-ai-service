"""
Drift guard for the vendored v1 modules.

chat-v2 is a deliberately separate service so the experiment can be deleted in
one `rm -rf`. The price of that isolation is that the compliance-critical pieces
(terminology lint, PII redaction, eligibility rules, the audit writer, the
response contract) exist as COPIES of services/chat.

Copies drift. A drifted terminology lint is a compliance bug that nobody would
notice until an Islamic-finance wording violation reached a customer. So this
test fails loudly the moment a vendored file stops matching its v1 original.

Files are vendored at IDENTICAL relative paths, which is what lets them keep
their `from app.x import y` imports unchanged — so this comparison is a plain
byte-for-byte check with no normalisation.

If v1 changes on purpose: re-copy the file, don't edit it here.
If v2 needs different behaviour: write a NEW module, don't fork a vendored one.

Skips when services/chat is absent (inside the container image, where only this
service's own files are COPYed).
"""
from __future__ import annotations

import pathlib

import pytest

SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
V1_ROOT = SERVICE_ROOT.parent / "chat"
MANIFEST = SERVICE_ROOT / "VENDORED.txt"


def _vendored_paths() -> list[str]:
    return [ln.strip() for ln in MANIFEST.read_text().splitlines() if ln.strip()]


pytestmark = pytest.mark.skipif(
    not V1_ROOT.exists(),
    reason="services/chat not present (container build); drift is checked in CI from the repo root.",
)


def test_manifest_is_not_empty():
    assert _vendored_paths(), "VENDORED.txt is empty — the drift guard would pass vacuously."


@pytest.mark.parametrize("rel", _vendored_paths())
def test_vendored_file_matches_v1(rel: str):
    ours, theirs = SERVICE_ROOT / rel, V1_ROOT / rel
    assert ours.exists(), f"{rel} is in VENDORED.txt but missing from chat-v2"
    assert theirs.exists(), (
        f"{rel} no longer exists in services/chat. If v1 deleted or moved it, "
        f"update VENDORED.txt and this service's imports deliberately."
    )
    assert ours.read_bytes() == theirs.read_bytes(), (
        f"{rel} has DRIFTED from services/chat.\n"
        f"  v1: {theirs}\n  v2: {ours}\n"
        f"Re-copy from v1 rather than editing the vendored copy. If v2 genuinely "
        f"needs different behaviour, add a new module instead of forking this one."
    )