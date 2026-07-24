# Extractable Metadata — Reference by File Type

**Date:** 2026-07-23
**Companion to:** [metadata-extraction.md](metadata-extraction.md) (the `/analyze` design)

This is a **field-level catalogue**: for each file type the service accepts, what
metadata can be pulled, by which tool, and why it matters for fake-document /
fraud detection. It describes *what is extractable* — scoring/flags are a
separate, later phase.

> **Contract reminder:** metadata blocks are **open maps** — the exact keys vary
> per file and per source software. Never assume a key is present; a missing
> field is a *signal*, not an error. See
> [metadata-extraction.md §8.6.1](metadata-extraction.md#861-response-stability-open-maps-vs-fixed-skeleton).

## Tools

| Tool | Reads | Notes |
| :--- | :--- | :--- |
| **exiftool** | Metadata *tags* — EXIF, XMP, IPTC, JFIF, ICC, PDF `/Info`, file structure | Invoked over stdin (`-j -G`); grouped keys like `EXIF:Software`. Best-in-class tag reader. |
| **PyMuPDF** (`fitz`) | PDF *container structure* + decoded image *pixels* | Cracks PDFs open (revisions, fonts, xref…), extracts embedded image streams, decodes image pixels. Does **not** parse EXIF. |
| **PIL / DQT** | JPEG *quantization tables* | The encoder fingerprint — lives in the compressed stream, survives EXIF stripping. |

Accepted MIME types: `application/pdf`, `image/jpeg`, `image/png`, `image/webp`,
`image/heic`, `image/heif`.

---

## 1. PDF (`application/pdf`)

The richest type — both tools run. exiftool reads the `/Info` + XMP tags;
PyMuPDF exposes the container structure and every embedded image.

### 1.1 Document / `/Info` metadata

| Field | Tool | Fraud relevance |
| :--- | :--- | :--- |
| `producer`, `creator` | both | Editor/generator software — Photoshop / a PDF-manipulation library on a doc that should be a clean export |
| `creationDate`, `modDate` | both | `modDate` later than `creationDate` ⇒ edited after creation |
| `title`, `author`, `subject`, `keywords` | both | Author/tool leakage; template mismatches |
| `format` (PDF version) | PyMuPDF | Version inconsistent with claimed origin |
| `encrypted`, `needs_pass`, `permissions` | PyMuPDF | Permission/encryption state |
| `page_count`, `xref_count` | PyMuPDF | Object-count anomalies |
| `is_repaired` | PyMuPDF | PyMuPDF had to repair the file to open it — often a tamper sign |

### 1.2 Revisions (strongest container tamper signal)

| Field | Tool | Fraud relevance |
| :--- | :--- | :--- |
| `eof_markers` | raw bytes | A one-shot export has exactly **one** `%%EOF` |
| `incremental_updates` | raw bytes | Each extra `%%EOF` = a save *after* the original ⇒ edited & re-saved |
| `startxref_count` | raw bytes | Corroborates incremental updates |
| `trailer_id` `[original, current]` | PyMuPDF | The two IDs **differ ⇒ file changed since first write** |

### 1.3 Provenance / structure

| Field | Tool | Fraud relevance |
| :--- | :--- | :--- |
| `xmp` (raw XMP, incl. `xmpMM:History`) | PyMuPDF | Explicit edit trail — which tools touched the file, when |
| `sigflags` | PyMuPDF | Digital signature state (`-1` none, `1` signed, `3` signed+append-only) |
| `javascript_markers` | raw bytes | JS in a plain certificate is unusual / possible automation |
| `embedded_files` | PyMuPDF | Unexpected attachments/payloads |
| `layers` (OCGs) | PyMuPDF | Optional-content layers ⇒ layered editing tools |

### 1.4 Content — fonts, annotations, links

| Field | Tool | Fraud relevance |
| :--- | :--- | :--- |
| fonts: `basefont`, `type`, `embedded` | PyMuPDF | A new or **non-embedded** font inconsistent with the page ⇒ spliced/edited text |
| annotations: `type`, `rect` | PyMuPDF | `FreeText` / `Stamp` / `Redact` ⇒ manual overlays / redaction |
| `link_count`, `form_field_count` | PyMuPDF | Structural sanity |
| page text | PyMuPDF | Eyeballing overlays / OCR-vs-text mismatches |

### 1.5 Embedded images (per image) — often the smoking gun

Each raster image inside the PDF is extracted (`doc.extract_image`) and analysed
in its own right. Deduped by `xref`, capped by count/size.

| Field | Tool | Fraud relevance |
| :--- | :--- | :--- |
| `xref`, `pages`, `width`, `height`, `bpc`, `colorspace`, `filter` | PyMuPDF | `DCTDecode` (JPEG) pasted into a text PDF; dims/colorspace anomalies |
| `metadata` (full exiftool dump of the extracted bytes) | exiftool | **Embedded image often retains its ORIGINAL EXIF** — editor `Software`, device `Make`, GPS — even when the PDF wrapper looks clean. *(Proven: survives embedding. But a re-encode can strip it — absence is normal.)* |
| `dqt`: `signature`, `quality_estimate`, `table_count`, (`tables` w/ `--full`) | PIL | **Encoder fingerprint** — metadata-independent, survives EXIF stripping. `quality_estimate` ~75 ⇒ re-saved; differing `signature` between images ⇒ one was spliced in |

---

## 2. JPEG (`image/jpeg`)

The richest *image* type. exiftool reads its tag blocks; PyMuPDF gives
ground-truth pixels; PIL gives the DQT fingerprint.

### 2.1 exiftool tag groups (open map — presence varies)

| Group / example keys | Fraud relevance |
| :--- | :--- |
| `EXIF:Make`, `EXIF:Model`, `EXIF:Software` | Device origin + **editor software** (Photoshop etc.) |
| `EXIF:DateTimeOriginal`, `EXIF:CreateDate`, `EXIF:ModifyDate` | Timeline — `Modify` after `Create` ⇒ edited |
| `EXIF:ExifImageWidth/Height` | *Claimed* dims — cross-check vs PyMuPDF ground truth |
| `EXIF:GPSLatitude/Longitude` | Capture location (or its suspicious absence) |
| `XMP:CreatorTool`, `XMP:History` | Editor + edit trail |
| `IPTC:*` | Caption/byline provenance |
| `ICC_Profile:*` | Colour profile — re-export tells |
| `File:EncodingProcess`, `File:YCbCrSubSampling`, `File:ColorComponents`, `File:BitsPerSample` | **Encoder fingerprint** (present even when EXIF stripped) |
| `Composite:ImageSize`, `Composite:Megapixels` | Derived conveniences |

> **Stripped-file case:** WhatsApp / screenshots / re-exports drop most `EXIF:*`.
> The thin result is itself informative — and the `File:*` + DQT fingerprint
> still carry signal.

### 2.2 PyMuPDF pixel facts (ground truth)

| Field | Fraud relevance |
| :--- | :--- |
| `width`, `height` | **Actual decoded pixels** — mismatch vs `EXIF:ExifImageWidth` ⇒ cropped/replaced without updating EXIF |
| `colorspace`, `n_components` | Unexpected colorspace ⇒ re-export |
| `xres_dpi`, `yres_dpi` | Claimed-vs-actual resolution |
| `has_alpha` | Structural sanity |

### 2.3 DQT — JPEG quantization tables (PIL)

| Field | Fraud relevance |
| :--- | :--- |
| `signature` | Hash of the tables — identical across two images ⇒ same encoder+settings; differing ⇒ likely spliced |
| `quality_estimate` | ~90+ = pristine original; ~75 = re-compressed by an editor |
| `table_count`, `tables` (`--full`) | Raw matrices — compare against known-encoder tables |

---

## 3. PNG / WEBP (`image/png`, `image/webp`)

Lossless, no JPEG DQT. Metadata is thinner than JPEG.

| Source | Extractable | Fraud relevance |
| :--- | :--- | :--- |
| exiftool | `File:*` (type, dims, bit depth, colour type, compression), `PNG:*` text chunks (`Software`, `Creation Time`), any `XMP:*`/`EXIF:*` chunks | Editor `Software` in a `tEXt` chunk; dims/bit-depth facts |
| PyMuPDF | `width`, `height`, `colorspace`, `n_components`, `has_alpha`, DPI | Ground-truth pixels for the dimension cross-check |
| DQT | ✗ (not a JPEG) | `dqt` returns `null` |

> A "photo" delivered as PNG is itself mildly unusual (cameras emit JPEG/HEIC) —
> possible screenshot or re-export.

---

## 4. HEIC / HEIF (`image/heic`, `image/heif`)

Apple's modern container. exiftool reads its metadata well; **PyMuPDF may or may
not decode it** depending on how the MuPDF wheel was built.

| Source | Extractable | Notes |
| :--- | :--- | :--- |
| exiftool | `EXIF:*`, `XMP:*`, `File:*`, QuickTime/HEIC structure — typically **rich** (device, dates, GPS) | Primary tool for HEIC |
| PyMuPDF | `width`/`height`/colorspace **if** the build decodes HEIC; else `image_structure: {"error": ...}` | Degrades gracefully; exiftool metadata still returned |
| DQT | ✗ (not baseline JPEG) | n/a |

---

## Quick matrix — what each type yields

| Capability | PDF | JPEG | PNG/WEBP | HEIC |
| :--- | :---: | :---: | :---: | :---: |
| exiftool metadata tags | ✅ (`/Info`+XMP) | ✅ rich | ◐ thinner | ✅ rich* |
| Container structure (revisions, trailer, fonts) | ✅ | — | — | — |
| Embedded-image extraction + per-image EXIF | ✅ | — | — | — |
| PyMuPDF ground-truth pixels | via embedded imgs | ✅ | ✅ | ◐ build-dependent |
| DQT encoder fingerprint | via embedded JPEGs | ✅ | ✗ | ✗ |

`*` HEIC exiftool support is reliable; PyMuPDF decode is not guaranteed.

---

## See also

- [metadata-extraction.md](metadata-extraction.md) — the `/analyze` endpoint design (§8).
- `services/extraction/notebooks/explore_pymupdf.py` — runnable dump of all of the
  above as JSON (`--full` includes raw DQT matrices).
