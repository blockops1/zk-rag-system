#!/bin/bash
# run_usage_report.sh — Daily RAG API usage report
# Cron: 8 AM ET daily
# Fetches /metrics from VPS, formats and sends Telegram report

set -euo pipefail

VPS="deruyter@militarymanuals.ai"
VPS_KEY="$HOME/.ssh/id_ed25519"
TELEGRAM_TARGET="374999219"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

send_tg() {
    openclaw message send --channel telegram --target "$TELEGRAM_TARGET" \
        --message "$1" --silent 2>/dev/null || true
}

# ── Fetch metrics ─────────────────────────────────────────────────────────────
METRICS_JSON=$(ssh -i "$VPS_KEY" -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$VPS" \
    "curl -sf http://127.0.0.1:8104/metrics" 2>/dev/null) || {
    log "ERROR: Failed to fetch /metrics from VPS"
    exit 1
}

TOTAL=$(echo "$METRICS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total_requests',0))")
ERRORS=$(echo "$METRICS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('errors',0))")
AVG_LAT=$(echo "$METRICS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('avg_latency_ms',0))")
UNIQUE_IPS=$(echo "$METRICS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('unique_ip_count',0))")

ENDPOINTS=$(echo "$METRICS_JSON" | python3 -c "
import sys,json,operator
d=json.load(sys.stdin)
counts=d.get('endpoint_counts',{})
sorted_ep=sorted(counts.items(), key=operator.itemgetter(1), reverse=True)
lines=[]
for ep,cnt in sorted_ep[:6]:
    lines.append(f'  {ep}  {cnt}')
print('\n'.join(lines) if lines else '  (none)')
" 2>/dev/null)

QUERY_STATS_JSON=$(ssh -i "$VPS_KEY" -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$VPS" \
    "curl -sf http://127.0.0.1:8104/query_stats" 2>/dev/null) || true

TOP_DOCS=$(echo "$QUERY_STATS_JSON" | python3 -c "
import sys,json
d=json.load(sys.stdin) if '$QUERY_STATS_JSON' else {}
docs=d.get('top_docs',[])
if not docs: print('  (no queries today)')
else:
    for item in docs[:5]:
        title=item.get('title','?')[:45]
        count=item.get('count',0)
        print(f'  {count:>2}q  {title}')
" 2>/dev/null || echo "  (unavailable)")

COLLECTIONS_JSON=$(ssh -i "$VPS_KEY" -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$VPS" \
    "curl -sf http://127.0.0.1:8104/collections" 2>/dev/null) || true

COLLECTION_INFO=$(echo "$COLLECTIONS_JSON" | python3 -c "
import sys,json
try:
    data=json.load(sys.stdin)
    for c in data:
        vc=c.get('vector_count',0)
        docs=len(c.get('doc_ids',[]))
        print(f\"  {c['name']} ({vc:,} vectors, {docs} docs)\")
except: print('  (unavailable)')
" 2>/dev/null || echo "  (unavailable)")

YESTERDAY=$(python3 -c "
from datetime import date, timedelta
d=date.today()-timedelta(days=1)
print(d.strftime('%b %d %Y'))
")

REPORT="📊 RAG API Daily Report — $YESTERDAY
━━━━━━━━━━━━━━━━━━━━
Queries:     $TOTAL
Unique IPs:  $UNIQUE_IPS
Errors:      $ERRORS
Avg latency: ${AVG_LAT}ms
━━━━━━━━━━━━━━━━━━━━
Endpoints:
$ENDPOINTS
━━━━━━━━━━━━━━━━━━━━
Top Docs:
$TOP_DOCS
━━━━━━━━━━━━━━━━━━━━
Collections:
$COLLECTION_INFO"

log "Sending report ($TOTAL queries)"
send_tg "$REPORT"
