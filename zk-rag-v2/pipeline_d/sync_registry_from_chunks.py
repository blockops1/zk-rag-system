#!/usr/bin/env python3
"""
sync_registry_from_chunks.py -- Sync the central registry from per-doc chunk data.

Reads every ./data/chunks/{doc_id}/chunks.jsonl and updates
the corresponding entry in ./data/registry.json.

D1 (chunk_document.py) outputs chunks.jsonl + chunk_ids.json but no metadata.json.
This script counts lines in chunks.jsonl to get chunk_count.

Fields synced: chunk_count (from chunks.jsonl line count)

Run once after Pipeline D1 completes. Safe to re-run -- overwrites with latest values.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

CHUNKS_DIR = Path("./data/chunks")
REGISTRY_PATH = Path("./data/registry.json")
BACKUP_SUFFIX = f".backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


def load_registry() -> dict:
    with open(REGISTRY_PATH, "r") as f:
        return json.load(f)


def save_registry(data: dict) -> None:
    # Backup first
    backup_path = REGISTRY_PATH.with_suffix(".json" + BACKUP_SUFFIX)
    with open(backup_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Backup written to {backup_path.name}")

    # Atomic write
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.rename(REGISTRY_PATH)


def main():
    print(f"Loading registry from {REGISTRY_PATH}...")
    registry = load_registry()

    docs = registry["documents"]
    total_docs = len(docs)
    print(f"Registry has {total_docs} documents")

    # Index registry by doc_id for fast lookup
    doc_map = {doc["doc_id"]: doc for doc in docs}

    # Scan all chunk directories
    chunk_dirs = [d for d in os.listdir(CHUNKS_DIR)
                  if os.path.isdir(CHUNKS_DIR / d)]
    print(f"Found {len(chunk_dirs)} chunk directories")

    updated = 0
    missing = []
    errors = []

    for doc_id in sorted(chunk_dirs):
        chunks_path = CHUNKS_DIR / doc_id / "chunks.jsonl"
        if not chunks_path.exists():
            missing.append(doc_id)
            continue

        try:
            with open(chunks_path, "r") as f:
                chunk_count = sum(1 for _ in f)
        except IOError as e:
            errors.append((doc_id, str(e)))
            continue

        # Find matching registry entry
        reg_entry = doc_map.get(doc_id)
        if not reg_entry:
            errors.append((doc_id, "doc_id not found in registry"))
            continue

        # Sync chunk_count
        changed = False
        if reg_entry.get("chunk_count") != chunk_count:
            reg_entry["chunk_count"] = chunk_count
            changed = True

        if changed:
            updated += 1

    # Update top-level metadata
    registry["updated_at"] = datetime.now(timezone.utc).isoformat()
    registry["total_documents"] = len(docs)

    # Remove any pipeline_e_status that shows "completed" but was never synced
    # Actually just update total_documents and updated_at above

    print("\n=== RESULTS ===")
    print(f"Chunk dirs scanned:  {len(chunk_dirs)}")
    print(f"Updated in registry: {updated}")
    print(f"Missing metadata:    {len(missing)}")
    print(f"Errors:             {len(errors)}")

    if missing:
        print(f"\nOrphan chunk dirs (no registry entry): {missing[:10]}"
              f"{'...' if len(missing) > 10 else ''}")

    if errors:
        print("\nErrors:")
        for doc_id, err in errors[:10]:
            print(f"  {doc_id}: {err}")

    # Save
    save_registry(registry)
    print(f"\nRegistry saved. updated_at = {registry['updated_at']}")
    print(f"total_documents = {registry['total_documents']}")


if __name__ == "__main__":
    main()
