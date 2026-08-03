"""
Stage 4 · Chunk (brief §5).

Splits each curated document on its `## Section` headings, one logical section per
unit, then greedily packs units into chunks of ~300–800 tokens (ceiling ~1,000),
merging tiny sections upward. A section that contains a Markdown table is NEVER
split and never merged past the ceiling — it becomes its own chunk whole.

Every chunk's embedded text is PREFIXED with a breadcrumb that always carries the
program code (`MIHP-I › Financing rate › …`), so a chunk is unambiguous in
isolation — the safety net against MHP-i/MIHP-i confusion (§6a, §7c-2).

Output: data/04_chunks/<corpus>/<doc_id>.jsonl — one human-readable chunk per line;
Stage 5 adds chunk_id + the full metadata schema.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from config.settings import get_settings

_ROOT = Path(__file__).resolve().parent.parent
_COMMENT = re.compile(r"<!--\s*section=(\S+)\s+pages=(\[[^\]]*\])\s+content_type=(\S+)\s*-->")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)   # ~4 chars/token; a heuristic for the 300–800 target


def _has_table(text: str) -> bool:
    return len(_TABLE_ROW.findall(text)) >= 2   # header + at least one data/separator row


def _parse_curated(md: str) -> tuple[dict, list[dict]]:
    """(front_matter, [section unit …]). Each unit = title/section/body/pages/content_type."""
    fm: dict = {}
    body = md
    if md.startswith("---"):
        _, fm_text, body = md.split("---", 2)
        fm = yaml.safe_load(fm_text) or {}

    units: list[dict] = []
    marks = list(re.finditer(r"^##\s+(.*)$", body, re.M))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        title = m.group(1).strip()
        seg = body[m.end():end].strip()
        cm = _COMMENT.search(seg)
        section, pages, content_type = "other", [], "standard"
        if cm:
            section, pages, content_type = cm.group(1), json.loads(cm.group(2)), cm.group(3)
            seg = (seg[: cm.start()] + seg[cm.end():]).strip()
        text = f"{title}\n{seg}".strip()
        units.append({"section": section, "title": title, "text": text,
                      "pages": pages, "content_type": content_type,
                      "tokens": _approx_tokens(text), "has_table": _has_table(seg)})
    return fm, units


def _pack(units: list[dict], max_t: int, merge_t: int) -> list[list[dict]]:
    """Greedy pack into chunks up to max_t; table units stand alone; a tiny trailing
    chunk merges into the previous."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_t = 0
    for u in units:
        if u["has_table"]:
            if cur:
                chunks.append(cur); cur, cur_t = [], 0
            chunks.append([u])
            continue
        if cur_t and cur_t + u["tokens"] > max_t:
            chunks.append(cur); cur, cur_t = [], 0
        cur.append(u); cur_t += u["tokens"]
    if cur:
        chunks.append(cur)
    if len(chunks) >= 2 and not chunks[-1][0]["has_table"] \
            and sum(u["tokens"] for u in chunks[-1]) < merge_t and not chunks[-2][0]["has_table"]:
        chunks[-2].extend(chunks.pop())
    return chunks


def _breadcrumb(label: str, titles: list[str]) -> str:
    return f"{label} › {'; '.join(titles)} › "


def run(args) -> int:
    settings = get_settings()
    max_t, ceil_t, merge_t = settings.chunk_target_max_tokens, settings.chunk_ceiling_tokens, settings.chunk_merge_below_tokens
    curated_root = _ROOT / settings.data_dir / "03_curated"
    if not curated_root.exists():
        print("[stage4_chunk] no curated docs — run stage3 first.")
        return 1

    corpora = [args.corpus] if getattr(args, "corpus", None) else [p.name for p in curated_root.iterdir() if p.is_dir()]
    total = 0
    for corpus in corpora:
        cdir = curated_root / corpus
        if not cdir.exists():
            continue
        for md_path in sorted(cdir.glob("*.md")):
            fm, units = _parse_curated(md_path.read_text())
            if args.doc and fm.get("doc_id") != args.doc:
                continue
            label = fm.get("program_code") or fm.get("doc_id")
            out_lines = []
            for chunk_units in _pack(units, max_t, merge_t):
                titles = [u["title"] for u in chunk_units]
                pages = sorted({p for u in chunk_units for p in u["pages"]})
                content_type = "indicative" if any(u["content_type"] == "indicative" for u in chunk_units) else "standard"
                crumb = _breadcrumb(label, titles)
                content = crumb + "\n" + "\n\n".join(u["text"] for u in chunk_units)
                rec = {
                    "doc_id": fm.get("doc_id"), "program_code": fm.get("program_code"),
                    "corpus": corpus, "section": chunk_units[0]["section"],
                    "breadcrumb": crumb.strip(), "content": content,
                    "pages": pages, "content_type": content_type,
                    "access_tier": fm.get("access_tier", "customer"),
                    "version": str(fm.get("version")),
                    "tokens": _approx_tokens(content),
                    "has_table": any(u["has_table"] for u in chunk_units),
                }
                over = " OVER-CEILING(table kept whole)" if rec["tokens"] > ceil_t else ""
                out_lines.append(json.dumps(rec, ensure_ascii=False))
                if over:
                    print(f"  {fm.get('doc_id')} :: {rec['section']} — {rec['tokens']} tok{over}")
            out_dir = _ROOT / settings.data_dir / "04_chunks" / corpus
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{fm.get('doc_id')}.jsonl"
            out.write_text("\n".join(out_lines) + "\n")
            total += len(out_lines)
            print(f"[stage4_chunk] {corpus}/{fm.get('doc_id')}: {len(out_lines)} chunks -> {out.name}")
    print(f"[stage4_chunk] total {total} chunks.")
    return 0
