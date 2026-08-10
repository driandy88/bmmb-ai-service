"""File-level metadata extraction via ExifTool (provenance for fraud signals).

Pipes raw bytes to `exiftool -j -G -` on stdin -- the extraction pipeline never
touches disk, so neither do we. v1 returns exiftool's raw, untouched dump: no
curation, no flags. A file with no metadata is not an error; it just comes back
as a thin dict. See docs/metadata-extraction.md.
"""
import json
import subprocess

_EXIFTOOL = "exiftool"
_TIMEOUT_SECONDS = 30


class MetadataError(Exception):
    """exiftool could not run (binary missing, non-zero exit, unparseable
    output, or timeout). Distinct from 'file has no metadata', which returns a
    thin dict normally -- callers translate this into an HTTP 502."""


def get_file_metadata(file_bytes: bytes) -> dict:
    """Return exiftool's raw grouped (`-G`) metadata dump for one file's bytes.

    Reads from stdin, so exiftool detects the type from magic bytes rather than
    a file extension; `SourceFile` in the result reads "-". Raises MetadataError
    on execution/parse failure.
    """
    try:
        proc = subprocess.run(
            [_EXIFTOOL, "-j", "-G", "-"],  # -j JSON, -G grouped keys, - = stdin
            input=file_bytes,
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:  # exiftool binary not installed / not on PATH
        raise MetadataError(f"exiftool not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MetadataError("exiftool timed out") from exc

    if proc.returncode != 0:
        raise MetadataError(proc.stderr.decode(errors="replace").strip() or "exiftool failed")

    try:
        return json.loads(proc.stdout)[0]  # -j emits a one-element array
    except (ValueError, IndexError) as exc:
        raise MetadataError(f"could not parse exiftool output: {exc}") from exc
