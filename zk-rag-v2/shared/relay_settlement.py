#!/usr/bin/env python3
"""
EIP-3009 Settlement Relay — Daily sweep of pending authorizations.

Reads /tmp/eip3009_pending.jsonl, submits each EIP-3009 transferWithAuthorization
to the USDC contract on Base, then removes settled entries from the queue.

Usage:
    python3 relay_settlement.py --dry-run    # log what would be submitted, don't send txns
    python3 relay_settlement.py --force      # submit for real (requires RPC and key)

Requirements:
    - eth_account (for sign_transaction if using a local key)
    - cast or RPC endpoint for submit_pending_authorization calls
    - BASE_RPC_URL env var (Alchemy free tier works)
    - RELAYER_PRIVATE_KEY env var (optional — needed only for immediate relay; daily sweep
      can also be submitted via the --raw flag which outputs the calldata)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("relay_settlement")

# ─── Constants ────────────────────────────────────────────────────────────────

USDC_CONTRACT = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
BASE_CHAIN_ID = 8453
NETWORK_SPEC = "eip155:8453"
RECEIVING_ADDRESS = os.environ.get("PAID_DOWNLOAD_RECEIVING_ADDRESS", "").lower()

# Queue file written by x402_paid_download.py on each verified payment
QUEUE_PATH = Path(os.environ.get("QUEUE_PATH", "/tmp/eip3009_pending.jsonl"))

# Settlement threshold: amounts below this get held for daily sweep,
# amounts above this are relayed immediately (if immediate relay is implemented)
DAILY_SWEEP_THRESHOLD_MICRO_USDC = 100_000  # $0.10 — always daily for now

# Base RPC — set BASE_RPC_URL env var
BASE_RPC_URL = os.environ.get("BASE_RPC_URL", "")
# Optional: private key for submitting txs (if not using a hardware wallet / KMS)
RELAYER_PRIVATE_KEY = os.environ.get("RELAYER_PRIVATE_KEY", "")


# ─── Logging ────────────────────────────────────────────────────────────────

def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ─── Queue Operations ───────────────────────────────────────────────────────

def read_queue() -> list[dict]:
    """Read all pending authorization entries from the queue file."""
    if not QUEUE_PATH.exists():
        return []
    entries = []
    with open(QUEUE_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("Skipping malformed queue line: %s", e)
    return entries


def write_queue(entries: list[dict]) -> None:
    """Rewrite the queue file with the remaining (unsettled) entries."""
    with open(QUEUE_PATH, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    logger.info("Queue rewritten: %d entries remaining", len(entries))


# ─── cast RPC helpers ───────────────────────────────────────────────────────

def cast_rpc(method: str, params: Optional[list] = None, timeout: int = 30) -> dict:
    """Call Base RPC via cast rpc."""
    if not BASE_RPC_URL:
        raise RuntimeError("BASE_RPC_URL env var not set")
    cmd = [
        "cast", "rpc",
        "--rpc-url", BASE_RPC_URL,
        method,
    ]
    if params:
        for p in params:
            cmd.extend(["--json" if isinstance(p, dict) else str(p)])
    # Simpler approach: use eth_call for read-only, eth_sendTransaction for writes
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=json.dumps(params) if params else None,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cast rpc failed: {result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"cast rpc returned non-JSON: {result.stdout}")


def eth_call(to: str, data: str, timeout: int = 30) -> str:
    """Make an eth_call and return the returndata hex."""
    result = cast_rpc(
        "eth_call",
        [{"to": to, "data": data}, "latest"],
        timeout=timeout,
    )
    return result.get("result", "")


def eth_send_raw_transaction(raw_tx: str, timeout: int = 60) -> str:
    """Submit a signed raw transaction and return the tx hash."""
    result = cast_rpc(
        "eth_sendRawTransaction",
        [raw_tx],
        timeout=timeout,
    )
    if "error" in result:
        raise RuntimeError(f"eth_sendRawTransaction error: {result['error']}")
    return result.get("result", "")


# ─── EIP-3009 Calldata Builder ───────────────────────────────────────────────

def build_transfer_with_auth_calldata(
    from_addr: str,
    to_addr: str,
    value: int,
    valid_after: int,
    valid_before: int,
    nonce: str,
    signature: str,
) -> str:
    """
    Build the calldata for USDC.transferWithAuthorization().

    Signature must be provided because the function validates the signature
    on-chain before transferring (per EIP-3009 spec).

    Returns: hex string (0x...) of the ABI-encoded function call.
    """
    # EIP-3009 function selector: transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,bytes)
    # We use cast to build the calldata properly
    cmd = [
        "cast", "calldata",
        "transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,bytes)",
        from_addr,
        to_addr,
        str(value),
        str(valid_after),
        str(valid_before),
        nonce,      # bytes32
        signature,   # bytes (65-byte sig or 64-byte vsig)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def submit_pending_auth(
    entry: dict,
    dry_run: bool = False,
) -> Optional[str]:
    """
    Submit a single pending EIP-3009 authorization to the USDC contract.

    Returns tx_hash if submitted, None if skipped/dry-run.
    """
    doc_id = entry.get("doc_id", "?")
    amount = entry.get("amount_micro_usdc", 0)
    from_addr = entry.get("from", "")
    to_addr = entry.get("to", "")
    value = int(entry.get("value", amount))  # value should match amount
    valid_after = int(entry.get("validAfter", 0))
    valid_before = int(entry.get("validBefore", 0))
    nonce = entry.get("nonce", "")
    signature = entry.get("signature", "")

    # Validate
    if not from_addr or not to_addr or not nonce or not signature:
        logger.error("Entry %s is missing required fields — skipping", doc_id)
        return None

    # Check if already expired
    now = int(time.time())
    if valid_before < now:
        logger.warning("Entry %s already expired (validBefore=%d < now=%d) — skipping", doc_id, valid_before, now)
        return None

    logger.info(
        "Submitting: doc_id=%s amount=%d from=%s nonce=%s",
        doc_id[:16], amount, from_addr, nonce[:16],
    )

    if dry_run:
        logger.info("[DRY RUN] Would submit transferWithAuthorization")
        return None

    try:
        calldata = build_transfer_with_auth_calldata(
            from_addr, to_addr, value, valid_after, valid_before, nonce, signature
        )
    except subprocess.CalledProcessError as e:
        logger.error("Failed to build calldata: %s", e.stderr)
        return None

    # Build the transaction
    # We need the nonce of the relayer wallet, gas price, etc.
    # For now use eth_sendTransaction via cast if we have a funded relayer wallet
    # Alternative: output raw calldata for hardware wallet submission
    logger.info("Calldata: %s", calldata[:80] + "...")

    if not RELAYER_PRIVATE_KEY:
        # Output the full submission payload so it can be submitted manually or via HW wallet
        logger.warning(
            "RELAYER_PRIVATE_KEY not set — cannot submit tx automatically.\n"
            "  Use --raw to output submission payload for manual relay:\n"
            "  to=%s data=%s",
            USDC_CONTRACT, calldata
        )
        return None

    # Get relayer address
    from eth_account import Account
    relayer = Account.from_key(RELAYER_PRIVATE_KEY)
    relayer_addr = relayer.address
    logger.info("Relayer address: %s", relayer_addr)

    # Get nonce
    nonce_result = cast_rpc("eth_getTransactionCount", [relayer_addr, "pending"])
    nonce_count = int(nonce_result["result"], 16)

    # Get gas price
    gas_result = cast_rpc("eth_gasPrice", [])
    gas_price = int(gas_result["result"], 16)

    # Estimate gas
    tx_params = {
        "from": relayer_addr,
        "to": USDC_CONTRACT,
        "data": calldata,
        "gas": hex(150_000),  # EIP-3009 relay typically ~100k-120k gas
        "gasPrice": hex(gas_price),
        "nonce": hex(nonce_count),
        "chainId": BASE_CHAIN_ID,
    }

    # Build, sign, and send
    signed = relayer.sign_transaction(tx_params)
    raw_tx = signed.rawTransaction.hex()
    tx_hash = eth_send_raw_transaction(raw_tx)
    logger.info("TX submitted: %s", tx_hash)
    return tx_hash


# ─── Main Sweep ──────────────────────────────────────────────────────────────

def sweep(dry_run: bool = False, force: bool = False) -> int:
    """
    Main daily sweep: read queue, submit each authorization, rewrite queue.
    """
    if not force and dry_run:
        logger.info("DRY RUN — no transactions will be submitted")
    elif not force:
        logger.info("Dry-run only. Use --force to submit for real.")

    entries = read_queue()
    if not entries:
        logger.info("Queue is empty — nothing to settle.")
        return 0

    logger.info("Read %d pending authorization(s) from queue", len(entries))

    submitted = []
    skipped = []

    for entry in entries:
        doc_id = entry.get("doc_id", "?")
        tx_hash = submit_pending_auth(entry, dry_run=dry_run)
        if tx_hash:
            submitted.append((doc_id, tx_hash))
        else:
            skipped.append(doc_id)

    # Rewrite queue with unprocessed entries (only on real runs)
    if not dry_run and skipped:
        # Keep only entries that couldn't be processed (expired, malformed, etc.)
        # This is a simplification — in production you'd track failures more carefully
        remaining = [e for e in entries if e.get("doc_id") in [s[0] for s in [(d, None) for d in skipped]]]
        write_queue(remaining)

    logger.info("Sweep complete: %d submitted, %d skipped", len(submitted), len(skipped))
    for doc_id, tx_hash in submitted:
        logger.info("  Settled: doc_id=%s tx=%s", doc_id[:16], tx_hash)

    return len(submitted)


def main() -> None:
    parser = argparse.ArgumentParser(description="EIP-3009 Settlement Relay — Daily Sweep")
    parser.add_argument("--dry-run", action="store_true", help="Log what would be submitted, don't send transactions")
    parser.add_argument("--force", action="store_true", help="Submit transactions for real (requires RPC and key)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.force and not BASE_RPC_URL:
        logger.error("BASE_RPC_URL env var is required for --force")
        sys.exit(1)

    if args.force:
        count = sweep(dry_run=False, force=True)
    else:
        count = sweep(dry_run=True, force=False)

    sys.exit(0 if count >= 0 else 1)


if __name__ == "__main__":
    main()
