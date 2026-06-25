#!/bin/bash
# run_geoip_refresh.sh — Monthly GeoLite2 DB refresh to VPS
# Cron: 1st of month, 10 AM ET
# Pushes local GeoLite2 DB to VPS and reloads OpenResty

set -euo pipefail

VPS="deruyter@militarymanuals.ai"
LOCAL_DB="/data/geoip/GeoLite2-Country.mmdb"
VPS_DB_DIR="/data/geoip/"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

send_tg() {
    openclaw message send --channel telegram --target 374999219 \
        --message "$1" --silent 2>/dev/null || true
}

if [ ! -f "$LOCAL_DB" ]; then
    log "ABORT: Local GeoLite2 DB not found at $LOCAL_DB"
    send_tg "❌ GeoIP refresh: DB not found at $LOCAL_DB"
    exit 1
fi

AGE_DAYS=$(( ( $(date +%s) - $(stat -c %Y "$LOCAL_DB") ) / 86400 ))
log "Local GeoLite2 DB age: ${AGE_DAYS} days"

log "Pushing GeoLite2 DB to VPS..."
rsync -az "$LOCAL_DB" "${VPS}:${VPS_DB_DIR}" 2>&1

RELOAD=$(ssh "$VPS" "sudo systemctl reload openresty 2>&1 && echo 'OK'" 2>&1)
if echo "$RELOAD" | grep -q "OK"; then
    log "OpenResty reloaded OK"
    send_tg "✅ GeoIP DB refreshed (${AGE_DAYS}d old) — OpenResty reloaded"
else
    log "WARNING: OpenResty reload output: $RELOAD"
    send_tg "⚠️ GeoIP refreshed but OpenResty reload uncertain — check manually"
fi
