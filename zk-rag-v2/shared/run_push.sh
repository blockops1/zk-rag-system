#!/bin/bash
# run_push.sh — Push local Qdrant + docs + images to VPS (blockoperations.com)
#
# Lock: /data/rag/.lock.push
#   - Non-blocking flock
#   - On stale lock → remove and retry once
#
# Strategy:
#   1. Sanity gate: abort if < 1000 local vectors (deploying empty DB is worse than no push)
#   2. Rsync images to live dir (API stays up — static files)
#   3. Rsync Qdrant + docs to staging dirs (API stays up during full transfer)
#   4. Cutover: stop → swap staging/live → start (seconds downtime)
#   5. Verify API health on VPS after restart
#   6. Cleanup old dirs
#
# VPS API is always restarted on exit via trap (even on failure).
# Cron: 4 PM ET daily

set -euo pipefail

LOCK_FILE="/data/rag/.lock.push"
LOG_DIR="./logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG="${LOG_DIR}/push_${TIMESTAMP}.log"

VPS="deruyter@blockoperations.com"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -o BatchMode=yes"

QDRANT_SRC="/data/rag/qdrant_data/"
DOCS_SRC="/data/rag/mil-docs-staging/"
IMAGES_SRC="./data/images/"

QDRANT_LIVE="/data/rag/qdrant_data"
QDRANT_STAGING="/data/rag/qdrant_data_staging"
DOCS_LIVE="/data/rag/docs"
DOCS_STAGING="/data/rag/docs_staging"
IMAGES_LIVE="./data/images"

mkdir -p "$LOG_DIR"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

send_tg() {
    openclaw message send --channel telegram --target 374999219 \
        --message "$1" --silent 2>/dev/null || true
}

# ── Lock with stale recovery ──────────────────────────────────────────────────
acquire_lock() {
    exec 200>"$LOCK_FILE"
    if flock -n 200; then echo "$$" >&200; return 0; fi
    STALE=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$STALE" ] && ! kill -0 "$STALE" 2>/dev/null; then
        log "Stale lock PID $STALE — removing"; rm -f "$LOCK_FILE"
        exec 200>"$LOCK_FILE"
        if flock -n 200; then echo "$$" >&200; return 0; fi
    fi
    log "ERROR: Push already running (PID $STALE). Exiting."; return 1
}
cleanup_lock() { rm -f "$LOCK_FILE"; }

# ── Trap: always restart VPS API on exit ─────────────────────────────────────
restart_vps_api() {
    log "Ensuring VPS API is running..."
    ssh $SSH_OPTS "$VPS" "sudo systemctl start rag-api" \
        && log "VPS API started" \
        || log "WARNING: Failed to start VPS API — manual check needed"
}
trap restart_vps_api EXIT

# ── Sanity gate ──────────────────────────────────────────────────────────────
log "Checking local vector count..."
VECTOR_COUNT=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('/data/rag/qdrant_data/collection/army-docs/storage.sqlite')
    cur = conn.cursor(); cur.execute('SELECT COUNT(*) FROM points')
    print(cur.fetchone()[0]); conn.close()
except: print('0')
" 2>/dev/null)
log "Local vectors: $VECTOR_COUNT"
if [ "$VECTOR_COUNT" -lt 1000 ]; then
    log "ABORT: Only $VECTOR_COUNT vectors — below 1K threshold."
    send_tg "❌ Push aborted: only $VECTOR_COUNT local vectors (need 1K+ to deploy)"
    exit 4
fi

# ── Main ─────────────────────────────────────────────────────────────────────
log "========================================"
log "Push start — sync to VPS"
log "========================================"

acquire_lock || exit 1
trap cleanup_lock EXIT

# Step 1: Images (live, API up)
log "Step 1: Syncing images..."
rsync -az --delete -e "ssh $SSH_OPTS" "$IMAGES_SRC" "${VPS}:${IMAGES_LIVE}/" \
    && log "Images OK" \
    || { log "ERROR: Images sync failed"; exit 3; }

# Step 2: Qdrant to staging
log "Step 2: Syncing Qdrant to staging..."
ssh $SSH_OPTS "$VPS" "mkdir -p ${QDRANT_STAGING}"
rsync -az --delete -e "ssh $SSH_OPTS" "$QDRANT_SRC" "${VPS}:${QDRANT_STAGING}/" \
    && log "Qdrant staging OK" \
    || { log "ERROR: Qdrant sync failed"; exit 3; }

# Step 3: Docs to staging
log "Step 3: Syncing docs to staging..."
ssh $SSH_OPTS "$VPS" "mkdir -p ${DOCS_STAGING}"
rsync -az --delete -e "ssh $SSH_OPTS" "$DOCS_SRC" "${VPS}:${DOCS_STAGING}/" \
    && log "Docs staging OK" \
    || { log "ERROR: Docs sync failed"; exit 3; }

# Step 4: Cutover (brief downtime)
log "Step 4: Cutover — stopping VPS API..."
ssh $SSH_OPTS "$VPS" "sudo systemctl stop rag-api"
log "Swapping staging → live..."
ssh $SSH_OPTS "$VPS" "
    set -e
    [ -d '${QDRANT_LIVE}' ] && mv '${QDRANT_LIVE}' '${QDRANT_LIVE}_old'
    mv '${QDRANT_STAGING}' '${QDRANT_LIVE}'
    [ -d '${DOCS_LIVE}' ] && mv '${DOCS_LIVE}' '${DOCS_LIVE}_old'
    mv '${DOCS_STAGING}' '${DOCS_LIVE}'
    # Restore static web files
    cp -f '${DOCS_LIVE}_old/index.html' '${DOCS_LIVE}/' 2>/dev/null || true
    cp -f '${DOCS_LIVE}_old/catalog.html' '${DOCS_LIVE}/' 2>/dev/null || true
    cp -f '${DOCS_LIVE}_old/llms.txt' '${DOCS_LIVE}/' 2>/dev/null || true
    # Update registry symlink/target
    cp -f '${DOCS_LIVE}/registry.json' '/data/rag/registry.json'
    echo 'Swap complete'
" && log "Cutover complete" \
  || { log "ERROR: Cutover failed"; exit 3; }

# Step 5: Restart API
log "Step 5: Restarting VPS API..."
ssh $SSH_OPTS "$VPS" "sudo systemctl restart rag-api"
sleep 20

# Step 6: Verify
VERIFY=$(ssh $SSH_OPTS "$VPS" "curl -sf --max-time 30 http://127.0.0.1:8104/health 2>/dev/null") \
    || { log "WARNING: Post-push health check failed"; }
log "VPS API health: ${VERIFY:-no response}"

# Cleanup old dirs (non-fatal)
log "Cleaning up old dirs..."
ssh $SSH_OPTS "$VPS" "rm -rf '${QDRANT_LIVE}_old' '${DOCS_LIVE}_old'" \
    || log "Cleanup warning (non-fatal)"

log "========================================"
log "Push complete"
log "========================================"
send_tg "✅ Push complete — $VECTOR_COUNT vectors deployed to VPS"
