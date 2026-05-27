#!/bin/bash
# ingest -- Deduplicate and ingest N pending PDFs into Qdrant
# Usage: ingest N
#   N = number of pending docs to process (default: all)
#
# What it does per doc:
#   1. Dedup gate -- check title+pub_year similarity vs registry, skip if duplicate
#   2. Ingest PDF -- text + images extracted, chunked, embedded
#   3. Upsert to Qdrant (no deletes -- safe)
#   4. Mark registry status=ingested
#   5. Extract images to ../data/images/{doc_id}/
#   6. Telegram notification per doc
#
# What it does NOT do: harvest, push

set -euo pipefail

ME=$(basename "$0")
RAG_DIR="rag"
VENV_PY="${RAG_DIR}/venv/bin/python3"

if [ $# -eq 0 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "Usage: $ME <count>|all"
    echo "  Deduplicate and ingest <count> pending docs into Qdrant."
    echo "  Use 'all' to process every pending doc."
    echo ""
    echo "  What happens per doc:"
    echo "    1. Dedup gate -- skip if title+pub_year matches existing doc"
    echo "    2. Ingest PDF -- text + images extracted, chunked, embedded"
    echo "    3. Upsert to Qdrant (no deletes)"
    echo "    4. Extract images to /data/rag/images/{doc_id}/"
    echo "    5. Telegram notification"
    echo ""
    echo "  Prerequisites: RAG API must be running on port 8100"
    exit 0
fi

COUNT="$1"

echo "[$ME] Starting ingest of $COUNT pending docs..."
"$VENV_PY" "${RAG_DIR}/upload/ingest_batch.py" "$COUNT"

echo ""
echo "[$ME] Done."
