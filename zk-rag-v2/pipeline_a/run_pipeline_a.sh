#!/bin/bash
# run_pipeline_a.sh — Pipeline A: fitz-only ingest with stale-lock recovery
#
# Lock: ./data/.lock.pipeline_a
#   - Non-blocking flock — fails immediately if another run is active
#   - On failure: checks if the lock holder process is dead (stale)
#     - Dead PID → removes stale lock, retries
#     - Live PID → exits with warning
#
# Prerequisites: rag-api.service must be running
# Entry: PDFs in ./data/sourcePDF/{branch}/
# Exit: writes needs_docling list, sends Telegram per-doc notifications
#
# Usage: ./run_pipeline_a.sh [limit]
#   limit  — process up to N PDFs (default: all pending)

set -euo pipefail

LOCK_FILE="./data/.lock.pipeline_a"
LOG_DIR="./logs"
VENV_PY="./venv/bin/python3"
SCRIPT_DIR="./shared"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG="${LOG_DIR}/pipeline_a_${TIMESTAMP}.log"

mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# ── Telegram helper ────────────────────────────────────────────────────────────
send_tg() {
    openclaw message send --channel telegram --target 374999219 \
        --message "$1" --silent 2>/dev/null || true
}

# ── Stale-lock guard ──────────────────────────────────────────────────────────
acquire_lock() {
    # Try to get lock non-blocking
    exec 200>"$LOCK_FILE"
    if flock -n 200; then
        echo "$$" >&200   # write our PID for debugging
        return 0         # got lock
    fi

    # Lock held — check if holder is alive
    LOCK_HOLDER=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$LOCK_HOLDER" ] && ! kill -0 "$LOCK_HOLDER" 2>/dev/null; then
        log "WARNING: Stale lock detected (PID $LOCK_HOLDER dead). Removing."
        rm -f "$LOCK_FILE"
        # Open new fd to the fresh lock file, then retry flock
        exec 200>"$LOCK_FILE"
        if flock -n 200; then
            echo "$$" >&200
            return 0
        fi
        # Retry also failed — close fd and exit
        exec 200>&-
        log "ERROR: Pipeline A already running (unknown PID). Exiting."
        return 1
    fi

    log "ERROR: Pipeline A already running (PID $LOCK_HOLDER). Exiting."
    return 1
}

cleanup_lock() {
    # Only remove if we own the lock (our PID is in the file)
    if [ -f "$LOCK_FILE" ]; then
        LOCK_HOLDER=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        [ "$LOCK_HOLDER" = "$$" ] && rm -f "$LOCK_FILE"
    fi
}

# ── API health check ───────────────────────────────────────────────────────────
check_api() {
    curl -sf --max-time 5 http://127.0.0.1:8100/health > /dev/null 2>&1
}

restart_api() {
    log "RAG API not responding — restarting..."
    sudo systemctl restart rag-api.service
    sleep 8
    if check_api; then
        log "RAG API restarted OK"
    else
        log "ERROR: RAG API still not responding after restart"
        send_tg "❌ Pipeline A: RAG API failed to restart"
        exit 1
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────────
log "========================================"
log "Pipeline A start — fitz ingest"
log "========================================"

acquire_lock || exit 1
trap cleanup_lock EXIT

# Verify API up
if ! check_api; then
    restart_api
fi

# Run pass 1
LIMIT_ARG="${1:-all}"
log "Running batch_ingest_branch.py (pass 1, limit=$LIMIT_ARG)"

if [ "$LIMIT_ARG" = "all" ]; then
    $VENV_PY "$SCRIPT_DIR/batch_ingest_branch.py" --pass 1 >> "$LOG" 2>&1
else
    $VENV_PY "$SCRIPT_DIR/batch_ingest_branch.py" --pass 1 "$LIMIT_ARG" >> "$LOG" 2>&1
fi

EXIT=$?

# Final storage snapshot
PDF_MB=$(du -sm ./data/sourcePDF/ 2>/dev/null | cut -f1)
DATA_PCT=$(df /data | tail -1 | awk '{print $5}')
log "Done (exit $EXIT) — PDFs: ${PDF_MB}MB, /data: ${DATA_PCT}"

if [ $EXIT -eq 0 ]; then
    send_tg "✅ Pipeline A complete — fitz ingest done"
else
    send_tg "⚠️ Pipeline A finished with exit $EXIT — check logs"
fi

exit $EXIT
