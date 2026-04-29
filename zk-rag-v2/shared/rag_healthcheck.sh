#!/usr/bin/env bash
# rag_healthcheck.sh — RAG system health monitor
# Runs every 30 min via system cron.
# Alerts on failure (once per incident). Daily "all green" digest at 08:00 ET.
#
# Checks:
#   1. VPS API health (HTTPS + collection = army-docs)
#   2. Public query returns results
#   3. Local services running (api_server.py, upload_server.py)
#   4. Last nightly pipeline succeeded
#   5. Storage headroom (/data < 80%)
#   6. SSL cert expiry > 30 days

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
VPS_API="https://militarymanuals.ai"
API_KEY_FILE="$ZK_RAG_HOME/.openclaw/workspace/secrets/vps-api-key.txt"
NIGHTLY_LOG_DIR="$REPO_DIR/logs"
STATE_FILE="$REPO_DIR/logs/healthcheck_state.json"
DAILY_DIGEST_HOUR=8   # 08:00 ET
OPENCLAW_CMD="openclaw"
ALERT_COOLDOWN_SEC=7200   # Re-alert every 2 hours if check still failing

# ── Helpers ───────────────────────────────────────────────────────────────────
API_KEY=""
[ -f "$API_KEY_FILE" ] && API_KEY=$(cat "$API_KEY_FILE")

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

send_telegram() {
    local msg="$1"
    $OPENCLAW_CMD message send --channel telegram --message "$msg" 2>/dev/null || true
}

# State file: JSON { "failures": {"check_name": <last_alerted_epoch>}, "last_digest_date": "2026-03-21" }
# failures value = 0 (never alerted) or unix timestamp of last alert
# Re-alert if still failing after ALERT_COOLDOWN_SEC seconds
load_state() {
    if [ -f "$STATE_FILE" ]; then
        cat "$STATE_FILE"
    else
        echo '{"failures":{},"last_digest_date":""}'
    fi
}

save_state() {
    echo "$1" > "$STATE_FILE"
}

# ── Checks ────────────────────────────────────────────────────────────────────

check_vps_api() {
    local response
    response=$(curl -sf --max-time 15 "${VPS_API}/health" 2>/dev/null) || {
        echo "FAIL: VPS API unreachable (curl failed)"
        return 1
    }
    # Response is plain text: 'RAG API -- see /docs/ for documentation'
    # Internal health is JSON — check via /api/collections instead
    local collection
    collection=$(curl -sf --max-time 15 "${VPS_API}/api/collections" \
        -H "Authorization: Bearer $API_KEY" 2>/dev/null \
        | python3 -c "
import json,sys
data=json.load(sys.stdin)
for c in data:
    if c['name'] == 'army-docs':
        print(c['vector_count'])
        sys.exit(0)
sys.exit(1)
" 2>/dev/null) || {
        echo "FAIL: army-docs collection not found or API error"
        return 1
    }
    if [ -z "$collection" ] || [ "$collection" -lt 100000 ]; then
        echo "FAIL: army-docs vector count too low: $collection"
        return 1
    fi
    echo "OK: army-docs $collection vectors"
}

