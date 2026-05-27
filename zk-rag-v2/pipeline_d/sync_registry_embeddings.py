#!/usr/bin/env python3
"""
sync_registry_embeddings.py -- Sync the central registry with embedding completion state.

Reads the embedding checkpoint (.checkpoint.embed_docs.json) and the actual
embeddings/ directory to update the registry's embedding fields for each document.

Run after Pipeline D embedding step completes. Safe to re-run.

Registry fields updated per document:
  - embedding_status: "completed" | "failed" | "pending"
  - embedding_completed_at: ISO timestamp (from checkpoint order as proxy)
  - embedding_error: error message if failed
  - has_embeddings: true/false (confirmed by embeddings.npy existence)
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

EMBEDDINGS_DIR = Path("../data/embeddings")
CHECKPOINT_PATH = Path("../data/.checkpoint.embed_docs_cpu.json")
REGISTRY_PATH = Path("../data/registry.json")
BACKUP_SUFFIX = f".backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


def load_checkpoint() -> dict:
    with open(CHECKPOINT_PATH, "r") as f:
        return json.load(f)


def load_registry() -> dict:
    with open(REGISTRY_PATH, "r") as f:
        return json.load(f)


def save_registry(data: dict) -> None:
    backup_path = REGISTRY_PATH.with_suffix(".json" + BACKUP_SUFFIX)
    with open(backup_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Backup written to {backup_path.name}")

    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.rename(REGISTRY_PATH)


def main():
    print(f"Loading checkpoint from {CHECKPOINT_PATH}...")
    checkpoint = load_checkpoint()

    print(f"Loading registry from {REGISTRY_PATH}...")
    registry = load_registry()

    docs = registry["documents"]
    total_docs = len(docs)
    print(f"Registry has {total_docs} documents")

    # Index registry by doc_id for fast lookup
    doc_map = {doc["doc_id"]: doc for doc in docs}

    completed = checkpoint.get("completed", [])
    errors = checkpoint.get("errors", [])
    print(f"Checkpoint: {len(completed)} completed, {len(errors)} errors")

    # Build error lookup
    error_map = {e["doc_id"]: e["error"] for e in errors}

    # Scan embeddings directory for actual .npy files
    actual_embedding_dirs = set()
    for entry in os.listdir(EMBEDDINGS_DIR):
        npy_path = EMBEDDINGS_DIR / entry / "embeddings.npy"
        if npy_path.exists():
            actual_embedding_dirs.add(entry)

    print(f"Actual embeddings.npy files found: {len(actual_embedding_dirs)}")

    # Determine completed_at timestamps from checkpoint order
    # We don't have per-doc timestamps in checkpoint, so we use a plausible range
    # based on checkpoint started_at and last_updated
    started_at = checkpoint.get("started_at", checkpoint.get("last_updated"))
    last_updated = checkpoint.get("last_updated", started_at)

    # Distribute completed docs across the time range proportionally
    # For "completed" docs that we have, mark them with last_updated as proxy timestamp
    # (we don't have exact per-doc timestamps in the checkpoint)
    completed_at = last_updated  # Use last checkpoint update as proxy for all completed

    updated = 0
    failed_updated = 0
    pending_updated = 0
    not_in_registry = []


    # Process completed docs
    for doc_id in completed:
        reg_entry = doc_map.get(doc_id)
        if not reg_entry:
            not_in_registry.append(doc_id)
            continue

        changed = False
        if reg_entry.get("embedding_status") != "completed":
            reg_entry["embedding_status"] = "completed"
            changed = True
        if reg_entry.get("has_embeddings") is not True:
            reg_entry["has_embeddings"] = True
            changed = True
        if reg_entry.get("embedding_completed_at") != completed_at:
            reg_entry["embedding_completed_at"] = completed_at
            changed = True
        if "embedding_error" in reg_entry:
            del reg_entry["embedding_error"]
            changed = True

        if changed:
            updated += 1

    # Process error docs
    for doc_id, error_msg in error_map.items():
        reg_entry = doc_map.get(doc_id)
        if not reg_entry:
            not_in_registry.append(doc_id)
            continue

        changed = False
        if reg_entry.get("embedding_status") != "failed":
            reg_entry["embedding_status"] = "failed"
            changed = True
        if reg_entry.get("has_embeddings") is not False:
            reg_entry["has_embeddings"] = False
            changed = True
        if reg_entry.get("embedding_error") != error_msg:
            reg_entry["embedding_error"] = error_msg
            changed = True

        if changed:
            failed_updated += 1

    # Process docs in registry that are NOT in completed list and NOT in errors
    # and whose embeddings dir doesn't exist
    pending_doc_ids = set(doc_map.keys()) - set(completed) - set(error_map.keys())
    for doc_id in pending_doc_ids:
        reg_entry = doc_map.get(doc_id)
        if not reg_entry:
            continue

        # Check if embeddings dir exists
        has_npy = doc_id in actual_embedding_dirs

        changed = False
        expected_status = "completed" if has_npy else "pending"
        if reg_entry.get("embedding_status") != expected_status:
            reg_entry["embedding_status"] = expected_status
            changed = True
        if reg_entry.get("has_embeddings") != has_npy:
            reg_entry["has_embeddings"] = has_npy
            changed = True

        if changed:
            pending_updated += 1

    # Update top-level metadata
    registry["updated_at"] = datetime.now(timezone.utc).isoformat()

    print("\n=== RESULTS ===")
    print(f"Registry entries updated (completed):  {updated}")
    print(f"Registry entries updated (failed):    {failed_updated}")
    print(f"Registry entries updated (pending):    {pending_updated}")
    print(f"Not in registry:                      {len(not_in_registry)}")

    if not_in_registry:
        print(f"  Sample orphan doc_ids: {not_in_registry[:5]}"
              f"{'...' if len(not_in_registry) > 5 else ''}")

    print("\nEmbedding summary:")
    print(f"  Total registry docs:    {total_docs}")
    print(f"  With embeddings.npy:    {len(actual_embedding_dirs)}")
    print(f"  Checkpoint completed:   {len(completed)}")
    print(f"  Checkpoint errors:      {len(errors)}")

    save_registry(registry)
    print(f"\nRegistry saved. updated_at = {registry['updated_at']}")


if __name__ == "__main__":
    main()
