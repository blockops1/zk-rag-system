#!/bin/bash
# push_to_vps.sh — One-way sync: DeRuyter → blockoperations.com
#
# Strategy:
#   1. Rsync images to live dir (API stays up — images are static, safe to update live)
#   2. Rsync Qdrant + docs to staging dirs (API stays up during full transfer)
#   3. Short cutover: stop API → swap staging → live → start API (seconds of downtime)
#
# VPS API is always restarted on exit, even on failure (trap guarantee).

set -euo pipefail

VPS="deruyter@blockoperations.com"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -o BatchMode=yes"

# Local sources
QDRANT_SRC="/data/rag/qdrant_data/"
DOCS_SRC="/data/rag/mil-docs-staging/"
REGISTRY_SRC="./data/registry.json"
IMAGES_SRC="./data/images/"

# VPS paths
QDRANT_LIVE="/data/rag/qdrant_data"
QDRANT_STAGING="/data/rag/qdrant_data_staging"
DOCS_LIVE="/data/rag/docs"
DOCS_STAGING="/data/rag/docs_staging"
IMAGES_LIVE="./data/images"

PUSH_FAILED=0

# ── Sanity gate: check we have vectors before pushing ─────────────
echo "[push_to_vps] Step 0: Verifying local vector count..."
VECTOR_COUNT=$(python3 -c "
import sqlite3, sys
try:
    conn = sqlite3.connect('/data/rag/qdrant_data/collection/army-docs/storage.sqlite')
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM points')
    print(cur.fetchone()[0])
    conn.close()
except Exception as e:
    print('0')
" 2>/dev/null)
echo "[push_to_vps] Local vectors: ${VECTOR_COUNT}"
if [ "$VECTOR_COUNT" -lt 1000 ]; then
    echo "[push_to_vps] ABORT: Only ${VECTOR_COUNT} vectors — below 1K threshold. Push to VPS would deploy near-empty database."
    echo "[push_to_vps] Run 'ingest N' to add docs before pushing."
    exit 4
fi

# ── Trap: always restart VPS API on exit ─────────────────────────
restart_vps_api() {
    echo "[push_to_vps] Ensuring VPS API is running..."
    ssh $SSH_OPTS "$VPS" "sudo systemctl start rag-api" \
        && echo "[push_to_vps] VPS API started" \
        || echo "[push_to_vps] WARNING: Failed to start VPS API — manual intervention needed"
}
trap restart_vps_api EXIT

# ── Step 1: Images (live, API stays up) ──────────────────────────
echo "[push_to_vps] Step 1: Syncing images (live, API up)..."
rsync -az --delete -e "ssh $SSH_OPTS" "$IMAGES_SRC" "${VPS}:${IMAGES_LIVE}/" \
    && echo "[push_to_vps] Images sync complete" \
    || { echo "[push_to_vps] ERROR: Images sync failed"; PUSH_FAILED=1; }

# ── Step 2: Qdrant to staging (API stays up) ─────────────────────
echo "[push_to_vps] Step 2: Syncing Qdrant to staging (API up)..."
ssh $SSH_OPTS "$VPS" "mkdir -p ${QDRANT_STAGING}"
rsync -az --delete -e "ssh $SSH_OPTS" "$QDRANT_SRC" "${VPS}:${QDRANT_STAGING}/" \
    && echo "[push_to_vps] Qdrant staging sync complete" \
    || { echo "[push_to_vps] ERROR: Qdrant staging sync failed"; PUSH_FAILED=1; }

# ── Step 3: Docs to staging (API stays up) ────────────────────────
echo "[push_to_vps] Step 3: Syncing docs to staging (API up)..."
ssh $SSH_OPTS "$VPS" "mkdir -p ${DOCS_STAGING}"
rsync -az --delete -e "ssh $SSH_OPTS" "$DOCS_SRC" "${VPS}:${DOCS_STAGING}/" \
    && echo "[push_to_vps] Docs staging sync complete" \
    || { echo "[push_to_vps] ERROR: Docs staging sync failed"; PUSH_FAILED=1; }

# ── Abort if any sync failed — don't cutover partial data ────────
if [ "$PUSH_FAILED" -eq 1 ]; then
    echo "[push_to_vps] ERROR: One or more syncs failed — aborting cutover"
    echo "[push_to_vps] Staging dirs left intact for inspection"
    exit 3
fi

# ── Step 4: Cutover (brief API downtime) ─────────────────────────
echo "[push_to_vps] Step 4: Cutover — stopping VPS API..."
ssh $SSH_OPTS "$VPS" "sudo systemctl stop rag-api"

echo "[push_to_vps] Swapping staging → live..."
ssh $SSH_OPTS "$VPS" "
    set -e
    # Qdrant swap
    if [ -d '${QDRANT_LIVE}' ]; then
        mv '${QDRANT_LIVE}' '${QDRANT_LIVE}_old'
    fi
    mv '${QDRANT_STAGING}' '${QDRANT_LIVE}'

    # Docs swap: mil-docs-staging content → docs/
    if [ -d '${DOCS_LIVE}' ]; then
        mv '${DOCS_LIVE}' '${DOCS_LIVE}_old'
    fi
    mv '${DOCS_STAGING}' '${DOCS_LIVE}'

    # Post-swap: restore static web files from docs_old to docs/
    cp -f '${DOCS_LIVE}_old/index.html' '${DOCS_LIVE}/' 2>/dev/null || true
    cp -f '${DOCS_LIVE}_old/catalog.html' '${DOCS_LIVE}/' 2>/dev/null || true
    cp -f '${DOCS_LIVE}_old/llms.txt' '${DOCS_LIVE}/' 2>/dev/null || true

    # Post-swap: copy registry to where api_server.py expects it
    cp -f '${DOCS_LIVE}/registry.json' '/data/rag/registry.json'

    echo 'Swap complete'
" && echo "[push_to_vps] Cutover complete" \
  || { echo "[push_to_vps] ERROR: Cutover swap failed — old data still in place"; exit 3; }

# Restart VPS API so it picks up the new registry path
echo "[push_to_vps] Restarting VPS API with updated registry..."
ssh $SSH_OPTS "$VPS" "sudo systemctl restart rag-api"

# trap fires here → starts VPS API
echo "[push_to_vps] VPS API restarting (via trap)..."
# Give it time to come up before verify
sleep 20

# ── Step 5: Verify ───────────────────────────────────────────────
echo "[push_to_vps] Step 5: Verifying VPS API..."
VERIFY=$(ssh $SSH_OPTS "$VPS" "curl -sf --max-time 30 http://127.0.0.1:8104/health 2>/dev/null") || {
    echo "[push_to_vps] WARNING: API health check failed after cutover"
    exit 1
}
echo "[push_to_vps] API health: $VERIFY"

# Clean up _old dirs on VPS (non-fatal)
echo "[push_to_vps] Cleaning up old dirs..."
ssh $SSH_OPTS "$VPS" "rm -rf '${QDRANT_LIVE}_old' '${DOCS_LIVE}_old'" \
    || echo "[push_to_vps] WARNING: Cleanup failed (non-fatal)"

echo "[push_to_vps] Done — push and cutover successful."
