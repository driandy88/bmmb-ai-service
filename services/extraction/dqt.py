"""JPEG quantization-table (DQT) fingerprint via PIL.

The DQT is the strongest encoder signature: it lives in the compressed stream,
so it SURVIVES EXIF stripping. Two images with the same `signature` came from
the same encoder+settings; a low `quality_estimate` (~75) means a re-save by an
editor. Raw facts only -- no scoring. Returns None for non-JPEG streams.
"""
import hashlib
import io

try:
    from PIL import Image
    _HAVE_PIL = True
except ImportError:  # pragma: no cover - pillow is a hard runtime dep, guard anyway
    _HAVE_PIL = False

# Standard IJG (Annex K) luminance quantization table @ quality 50 -- the baseline
# we invert against to estimate a JPEG's save quality from its own tables.
_STD_LUM = [
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
]


def _quality(qt: dict) -> int | None:
    """Estimate save quality (1-100) by inverting the IJG scaling formula against
    the luminance table. An approximation, but a reliable re-compression tell."""
    lum = qt.get(0)
    if not lum:
        return None
    scales = [(q * 100 - 50) / s for q, s in zip(lum, _STD_LUM) if s]
    if not scales:
        return None
    S = sum(scales) / len(scales)
    if S <= 0:
        return 100
    q = (200 - S) / 2 if S < 100 else 5000 / S
    return round(max(1, min(100, q)))


def jpeg_dqt(image_bytes: bytes, include_tables: bool = False):
    """DQT fingerprint for one image's bytes. None for non-JPEG (no DQT);
    {"skipped"/"error": ...} when unavailable. Set include_tables to embed the
    raw 64-value matrices (verbose)."""
    if not _HAVE_PIL:
        return {"skipped": "pillow not installed"}
    try:
        im = Image.open(io.BytesIO(image_bytes))
        qt = getattr(im, "quantization", None)  # {table_id: [64 ints]} for JPEGs
    except Exception as exc:  # noqa: BLE001 - any decode failure is non-fatal here
        return {"error": str(exc)}
    if not qt:
        return None  # not a JPEG / no quantization tables (e.g. PNG-reconstructed)
    tables = {idx: list(vals) for idx, vals in qt.items()}
    signature = hashlib.sha256(
        repr(sorted((k, list(v)) for k, v in qt.items())).encode()
    ).hexdigest()[:16]
    result = {
        "table_count": len(tables),
        "signature": signature,
        "quality_estimate": _quality(qt),
    }
    if include_tables:
        result["tables"] = tables
    return result
