#!/bin/bash
# run_pipeline_d.sh — Pipeline D: Qdrant index (THE ONLY QDRANT WRITE POINT)
#
# Lock: $DATA_DIR/.lock.pipeline_d
#   - Blocking flock — waits for exclusive lock
#   - On stale lock → remove and retry once
#
# What: for all docs with status=extracted that aren't yet in Qdrant,
#   reads page JSONs from ingested-vision/ (preferred) or ingested/,
#   chunks them, embeds them, upserts to Qdrant, updates registry to status=ingested.
#
# Prerequisites: api_server running (for Qdrant), venv-docling (for docling)
# Entry: docs with status=extracted in registry, not yet in Qdrant
# Exit: sends Telegram summary

set -euo pipefail

LOCK_FILE="$DATA_DIR/.lock.pipeline_d"
LOG_DIR="$REPO_DIR/logs"
VENV_PY="$REPO_DIR/venv/bin/python3"
SCRIPT_DIR="$REPO_DIR/shared"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG="${LOG_DIR}/pipeline_d_${TIMESTAMP}.log"

mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

send_tg() {
    openclaw message send --channel telegram --target 374999219 \
        --message "$1" --silent 2>/dev/null || true
}

# ── Stale-lock guard ──────────────────────────────────────────────────────────
acquire_lock() {
    exec 200>"$LOCK_FILE"
    if flock -n 200; then
        echo "$$" >&200
        return 0
    fi

    LOCK_HOLDER=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$LOCK_HOLDER" ] && ! kill -0 "$LOCK_HOLDER" 2>/dev/null; then
        log "WARNING: Stale lock detected (PID $LOCK_HOLDER dead). Removing."
        rm -f "$LOCK_FILE"
        exec 200>"$LOCK_FILE"
        if flock -n 200; then
            echo "$$" >&200
            return 0
        fi
        exec 200>&-
        log "ERROR: Pipeline D already running (unknown PID). Exiting."
        return 1
    fi

    log "ERROR: Pipeline D already running (PID $LOCK_HOLDER). Exiting."
    return 1
}

cleanup_lock() {
    if [ -f "$LOCK_FILE" ]; then
        LOCK_HOLDER=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        [ "$LOCK_HOLDER" = "$$" ] && rm -f "$LOCK_FILE"
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────────
log "========================================"
log "Pipeline D start — Qdrant index (THE ONLY QDRANT WRITE POINT)"
log "========================================"

acquire_lock || exit 1
trap cleanup_lock EXIT

$VENV_PY "$SCRIPT_DIR/pipeline_d.py" >> "$LOG" 2>&1
EXIT=$?

log "Done (exit $EXIT)"

if [ $EXIT -eq 0 ]; then
    send_tg "Pipeline D complete"
else
    send_tg "Pipeline D exited with code $EXIT — check logs"
fi

exit $EXIT
