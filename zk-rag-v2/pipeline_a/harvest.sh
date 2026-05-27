#!/bin/bash
# harvest — Download N PDFs from Internet Archive + armypubs
# Usage: harvest N
#   N = number of docs to attempt to download (may be fewer if not found)
#
# What it does:
#   1. Search Internet Archive + armypubs for new PDFs
#   2. SHA256 gate — skip if already in registry
#   3. Extract title from first 2 pages
#   4. Stage PDF, add to registry as status=pending
#
# What it does NOT do: ingest, dedup check, push

set -euo pipefail

ME=$(basename "$0")
RAG_DIR="rag"
VENV_PY="${RAG_DIR}/venv/bin/python3"

if [ $# -eq 0 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "Usage: $ME <count>"
    echo "  Download up to <count> new PDFs from Internet Archive and armypubs.mil."
    echo "  Skips PDFs already in registry (SHA256 check)."
    echo "  Adds new entries as status=pending — run 'ingest N' after this."
    exit 0
fi

COUNT="$1"

echo "[$ME] Starting harvest of up to $COUNT docs..."
"$VENV_PY" "${RAG_DIR}/harvester/run_harvest.py" --limit "$COUNT" --skip-ingest

echo ""
echo "[$ME] Done. Run 'ingest N' to ingest the pending docs."