check_public_query() {
    local result
    result=$(curl -sf --max-time 20 -X POST "${VPS_API}/api/query" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $API_KEY" \
        -d '{"query": "infantry tactics field manual", "top_k": 1}' 2>/dev/null \
        | python3 -c "
import json,sys
data=json.load(sys.stdin)
# API returns a top-level array of results
results=data if isinstance(data,list) else data.get('results',[])
if results and results[0].get('score',0) > 0.3:
    print(f\"score={results[0]['score']:.3f}\")
else:
    sys.exit(1)
" 2>/dev/null) || {
        echo "FAIL: public query returned no results or low score"
        return 1
    }
    echo "OK: query $result"
}

check_local_services() {
    local failures=()
    pgrep -f "api_server.py" > /dev/null 2>&1 || failures+=("api_server.py not running")
    pgrep -f "upload_server.py" > /dev/null 2>&1 || failures+=("upload_server.py not running")
    if [ ${#failures[@]} -gt 0 ]; then
        echo "FAIL: ${failures[*]}"
        return 1
    fi
    echo "OK: api_server + upload_server running"
}

check_pipeline() {
    # Find most recent nightly report
    local report
    report=$(ls -t "$NIGHTLY_LOG_DIR"/nightly-report-*.json 2>/dev/null | head -1)
    if [ -z "$report" ]; then
        echo "WARN: no nightly report found"
        return 0  # Warn only, not a hard fail
    fi
    local age_hours
    age_hours=$(python3 -c "
import os,time
age = time.time() - os.path.getmtime('$report')
print(int(age/3600))
" 2>/dev/null)
    if [ "$age_hours" -gt 26 ]; then
        echo "FAIL: last pipeline report is ${age_hours}h old (pipeline may not have run)"
        return 1
    fi
    local ingest_exit push_exit
    ingest_exit=$(python3 -c "import json; d=json.load(open('$report')); print(d['ingest_exit'])" 2>/dev/null)
    push_exit=$(python3 -c "import json; d=json.load(open('$report')); print(d['push_exit'])" 2>/dev/null)
    if [ "$ingest_exit" != "0" ]; then
        echo "FAIL: last pipeline ingest_exit=$ingest_exit"
        return 1
    fi
    if [ "$push_exit" != "0" ]; then
        echo "WARN: last pipeline push_exit=$push_exit (ingest OK, push failed)"
        # Warn only — ingest is more critical
    fi
    echo "OK: pipeline ingest=0 push=$push_exit (${age_hours}h ago)"
}

check_storage() {
    local pct
    pct=$(df /data | tail -1 | awk '{print $5}' | tr -d '%')
    if [ "$pct" -gt 80 ]; then
        echo "FAIL: /data at ${pct}% — above 80% threshold"
        return 1
    fi
    echo "OK: /data at ${pct}%"
}

check_vps_disk() {
    local pct
    pct=$(ssh deruyter@militarymanuals.ai "df / | tail -1 | awk '{print \$5}'" 2>/dev/null | tr -d '%') || {
        echo "WARN: could not check VPS disk (SSH failed)"
        return 0
    }
    if [ "$pct" -gt 80 ]; then
        echo "FAIL: VPS / at ${pct}% — above 80% threshold"
        return 1
    fi
    echo "OK: VPS / at ${pct}%"
}

check_ssl() {
    local days_left
    days_left=$(echo | openssl s_client -connect militarymanuals.ai:443 \
        -servername militarymanuals.ai 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null \
        | python3 -c "
import sys,datetime
line=sys.stdin.read().strip()
date_str=line.replace('notAfter=','')
exp=datetime.datetime.strptime(date_str,'%b %d %H:%M:%S %Y %Z')
print((exp - datetime.datetime.utcnow()).days)
" 2>/dev/null) || {
        echo "WARN: could not check SSL cert"
        return 0
    }
    if [ "$days_left" -lt 30 ]; then
        echo "FAIL: SSL cert expires in ${days_left} days"
        return 1
    fi
    echo "OK: SSL cert valid ${days_left} days"
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    local state
    state=$(load_state)
    local current_date
    current_date=$(TZ=America/New_York date '+%Y-%m-%d')
    local current_hour
    current_hour=$(TZ=America/New_York date '+%-H')

    local all_ok=true
    local fail_msgs=()
    local ok_msgs=()

    # Run all checks
    declare -A checks=(
        [vps_api]=check_vps_api
        [public_query]=check_public_query
        [local_services]=check_local_services
        [pipeline]=check_pipeline
        [storage]=check_storage
        [vps_disk]=check_vps_disk
        [ssl]=check_ssl
    )

    declare -A check_labels=(
        [vps_api]="VPS API"
        [public_query]="Public Query"
        [local_services]="Local Services"
        [pipeline]="Nightly Pipeline"
        [storage]="Local Storage"
        [vps_disk]="VPS Disk"
        [ssl]="SSL Cert"
    )

    for check_name in vps_api public_query local_services pipeline storage vps_disk ssl; do
        local fn="${checks[$check_name]}"
        local label="${check_labels[$check_name]}"
        local result
        if result=$($fn 2>&1); then
            ok_msgs+=("✅ $label: $result")
            # Clear prior failure state for this check
            state=$(echo "$state" | python3 -c "
import json,sys
d=json.load(sys.stdin)
d['failures'].pop('$check_name',None)
print(json.dumps(d))
")
        else
            all_ok=false
            fail_msgs+=("❌ $label: $result")
            # Alert on first failure, then re-alert after cooldown
            local now last_alerted
            now=$(date +%s)
            last_alerted=$(echo "$state" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(d['failures'].get('$check_name', 0))
")
            local elapsed=$(( now - last_alerted ))
            if [ "$last_alerted" = "0" ] || [ "$elapsed" -ge "$ALERT_COOLDOWN_SEC" ]; then
                send_telegram "🚨 RAG Health Alert
❌ $label: $result
$(date '+%Y-%m-%d %H:%M ET')"
                state=$(echo "$state" | python3 -c "
import json,sys
d=json.load(sys.stdin)
d['failures']['$check_name']=$now
print(json.dumps(d))
")
            fi
        fi
    done

    # Daily digest at 08:00 ET (if all green + haven't sent today)
    local last_digest
    last_digest=$(echo "$state" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(d.get('last_digest_date',''))
")
    if $all_ok && [ "$current_hour" -eq "$DAILY_DIGEST_HOUR" ] && [ "$last_digest" != "$current_date" ]; then
        send_telegram "✅ RAG System Healthy — $(date '+%Y-%m-%d 08:00 ET')
$(printf '%s\n' "${ok_msgs[@]}")"
        state=$(echo "$state" | python3 -c "
import json,sys
d=json.load(sys.stdin)
d['last_digest_date']='$current_date'
print(json.dumps(d))
")
    fi

    save_state "$state"

    # Log summary
    if $all_ok; then
        log "All checks passed"
    else
        log "FAILURES: ${fail_msgs[*]}"
    fi
}

main "$@"
