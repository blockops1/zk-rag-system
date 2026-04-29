#!/bin/bash
# run_session_cleanup.sh — Hourly session + temp file cleanup
# Cron: top of every hour
# Cleans: old session DBs, stale temp files, old lock files

set -euo pipefail

LOG_DIR="$REPO_DIR/logs"
HERMES_HOME="${HOME}/.openclaw"
AGE_CUTOFF=86400   # 24 hours — sessions older than this are deleted

mkdir -p "$LOG_DIR"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_DIR/session_cleanup.log"; }

# ── Session DB cleanup ──────────────────────────────────────────────────────
# Sessions older than 24h are abandoned — remove them to keep DB small
cleanup_sessions() {
    local session_dir="${HERMES_HOME}/sessions"
    [ ! -d "$session_dir" ] && return

    local count=0
    local now=$(date +%s)
    for f in "$session_dir"/*.db; do
        [ -f "$f" ] || continue
        local age=$(( now - $(stat -c %Y "$f") ))
        if [ "$age" -gt "$AGE_CUTOFF" ]; then
            rm -f "$f"
            count=$((count + 1))
        fi
    done
    [ "$count" -gt 0 ] && log "Removed $count stale session DBs"
}

# ── Temp file cleanup ────────────────────────────────────────────────────────
cleanup_temp() {
    local count=0

    # Old nightly pipeline temp files
    for f in $REPO_DIR/logs/nightly-*.tmp \
             $REPO_DIR/logs/pipeline_*.tmp; do
        [ -f "$f" ] || continue
        local age=$(($(date +%s) - $(stat -c %Y "$f" 2>/dev/null || echo 0)))
        if [ "$age" -gt 172800 ]; then  # 48h
            rm -f "$f"; count=$((count + 1))
        fi
    done

    # Stale lock files (no process holding them)
    for lock in /data/rag/.lock.*; do
        [ -f "$lock" ] || continue
        local pid
        pid=$(cat "$lock" 2>/dev/null || echo "")
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$lock"
            log "Removed stale lock: $lock (PID $pid gone)"
            count=$((count + 1))
        fi
    done

    [ "$count" -gt 0 ] && log "Removed $count temp/stale files"
}

# ── Run ─────────────────────────────────────────────────────────────────────
cleanup_sessions
cleanup_temp
