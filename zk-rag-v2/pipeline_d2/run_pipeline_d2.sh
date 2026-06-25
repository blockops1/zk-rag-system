#!/bin/bash
# run_pipeline_d2.sh — Pipeline D2: embed chunks with NomicEmbedText-v1.5
#
# Lock: ./data/.lock.pipeline_d2
#   - Non-blocking flock — fails immediately if another instance is running
#
# What: reads chunks from ./data/chunks/{doc_id}/chunks.jsonl,
#   embeds via NomicEmbedText-v1.5 (FastEmbed), writes .npy + meta.json to
#   ./data/embeddings/{doc_id}/.
#
# Entry: docs with chunks in ./data/chunks/
# Exit: sends Telegram summary

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="./data/.lock.pipeline_d2"
VENV_PY="./venv/bin/python3"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_DIR=".../data/logs"
STRUCTURED_LOG="${LOG_DIR}/pipeline_d2_${TIMESTAMP}.jsonl"
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
    log "ERROR" "Pipeline D2 already running (PID $LOCK_HOLDER). Exiting."
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
log "INFO" "Pipeline D2 start — NomicEmbedText-v1.5 embedding"
log "INFO" "========================================"

acquire_lock || exit 1
trap cleanup_lock EXIT

DOC_COUNT=$(python3 -c "
import pathlib
print(len([d for d in pathlib.Path('./data/chunks').iterdir() if d.is_dir()]))
" 2>/dev/null) || DOC_COUNT="?"

log "INFO" "Found $DOC_COUNT docs with chunks to embed"

$VENV_PY "$SCRIPT_DIR/embed_chunks.py" --all 2>&1 | tee -a "$STRUCTURED_LOG"
EXIT=${PIPESTATUS[0]}

TOTAL_CHUNKS=$(python3 -c "
import json, pathlib
count = 0
for meta_path in pathlib.Path('./data/embeddings').glob('*/meta.json'):
    try:
        count += json.loads(meta_path.read_text())['chunk_count']
    except: pass
print(count)
" 2>/dev/null) || TOTAL_CHUNKS="?"

log "INFO" "Done (exit $EXIT) — $TOTAL_CHUNKS total chunks embedded"

if [ $EXIT -eq 0 ]; then
    send_tg "✅ Pipeline D2 complete — $TOTAL_CHUNKS chunks embedded (768d)"
else
    send_tg "⚠️ Pipeline D2 exited with $EXIT — check logs"
fi

exit $EXIT
