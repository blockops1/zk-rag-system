#!/usr/bin/env bash
# run_pipeline_e.sh — Run Pipeline E (Merkle tree builder) and update the registry.
#
# Usage:
#   ./run_pipeline_e.sh --doc-id <doc_id>           Single doc
#   ./run_pipeline_e.sh --batch [--force]           All docs with chunks
#
# Requires DEPLOYER_KEY env var (sourced from ./.env).
# Pipeline E binary is at: ./zk-circuit/target/debug/pipeline_e

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ZK_CIRCUIT_DIR="./zk-circuit"
SOURCE_ENV="./.env"

if [[ -f "$SOURCE_ENV" ]]; then
    set -a
    source "$SOURCE_ENV"
    set +a
fi

PIPELINE_E="$ZK_CIRCUIT_DIR/target/release/pipeline_e"
UPDATE_REG="$SCRIPT_DIR/update_registry.py"

if [[ ! -x "$PIPELINE_E" ]]; then
    echo "ERROR: Pipeline E binary not found at $PIPELINE_E"
    echo "Build it with: cd $ZK_CIRCUIT_DIR && cargo build -p pipeline_e"
    exit 1
fi

# ── Parse args ────────────────────────────────────────────────────────────────

BATCH_MODE=false
FORCE_FLAG=""
DOC_ID=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --batch)
            BATCH_MODE=true
            shift
            ;;
        --force)
            FORCE_FLAG="--force"
            shift
            ;;
        --doc-id)
            DOC_ID="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

CHUNKS_DIR="../data/chunks"
OUT_DIR="../data/merkleTrees"

if [[ "$BATCH_MODE" == "true" ]]; then
    echo "=== Pipeline E (batch mode) ==="

    # Run batch — output goes to stderr, one line per doc
    # We need to capture doc_ids that succeeded so we can update registry
    "$PIPELINE_E" --batch \
        --chunks-dir "$CHUNKS_DIR" \
        --out-dir "$OUT_DIR" \
        $FORCE_FLAG \
        2>&1 | tee /tmp/pipeline_e_batch.log

    # Extract doc_ids from successful tree writes
    # Format: "[<doc_id>] Wrote /path/to/<doc_id>_tree.json"
    echo ""
    echo "=== Updating registry for batch ==="
    grep "Wrote" /tmp/pipeline_e_batch.log | while read -r line; do
        # Extract doc_id from path like "../data/merkleTrees/<doc_id>_tree.json"
        DOC_ID_FROM_LINE=$(echo "$line" | grep -oP '(?<=/)\w{64}(?=_tree\.json)')
        if [[ -n "$DOC_ID_FROM_LINE" ]]; then
            python3 "$UPDATE_REG" "$DOC_ID_FROM_LINE" || true
        fi
    done

    echo "=== Batch complete ==="

elif [[ -n "$DOC_ID" ]]; then
    echo "=== Pipeline E: $DOC_ID ==="

    PIPELINE_E_OUTPUT=$("$PIPELINE_E" \
        --doc-id "$DOC_ID" \
        --chunks-dir "$CHUNKS_DIR" \
        --out-dir "$OUT_DIR" \
        $FORCE_FLAG \
        2>&1)
    PIPELINE_E_RC=$?

    echo "$PIPELINE_E_OUTPUT"

    if [[ $PIPELINE_E_RC -ne 0 ]]; then
        echo "ERROR: Pipeline E exited with code $PIPELINE_E_RC — skipping registry update"
        exit 1
    fi

    # Update registry only if Pipeline E succeeded
    # Check that a tree file was actually written (not just "OK" from --force skip)
    if echo "$PIPELINE_E_OUTPUT" | grep -q "Wrote "; then
        python3 "$UPDATE_REG" "$DOC_ID"
    else
        echo "SKIP: No tree file written — not updating registry"
    fi

else
    echo "Usage: $0 --doc-id <doc_id>   OR   $0 --batch [--force]"
    exit 1
fi
