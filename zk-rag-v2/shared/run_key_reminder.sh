#!/bin/bash
# run_key_reminder.sh — Quarterly API key rotation reminder
# Cron: 1st of Jan, Apr, Jul, Oct 9 AM ET

set -euo pipefail

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

send_tg() {
    openclaw message send --channel telegram --target 374999219 \
        --message "$1" --silent 2>/dev/null || true
}

MSG="🔑 Quarterly API Key Rotation Reminder

Time to rotate the militarymanuals.ai API key.

Steps:
  1. Generate new key: openssl rand -hex 32
  2. Update /etc/nginx/conf.d/rag-api.conf on VPS (api_key in Lua block)
  3. Update $ZK_RAG_HOME/.openclaw/workspace/secrets/vps-api-key.txt on DeRuyter
  4. Update any agents/scripts using the key
  5. Test: curl -H 'Authorization: Bearer <key>' https://militarymanuals.ai/api/collections
  6. Reload OpenResty: sudo systemctl reload openresty"

log "Key reminder sent"
send_tg "$MSG"
