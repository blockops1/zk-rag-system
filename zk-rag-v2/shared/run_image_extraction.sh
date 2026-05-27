#!/bin/bash
# run_image_extraction.sh — Run full image extraction for all ingested docs
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAG_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$RAG_ROOT/logs"
LOG_FILE="$LOG_DIR/image_extraction.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Log header
echo "========================================" >> "$LOG_FILE"
echo "[$TIMESTAMP] Starting image extraction" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

# Run extraction and capture output
OUTPUT=$(cd "$RAG_ROOT" && venv/bin/python3 scripts/extract_images.py \
    --registry-path ../data/registry.json \
    --uploads-dir ../data/sourcePDF \
    --images-out-dir ../data/images \
    2>&1) || true

# Log full output
echo "$OUTPUT" >> "$LOG_FILE"

# Parse summary from output
TOTAL_DOCS=$(echo "$OUTPUT" | grep -oP 'Found \K\d+' | head -1 || echo "0")
TOTAL_EXTRACTED=$(echo "$OUTPUT" | grep -oP '\d+ images extracted' | grep -oP '\d+' | awk '{s+=$1} END {print s+0}')
TOTAL_SKIPPED=$(echo "$OUTPUT" | grep -oP '\d+ skipped' | grep -oP '\d+' | awk '{s+=$1} END {print s+0}')

# Print summary to stdout
echo ""
echo "========================================"
echo "IMAGE EXTRACTION SUMMARY"
echo "========================================"
echo "Total documents processed: $TOTAL_DOCS"
echo "Total images extracted:    $TOTAL_EXTRACTED"
echo "Total images skipped:      $TOTAL_SKIPPED"
echo "========================================"
echo "Log file: $LOG_FILE"
echo "========================================"
