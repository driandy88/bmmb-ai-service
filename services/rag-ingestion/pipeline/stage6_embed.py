"""
Stage 6 · Embed (brief §5).

Embeds each enriched chunk with gemini-embedding-001, output_dimensionality=1536,
task type RETRIEVAL_DOCUMENT (queries later use RETRIEVAL_QUERY — never the same
type for both). Vectors are L2-normalised (recommended for a truncated Matryoshka
dimension and makes cosine == dot on the HNSW index).

Batched with a checkpoint: already-embedded chunk_ids are skipped unless --force,
so a failed run resumes rather than restarts. Records embedding_model + dimensions
in a run manifest — changing either is a full re-embed / versioned migration (§7b).

Reads data/05_enriched/<corpus>/<doc_id>.jsonl → writes data/06_embedded/<corpus>/<doc_id>.jsonl
(the enriched record + an `embedding` array).
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from config.settings import Settings, get_settings

_ROOT = Path(__file__).resolve().parent.parent
_TASK_TYPE = "RETRIEVAL_DOCUMENT"


def _client(s: Settings):
    from google import genai
    return genai.Client(vertexai=True, project=s.gcp_project_id, location=s.vertex_location)


def _normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _embed_batch(client, model: str, texts: list[str], dims: int, *, retries: int = 4) -> list[list[float]]:
    from google.genai import types
    cfg = types.EmbedContentConfig(task_type=_TASK_TYPE, output_dimensionality=dims)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.models.embed_content(model=model, contents=texts, config=cfg)
            return [_normalise(list(e.values)) for e in resp.embeddings]
        except Exception as e:  # transient Vertex error — back off and retry
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


def _existing(out_path: Path) -> dict[str, dict]:
    if not out_path.exists():
        return {}
    return {r["chunk_id"]: r for r in (json.loads(l) for l in out_path.read_text().splitlines() if l.strip())}


def run(args) -> int:
    settings = get_settings()
    if not settings.gcp_project_id:
        print("[stage6_embed] GCP_PROJECT_ID not set — cannot call Vertex.")
        return 1
    model, dims, batch = settings.embedding_model_id, settings.embedding_dimensions, settings.embed_batch_size
    force = getattr(args, "force", False)

    enriched_root = _ROOT / settings.data_dir / "05_enriched"
    if not enriched_root.exists():
        print("[stage6_embed] no enriched chunks — run stage5 first.")
        return 1

    client = _client(settings)
    corpora = [args.corpus] if getattr(args, "corpus", None) else [p.name for p in enriched_root.iterdir() if p.is_dir()]
    total_new = 0
    for corpus in corpora:
        cdir = enriched_root / corpus
        if not cdir.exists():
            continue
        for jf in sorted(cdir.glob("*.jsonl")):
            doc_id = jf.stem
            if args.doc and doc_id != args.doc:
                continue
            records = [json.loads(l) for l in jf.read_text().splitlines() if l.strip()]
            out_dir = _ROOT / settings.data_dir / "06_embedded" / corpus
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{doc_id}.jsonl"
            done = {} if force else _existing(out)

            todo = [r for r in records if r["chunk_id"] not in done]
            for i in range(0, len(todo), batch):
                part = todo[i:i + batch]
                vecs = _embed_batch(client, model, [r["content"] for r in part], dims)
                for r, v in zip(part, vecs):
                    done[r["chunk_id"]] = {**r, "embedding": v}
                # checkpoint after each batch so a later failure resumes here
                ordered = [done[r["chunk_id"]] for r in records if r["chunk_id"] in done]
                out.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in ordered) + "\n")

            total_new += len(todo)
            print(f"[stage6_embed] {corpus}/{doc_id}: embedded {len(todo)} new, "
                  f"{len(records) - len(todo)} cached -> {out.name}")

    manifest = {
        "embedding_model": model, "dimensions": dims, "task_type": _TASK_TYPE,
        "normalised": True, "new_this_run": total_new,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (_ROOT / settings.data_dir / "06_embedded" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"[stage6_embed] {total_new} new vectors · {model} @ {dims}d · task={_TASK_TYPE}")
    return 0
