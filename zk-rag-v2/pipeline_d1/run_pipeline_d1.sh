#!/bin/bash
# run_pipeline_d1.sh — Pipeline D1: chunk page JSONs into text chunks
#
# Lock: ./data/.lock.pipeline_d1
#   - Non-blocking flock — fails immediately if another instance is running
#
# What: reads page JSONs from ./data/extracted/{doc_id}/pages/,
#   splits text into chunks, writes ./data/chunks/{doc_id}/.
#   Vision descriptions (if present) are inlined as [VISUAL: ...] in page text.
#
# Entry: docs in ./data/extracted/ that have page JSONs
# Exit: sends Telegram summary

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="./data/.lock.pipeline_d1"
VENV_PY="./venv/bin/python3"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_DIR=".../data/logs"
STRUCTURED_LOG="${LOG_DIR}/pipeline_d1_${TIMESTAMP}.jsonl"
mkdir -p "$LOG_DIR"

# Structured JSON log — one JSON object per line
log() {
    local level="$1"; shift
    local msg="$*"
    local ts=$(date +%Y-%m-%dT%H:%M:%S.%3NZ)
    printf '%s\n' "$(jq -n \
        --arg ts "$ts" \
        --arg level "$level" \
        --arg msg "$msg" \
        '{ts: $ts, level: $level, msg: $msg}')" | tee -a "$STRUCTURED_LOG"
}

send_tg() {
    openclaw message send --channel telegram --target 374999219 \
        --message "$1" --silent 2>/dev/null || true
}

# ── Lock guard ─────────────────────────────────────────────────────────────────
acquire_lock() {
    exec 200>"$LOCK_FILE"
    if flock -n 200; then
        echo "$$" >&200
        return 0
    fi

    LOCK_HOLDER=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    log "ERROR" "Pipeline D1 already running (PID $LOCK_HOLDER). Exiting."
    return 1
}

cleanup_lock() {
    if [ -f "$LOCK_FILE" ]; then
        LOCK_HOLDER=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        [ "$LOCK_HOLDER" = "$$" ] && rm -f "$LOCK_FILE"
    fi
}

# ── Main ───────────────────────────────────────────────────────────────────────
log "INFO" "========================================"
log "INFO" "Pipeline D1 start — document chunking"
log "INFO" "========================================"

acquire_lock || exit 1
trap cleanup_lock EXIT

DOC_COUNT=$(python3 -c "
import pathlib
print(len([d for d in pathlib.Path('./data/extracted').iterdir() if d.is_dir()]))
" 2>/dev/null || echo "?")

log "INFO" "Found $DOC_COUNT docs to process in ./data/extracted/"

# Run chunking for all docs — capture chunk counts per doc
$VENV_PY "$SCRIPT_DIR/chunk_document.py" --all >> "$STRUCTURED_LOG" 2>&1
EXIT=$?

TOTAL_CHUNKS=$(python3 -c "
import json, pathlib
count = 0
for chunk_ids_path in pathlib.Path('./data/chunks').glob('*/chunk_ids.json'):
    try:
        count += len(json.loads(chunk_ids_path.read_text()))
    except: pass
print(count)
" 2>/dev/null || echo "?")

log "INFO" "Done (exit $EXIT) — $TOTAL_CHUNKS total chunks written"

if [ $EXIT -eq 0 ]; then
    send_tg "✅ Pipeline D1 complete — $TOTAL_CHUNKS chunks from $DOC_COUNT docs"
else
    send_tg "⚠️ Pipeline D1 exited with $EXIT — check logs"
fi

exit $EXIT
