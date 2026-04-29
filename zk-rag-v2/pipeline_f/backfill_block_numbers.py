#!/usr/bin/env python3
"""
Backfill block_number for emitted documents that have tx_hash but missing block_number.

Two passes:
  1. On-chain lookup via `cast tx <tx_hash>` for docs with valid tx_hash
  2. Re-emit via `emit_all.py --force` for docs where tx_hash is "unknown"

Usage:
    python3 backfill_block_numbers.py [--dry-run] [--rpc-url <url>]
"""

import argparse
import json
import subprocess
import time
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

REGISTRY_PATH = Path("$DATA_DIR/registry.json")
RPC_URL = "https://horizen-testnet.rpc.caldera.xyz"
CAST_BIN = "$FOUNDRY_BIN/cast"


def load_registry() -> tuple[dict, dict]:
    with open(REGISTRY_PATH) as f:
        data = json.load(f)
    index = {d["doc_id"]: i for i, d in enumerate(data["documents"])}
    return data, index


def save_registry(data: dict) -> None:
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.rename(REGISTRY_PATH)


def get_block_number_from_chain(tx_hash: str) -> int | None:
    """Query on-chain receipt for blockNumber."""
    try:
        result = subprocess.run(
            [CAST_BIN, "tx", tx_hash, "--rpc-url", RPC_URL],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("blockNumber"):
                return int(line.split()[1])
        return None
    except Exception:
        return None


def re_emit_doc(doc_id: str, dry_run: bool) -> tuple[str, str]:
    """
    Re-emit a single doc via emit_all.py --force to get a fresh tx hash + block number.
    Returns (tx_hash, block_number).
    """
    cmd = [
        "python3", "emit_all.py",
        "--doc-id", doc_id,
        "--verify",
        "--force",
    ]
    if dry_run:
        cmd.append("--dry-run")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            timeout=300,
        )
        output = result.stdout + result.stderr
    except Exception:
        return "", ""

    # Parse tx_hash and block_number from emit_all output
    # Format: "[EMIT] <doc_id> tx=<tx_hash>" or "[EMIT] <doc_id> tx=unknown"
    tx_hash = ""
    block_number = ""

    for line in output.splitlines():
        line = line.strip()
        if line.startswith("tx_hash:"):
            tx_hash = line.split(":", 1)[1].strip()
        elif line.startswith("block_number:"):
            block_number = line.split(":", 1)[1].strip()

    # Also check the verify log
    script_dir = Path(__file__).parent
    if not tx_hash or not block_number:
        verify_log = script_dir / "logs" / f"emit_all_verify_{doc_id}.log"
        if verify_log.exists():
            for line in verify_log.read_text().splitlines():
                line = line.strip()
                if line.startswith("tx_hash:"):
                    tx_hash = line.split(":", 1)[1].strip()
                elif line.startswith("block_number:"):
                    block_number = line.split(":", 1)[1].strip()

    return tx_hash, block_number


def main():
    parser = argparse.ArgumentParser(description="Backfill block_number for emitted docs")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without making changes")
    parser.add_argument("--rpc-url", default=RPC_URL,
                        help="RPC URL for on-chain lookups")
    parser.add_argument("--skip-unknown", action="store_true",
                        help="Skip docs with tx_hash='unknown' (only backfill via cast)")
    args = parser.parse_args()

    registry_data, doc_id_index = load_registry()

    emitted = [
        d for d in registry_data["documents"]
        if d.get("emitted_testnet", {}).get("status") == "emitted"
    ]
    print(f"Found {len(emitted)} emitted documents")

    # ── Pass 1: docs with valid tx_hash ───────────────────────────────────────
    valid_tx: list[dict] = []
    unknown_tx: list[dict] = []

    for d in emitted:
        et = d["emitted_testnet"]
        tx = et.get("tx_hash", "")
        if tx and tx != "unknown":
            valid_tx.append(d)
        else:
            unknown_tx.append(d)

    print(f"\nPass 1 — on-chain lookups: {len(valid_tx)} docs with valid tx_hash")
    updated_cast = 0
    for d in valid_tx:
        doc_id = d["doc_id"]
        tx_hash = d["emitted_testnet"]["tx_hash"]
        existing_bn = d["emitted_testnet"].get("block_number", 0)

        if args.dry_run:
            bn = get_block_number_from_chain(tx_hash)
            print(f"  [DRY] {doc_id[:16]}... tx={tx_hash[:16]}... → block={bn} (existing={existing_bn})")
            continue

        bn = get_block_number_from_chain(tx_hash)
        if bn is None:
            print(f"  [FAIL] {doc_id[:16]}... tx={tx_hash[:16]}... → could not fetch from chain")
            continue

        entry = registry_data["documents"][doc_id_index[doc_id]]
        entry["emitted_testnet"]["block_number"] = bn
        print(f"  [OK]   {doc_id[:16]}... tx={tx_hash[:16]}... → block={bn}")
        updated_cast += 1
        time.sleep(0.5)  # rate-limit RPC

    # ── Pass 2: docs with unknown tx_hash ─────────────────────────────────────
    if not args.skip_unknown:
        print(f"\nPass 2 — re-emit for unknown tx_hash: {len(unknown_tx)} docs")
        for d in unknown_tx:
            doc_id = d["doc_id"]
            print(f"  [RE-EMIT] {doc_id[:16]}... (tx_hash was 'unknown', need fresh emit)")
            if args.dry_run:
                print(f"  [DRY] Would re-emit {doc_id[:16]}...")
                continue

            tx_hash, block_number = re_emit_doc(doc_id, dry_run=False)

            # emit_all.py saves the updated registry directly.
            # Re-load so our in-memory copy stays in sync.
            registry_data, doc_id_index = load_registry()

            entry = registry_data["documents"][doc_id_index[doc_id]]
            et = entry.get("emitted_testnet", {})
            tx_hash = et.get("tx_hash", "")
            block_number = str(et.get("block_number", ""))

            if tx_hash and tx_hash != "unknown" and block_number and block_number != "0":
                print(f"  [OK]   {doc_id[:16]}... → tx={tx_hash[:16]}... block={block_number}")
            else:
                print(f"  [FAIL] {doc_id[:16]}... re-emit may have failed, check registry")
    else:
        print(f"\nPass 2 — skipped ({len(unknown_tx)} docs with unknown tx_hash)")

    # ── Save ───────────────────────────────────────────────────────────────────
    if not args.dry_run:
        save_registry(registry_data)
        print(f"\nRegistry saved. Updated {updated_cast} block_numbers via cast.")
    else:
        print("\nDry run — no changes saved.")


if __name__ == "__main__":
    main()
