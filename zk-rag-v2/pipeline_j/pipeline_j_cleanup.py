#!/usr/bin/env python3
"""
pipeline_j_cleanup.py

Remove documents from the active corpus that have no chunks on disk:
- Check each registry doc for chunks.jsonl at chunks/{doc_id}/chunks.jsonl
- Move source PDF from sourcePDF/ to failed_pdfs/{branch}/
- Remove document entry from registry.json

Run as a final cleanup pass after the embedding pipeline completes.
NOT during active pipeline runs.

Usage:
    python3 pipeline_j_cleanup.py --list-only   # show candidates
    python3 pipeline_j_cleanup.py --dry-run      # show what would be done
    python3 pipeline_j_cleanup.py                # execute cleanup
"""

import json
import shutil
import argparse
import sys
from pathlib import Path

REGISTRY = Path("./data/registry.json")
FAILED_ROOT = Path("./data/failed_pdfs")
SOURCE_ROOT = Path("./data/sourcePDF")
CHUNKS_ROOT = Path("./data/chunks")


def get_cleanup_candidates(reg):
    """
    Return docs that should be cleaned up: no chunks.jsonl on disk.

    Criteria:
    - No non-empty chunks.jsonl exists at chunks/{doc_id}/chunks.jsonl
    - Not currently being processed by an active pipeline
    """
    candidates = []
    for doc in reg["documents"]:
        doc_id = doc["doc_id"]
        chunk_file = CHUNKS_ROOT / doc_id / "chunks.jsonl"
        if chunk_file.exists() and chunk_file.stat().st_size > 0:
            continue  # has chunks, skip
        candidates.append(doc)
    return candidates


def move_pdf(branch, filename):
    """
    Move source PDF from sourcePDF/{branch}/ to failed_pdfs/{branch}/.

    Creates destination directory if needed.
    Returns path to where the file was moved, or note if source was already missing.
    """
    src = SOURCE_ROOT / branch / filename
    dst_dir = FAILED_ROOT / branch
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / filename

    if not src.exists():
        return f"(source already gone: {src})"

    shutil.move(str(src), str(dst))
    return str(dst)


def cleanup_docs(dry_run=True, list_only=False):
    """List or remove documents that have no chunks on disk."""
    if not REGISTRY.exists():
        print(f"ERROR: Registry not found at {REGISTRY}")
        sys.exit(1)

    reg = json.loads(REGISTRY.read_text())
    candidates = get_cleanup_candidates(reg)

    print(f"Cleanup candidates: {len(candidates)}")
    print()

    if list_only:
        for doc in candidates:
            print(f"  {doc['doc_id']} | status={doc.get('status')} | "
                  f"branch={doc.get('branch')} | filename={doc.get('filename')} | "
                  f"title={doc.get('title', '')[:60]}")
        return

    print(f"{'DRY RUN' if dry_run else 'EXECUTING'} — {len(candidates)} docs to remove")
    print()

    removed = []
    for doc in candidates:
        doc_id = doc["doc_id"]
        branch = doc.get("branch", "")
        filename = doc.get("filename", "")

        if dry_run:
            moved_note = f"would move to failed_pdfs/{branch}/{filename}"
        else:
            moved_note = move_pdf(branch, filename)

        print(f"  {'REMOVE' if not dry_run else 'WOULD REMOVE'}:")
        print(f"    doc_id:   {doc_id}")
        print(f"    status:   {doc.get('status')}")
        print(f"    branch:   {branch}")
        print(f"    filename: {filename}")
        print(f"    title:    {doc.get('title', '')[:60]}")
        print(f"    moved:    {moved_note}")
        print()

        removed.append(doc_id)

    print(f"{'Would remove' if dry_run else 'Removed'} {len(removed)} docs")

    if not dry_run:
        reg["documents"] = [d for d in reg["documents"] if d["doc_id"] not in removed]
        REGISTRY.write_text(json.dumps(reg, indent=2))
        print(f"Registry written — now has {len(reg['documents'])} docs")
    else:
        print("Dry run — no changes made.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pipeline J: remove docs with no chunks from the corpus"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done, but don't do it"
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="List candidates only, don't do anything"
    )
    args = parser.parse_args()

    if args.list_only and args.dry_run:
        print("ERROR: --list-only and --dry-run are mutually exclusive")
        sys.exit(1)

    cleanup_docs(dry_run=args.dry_run, list_only=args.list_only)
