#!/usr/bin/env python3
"""
Pipeline D — Embedding Stage (CPU, standalone)

Reads chunks from chunks/{doc_id}/chunks.jsonl, embeds them via
sentence-transformers + Qwen/Qwen3-Embedding-0.6B on CPU, writes embeddings.npy to disk.

Idempotent: skips docs that already have embeddings/{doc_id}/embeddings.npy
Resumable: checkpoint file tracks progress and errors
Loggable: JSON Lines to logs/embed_docs_cpu_{date}.log

Usage:
    embed_docs_cpu.py                    # all docs
    embed_docs_cpu.py --doc-id <id>      # single doc
    embed_docs_cpu.py --dry-run          # show what would be done
    embed_docs_cpu.py --force            # re-embed even if already done
"""

import argparse
import fcntl
import json
import os
import signal
import sys
import time
from datetime import datetime, date, timezone
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer


# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR         = Path(os.getenv("BASE_DIR",         "$DATA_DIR"))
CHUNKS_DIR       = Path(os.getenv("CHUNKS_DIR",       "$DATA_DIR/chunks"))
EMBEDDINGS_DIR   = Path(os.getenv("EMBEDDINGS_DIR",   "$DATA_DIR/embeddings"))
LOG_DIR          = Path(os.getenv("LOG_DIR",          "$DATA_DIR/logs"))
LOCK_FILE        = Path(os.getenv("LOCK_FILE",        "$DATA_DIR/.lock.embed_docs_cpu"))
CHECKPOINT_FILE  = Path(os.getenv("CHECKPOINT_FILE",  "$DATA_DIR/.checkpoint.embed_docs_cpu.json"))

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")

# Batch size for inference — keeps memory bounded on CPU
BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "8"))


# ── Checkpoint ────────────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    if not CHECKPOINT_FILE.exists():
        return {
            "script": "embed_docs_cpu",
            "version": 1,
            "started_at": None,
            "last_updated": None,
            "completed": [],
            "in_progress": None,
            "errors": [],
        }
    return json.loads(CHECKPOINT_FILE.read_text())


def save_checkpoint(cp: dict):
    cp["last_updated"] = datetime.now(timezone.utc).isoformat()
    CHECKPOINT_FILE.write_text(json.dumps(cp, indent=2))


def init_checkpoint(cp: dict):
    if cp["started_at"] is None:
        cp["started_at"] = datetime.now(timezone.utc).isoformat()
        save_checkpoint(cp)


# ── Logging ───────────────────────────────────────────────────────────────────

def log_event(level: str, doc_id: str | None, msg: str, extra: dict | None = None):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"embed_docs_cpu_{date.today().strftime('%Y%m%d')}.log"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "doc_id": doc_id,
        "msg": msg,
    }
    if extra:
        entry.update(extra)
    line = json.dumps(entry)
    log_path.open("a").write(line + "\n")
    print(f"  [{level}] {msg}" + (f" doc_id={doc_id}" if doc_id else ""))


# ── Embedding model (lazy singleton) ─────────────────────────────────────────

_model = None


def get_model() -> SentenceTransformer:
    """Load and cache the sentence-transformer model (lazy singleton)."""
    global _model
    if _model is None:
        print(f"    Loading {EMBEDDING_MODEL} on CPU ...", flush=True)
        _model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
        print(f"    Model loaded. Prompt names: {list(_model.prompts.keys())}", flush=True)
    return _model


def embed_texts(texts: list[str], batch_size: int = BATCH_SIZE) -> np.ndarray:
    """Embed a list of texts in batches using document-mode (no query prompt).

    sentence-transformers applies the model's configured pooling (lasttoken for
    Qwen3-Embedding-0.6B) and returns already-normalized vectors.

    Returns: (N, 1024) float32 array.
    """
    model = get_model()

    # Use "document" prompt — corpus chunks are documents, not queries.
    # model.encode with prompt=None uses no prefix (correct for corpus chunks).
    # The model has prompts={'query': ..., 'document': ''} per config.
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        prompt_name="document",
        prompt=None,        # None = use prompt_name's default ('' for document)
        normalize_embeddings=True,  # L2 normalize for cosine similarity
        show_progress_bar=False,
    )
    return embeddings.astype(np.float32)


# ── Core embedding ────────────────────────────────────────────────────────────

def embed_document(doc_id: str) -> dict:
    """Embed all chunks for one document via sentence-transformers on CPU."""
    chunks_path = CHUNKS_DIR / doc_id / "chunks.jsonl"
    metadata_path = CHUNKS_DIR / doc_id / "metadata.json"

    # Load chunks
    chunks = []
    with open(chunks_path) as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    if not chunks:
        raise ValueError(f"No chunks found for {doc_id}")

    texts = [c["text"] for c in chunks]

    # Load metadata for source info
    meta = {}
    if metadata_path.exists():
        meta = json.loads(metadata_path.read_text())

    # Embed
    print(f"    Embedding {len(texts)} chunks in batches of {BATCH_SIZE}...", flush=True)
    embeddings = embed_texts(texts)
    embedding_dim = embeddings.shape[1]

    # Write
    out_dir = EMBEDDINGS_DIR / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(str(out_dir / "embeddings.npy"), embeddings.astype(np.float32))

    return {"chunk_count": len(chunks), "embedding_dim": embedding_dim, "source": meta.get("source", "unknown")}


