#!/bin/bash
# run_pipeline_c.sh — Pipeline C: SmolVLM2 vision description with stale-lock recovery
#
# Lock: $DATA_DIR/.lock.pipeline_c
#   - Non-blocking flock — fails immediately if another instance is running
#   - On stale lock → remove and retry once
#
# What: for all ingested docs with figure_only=true pages, runs SmolVLM2-2.2B
#   via llama-mtmd-cli to generate descriptions, writes back to page JSONs.
#   Next ingest pass picks them up automatically.
#
# Prerequisites: none (CPU-only, llama-mtmd-cli + model must exist)
# Entry: docs in $DATA_DIR/extracted/ with figure_only=true pages
# Exit: sends Telegram summary

set -euo pipefail

LOCK_FILE="$DATA_DIR/.lock.pipeline_c"
LOG_DIR="$REPO_DIR/logs"
VENV_PY="$REPO_DIR/venv/bin/python3"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG="${LOG_DIR}/pipeline_c_${TIMESTAMP}.log"

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
        # Retry also failed — close fd and exit
        exec 200>&-
        log "ERROR: Pipeline C already running (unknown PID). Exiting."
        return 1
    fi

    log "ERROR: Pipeline C already running (PID $LOCK_HOLDER). Exiting."
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
log "Pipeline C start — SmolVLM2 vision description"
log "========================================"

acquire_lock || exit 1
trap cleanup_lock EXIT

log "Running batch_image_describe.py --resume (copy-first, output to $DATA_DIR/extracted-vision)"
$VENV_PY "$SCRIPT_DIR/batch_image_describe.py" --resume --output-dir $DATA_DIR/extracted-vision >> "$LOG" 2>&1
EXIT=$?

# Count described pages — pipeline writes to ingested-vision/, NOT ingested/
DESCRIBED=$(python3 -c "
import json, pathlib
count = 0
for doc_dir in pathlib.Path('$DATA_DIR/extracted-vision').iterdir():
    if not doc_dir.is_dir(): continue
    for page_file in (doc_dir / 'pages').glob('*.json'):
        try:
            if json.loads(page_file.read_text()).get('vision_description'):
                count += 1
        except: pass
print(count)
" 2>/dev/null || echo "?")

log "Done (exit $EXIT) — $DESCRIBED pages described"

if [ $EXIT -eq 0 ]; then
    send_tg "✅ Pipeline C complete — $DESCRIBED pages described"
else
    send_tg "⚠️ Pipeline C exited with $EXIT — check logs"
fi

exit $EXIT
