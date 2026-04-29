#!/bin/bash
# daily_git_push.sh — Auto-commit and push tracked changes in $REPO_DIR
# Runs silently if nothing to commit

set -uo pipefail

RAG_DIR="$REPO_DIR"
LOG="${RAG_DIR}/logs/daily-git-push.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

log() { echo "[$TIMESTAMP] $1" >> "$LOG"; }

cd "$RAG_DIR" || exit 1

# Check for uncommitted changes
if git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null; then
    log "No changes — nothing to commit"
    exit 0
fi

# Show what changed
CHANGES=$(git status --short)
log "Changes detected: $CHANGES"

# Add all tracked changes and commit with timestamp
git add -A
git commit -m "auto-commit: $(date '+%Y-%m-%d %H:%M')" >> "$LOG" 2>&1

# Push
git push origin main >> "$LOG" 2>&1
EXIT=$?

if [ $EXIT -eq 0 ]; then
    log "Push successful"
else
    log "Push FAILED (exit $EXIT)"
fi

exit $EXIT
