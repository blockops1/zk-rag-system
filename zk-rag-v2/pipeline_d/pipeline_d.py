#!/usr/bin/env python3
"""
Pipeline D — Chunk + Embed (OFFLINE ONLY — no Qdrant writes)

Uses LlamaIndex HierarchicalNodeParser + SemanticDoubleMergingSplitterNodeParser
for chunking, and mlx-embeddings + Qwen/Qwen3-Embedding-8B for embeddings.

Reads page JSONs from ingested-vision/{doc_id}/ (),
chunks them (via chunk_document.py) and embeds them to disk.

Qdrant upsert is handled separately by Pipeline F — not this script.

Usage:
    python pipeline_d.py                    # process all extracted docs
    python pipeline_d.py --doc-id XXX       # single doc
    python pipeline_d.py --dry-run          # show what would be done
"""

import argparse
import json
import os
import sys
import fcntl
from pathlib import Path

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).parent))

# Import chunking from chunk_document.py (PRD-MIL-01 spec)
from chunk_document import _chunk_document

# Paths
V2_REGISTRY_FILE = Path("../data/registry.json")
CHUNKS_DIR       = Path("../data/chunks")
EMBEDDINGS_DIR   = Path("../data/embeddings")
INGESTED_BASE    = Path("../data/extracted")

EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B"

LOCK_FILE = Path(os.getenv("LOCK_FILE", "../data/.lock.pipeline_d"))


def acquire_lock():
    """Acquire exclusive lock. Exit if already locked."""
    lock_fh = open(str(LOCK_FILE), "w")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print("Pipeline D is already running — exiting.")
        sys.exit(0)
    return lock_fh


def load_v2_registry() -> dict:
    """Load v2 registry from disk."""
    if not V2_REGISTRY_FILE.exists():
        return {}
    return json.load(open(V2_REGISTRY_FILE))


def pick_source_dir(doc_id: str) -> tuple[Path | None, str]:
    """Choose the best source for page JSONs.

    Returns (source_dir, source_label). Uses extracted/{doc_id}/.
    """
    ingested_dir  = INGESTED_BASE / doc_id

    if ingested_dir.exists():
        return (ingested_dir, "ingested")
    else:
        return (None, None)


def embed_chunks(chunks_path: Path, out_dir: Path, model_name: str) -> tuple[int, int]:
    """Embed chunks via mlx-embeddings + Qwen3-Embedding-8B. Returns (chunk_count, embedding_dim)."""
    import mlx_embeddings as me
    import numpy as np

    chunks = []
    with open(chunks_path) as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    if not chunks:
        return (0, 0)

    texts    = [c["text"] for c in chunks]
    chunk_ids = [c["chunk_id"] for c in chunks]

    print(f"    Loading {model_name} ...")
    model, processor = me.load(model_name, lazy=False)

    print(f"    Embedding {len(texts)} chunks ...")
    result = me.generate(model, processor, texts)
    embeddings = np.array(result.text_embeds)  # (num_chunks, dim)

    out_dir.mkdir(parents=True, exist_ok=True)
    emb_path = out_dir / "embeddings.npy"
    np.save(str(emb_path), embeddings.astype(np.float32))

    return (len(chunk_ids), embeddings.shape[1])


def process_doc(doc_id: str, dry_run: bool = False) -> dict:
    """Process one doc through Pipeline D — chunking only (D1), no embedding."""
    # Load registry entry
    registry = load_v2_registry()
    doc_entry = None
    for d in registry.get("documents", []):
        doc_id_norm = "".join(
            c.lower() if c.isalnum() or c == "-" else "-"
            for c in d.get("doc_id", "")
        ).strip("-")
        if doc_id_norm == "".join(
            c.lower() if c.isalnum() or c == "-" else "-"
            for c in doc_id
        ).strip("-"):
            doc_entry = d
            break

    if not doc_entry:
        return {"status": "not_found", "doc_id": doc_id}

        source_dir, source_label = pick_source_dir(doc_id)
    if source_dir is None:
        return {"status": "no_pages", "doc_id": doc_id}

    # If dry run, report what would happen
    if dry_run:
        page_count = len(list((source_dir / "pages").glob("*.json")))
        return {
            "status":     "dry_run",
            "doc_id":     doc_id,
            "branch":     doc_entry.get("branch", "other"),
            "source":     source_label,
            "page_count": page_count,
        }

    # Step 1: Chunk (delegated to chunk_document.py) — D1 complete
    chunk_result = _chunk_document(
        source_dir=source_dir,
        doc_id=doc_id,
        out_dir=CHUNKS_DIR,
    )


    # Load chunk_count from result
    chunk_count = chunk_result["chunk_count"]

    return {
        "status":      "ok",
        "doc_id":      doc_id,
        "branch":      doc_entry.get("branch", "other"),
        "chunk_count": chunk_count,
        "source":      source_label,
    }


def run_pipeline(doc_id_filter: str | None = None, dry_run: bool = False):
    """Run Pipeline D on all extracted docs — chunk + embed to disk only."""
    lock_fh = acquire_lock()
    print("Pipeline D — Chunk + Embed (OFFLINE ONLY)")
    print(f"  Embedding model: {EMBEDDING_MODEL}")
    print(f"  Dry run: {dry_run}")

    registry = load_v2_registry()

    # Find all docs that have page files but are not yet chunked/embedded
    candidates = []
    for doc in registry.get("documents", []):
        doc_id = doc.get("doc_id", "")
        if not (INGESTED_BASE / doc_id / "pages").exists():
            continue
        if doc_id_filter and doc_id != doc_id_filter:
            continue
        candidates.append(doc_id)

    print(f"  {len(candidates)} docs to process")

    if not candidates:
        print("Nothing to do.")
        lock_fh.close()
        LOCK_FILE.unlink(missing_ok=True)
        return

    results = {"ok": [], "skipped": [], "errors": []}
    for doc_id in sorted(candidates):
        print(f"  Processing: {doc_id}...", end=" ", flush=True)
        result = process_doc(doc_id, dry_run=dry_run)
        status = result["status"]
        if status == "ok":
            print(f"OK — {result['chunk_count']} chunks, source={result['source']}")
            results["ok"].append(result)
        elif status == "dry_run":
            print(f"DRY RUN — would process {result['page_count']} pages from {result['source']}")
        elif status == "no_pages":
            print("SKIP — no page files found")
            results["skipped"].append(result)
        elif status == "not_found":
            print("SKIP — not in registry")
            results["skipped"].append(result)
        else:
            print(f"ERROR: {status}")
            results["errors"].append(result)

    # Summary
    print("\nPipeline D complete:")
    print(f"  Processed: {len(results['ok'])}")
    print(f"  Skipped:    {len(results['skipped'])}")
    print(f"  Errors:     {len(results['errors'])}")

    lock_fh.close()
    LOCK_FILE.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline D: Chunk + Embed (OFFLINE ONLY — no Qdrant)"
    )
    parser.add_argument("--doc-id",    default=None, help="Process single doc")
    parser.add_argument("--dry-run",  action="store_true", help="Show what would be done")
    args = parser.parse_args()

    run_pipeline(doc_id_filter=args.doc_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
