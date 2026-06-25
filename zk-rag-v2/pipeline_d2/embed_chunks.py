#!/usr/bin/env python3
"""
Pipeline D2: Embed chunks via NomicEmbedText-v1.5 using FastEmbed.

Design (per PRD REBUILD-PIPELINES.md, Pipeline D):
  - Model: nomic-ai/nomic-embed-text-v1.5 via FastEmbed
  - Prefix: "passage: " prepended to each chunk text (required by Nomic)
  - Output: embeddings.npy (float32) + meta.json (count, dim)
  - Dimensionality: 768

Usage:
    python embed_chunks.py --doc-id <doc_id>
    python embed_chunks.py --all
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding


# ── Configuration ─────────────────────────────────────────────────────────────

CHUNKS_BASE = Path("./data/chunks")
EMBEDDINGS_BASE = Path("./data/embeddings")
MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
PASSAGE_PREFIX = "passage: "
BATCH_SIZE = 256  # embed this many texts at a time


# ── Model cache (singletons — avoid re-downloading on every call) ───────────────

_model_cache: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    """Load and cache the embedding model globally."""
    global _model_cache
    if _model_cache is None:
        _model_cache = TextEmbedding(model_name=MODEL_NAME)
    return _model_cache


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_chunks(chunks_path: Path, out_dir: Path) -> dict:
    """
    Embed chunk texts via NomicEmbedText-v1.5.

    Returns dict with chunk_count, dim, and paths written.
    """
    # Read chunks
    chunks = []
    with open(chunks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    if not chunks:
        return {"chunk_count": 0, "dim": 0, "embeddings_path": None, "meta_path": None}

    # Extract texts and prepend passage prefix (required by Nomic)
    texts = [f"{PASSAGE_PREFIX}{c['text']}" for c in chunks]

    # Load model (cached globally — no re-download on repeated calls)
    model = _get_model()

    # Batch embed
    embedding_list: list[np.ndarray] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        batch_embeddings = list(model.embed(batch))
        embedding_list.extend(batch_embeddings)

    embeddings = np.array(embedding_list, dtype=np.float32)  # (num_chunks, 768)
    chunk_count, dim = embeddings.shape

    # Write outputs
    out_dir.mkdir(parents=True, exist_ok=True)
    embeddings_path = out_dir / "embeddings.npy"
    np.save(str(embeddings_path), embeddings)

    meta = {"chunk_count": chunk_count, "dim": dim}
    meta_path = out_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    return {
        "chunk_count": chunk_count,
        "dim": dim,
        "embeddings_path": str(embeddings_path),
        "meta_path": str(meta_path),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Pipeline D2: embed chunks with NomicEmbedText-v1.5")
    parser.add_argument("--doc-id", help="Embed chunks for a single doc")
    parser.add_argument("--all", action="store_true", help="Embed all docs with chunks/")
    args = parser.parse_args()

    if not args.doc_id and not args.all:
        parser.error("--doc-id or --all required")

    if args.all:
        doc_ids = [d.name for d in CHUNKS_BASE.iterdir() if d.is_dir()]
        ok, skipped, errors = 0, 0, 0
        for doc_id in sorted(doc_ids):
            chunks_path = CHUNKS_BASE / doc_id / "chunks.jsonl"
            if not chunks_path.exists():
                print(f"  {doc_id[:8]}: SKIP — no chunks.jsonl")
                skipped += 1
                continue
            out_dir = EMBEDDINGS_BASE / doc_id
            try:
                result = embed_chunks(chunks_path, out_dir)
                print(f"  {doc_id[:8]}: {result['chunk_count']} chunks × {result['dim']}d")
                ok += 1
            except Exception as e:
                print(f"  {doc_id[:8]}: ERROR — {e}")
                errors += 1
        print(f"\nDone: {ok} ok, {skipped} skipped, {errors} errors")
        return

    # Single doc
    doc_id = args.doc_id
    chunks_path = CHUNKS_BASE / doc_id / "chunks.jsonl"
    if not chunks_path.exists():
        print(f"Error: no chunks.jsonl for {doc_id}")
        return
    out_dir = EMBEDDINGS_BASE / doc_id
    result = embed_chunks(chunks_path, out_dir)
    print(f"Done. {result['chunk_count']} chunks × {result['dim']}d → {result['embeddings_path']}")


if __name__ == "__main__":
    main()
