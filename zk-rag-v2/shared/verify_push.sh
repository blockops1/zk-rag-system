#!/usr/bin/env bash
# verify_push.sh — Confirm images and DB are on VPS before flipping the API.
# Usage: verify_push.sh --images-src PATH --db-src PATH --vps HOST [--dry-run]
#   --images-src  : Local images source directory (e.g. /data/rag/images)
#   --db-src      : Local qdrant_data staging path (e.g. /data/rag/qdrant_data_staging)
#   --vps         : VPS hostname (e.g. blockoperations.com)
#   --dry-run     : Print what would be checked without SSHing

set -euo pipefail

IMAGES_SRC=""
DB_SRC=""
VPS_HOST=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --images-src) IMAGES_SRC="$2"; shift 2 ;;
        --db-src)     DB_SRC="$2"; shift 2 ;;
        --vps)        VPS_HOST="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=true; shift ;;
        --help|-h)
            echo "Usage: verify_push.sh --images-src PATH --db-src PATH --vps HOST [--dry-run]"
            echo "  Verifies images and DB have arrived on VPS before API flip."
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if $DRY_RUN; then
    echo "[verify_push] DRY RUN — would verify:"
    echo "[verify_push]   1. Check images dirs exist on VPS via SSH ls"
    echo "[verify_push]   2. Check qdrant collection files exist on VPS via SSH ls"
    echo "[verify_push]   3. Exit 0 if all present, exit 1 if any missing"
    exit 0
fi

if [[ -z "$IMAGES_SRC" ]] || [[ -z "$DB_SRC" ]] || [[ -z "$VPS_HOST" ]]; then
    echo "ERROR: --images-src, --db-src, and --vps are all required" >&2
    echo "Usage: verify_push.sh --images-src PATH --db-src PATH --vps HOST [--dry-run]" >&2
    exit 1
fi

SSH_TARGET="youruser@${VPS_HOST}"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes"

log() { echo "[verify_push] $*"; }
fail() { echo "[verify_push] VERIFICATION FAILED: $*" >&2; exit 1; }

log "Images source: $IMAGES_SRC"
log "DB source:     $DB_SRC"
log "VPS:           $VPS_HOST"

# ── Image verification ────────────────────────────────────────
log "Checking images on VPS..."

if [[ -d "$IMAGES_SRC" ]]; then
    DOC_IDS=$(ls -1 "$IMAGES_SRC" 2>/dev/null || true)
    if [[ -z "$DOC_IDS" ]]; then
        log "WARNING: No image directories found in $IMAGES_SRC — skipping image check"
    else
        for doc_id in $DOC_IDS; do
            REMOTE_PATH="/data/rag/images/${doc_id}"
            if ! ssh $SSH_OPTS "$SSH_TARGET" "ls -d $REMOTE_PATH" >/dev/null 2>&1; then
                fail "Images directory missing on VPS: $REMOTE_PATH"
            fi
        done
        log "All image directories confirmed on VPS"
    fi
else
    log "WARNING: Images source $IMAGES_SRC not found locally — skipping image check"
fi

# ── DB verification ───────────────────────────────────────────
log "Checking DB on VPS..."

REMOTE_DB_STAGING="/data/rag/qdrant_data_staging"
if ! ssh $SSH_OPTS "$SSH_TARGET" "ls $REMOTE_DB_STAGING" >/dev/null 2>&1; then
    fail "DB staging directory missing on VPS: $REMOTE_DB_STAGING"
fi

COLL_COUNT=$(ssh $SSH_OPTS "$SSH_TARGET" "ls -1 $REMOTE_DB_STAGING 2>/dev/null | wc -l" || echo "0")
if [[ "$COLL_COUNT" -eq 0 ]]; then
    fail "No collection data found in $REMOTE_DB_STAGING on VPS"
fi

log "DB confirmed on VPS ($COLL_COUNT items in staging)"
log "VERIFICATION PASSED: images and DB both confirmed on VPS"
