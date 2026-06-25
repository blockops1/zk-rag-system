#!/bin/bash
# run_pipeline_b.sh — Pipeline B: continuous docling loop with stale-lock recovery
#
# Lock: ./data/.lock.pipeline_b
#   - Non-blocking flock — fails immediately if another instance is running
#   - On stale lock → remove and retry once
#
# This is a LONG-RUNNING daemon: runs continuously, processing the needs_docling
# queue as Pipeline A feeds it. Expects to run for hours per day.
# Cron kicks it off at 09:30 ET; it drains the queue and exits cleanly.
#
# Prerequisites: rag-api.service must be running
# Entry: ./data/extraction_queue.json
# Exit: needs_docling list empty, or API unreachable

set -euo pipefail

LOCK_FILE="./data/.lock.pipeline_b"
LOG_DIR="./logs"
VENV_PY="./venv/bin/python3"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG="${LOG_DIR}/pipeline_b_${TIMESTAMP}.log"

mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

send_tg() {
    openclaw message send --channel telegram --target 374999219 \
        --message "$1" --silent 2>/dev/null || true
}

# ── Lock acquisition with stale-lock recovery ──────────────────────────────────
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
        # Retry also failed — close fd and exit
        exec 200>&-
        log "ERROR: Pipeline B already running (unknown PID). Exiting."
        return 1
    fi

    log "ERROR: Pipeline B already running (PID $LOCK_HOLDER). Exiting."
    return 1
}

cleanup_lock() {
    if [ -f "$LOCK_FILE" ]; then
        LOCK_HOLDER=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        [ "$LOCK_HOLDER" = "$$" ] && rm -f "$LOCK_FILE"
    fi
}

# ── Check if queue has docs ────────────────────────────────────────────────────
queue_count() {
    python3 -c "
import json, sys
try:
    with open('./data/extraction_queue.json') as f:
        data = json.load(f)
    print(len(data.get('extraction_queue', [])))
except (FileNotFoundError, json.JSONDecodeError):
    print(0)
" 2>/dev/null || echo "0"
}

# ── API health ─────────────────────────────────────────────────────────────────
check_api() {
    curl -sf --max-time 5 http://127.0.0.1:8100/health > /dev/null 2>&1
}

# ── Main loop ─────────────────────────────────────────────────────────────────
log "========================================"
log "Pipeline B start — continuous docling"
log "========================================"

acquire_lock || exit 1
trap cleanup_lock EXIT

log "Verifying RAG API..."
if ! check_api; then
    log "RAG API not responding — restarting..."
    sudo systemctl restart rag-api.service
    sleep 8
    if ! check_api; then
        log "ERROR: RAG API unreachable after restart. Exiting."
        exit 1
    fi
fi
log "RAG API OK"

SLEEP_INTERVAL=60
MAX_EMPTY_CYCLES=10    # exit after 10 consecutive empty-queue checks (~10 min)
empty_cycles=0

while true; do
    count=$(queue_count)

    if [ "$count" -eq 0 ]; then
        empty_cycles=$((empty_cycles + 1))
        if [ "$empty_cycles" -ge "$MAX_EMPTY_CYCLES" ]; then
            log "Queue empty for ${MAX_EMPTY_CYCLES} consecutive checks — exiting cleanly."
            send_tg "✅ Pipeline B: queue drained, exiting"
            exit 0
        fi
        log "Queue empty (cycle $empty_cycles/$MAX_EMPTY_CYCLES) — sleeping ${SLEEP_INTERVAL}s..."
        sleep $SLEEP_INTERVAL
        continue
    fi

    empty_cycles=0
    log "Queue has $count docs — running docling pass"

    $VENV_PY "$SCRIPT_DIR/batch_ingest_branch.py" --pass 2 >> "$LOG" 2>&1
    EXIT=$?

    if [ $EXIT -ne 0 ]; then
        log "WARNING: pass 2 exited with $EXIT — sleeping and retrying"
        send_tg "⚠️ Pipeline B: pass 2 exited with $EXIT — retrying in 60s"
        sleep $SLEEP_INTERVAL
        continue
    fi

    log "Pass 2 complete — sleeping ${SLEEP_INTERVAL}s before next check"
    sleep $SLEEP_INTERVAL
done
