# Metadata Extraction Integration — Extraction Service

**Document version:** 2.5
**Date:** 2026-07-23
**Status:** exiftool `/extract-metadata` shipped · **`/extract-metadata-v2` (combined forensics) implemented**

> **v2.3 extends [§8 — structural analysis (PyMuPDF)](#8-structural-analysis-pymupdf--combined-extract-metadata-v2):**
> a second forensic tool combined with exiftool under one endpoint,
> covering **both PDFs (container structure) and images (decoded pixel facts)**.
> **v2.4** fixes the metadata contract as **open maps** ([§8.6.1](#861-response-stability-open-maps-vs-fixed-skeleton)).
> **v2.5** the combined endpoint is **built as `POST /extract-metadata-v2`** — layered
> `schema_version` envelope (7 keys, raw facts only), embedded-image exiftool + DQT,
> ground-truth image pixels. Modules: `structure_analyzer.py`, `dqt.py`,
> `forensics.py`; tests in `tests/test_metadata_v2.py`. *(Feature engineering / fraud
> scoring is out of scope for now — no `features`/`assessment` fields.)*

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
- Deeper PDF structural analysis via `PyMuPDF` — now a concrete design, see
  [§8](#8-pdf-structural-analysis-pymupdf--combined-analyze).

---

## 7. Risks & mitigations

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| Missing fields on stripped files | High (crashes if assumed present) | Raw dump only; downstream treats absent metadata as "unknown/suspicious", never an error. Extractor never raises on empty metadata. |
| Performance overhead (per-call spawn) | Medium (slower responses) | Accepted tradeoff of the stdin approach; `timeout=30` caps worst case. If profiling shows it's a bottleneck, fall back to Option A (persistent process). |
| Security (RCE / injection) | High | stdin only — no file paths at all; args passed as a list, never `shell=True`. Reuse existing MIME allowlist + size cap. |
| Missing exiftool binary in image | High (endpoint 502s) | apt layer in Dockerfile; extractor raises `MetadataError` → 502; Phase 4 verifies the binary resolves on `PATH`. |

---

## 8. Structural analysis (PyMuPDF) — combined `/extract-metadata-v2`

**Added v2.2, extended to images in v2.3.** exiftool reads a file's *metadata
dictionary* — flat, file-level. [PyMuPDF](https://pymupdf.readthedocs.io/)
(`fitz`) goes deeper, and runs on **both** file kinds:

- **PDFs** — cracks the **container structure** open (revisions, trailer, fonts,
  xref…), surfacing the signals that distinguish a genuine one-shot export from
  a doctored file.
- **Images** — decodes the pixels into a `Pixmap` and reports **ground-truth
  pixel facts** (real width/height, colorspace, DPI) to cross-check against
  exiftool's *claimed* EXIF values.

Together they form the **data basis** for fake-document / fraud detection.

> **Scope honesty:** the rich container signals (incremental updates, trailer
> `/ID`, fonts, xref, annotations, layers) are **PDF-only** — a flat JPEG/PNG has
> none of them. On images PyMuPDF adds a *ground-truth cross-check*, not an edit
> history; exiftool stays the primary tool for images.

Same v1 philosophy as exiftool: **collect raw structural facts, no verdicts /
flags.** Scoring is deferred (see [Phase 5](#phase-5-post-v1-derived-flags--rules)).

### 8.1 Decisions

| Decision | Choice |
| :--- | :--- |
| Endpoint shape | **One combined `POST /extract-metadata-v2`** — exiftool always runs; PyMuPDF runs on the matching branch (PDFs → `pdf_structure` incl. per-embedded-image exiftool+DQT, images → `image_structure` incl. DQT). Layered `schema_version` envelope, raw facts only. |
| Output | **Raw facts only**, no flags — consistent with the exiftool decision. |
| PyMuPDF parse failure | Recorded as `{"error": ...}` in the relevant block, **not** a 502 — an unparseable PDF (or an undecodable image, e.g. HEIC) is itself a forensic signal, and the exiftool metadata is still returned. exiftool failure remains a genuine 502 (core signal). |
| **Metadata blocks are open maps** | The `metadata` blocks (exiftool dump, PyMuPDF `document.metadata`) have **no fixed key set**. New/rare tags pass through verbatim; consumers **must use `.get()`** and never assume a key exists — a missing field is a **signal, not an error**. See [§8.6.1](#861-response-stability-open-maps-vs-fixed-skeleton). |
| `/extract-metadata` | **Kept as-is** (metadata-only building block, already shipped + tested). `/extract-metadata-v2` composes the same `get_file_metadata()`. |

### 8.2 Signals PyMuPDF exposes (the fraud "data basis")

**PDF container signals** (`fitz.open(...)`):

| Signal | PyMuPDF source | Why it matters |
| :--- | :--- | :--- |
| Producer / Creator software | `doc.metadata` | Editor software (Photoshop, iLovePDF…) on a doc that should be a clean bank/gov export |
| `creationDate` vs `modDate` | `doc.metadata` | modDate after creationDate ⇒ edited after it was made |
| **Incremental updates** (`%%EOF` count) | raw bytes / xref | Genuine one-shot exports have **one** `%%EOF`; each extra = a save *after* the original. Strongest single tamper signal. |
| **Trailer `/ID` pair** | trailer dict | Two IDs: original + current. If they differ ⇒ file changed since first write |
| **Fonts** (embedded? subset? mixed) | `page.get_fonts()` | Spliced/edited text usually introduces a new or non-embedded font inconsistent with the rest of the page |
| **Images** (filter, colorspace, dims) | `page.get_images()` | A JPEG pasted into an otherwise-text PDF, or recompressed regions |
| Annotations (Redact / FreeText / Stamp) | `page.annots()` | Redactions or overlaid text boxes = manual editing |
| Layers / OCGs | `doc.get_ocgs()` | Optional-content layers indicate layered editing tools |
| **XMP history** (`xmpMM:History`) | `doc.xref_xml_metadata()` | Explicit edit trail — which tools touched the file, when |
| Embedded files / JavaScript | `doc.embfile_names()`, JS | Unusual on a plain certificate; possible payload/automation |
| Digital signature + modified-after-sign | `doc.is_signed` / sigflags | A signed doc altered after signing is a red flag |

**Image pixel signals** (`fitz.Pixmap(...)`), a ground-truth cross-check on exiftool:

| Signal | PyMuPDF source | Why it matters |
| :--- | :--- | :--- |
| **Actual `width` / `height`** | `Pixmap.width/height` | **The key one:** compare against exiftool's *claimed* `EXIF:ExifImageWidth` / `File:ImageWidth`. A mismatch ⇒ the image was cropped/replaced but the EXIF wasn't updated. |
| `colorspace`, `n_components` | `Pixmap.colorspace`, `Pixmap.n` | Unexpected colorspace (e.g. CMYK on a phone photo) hints at a re-export |
| `xres` / `yres` (DPI) | `Pixmap.xres/yres` | Claimed-vs-actual resolution checks |
| `has_alpha` | `Pixmap.alpha` | Structural sanity |

> **Note:** `Pixmap` normalizes samples to **8 bits-per-component on decode**, so it
> is *not* the file's original bit depth — use exiftool's `File:BitsPerSample`
> for that. Don't conflate the two downstream.

### 8.3 Dependency

`PyMuPDF` ships as a **self-contained wheel** (bundles MuPDF) — **no apt layer
needed**, unlike exiftool. The Docker image is already covered.

**`requirements.txt`:**
```
PyMuPDF==1.24.*
```

### 8.4 `app/structure_analyzer.py` (sketch)

One module, two entry points (`analyze_pdf`, `analyze_image`) sharing a single
error type.

```python
"""Structural analysis via PyMuPDF (fitz) — a forensic 'data basis' for
fake-document / fraud detection. PDFs → container structure; images → decoded
pixel facts. v1 collects raw facts only; no scoring."""
import fitz  # PyMuPDF


class StructureAnalysisError(Exception):
    """PyMuPDF could not open/parse the bytes (bad PDF, or an image format
    MuPDF can't decode, e.g. some HEIC builds)."""


def analyze_pdf(file_bytes: bytes) -> dict:
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        raise StructureAnalysisError(str(exc)) from exc
    try:
        return {
            "document": {
                "page_count": doc.page_count,
                "xref_count": doc.xref_length(),
                "encrypted": doc.is_encrypted,
                "is_signed": bool(getattr(doc, "is_signed", False)),
                "metadata": doc.metadata,          # producer, creator, dates, ...
            },
            "revisions": {
                # one-shot exports = 1 EOF; each extra marker = an incremental save
                "eof_markers": file_bytes.count(b"%%EOF"),
                "incremental_updates": max(file_bytes.count(b"%%EOF") - 1, 0),
                "trailer_id": _trailer_id_pair(doc),   # [original, current]
            },
            "xmp": doc.xref_xml_metadata() or None,     # raw XMP incl. edit history
            "layers": doc.get_ocgs(),                   # optional-content groups
            "embedded_files": doc.embfile_names(),
            "fonts": _fonts_per_page(doc),              # name/type/embedded/subset
            "images": _images_per_page(doc),            # filter/colorspace/dims
            "annotations": _annotations_per_page(doc),  # types present per page
        }
    finally:
        doc.close()


def analyze_image(file_bytes: bytes) -> dict:
    """Ground-truth pixel facts, to cross-check exiftool's claimed EXIF dims."""
    try:
        pix = fitz.Pixmap(file_bytes)               # decodes the image
    except Exception as exc:
        raise StructureAnalysisError(str(exc)) from exc
    return {
        "width": pix.width,                          # ACTUAL decoded pixels
        "height": pix.height,
        "colorspace": pix.colorspace.name if pix.colorspace else None,
        "n_components": pix.n,
        "has_alpha": bool(pix.alpha),
        "xres": pix.xres,
        "yres": pix.yres,
        # NB: no bit-depth here — Pixmap is 8bpc post-decode; use exiftool's
        # File:BitsPerSample for the original depth.
    }

# _fonts_per_page  -> [{"page":1,"name":"Arial","type":"TrueType","embedded":true,"subset":false}, ...]
# _images_per_page -> [{"page":1,"xref":12,"width":800,"height":200,"colorspace":"DeviceRGB","filter":"DCTDecode"}, ...]
# _annotations_per_page -> [{"page":1,"types":["FreeText"]}, ...]
# _trailer_id_pair -> ["<a1b2…>", "<a1b2…>"]  (equal = untouched; differ = modified)
```

### 8.5 Architecture (as built)

Router branches by MIME; each branch is an **orchestrated multi-tool pipeline**
with a shared image core (extract → exiftool + DQT). Extractors stay
single-purpose; composition lives in one orchestrator.

| Layer | Module | Responsibility |
| :--- | :--- | :--- |
| Extractor | `app/metadata_extractor.py` | exiftool (stdin) → dict |
| Extractor | `app/structure_analyzer.py` | PyMuPDF **only**: `open_pdf` / `container_facts` / `iter_embedded_images` / `image_pixels`; `StructureAnalysisError` |
| Extractor | `app/dqt.py` | PIL → JPEG quantization fingerprint (`jpeg_dqt`) |
| **Orchestrator** | `app/forensics.py` | `analyze()` builds the envelope; `analyze_pdf()` loops embedded images enriching each with exiftool + DQT; `analyze_image()` = pixels + DQT |
| Router | `app/metadata.py` | `POST /extract-metadata-v2` — validate (shared `_read_validated`) → `forensics.analyze` → `ApiResponse` |

Error policy: exiftool on the **primary** file failing → **502** (core signal);
any PyMuPDF/DQT failure (PDF won't open, image won't decode, one bad embedded
image) is recorded as `{"error": ...}`/`{"skipped": ...}` in its block and never
sinks the response. Embedded images are deduped by `xref` and capped
(`MAX_EMBEDDED_IMAGES`, `MAX_EMBEDDED_IMAGE_BYTES`).

### 8.6 Response contract — layered envelope (`schema_version` "1")

`POST /extract-metadata-v2`. The `data` object always carries the **same 7 keys**
(`null` where not applicable), so integrators code against a fixed shape. **Raw
facts only** — feature engineering / fraud scoring is out of scope for now.
`schema_version` keeps the envelope forward-compatible if such fields are added
later. Integration guarantees are in [§8.6.1](#861-response-stability-open-maps-vs-fixed-skeleton).

**PDF — both tools, embedded image carries its own metadata + DQT fingerprint:**
```json
{
  "success": true,
  "data": {
    "schema_version": "1",
    "file": "payslip.pdf",
    "content_type": "application/pdf",
    "kind": "pdf",
    "metadata": { "PDF:Producer": "4-Heights PDF Library", "...": "...exiftool dump..." },
    "pdf_structure": {
      "document":   { "page_count": 1, "xref_count": 14, "encrypted": false, "needs_pass": false,
                      "permissions": -4, "is_repaired": false,
                      "metadata": { "producer": "4-Heights PDF Library", "creator": "(unspecified)",
                                    "creationDate": "D:20260709101608+00'00'", "modDate": "D:20260723101542Z" } },
      "revisions":  { "eof_markers": 1, "incremental_updates": 0, "startxref_count": 1,
                      "trailer_id": ["93216A19...", "CEEF0E4C..."] },
      "provenance": { "xmp": null, "sigflags": -1, "javascript_markers": 0,
                      "embedded_files": [], "layers": {} },
      "content": {
        "fonts":       [{ "page": 1, "xref": 4, "type": "Type1", "basefont": "Helvetica", "embedded": false }],
        "images":      [{
          "xref": 8, "pages": [1], "width": 1920, "height": 1080, "bpc": 8,
          "colorspace": "DeviceRGB", "filter": "DCTDecode", "extracted_ext": "jpeg",
          "metadata": { "File:FileType": "JPEG", "...": "...exiftool on the extracted image..." },
          "dqt":      { "table_count": 2, "signature": "071f0329733acb1d", "quality_estimate": 75 }
        }],
        "annotations": [],
        "link_count": 0,
        "form_field_count": 0
      }
    },
    "image_structure": null
  }
}
```

The tells (left for the scoring phase): **trailer IDs differ** (modified after
write) · `modDate` ≫ `creationDate` · a PDF-manipulation-library producer · the
embedded image's `dqt.quality_estimate` 75 (re-saved) even though its EXIF was
stripped.

**Image — `pdf_structure: null`, DQT nested in `image_structure`:**
```json
{
  "success": true,
  "data": {
    "schema_version": "1",
    "file": "receipt.jpg",
    "content_type": "image/jpeg",
    "kind": "image",
    "metadata": { "EXIF:Make": "Apple", "EXIF:Software": "17.5.1",
                  "EXIF:ExifImageWidth": 4032, "EXIF:ExifImageHeight": 3024, "...": "..." },
    "pdf_structure": null,
    "image_structure": {
      "width": 620, "height": 45, "colorspace": "DeviceRGB", "n_components": 3,
      "has_alpha": false, "xres_dpi": 72, "yres_dpi": 72,
      "dqt": { "table_count": 2, "signature": "a1b2...", "quality_estimate": 78 }
    }
  }
}
```

Tell: **dimension mismatch** — EXIF claims 4032×3024 but decoded pixels are
620×45 ⇒ cropped/replaced without updating EXIF. (PNG/WEBP → `dqt: null`.)

**PDF that won't parse — metadata still returned, error recorded (not a 502):**
```json
{
  "success": true,
  "data": {
    "schema_version": "1", "file": "corrupt.pdf", "content_type": "application/pdf", "kind": "pdf",
    "metadata": { "File:FileType": "PDF", "...": "..." },
    "pdf_structure": { "error": "cannot open broken document" },
    "image_structure": null
  }
}
```

**Image PyMuPDF can't decode (e.g. some HEIC builds) — metadata still returned:**
```json
{
  "success": true,
  "data": {
    "schema_version": "1", "file": "photo.heic", "content_type": "image/heic", "kind": "image",
    "metadata": { "File:FileType": "HEIC", "EXIF:Make": "Apple", "...": "..." },
    "pdf_structure": null,
    "image_structure": { "error": "unsupported image format" }
  }
}
```

#### 8.6.1 Response stability: open maps vs fixed skeleton

The response has **two layers with opposite stability guarantees** — consumers
must know which is which:

| Layer | Examples | Behaviour when a new field appears |
| :--- | :--- | :--- |
| **Fixed skeleton** (we assemble it) | `pdf_structure.revisions`, `.content.fonts`, `image_structure.width`… | Stable — a new PyMuPDF capability appears **only** if we add code for it. Presence still isn't guaranteed (`trailer_id` may be `null`; a block may be `{"error": ...}`). |
| **Open maps** (pass-through) | `metadata` (exiftool dump), `pdf_structure.document.metadata` | **No fixed key set.** New/rare tags flow through automatically; keys vary per file. |

**Contract — consumers of the metadata blocks:**

- **Always use `.get()` / optional access.** Never assume a key is present.
- **A missing field is a fraud *signal*, not an error** (a stripped/scrubbed file
  legitimately lacks EXIF; that absence is informative). Do **not** validate the
  metadata blocks against a closed schema — an unexpected key must never cause a
  failure.
- The service emits new metadata keys silently; downstream absorbs them without
  code changes precisely because nothing treats these maps as a fixed shape.

```python
# ✅ correct — tolerant of any key set
software = metadata.get("EXIF:Software") or metadata.get("XMP:CreatorTool")
if metadata.get("EXIF:Make") is None:
    ...  # absent camera make -> a signal, handled, not raised

# ❌ wrong — assumes keys exist; breaks on a stripped file or a new tag
software = metadata["EXIF:Software"]
```

### 8.7 Checklist — status

- [x] `PyMuPDF==1.24.*` + `pillow==11.*` in `requirements.txt` (no Docker change; exiftool apt layer already present).
- [x] `app/structure_analyzer.py` — PyMuPDF-only: `open_pdf`, `container_facts`, `iter_embedded_images` (dedupe + caps), `image_pixels`; `StructureAnalysisError`.
- [x] `app/dqt.py` — `jpeg_dqt()` fingerprint (signature + quality estimate; `include_tables` for raw matrices).
- [x] `app/forensics.py` — orchestrator building the layered `schema_version` envelope; embedded-image loop enriches each with exiftool + DQT.
- [x] `POST /extract-metadata-v2` in `app/metadata.py` (shared `_read_validated` guardrails; exiftool-primary failure → 502, structural failures recorded as data).
- [x] Tests ([tests/test_metadata_v2.py](../services/extraction/tests/test_metadata_v2.py)) — validation, wiring, DQT units, orchestrator error handling, and real-tool runs against the three samples incl. `Tampered_Image.pdf` (trailer-ID mismatch + embedded-image DQT). **30 passing** (with v1).
- [ ] **Deploy-time:** verify HEIC/HEIF behaviour in the built wheel — decodes, or degrades to `image_structure: {"error": ...}` (exiftool metadata still returned).

### 8.8 Risks

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| PyMuPDF version API drift (`fitz` renames) | Medium | Pin `PyMuPDF==1.24.*`; guard optional attrs with `getattr`. |
| Malformed / tampered PDF crashes the parser | Medium | `analyze_pdf`/`analyze_image` catch broadly → `StructureAnalysisError`; endpoint records it as data, not a 502. |
| **HEIC/HEIF not decodable** by the MuPDF wheel | Low–Med | Degrades to `image_structure: {"error": ...}`; exiftool metadata still returned. Verified in the deploy checklist. |
| `Pixmap` bit depth conflated with original | Low | Documented: `Pixmap` is 8bpc post-decode; original depth comes from exiftool's `File:BitsPerSample`. |
| Large/complex PDF slow to parse | Low–Med | Same `MAX_FILE_SIZE` cap; revisit a per-analysis timeout if profiling shows it. |
| Downstream consumer assumes a fixed key set (breaks on a new/absent tag) | Medium | **Open-map contract** ([§8.6.1](#861-response-stability-open-maps-vs-fixed-skeleton)): metadata blocks use `.get()`; never validate them against a closed schema; absent key = signal. |
