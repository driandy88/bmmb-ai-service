# Metadata Extraction Integration — Extraction Service

**Document version:** 2.1
**Date:** 2026-07-23
**Status:** Draft / ready for development

---

## 1. Summary

Add a **file-level metadata extraction** capability to the extraction service.
Using [ExifTool](https://exiftool.org/) — invoked directly as a subprocess with
file bytes piped in on **stdin** (no temp files, no `PyExifTool`) — the service
extracts every piece of provenance metadata a file exposes — EXIF, XMP, PDF
dictionary, software tags, creation/modification dates — and returns it as a raw
JSON dump alongside the existing content extraction.

**Primary use case:** downstream fraud detection (software anomalies, timeline
discrepancies, manipulated imagery).

### Scope of v1 (this document)

- **Dump ANY metadata a file exposes — raw, untouched.** No curation, no
  summary view, **no flags/heuristics**. The endpoint returns exiftool's output
  as-is.
- Derived fraud signals (editor-software detection, `modify > create` timeline
  checks, "metadata stripped" heuristics) are explicitly **out of scope for v1**
  and deferred to [Phase 5](#phase-5-post-v1-derived-flags--rules).

---

## 2. Current-state constraints

Grounding the plan in how the extraction service actually works today:

| Fact | Source | Implication |
| :--- | :--- | :--- |
| Pipeline is **in-memory** — uploads are read to `bytes`, never written to disk. | `app/extraction.py` (`await f.read()`) | Keep it that way: bytes are piped to exiftool on stdin, no disk staging. See [§4](#4-decision-feeding-bytes-to-exiftool). |
| Service is a **FastAPI app** using an `APIRouter` per feature, wired in `main.py`. | `app/extraction.py`, `app/main.py` | New endpoint follows the same router pattern. |
| Responses use a shared **`ApiResponse`** envelope (`{success, data, error}`). | `app/schemas.py` | Reuse it; don't invent a new response shape. |
| MIME allowlist + 20 MB cap already exist. | `app/extraction.py` (`ALLOWED_MIME_TYPES`, `MAX_FILE_SIZE`) | Reuse the same guardrails. |
| Service is **stateless** — no database in this service. | Repo layout / README | Persistence (JSONB column etc.) belongs in the downstream backend, **not here**. See [Phase 6](#phase-6-post-v1-persistence-downstream). |
| Docker base is `python:3.12-slim` with pip only. | `Dockerfile` | ExifTool binary must be apt-installed; `pip install PyExifTool` alone is insufficient. |

---

## 3. Dependencies

The stdin approach uses **only the exiftool binary** via Python's stdlib
`subprocess` — **no new Python package** (no `PyExifTool`).

**`requirements.txt`:** no change.

**`Dockerfile`** — add before the `pip install` layer:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*
```

Local dev also needs the binary (`brew install exiftool` / `apt install
libimage-exiftool-perl`).

---

## 4. Decision: feeding bytes to ExifTool

The pipeline holds files as `bytes`, but exiftool reads a file. Two ways to
bridge that gap. **This decision drives whether we can use `PyExifTool` at all.**

> **Decision: Option B (stdin).** See the recommendation below. Option A is
> retained for context and as a documented fallback.

### Option A — Temp file + PyExifTool (persistent process)

Stage the bytes in a `NamedTemporaryFile`, hand the path to a long-lived
`ExifToolHelper` (`-stay_open`), delete the temp file in a `finally`.

| Pros | Cons |
| :--- | :--- |
| Uses `PyExifTool` — clean, typed API (`get_metadata`), well-maintained. | Touches disk: adds temp-file lifecycle + cleanup responsibility. |
| **Persistent exiftool process** — one process serves many requests; no per-call ~100–200 ms interpreter/startup cost. Big win under load. | Requires care around temp-file cleanup on every path (success *and* error) to avoid leaking files in the container. |
| Handles all file types uniformly (PDF, HEIC, JPEG). | Temp dir must be writable & sized; a full/again read-only `/tmp` breaks it. |
| No shell involved; path is server-generated → no injection risk. | Persistent process is shared state — needs thread/concurrency consideration under a multi-worker uvicorn. |

### Option B — stdin pipe + raw subprocess (no temp file)

Pipe bytes to `exiftool -j -G -` on stdin per request (`subprocess`), parse
stdout. **Note: this cannot use `PyExifTool`'s `-stay_open` mode**, because that
mode already uses stdin to receive commands — so you lose the persistent process
and spawn exiftool fresh each call.

| Pros | Cons |
| :--- | :--- |
| **Never touches disk** — no temp-file lifecycle, no cleanup, smallest RCE/leak surface. | **Not `PyExifTool`.** Contradicts the chosen library; hand-rolled subprocess + JSON parsing. |
| Simplest security story (no path handling at all). | **Process spawned per request** (~100–200 ms exiftool startup each time) — the latency the persistent process was meant to avoid. |
| Stateless — trivially safe across concurrent workers. | Some formats/edge cases behave differently when read from a stream vs a named file; slightly less battle-tested. |
| No dependency on a writable temp dir. | We reimplement error handling `PyExifTool` already provides. |

### Recommendation

**Option B (stdin + raw subprocess).** Keeps the pipeline's in-memory,
disk-free property intact — no temp-file lifecycle, no cleanup, smallest
security surface — using only the exiftool binary and stdlib `subprocess`. The
tradeoff we accept: exiftool is spawned per request (~100–200 ms startup each),
and we forgo the `PyExifTool` wrapper. If load testing (Phase 3) shows that
per-call startup is a real bottleneck, Option A (temp file + PyExifTool's
persistent `-stay_open` process) is the documented fallback and can replace the
extractor internals without changing the endpoint contract.

---

## 5. Phased plan

### Phase 1 — Core extractor utility

**Goal:** a safe, standalone bytes → metadata function.

- [ ] `app/metadata_extractor.py` with `get_file_metadata(file_bytes: bytes) -> dict`.
- [ ] Pipe bytes to `exiftool -j -G -` on **stdin** via stdlib `subprocess` —
      args as a list, no `shell=True`, no temp files.
- [ ] `MetadataError` for exiftool execution failures (binary missing, non-zero
      exit, unparseable output, timeout) — distinct from "file has no metadata",
      which returns a thin dict, **not** an error.
- [ ] Return exiftool's **raw grouped dump untouched** (`-G` keys like
      `EXIF:Software`, `PDF:Producer`). No curation.

**Acceptance:** returns metadata for rich files; returns a thin dict (never
raises) for stripped files; touches no disk; surfaces `MetadataError` on a
missing binary or corrupt input.

**Sketch:**
```python
"""File-level metadata extraction via ExifTool (provenance for fraud signals).

Pipes raw bytes to `exiftool -j -G -` on stdin — the pipeline never touches
disk. v1 returns the raw, untouched dump: no curation, no flags.
"""
import json
import subprocess

_EXIFTOOL = "exiftool"


class MetadataError(Exception):
    """exiftool failed to run (binary missing / non-zero exit / bad output).
    NOT the same as 'file has no metadata', which returns a thin dict."""


def get_file_metadata(file_bytes: bytes) -> dict:
    try:
        proc = subprocess.run(
            [_EXIFTOOL, "-j", "-G", "-"],   # -j JSON, -G grouped keys, - = stdin
            input=file_bytes,
            capture_output=True,
            timeout=30,
        )
    except FileNotFoundError as exc:         # binary not installed
        raise MetadataError(f"exiftool not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MetadataError("exiftool timed out") from exc

    if proc.returncode != 0:
        raise MetadataError(proc.stderr.decode(errors="replace").strip())

    try:
        return json.loads(proc.stdout)[0]   # -j emits a one-element array
    except (ValueError, IndexError) as exc:
        raise MetadataError(f"could not parse exiftool output: {exc}") from exc
```

> **Note:** reading from stdin, exiftool detects type from magic bytes rather
> than a file extension. This is reliable for the allowed types (JPEG/PNG/WEBP/
> HEIC/PDF); `SourceFile` in the dump will read `"-"` instead of a path.

### Phase 2 — Standalone endpoint

**Goal:** expose the utility for isolated testing / microservice access.

- [ ] `app/metadata.py` — new `APIRouter`, route `POST /extract-metadata`
      (multipart/form-data, single `file`).
- [ ] Reuse `ALLOWED_MIME_TYPES`, `MAX_FILE_SIZE`, and the `ApiResponse` envelope.
- [ ] Wire `app.include_router(metadata_router)` into `main.py`.

**Sketch:**
```python
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from app.extraction import ALLOWED_MIME_TYPES, MAX_FILE_SIZE
from app.metadata_extractor import MetadataError, get_file_metadata
from app.schemas import ApiResponse

router = APIRouter(tags=["Metadata"])


@router.post("/extract-metadata", status_code=status.HTTP_200_OK)
async def extract_metadata(file: UploadFile = File(...)):
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(400, f"Unsupported file type '{content_type}'.")
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(400, f"'{file.filename}' is empty.")
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(413, f"'{file.filename}' exceeds the 20 MB limit.")

    try:
        metadata = get_file_metadata(file_bytes)
    except MetadataError as exc:
        raise HTTPException(502, f"Metadata extraction failed: {exc}")

    return ApiResponse(data={"filename": file.filename, "metadata": metadata})
```

#### Response contract (v1 — raw dump, no flags)

**A. Rich file (iPhone photo):**
```json
{
  "success": true,
  "error": null,
  "data": {
    "filename": "receipt.jpg",
    "metadata": {
      "SourceFile": "-",
      "File:FileType": "JPEG",
      "File:MIMEType": "image/jpeg",
      "EXIF:Make": "Apple",
      "EXIF:Model": "iPhone 14 Pro",
      "EXIF:Software": "17.5.1",
      "EXIF:DateTimeOriginal": "2026:07:20 14:32:10",
      "EXIF:CreateDate": "2026:07:20 14:32:10",
      "EXIF:ModifyDate": "2026:07:20 14:32:10",
      "EXIF:GPSLatitude": "...",
      "...": "... every other tag exiftool emits ..."
    }
  }
}
```

**B. Edited file (Photoshop) — dumped as-is, no interpretation in v1:**
```json
{
  "success": true,
  "data": {
    "filename": "altered.jpg",
    "metadata": {
      "File:FileType": "JPEG",
      "EXIF:Make": "Apple",
      "EXIF:Software": "Adobe Photoshop 26.0 (Macintosh)",
      "EXIF:CreateDate": "2026:07:20 14:32:10",
      "EXIF:ModifyDate": "2026:07:23 09:15:44",
      "XMP:CreatorTool": "Adobe Photoshop 26.0"
    }
  }
}
```

**C. Stripped file (WhatsApp) — thin dump, `success: true`, not an error:**
```json
{
  "success": true,
  "data": {
    "filename": "IMG-20260723-WA0001.jpg",
    "metadata": {
      "SourceFile": "-",
      "File:FileType": "JPEG",
      "File:MIMEType": "image/jpeg",
      "File:FileSize": "84 kB"
    }
  }
}
```

### Phase 3 — Testing & validation

- [ ] **Unit tests** with sample fixtures: standard iPhone photo, WhatsApp-stripped
      photo, Word-generated PDF, an edited/altered image, and a non-image (e.g. plain PDF).
- [ ] Assert stripped files return `success: true` with a thin dict (never a 5xx).
- [ ] **Latency check:** measure per-request overhead, i.e. the per-call
      exiftool spawn cost. If it's a bottleneck under load, revisit Option A
      (see [§4](#recommendation)).
- [ ] Confirm behaviour on a corrupt/garbage file (should surface `MetadataError` → 502).
- [ ] Confirm exiftool detects type correctly from stdin (magic bytes, no
      extension) for each allowed MIME type.

### Phase 4 — Deployment readiness

- [ ] Verify the exiftool apt layer builds and the binary resolves on `PATH` in the Cloud Run image.
- [ ] Smoke-test `/extract-metadata` against the deployed image.

---

## 6. Deferred (post-v1)

### Phase 5 (post-v1) — Derived flags & rules

Once raw dumps are trusted, add a **separate** signal layer (`app/metadata_flags.py`)
computing deterministic fraud signals from the raw dump — e.g.
`editor_software_present`, `modify_after_create`, `metadata_stripped`. Kept out
of v1 deliberately so the raw extraction is validated first and flag logic can
evolve without touching the extractor. Datetime normalization
(exiftool's `YYYY:MM:DD HH:MM:SS` → ISO-8601) belongs here.

### Phase 6 (post-v1) — Persistence (downstream)

This service is stateless. Storing metadata (e.g.
`ALTER TABLE document_extractions ADD COLUMN file_metadata JSONB`) and folding
it into the unified ingestion result is the **downstream backend's**
responsibility, not this service's.

### Other future enhancements

- Fold metadata into the main `/extract` response (vs. a standalone call) if the
  ingestion flow prefers a single round-trip.
- `PyMuPDF` for deeper PDF XMP extraction if exiftool proves insufficient on
  complex PDFs.

---

## 7. Risks & mitigations

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| Missing fields on stripped files | High (crashes if assumed present) | Raw dump only; downstream treats absent metadata as "unknown/suspicious", never an error. Extractor never raises on empty metadata. |
| Performance overhead (per-call spawn) | Medium (slower responses) | Accepted tradeoff of the stdin approach; `timeout=30` caps worst case. If profiling shows it's a bottleneck, fall back to Option A (persistent process). |
| Security (RCE / injection) | High | stdin only — no file paths at all; args passed as a list, never `shell=True`. Reuse existing MIME allowlist + size cap. |
| Missing exiftool binary in image | High (endpoint 502s) | apt layer in Dockerfile; extractor raises `MetadataError` → 502; Phase 4 verifies the binary resolves on `PATH`. |
