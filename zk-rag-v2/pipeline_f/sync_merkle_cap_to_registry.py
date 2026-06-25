#!/usr/bin/env python3
"""
sync_merkle_cap_to_registry.py

DEPRECATED — Pipeline E now emits a single merkle_root string, not a
16-element merkle_cap array. This script is kept for reference only.

Historical function:
    Read Pipeline E tree files, extract merkle_cap (cap[0] = tree root),
    and write it as `merkle_cap` into the registry.

Current pipeline (Pipeline F / emit_all.py):
    Reads merkle_root directly from tree JSON — no cap sync step needed.
    Registry no longer uses `merkle_cap` field.


Reads every {doc_id}_tree.json from Pipeline E, extracts the 16-element
MerkleCap array (tree.merkle_root), and writes it as `merkle_cap` into the
central registry.json for each document.

This is a ONE-TIME migration script — it is idempotent and can be re-run
safely: docs that already have `merkle_cap` are skipped.

After this script has run, Pipeline F (emit_all.py) will read merkle_cap
from the registry instead of directly from tree files, and will update
`emitted_testnet` / `emitted_mainnet` on the registry after each tx.

Usage:
    python3 sync_merkle_cap_to_registry.py          # dry run (default)
    python3 sync_merkle_cap_to_registry.py --write  # actually write to registry
    python3 sync_merkle_cap_to_registry.py --limit 20 --write  # limit for testing
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

MERKLE_TREES_DIR = Path("./data/merkleTrees")
REGISTRY_PATH    = Path("./data/registry.json")
LOG_DIR          = Path("../data/logs")

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_registry() -> dict:
    with open(REGISTRY_PATH, "r") as f:
        return json.load(f)


def save_registry(data: dict) -> None:
    """Atomically write registry."""
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.rename(REGISTRY_PATH)


def get_tree_files() -> list[Path]:
    return sorted(MERKLE_TREES_DIR.glob("*_tree.json"))


def extract_doc_id(filepath: Path) -> str:
    """Strip '_tree' suffix to get doc_id from filename like 'abcd..._tree.json'."""
    stem = filepath.stem
    if stem.endswith("_tree"):
        return stem[:-5]
    return stem


def build_doc_id_index(registry: dict) -> dict[str, int]:
    """Map doc_id → index in registry['documents'] list."""
    return {doc["doc_id"]: idx for idx, doc in enumerate(registry["documents"])}


# ── Core logic ────────────────────────────────────────────────────────────────

def sync_cap(tree_file: Path, registry: dict, index: dict, write: bool, dry_run: bool):
    """
    Process a single tree file.

    Returns (status_label, doc_id, message)
    Labels: SKIP (already has merkle_cap), SYNCED, FAIL
    """
    doc_id = extract_doc_id(tree_file)

    # Locate doc in registry
    if doc_id not in index:
        return "FAIL", doc_id, "not_found_in_registry"

    doc_idx = index[doc_id]
    doc_entry = registry["documents"][doc_idx]

    # Idempotency: skip if merkle_cap already present
    if "merkle_cap" in doc_entry:
        return "SKIP", doc_id, "merkle_cap_already_present"

    # Load tree JSON
    try:
        with open(tree_file, "r") as f:
            tree_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return "FAIL", doc_id, f"tree_json_error={e}"

    merkle_root = tree_data.get("merkle_root", [])

    # Validate: merkle_root must be a non-empty list
    if not merkle_root or not isinstance(merkle_root, list):
        return "FAIL", doc_id, f"invalid_merkle_root type={type(merkle_root)}"

    # Pad to exactly 16 elements (small trees may have <16)
    while len(merkle_root) < 16:
        merkle_root.append("0x" + "00" * 32)
    merkle_cap = merkle_root[:16]

    if write:
        doc_entry["merkle_cap"] = merkle_cap
        # Also update tree_root to be the first cap element (for backwards compat)
        doc_entry["tree_root"] = merkle_cap[0]
        doc_entry["merkle_cap_synced_at"] = datetime.now(timezone.utc).isoformat()

    cap0 = merkle_cap[0]
    return "SYNCED", doc_id, f"cap0={cap0[:16]}... ({len(merkle_root)} tree elements → padded to 16)"


def run_sync(write: bool, limit: int | None = None):
    """
    Run the full sync.
    
    Returns (synced_count, skipped_count, failed_count)
    """
    if not write:
        print("=== DRY RUN — no changes will be written ===\n")

    registry = load_registry()
    index    = build_doc_id_index(registry)

    tree_files = get_tree_files()
    if limit:
        tree_files = tree_files[:limit]

    total = len(tree_files)
    synced  = 0
    skipped = 0
    failed  = 0

    for idx, tree_file in enumerate(tree_files):
        label, doc_id, msg = sync_cap(tree_file, registry, index, write=write, dry_run=not write)

        print(f"[{label:6}] {doc_id[:24]}...  {msg}")

        if label == "SYNCED":
            synced += 1
        elif label == "SKIP":
            skipped += 1
        elif label == "FAIL":
            failed += 1

        if (idx + 1) % 50 == 0:
            print(f"\n  Progress: {idx + 1}/{total}  synced={synced}  skipped={skipped}  failed={failed}")

    if write:
        save_registry(registry)
        print(f"\n  Registry written: {REGISTRY_PATH}")

    return synced, skipped, failed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sync MerkleCap (16-element array) from tree JSONs into registry.json"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually write changes to registry. Omit for dry-run mode.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Limit to first N tree files (for testing).",
    )
    args = parser.parse_args()

    print(f"Merkle trees dir : {MERKLE_TREES_DIR}")
    print(f"Registry         : {REGISTRY_PATH}")
    print(f"Write mode       : {'ENABLED' if args.write else 'DRY RUN (--write to commit)'}\n")

    if not args.write:
        print(">>> DRY RUN: No files will be modified. Use --write to commit. <<<\n")

    synced, skipped, failed = run_sync(write=args.write, limit=args.limit)

    print("\n=== SUMMARY ===")
    print(f"Total processed : {synced + skipped + failed}")
    print(f"  Synced       : {synced}")
    print(f"  Skipped      : {skipped}  (already had merkle_cap)")
    print(f"  Failed       : {failed}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
