#!/usr/bin/env bash
# rollback_on_failure.sh — Roll back qdrant_data/ to the previous snapshot on ingest failure.
# Usage: rollback_on_failure.sh [SNAPSHOT_PATH] [--dry-run]
#   SNAPSHOT_PATH  : Path to qdrant_data_prev (default: /data/rag/qdrant_data_prev)
#   --dry-run      : Print what would be done without doing it

set -euo pipefail

DRY_RUN=false
POSITIONAL=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --help|-h)
            echo "Usage: rollback_on_failure.sh [SNAPSHOT_PATH] [--dry-run]"
            echo "  SNAPSHOT_PATH : Path to qdrant_data_prev (default: /data/rag/qdrant_data_prev)"
            echo "  --dry-run    : Print what would be done without doing it"
            exit 0 ;;
        -*) echo "Unknown option: $1" >&2; exit 1 ;;
        *)  POSITIONAL+=("$1"); shift ;;
    esac
done

set -- "${POSITIONAL[@]:-}"
SNAPSHOT_PATH="${1:-/data/rag/qdrant_data_prev}"

log() { echo "[rollback] $*"; }

DATA_DIR="/data/rag/qdrant_data"
BROKEN_DIR="/data/rag/qdrant_data_broken"

# ── Dry run ───────────────────────────────────────────────────
if $DRY_RUN; then
    log "DRY RUN — would do the following:"
    log "  Snapshot: $SNAPSHOT_PATH"
    log "  1. Rename $DATA_DIR → $BROKEN_DIR (preserved for inspection)"
    log "  2. Rename $SNAPSHOT_PATH → $DATA_DIR"
    log "  3. Restart rag-api-local"
    exit 0
fi

# ── Checks ────────────────────────────────────────────────────
if [[ ! -d "$SNAPSHOT_PATH" ]]; then
    log "No snapshot available at $SNAPSHOT_PATH — cannot rollback"
    exit 1
fi

if [[ -z "$(ls -A "$SNAPSHOT_PATH" 2>/dev/null)" ]]; then
    log "Snapshot at $SNAPSHOT_PATH is empty — cannot rollback"
    exit 1
fi

if [[ ! -d "$DATA_DIR" ]]; then
    log "No current qdrant_data/ found at $DATA_DIR — nothing to roll back"
    exit 1
fi

# ── Rollback ──────────────────────────────────────────────────
log "Rolling back qdrant_data/ to previous snapshot..."

log "  → Moving current data to $BROKEN_DIR (preserved for inspection)"
mv "$DATA_DIR" "$BROKEN_DIR"

log "  → Restoring snapshot from $SNAPSHOT_PATH"
mv "$SNAPSHOT_PATH" "$DATA_DIR"

log "  → Restarting rag-api-local..."
systemctl --user restart rag-api-local

log "Rollback complete."
