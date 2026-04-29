#!/usr/bin/env python3
"""
update_registry.py — Update registry fields from a Pipeline E Merkle tree JSON.

Usage:
    python3 update_registry.py <doc_id> [--tree-dir $DATA_DIR/merkle_trees]

Reads <doc_id>_tree.json, looks up the doc in the registry, and updates:
    has_merkle_tree    = true
    tree_root           = merkle_root from tree JSON
    tree_depth          = tree_config.depth
    chunk_count         = chunk_count
    padded_leaf_count    = padded_leaf_count
    doc_id_leaf_index   = doc_id_leaf_index (always 0)
    pipeline_e_status   = "complete"
    processed_at        = RFC3339 timestamp (from tree JSON)
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REGISTRY_PATH = Path("$DATA_DIR/registry.json")


def update_registry(doc_id: str, tree_dir: Path) -> bool:
    tree_path = tree_dir / f"{doc_id}_tree.json"
    if not tree_path.exists():
        print(f"ERROR: tree file not found: {tree_path}", file=sys.stderr)
        return False

    with open(tree_path) as f:
        tree = json.load(f)

    with open(REGISTRY_PATH) as f:
        registry = json.load(f)

    # Find doc in registry
    idx = None
    for i, doc in enumerate(registry["documents"]):
        if doc["doc_id"] == doc_id:
            idx = i
            break

    if idx is None:
        print(f"ERROR: doc_id not found in registry: {doc_id}", file=sys.stderr)
        return False

    entry = registry["documents"][idx]

    # Fields to update
    updates = {
        "has_merkle_tree": True,
        "tree_root": tree["merkle_root"],
        "tree_depth": tree["tree_config"]["depth"],
        "chunk_count": tree["chunk_count"],
        "padded_leaf_count": tree["padded_leaf_count"],
        "doc_id_leaf_index": tree["doc_id_leaf_index"],
        "pipeline_e_status": "complete",
        "processed_at": tree.get("computed_at", datetime.now(timezone.utc).isoformat()),
    }

    for key, value in updates.items():
        entry[key] = value

    # Write registry
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(registry, f, indent=2)
    tmp.rename(REGISTRY_PATH)

    print(f"OK   {doc_id} — tree_root={tree['merkle_root'][:18]}...")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: update_registry.py <doc_id> [--tree-dir <path>]")
        sys.exit(1)

    doc_id = sys.argv[1]
    tree_dir = Path("$DATA_DIR/merkle_trees")

    if "--tree-dir" in sys.argv:
        idx = sys.argv.index("--tree-dir")
        tree_dir = Path(sys.argv[idx + 1])

    success = update_registry(doc_id, tree_dir)
    sys.exit(0 if success else 1)
