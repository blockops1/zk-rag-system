#!/bin/bash
# run_ssl_check.sh — Monthly SSL cert + certbot timer check
# Cron: 1st of month, 9 AM ET
# Alerts if cert expires within 30 days or certbot.timer is not active

set -euo pipefail

VPS="deruyter@militarymanuals.ai"
DOMAIN="militarymanuals.ai"
WARN_DAYS=30

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

send_tg() {
    openclaw message send --channel telegram --target 374999219 \
        --message "$1" --silent 2>/dev/null || true
}

ISSUES=()

# ── Cert expiry ──────────────────────────────────────────────────────────────
EXPIRY_RAW=$(echo | openssl s_client -connect "${DOMAIN}:443" \
    -servername "$DOMAIN" 2>/dev/null \
    | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)

if [ -z "$EXPIRY_RAW" ]; then
    ISSUES+=("🔴 Could not fetch SSL cert from $DOMAIN")
else
    EXPIRY_EPOCH=$(date -d "$EXPIRY_RAW" +%s)
    NOW_EPOCH=$(date +%s)
    DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))
    EXPIRY_PRETTY=$(date -d "$EXPIRY_RAW" "+%Y-%m-%d")

    if [ "$DAYS_LEFT" -le "$WARN_DAYS" ]; then
        ISSUES+=("⚠️ SSL cert expires in ${DAYS_LEFT} days ($EXPIRY_PRETTY) — renew now!")
    else
        log "OK: SSL cert expires $EXPIRY_PRETTY (${DAYS_LEFT} days)"
    fi
fi

# ── Certbot timer ───────────────────────────────────────────────────────────
TIMER_STATUS=$(ssh "$VPS" "systemctl is-active certbot.timer 2>/dev/null" 2>&1)
if [ "$TIMER_STATUS" != "active" ]; then
    ISSUES+=("⚠️ certbot.timer is ${TIMER_STATUS} — auto-renew may not be running")
else
    log "OK: certbot.timer active"
fi

# ── Report ─────────────────────────────────────────────────────────────────
if [ ${#ISSUES[@]} -gt 0 ]; then
    log "ALERTS: ${ISSUES[*]}"
    send_tg "🔒 SSL/Certbot Alert\n${ISSUES[*]}"
    exit 1
fi

send_tg "✅ SSL cert OK — $DAYS_LEFT days, certbot.timer active"
