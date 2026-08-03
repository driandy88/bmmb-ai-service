"""
Stage 3 · Curate (brief §5).

Reorganises page-ordered Markdown into ONE canonical document per program with
consistent sections (config: corpora.yaml `sections` / `section_keywords`), routes
pages across corpora (program vs sales_dir), drops skipped filler, and carries the
access_tier / content_type so downstream stages can enforce §11 and disclaimers.

Curation is DETERMINISTIC — it maps slide headings to canonical sections and moves
the body text VERBATIM. No LLM rewrites the content, so every figure the human
signed off in Stage 2 survives byte-for-byte.

Output: data/03_curated/<corpus>/<program_code|doc_id>.md — YAML front-matter +
`## Section` blocks, each with a `<!-- section=… pages=… content_type=… -->`
provenance comment that Stage 4/5 read.

Hard gate (§5, §11): refuses to run for any document without a valid Stage-2 sign-off.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import yaml

from config.settings import get_settings
from pipeline.stage2_verify import signoff_ok

_ROOT = Path(__file__).resolve().parent.parent
_DOCS_PATH = _ROOT / "config" / "documents.yaml"
_CORPORA_PATH = _ROOT / "config" / "corpora.yaml"

_H2 = re.compile(r"^##\s+(.*)$", re.M)
_TITLES = {
    "financing_size": "Financing size", "financing_rate": "Financing rate",
    "margin": "Margin of financing", "guarantee": "Guarantee", "fees": "Fees & benefits",
    "documents": "Required documents", "criteria": "Filtering criteria",
}


def _load_yaml(path: Path):
    with open(path) as f:
        return yaml.safe_load(f)


def _title(section: str) -> str:
    return _TITLES.get(section, section.replace("_", " ").capitalize())


def _matchers(corpora) -> list[tuple[str, list[str]]]:
    return [(sec, [k.lower() for k in kws]) for sec, kws in (corpora.get("section_keywords") or {}).items()]


def _canonical_section(heading: str | None, matchers) -> str:
    if not heading:
        return "overview"                       # title / intro block
    h = heading.lower()
    for sec, kws in matchers:
        if any(k in h for k in kws):
            return sec
    return "other"


def _blocks(md: str) -> list[tuple[str | None, str]]:
    """(heading, body) per `## ` block; content before the first heading -> (None, …)."""
    out: list[tuple[str | None, str]] = []
    marks = list(_H2.finditer(md))
    if not marks:
        if md.strip():
            out.append((None, md.strip()))
        return out
    pre = md[: marks[0].start()].strip()
    if pre:
        out.append((None, pre))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(md)
        body = md[m.end():end].strip()
        if body:
            out.append((m.group(1).strip(), body))
    return out


def _page_overrides(doc: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for ov in doc.get("page_overrides") or []:
        for p in ov.get("pages", []):
            entry = out.setdefault(int(p), {})
            for k in ("corpus", "content_type", "access_tier", "skip"):
                if k in ov:
                    entry[k] = ov[k]
    return out


def _write_curated(doc, corpus, items, section_order, settings) -> Path:
    by_sec: dict[str, list] = defaultdict(list)
    for it in items:
        by_sec[it["section"]].append(it)
    ordered = [s for s in section_order if s in by_sec] + [s for s in by_sec if s not in section_order]

    name = doc.get("program_code") or doc["doc_id"]
    fm = {
        "doc_id": doc["doc_id"], "program_code": doc.get("program_code"), "title": doc.get("title"),
        "corpus": corpus, "access_tier": doc.get("default_access_tier", "customer"),
        "version": str(doc.get("version")), "effective_date": str(doc.get("effective_date") or ""),
        "expiry_date": str(doc.get("expiry_date") or ""), "lang": doc.get("lang", "en"),
        "source_uri": doc.get("source_uri"),
        "source_pages": sorted({it["page"] for it in items}), "sections": ordered,
    }
    lines = ["---", yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip(), "---", ""]
    for sec in ordered:
        rows = by_sec[sec]
        pages = sorted({it["page"] for it in rows})
        ctype = "indicative" if any(it["content_type"] == "indicative" for it in rows) else "standard"
        lines.append(f"## {_title(sec)}")
        lines.append(f"<!-- section={sec} pages={pages} content_type={ctype} -->")
        for it in rows:
            if it["heading"] and it["heading"].strip().lower() != _title(sec).lower():
                lines.append(f"**{it['heading'].strip().title()}**")
            lines.append(it["body"])
            lines.append("")
    out_dir = _ROOT / settings.data_dir / "03_curated" / corpus
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.md"
    out.write_text("\n".join(lines).rstrip() + "\n")
    return out


def run(args) -> int:
    settings = get_settings()
    corpora = _load_yaml(_CORPORA_PATH)
    matchers = _matchers(corpora)
    section_order = corpora.get("sections", [])
    documents = _load_yaml(_DOCS_PATH) or []
    if args.doc:
        documents = [d for d in documents if d["doc_id"] == args.doc]
        if not documents:
            print(f"[stage3_curate] no doc_id={args.doc!r} in documents.yaml")
            return 1

    # ── §11 hard gate: every targeted document needs a valid Stage-2 sign-off ──
    blocked = []
    for doc in documents:
        version = getattr(args, "version", None) or doc.get("version")
        ok, reason = signoff_ok(doc["doc_id"], version)
        if not ok:
            blocked.append((doc["doc_id"], reason))
    if blocked:
        for doc_id, reason in blocked:
            print(f"[stage3_curate] REFUSED {doc_id}: {reason}")
        print('[stage3_curate] Stage 3 requires a Stage-2 sign-off (§11 hard gate). '
              'Run: cli.py stage2 --doc <id> --approve --by "<name>"')
        return 1

    parsed_root = _ROOT / settings.data_dir / "01_parsed"
    for doc in documents:
        doc_id = doc["doc_id"]
        parsed_dir = parsed_root / doc_id
        pages = sorted(int(p.stem.split("_")[1]) for p in parsed_dir.glob("page_*.md"))
        if not pages:
            print(f"[stage3_curate] {doc_id}: nothing parsed — run stage1 first. Skipping.")
            continue
        overrides = _page_overrides(doc)
        default_corpus = doc.get("default_corpus", "program")
        default_tier = doc.get("default_access_tier", "customer")

        buckets: dict[str, list] = defaultdict(list)
        unmapped: list[str] = []
        for page_no in pages:
            ov = overrides.get(page_no, {})
            if ov.get("skip"):
                continue
            corpus = ov.get("corpus", default_corpus)
            content_type = ov.get("content_type", "standard")
            for heading, body in _blocks((parsed_dir / f"page_{page_no:03d}.md").read_text()):
                section = _canonical_section(heading, matchers)
                if section == "other" and heading:
                    unmapped.append(f"p{page_no}:{heading}")
                buckets[corpus].append({
                    "section": section, "heading": heading, "body": body,
                    "page": page_no, "content_type": content_type,
                })

        # access_tier is doc-level here (customer kits vs the internal doc)
        written = []
        for corpus, items in buckets.items():
            out = _write_curated({**doc, "default_access_tier": default_tier}, corpus, items, section_order, settings)
            written.append(f"{corpus}/{out.name}")
        note = f" · unmapped->other: {unmapped}" if unmapped else ""
        print(f"[stage3_curate] {doc_id}: {', '.join(written)}{note}")

    if any(d.get("default_corpus", "program") == "sales_dir" or
           any(ov.get("corpus") == "sales_dir" for ov in (d.get("page_overrides") or []))
           for d in documents):
        print("[stage3_curate] NOTE: sales_dir contact pages are near-duplicates across kits; "
              "recommend the workbook Sheet 2 as the authoritative directory (SQL lookup) — §7c-6/§12.")
    return 0
