#!/bin/bash
# run_vps_health.sh — VPS weekly health check
# Cron: Mon 8 AM ET
# Checks: disk, OpenResty web server, RAG API port, API vector counts

set -euo pipefail

LOG_DIR="./logs"
STATE_FILE="./logs/vps_health_state.json"
ALERT_COOLDOWN=7200   # seconds between repeated alerts

VPS="deruyter@militarymanuals.ai"
ALERT_DISK_PCT=80

mkdir -p "$LOG_DIR"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

send_tg() {
    openclaw message send --channel telegram --target 374999219 \
        --message "$1" --silent 2>/dev/null || true
}

load_state() {
    [ -f "$STATE_FILE" ] && cat "$STATE_FILE" || echo '{"failures":{}}'
}
save_state() { echo "$1" > "$STATE_FILE"; }

ISSUES=()

# ── Disk check ───────────────────────────────────────────────────────────────
disk_check() {
    local info
    info=$(ssh "$VPS" "df -h / /data 2>/dev/null || df -h /" 2>&1) || return 1
    while IFS read -r line; do
        local pct mnt
        pct=$(echo "$line" | awk 'NR>1 {gsub(/%/,"",$5); print $5}')
        mnt=$(echo "$line" | awk 'NR>1 {print $6}')
        [ -z "$pct" ] && continue
        if [ "${pct:-0}" -ge "$ALERT_DISK_PCT" ] 2>/dev/null; then
            ISSUES+=("⚠️ Disk $mnt at ${pct}%")
        fi
    done <<< "$info"
}

# ── OpenResty check ──────────────────────────────────────────────────────────
web_check() {
    local code
    code=$(ssh "$VPS" "curl -sf --max-time 5 -o /dev/null -w '%{http_code}' https://militarymanuals.ai/ 2>/dev/null" 2>&1)
    if [ "$code" != "200" ]; then
        ISSUES+=("🔴 OpenResty HTTP $code (expected 200)")
    fi
}

# ── RAG API port check ───────────────────────────────────────────────────────
api_check() {
    local resp
    resp=$(ssh "$VPS" "curl -sf --max-time 5 http://127.0.0.1:8104/health 2>/dev/null" 2>&1)
    if [ -z "$resp" ]; then
        ISSUES+=("🔴 RAG API port 8104 not responding")
    fi
}

# ── API vector count check ───────────────────────────────────────────────────
vectors_check() {
    local api_key
    api_key=$(sed -n '2p' ./.openclaw/workspace/secrets/vps-api-key.txt 2>/dev/null)
    local resp
    resp=$(ssh "$VPS" "curl -sf --max-time 10 https://militarymanuals.ai/api/collections \
        -H 'Authorization: Bearer ${api_key}' 2>/dev/null" 2>&1) || return 1
    local total
    total=$(echo "$resp" | python3 -c "import sys,json; data=json.load(sys.stdin); print(sum(c.get('vector_count',0) for c in data))" 2>/dev/null || echo "0")
    if [ "${total:-0}" -eq 0 ]; then
        ISSUES+=("🔴 /api/collections returned 0 total vectors — DB may be empty")
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────────
log "VPS weekly health check"

disk_check
web_check
api_check
vectors_check

NOW=$(date +%s)
STATE=$(load_state)

if [ ${#ISSUES[@]} -eq 0 ]; then
    log "All checks OK"
    # Clear all failure states
    echo '{"failures":{}}' > "$STATE_FILE"
else
    log "ISSUES: ${ISSUES[*]}"
    # Alert only on new failures or after cooldown
    for issue in "${ISSUES[@]}"; do
        local check_name
        check_name=$(echo "$issue" | cut -d: -f1 | tr -d ' ')
        local last_alert
        last_alert=$(echo "$STATE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['failures'].get('$check_name',0))" 2>/dev/null || echo "0")
        local elapsed=$(( NOW - last_alert ))
        if [ "$last_alert" -eq 0 ] || [ "$elapsed" -ge "$ALERT_COOLDOWN" ]; then
            send_tg "🚨 VPS Health Alert\n${ISSUES[*]}"
            STATE=$(echo "$STATE" | python3 -c "import sys,json; d=json.load(sys.stdin); d['failures']['$check_name']=$NOW; print(json.dumps(d))")
        fi
    done
    save_state "$STATE"
fi