# ── Shutdown ──────────────────────────────────────────────────────────────────

_shutting_down = False


def _handle_signal(signum, frame):
    global _shutting_down
    _shutting_down = True
    print(f"\nReceived signal {signum} — finishing current doc, then stopping.")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(doc_id_filter: str | None = None, dry_run: bool = False, force: bool = False):
    lock_fh = open(str(LOCK_FILE), "w")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print("embed_docs_cpu is already running — exiting.")
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle_signal)

    cp = load_checkpoint()
    init_checkpoint(cp)
    start_time = time.time()

    # Collect candidates: dirs in CHUNKS_DIR that have chunks.jsonl
    candidates = []
    if CHUNKS_DIR.exists():
        for doc_dir in CHUNKS_DIR.iterdir():
            if not doc_dir.is_dir():
                continue
            doc_id = doc_dir.name
            chunks_path = doc_dir / "chunks.jsonl"
            if not chunks_path.exists():
                continue
            # Skip if already embedded (unless force)
            emb_path = EMBEDDINGS_DIR / doc_id / "embeddings.npy"
            if emb_path.exists() and not force:
                continue
            # Filter by doc_id if provided
            if doc_id_filter and doc_id != doc_id_filter:
                continue
            candidates.append(doc_id)

    # Remove in_progress from completed (it was interrupted)
    if cp["in_progress"] and cp["in_progress"] in cp["completed"]:
        cp["completed"].remove(cp["in_progress"])
    cp["in_progress"] = None
    save_checkpoint(cp)

    # Filter out already-completed
    candidates = [d for d in candidates if d not in cp["completed"]]

    print(f"embed_docs_cpu — CPU Embedding Stage (sentence-transformers + {EMBEDDING_MODEL})")
    print(f"  Model:       {EMBEDDING_MODEL}")
    print(f"  Chunks dir:  {CHUNKS_DIR}")
    print(f"  Embeddings:  {EMBEDDINGS_DIR}")
    print(f"  Batch size:  {BATCH_SIZE}")
    print(f"  Candidates:  {len(candidates)}")
    print(f"  Dry run:     {dry_run}")
    print(f"  Force:       {force}")

    if dry_run:
        for doc_id in candidates[:10]:
            chunks_path = CHUNKS_DIR / doc_id / "chunks.jsonl"
            chunk_count = sum(1 for _ in open(chunks_path)) if chunks_path.exists() else 0
            print(f"  Would embed: {doc_id} ({chunk_count} chunks)")
        if len(candidates) > 10:
            print(f"  ... and {len(candidates) - 10} more")
        lock_fh.close()
        LOCK_FILE.unlink(missing_ok=True)
        return

    results = {"ok": 0, "skipped": 0, "errors": 0}

    for doc_id in candidates:
        if _shutting_down:
            print("Shutdown requested — stopping after current doc.")
            break

        print(f"  Processing: {doc_id}...", end=" ", flush=True)
        cp["in_progress"] = doc_id
        save_checkpoint(cp)

        try:
            result = embed_document(doc_id)
            cp["completed"].append(doc_id)
            cp["in_progress"] = None
            save_checkpoint(cp)
            log_event("INFO", doc_id, "embedded", {
                "chunks": result["chunk_count"],
                "dim": result["embedding_dim"],
                "source": result["source"],
            })
            print(f"OK — {result['chunk_count']} chunks, dim={result['embedding_dim']}, source={result['source']}")
            results["ok"] += 1
        except Exception as e:
            cp["errors"].append({"doc_id": doc_id, "error": str(e), "ts": datetime.now(timezone.utc).isoformat()})
            cp["in_progress"] = None
            save_checkpoint(cp)
            log_event("ERROR", doc_id, "failed", {"error": str(e)})
            print(f"ERROR: {e}")
            results["errors"] += 1

    elapsed = time.time() - start_time
    print("\nembed_docs_cpu complete:")
    print(f"  Processed: {results['ok']}")
    print(f"  Errors:    {results['errors']}")
    print(f"  Elapsed:   {elapsed:.1f}s")

    log_event("INFO", None, "run_complete", {
        "results": results,
        "elapsed_s": round(elapsed, 1),
    })

    lock_fh.close()
    LOCK_FILE.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="embed_docs_cpu — Embed document chunks on CPU")
    parser.add_argument("--doc-id",  default=None, help="Process single doc")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--force",   action="store_true", help="Re-embed even if already done")
    args = parser.parse_args()
    run(doc_id_filter=args.doc_id, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
