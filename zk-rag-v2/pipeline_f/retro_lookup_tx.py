#!/usr/bin/env python3
"""
retro_lookup_tx.py -- Retroactively look up missing EVM transaction hashes.

Given a block number + uploader address, queries the RPC for the block and finds
the emit transaction. Then updates both the central registry and Qdrant payloads.

Usage:
    # Single doc by block number (most flexible)
    python3 retro_lookup_tx.py --doc-id <doc_id> --block <block_number> [--dry-run]

    # Single doc by doc-id only (scans registry for block_number + uploader)
    python3 retro_lookup_tx.py --doc-id <doc_id> [--dry-run]

    # All docs with tx_hash="unknown" in registry
    python3 retro_lookup_tx.py --batch-unknown [--dry-run]

    # All emitted docs in registry (checks all for unknown/missing)
    python3 retro_lookup_tx.py --batch-all [--dry-run]

    # Skip registry update (Qdrant only)
    python3 retro_lookup_tx.py --doc-id <doc_id> --block <n> --qdrant-only

    # Skip Qdrant update (registry only)
    python3 retro_lookup_tx.py --doc-id <doc_id> --block <n> --registry-only

    # Custom RPC URL
    python3 retro_lookup_tx.py --doc-id <doc_id> --block <n> --rpc-url <url>

Exit codes:
    0  - success (all updated or nothing needed)
    1  - one or more docs failed
    2  - usage error
"""

import argparse
import json
import logging
import sys
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

# ── Paths ────────────────────────────────────────────────────────────────────

REGISTRY_PATH = Path("$DATA_DIR/registry.json")
LOG_DIR       = Path("$DATA_DIR/logs")
SCRIPT_DIR     = Path(__file__).parent

# ── RPC config ────────────────────────────────────────────────────────────────

DEFAULT_RPC = "https://horizen-testnet.rpc.caldera.xyz"
CHAIN_ID    = 2651420          # testnet; change to 26514 for mainnet
EMITTER_ADDR = "YOUR_WALLET_ADDRESS"  # uploader address
CONTRACT_ADDR = "0x17a6e8ae3f6eb315f4c117630f3aac8865bd2b15"  # AppendRoot contract

# AppendRoot.appendRoot signature: 0x42d28b24
APPEND_ROOT_SIG = "0x42d28b24"

# ── Logging ──────────────────────────────────────────────────────────────────

LOG_DIR.mkdir(parents=True, exist_ok=True)

def setup_logging(dry_run: bool) -> logging.Logger:
    tag = "DRY" if dry_run else "LIVE"
    log_file = LOG_DIR / f"retro_lookup_{tag}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
    logger = logging.getLogger(f"retro_lookup.{tag}")
    logger.setLevel(logging.DEBUG)
    # Avoid duplicate handlers on re-runs
    if logger.handlers:
        logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ── RPC helpers ───────────────────────────────────────────────────────────────

def rpc_call(rpc_url: str, method: str, params: list, logger: logging.Logger) -> dict:
    """Make a JSON-RPC call. Returns parsed JSON or dies."""
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    try:
        resp = requests.post(rpc_url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"RPC call {method} failed: {e}")
        raise
    data = resp.json()
    if "error" in data:
        logger.error(f"RPC error: {data['error']}")
        raise RuntimeError(f"RPC error: {data['error']}")
    return data.get("result")


def find_tx_in_block(
    rpc_url: str,
    block_number: int,
    from_address: str,
    logger: logging.Logger,
) -> Optional[str]:
    """
    Fetch a block, find the transaction sent by `from_address` to the AppendRoot
    contract (identified by the method signature prefix).

    Returns the tx hash hex string, or None if not found.
    """
    block_hex = hex(block_number)
    txs = rpc_call(rpc_url, "eth_getBlockByNumber", [block_hex, True], logger)
    if not txs:
        logger.warning(f"Block {block_number} not found or empty")
        return None

    for tx in txs.get("transactions", []):
        if tx["from"].lower() != from_address.lower():
            continue
        to_addr = (tx.get("to") or "").lower()
        input_sig = tx.get("input", "")[:len(APPEND_ROOT_SIG)]
        # Match: to = contract AND input starts with appendRoot signature
        if to_addr == CONTRACT_ADDR.lower() and input_sig == APPEND_ROOT_SIG.lower():
            logger.info(f"  Found emit tx: {tx['hash']}")
            return tx["hash"]

    logger.warning(f"No emit tx from {from_address} in block {block_number} ({len(txs.get('transactions',[]))} txs)")
    return None


def get_block_timestamp(rpc_url: str, block_number: int, logger: logging.Logger) -> Optional[str]:
    """Get the timestamp of a block as ISO8601 UTC string."""
    block_hex = hex(block_number)
    block = rpc_call(rpc_url, "eth_getBlockByNumber", [block_hex, False], logger)
    if not block:
        return None
    ts_hex = block.get("timestamp")
    if not ts_hex:
        return None
    ts_int = int(ts_hex, 16)
    return datetime.fromtimestamp(ts_int, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Registry helpers ─────────────────────────────────────────────────────────

def load_registry() -> tuple[dict, dict]:
    """Load registry JSON. Returns (data, doc_id_index)."""
    with open(REGISTRY_PATH) as f:
        data = json.load(f)
    index = {d["doc_id"]: i for i, d in enumerate(data["documents"])}
    return data, index


def save_registry(data: dict) -> None:
    """Atomically write registry."""
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.rename(REGISTRY_PATH)


def get_emitted_docs_with_missing_tx(registry_data: dict) -> list[dict]:
    """Return all emitted docs where tx_hash is missing or 'unknown'."""
    results = []
    for doc in registry_data["documents"]:
        for net in ("emitted_testnet", "emitted_mainnet"):
            val = doc.get(net)
            if not isinstance(val, dict):
                continue
            if val.get("status") != "emitted":
                continue
            tx = val.get("tx_hash", "")
            block = val.get("block_number")
            if not tx or tx in ("unknown", "", None) or not block:
                results.append({
                    "doc_id": doc["doc_id"],
                    "title": doc.get("title", "N/A"),
                    "chain": net,
                    "tx_hash": tx or "MISSING",
                    "block_number": block,
                    "uploader": val.get("uploader") or EMITTER_ADDR,
                    "chain_id": val.get("chain_id") or CHAIN_ID,
                })
    return results


# ── Qdrant helpers ────────────────────────────────────────────────────────────

def qdrant_collections_for_doc(client: QdrantClient, doc_id: str) -> list[str]:
    """Return which collection(s) contain this doc_id. Returns all matches."""
    all_cols = ["army", "navy", "marines", "other"]
    found = []
    for coll in all_cols:
        try:
            r = client.scroll(collection_name=coll, limit=1, with_payload=False,
                scroll_filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]))
            if r[0]:
                found.append(coll)
        except Exception:
            pass
    return found


def update_qdrant_tx_hash(
    client: QdrantClient,
    doc_id: str,
    collections: list[str],
    new_tx_hash: str,
    logger: logging.Logger,
    batch_size: int = 200,
) -> int:
    """
    Update evm_tx_hash on all chunks for a doc across the given collections.
    Uses scroll + set_payload (payload-only merge, no vector needed).

    Returns number of points updated.
    """
    total_updated = 0

    for coll in collections:
        offset = None
        while True:
            try:
                result = client.scroll(
                    collection_name=coll,
                    limit=batch_size,
                    offset=offset,
                    with_payload=False,   # only need IDs, not full payload
                    scroll_filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]),
                )
            except Exception as e:
                logger.error(f"  Qdrant scroll failed ({coll}): {e}")
                break

            points = result[0]
            if not points:
                break

            # next_page_offset is the offset to pass for the next page;
            # result[1] is the same value accessible as .next_page_offset on newer clients
            offset = getattr(result, 'next_page_offset', None) or (result[1] if len(result) > 1 else None)

            point_ids = [pt.id for pt in points]

            try:
                client.set_payload(
                    collection_name=coll,
                    payload={"evm_tx_hash": new_tx_hash},
                    points=point_ids,
                )
                total_updated += len(point_ids)
                logger.info(f"  {coll}: updated {len(point_ids)} points (total {total_updated})")
            except Exception as e:
                logger.error(f"  Qdrant set_payload failed ({coll}): {e}")

            if offset is None:
                break

    return total_updated


# ── Core logic ────────────────────────────────────────────────────────────────

def retro_lookup_single(
    doc_id: str,
    block_number: int,
    rpc_url: str,
    logger: logging.Logger,
    dry_run: bool,
    registry_only: bool,
    qdrant_only: bool,
    chain: str = "emitted_testnet",
    uploader: str = EMITTER_ADDR,
) -> bool:
    """
    For a single doc:
      1. Query RPC -> find emit tx hash in the block
      2. Update registry
      3. Update Qdrant

    Returns True on success.
    """
    if not dry_run:
        logger.info(f"=== Retro lookup: doc_id={doc_id[:20]}... block={block_number} ===")
    else:
        logger.info(f"[DRY RUN] === Retro lookup: doc_id={doc_id[:20]}... block={block_number} ===")

    # ── Step 1: RPC lookup ────────────────────────────────────────────────────
    if dry_run:
        fake_tx = "0x" + "a" * 64
        new_tx_hash = fake_tx
        new_timestamp = "2026-04-21T00:00:00Z"
        logger.info(f"[DRY] Would look up block {block_number} for tx from {uploader}")
        logger.info(f"[DRY] Would update registry + Qdrant with tx_hash={fake_tx}")
    else:
        new_tx_hash = find_tx_in_block(rpc_url, block_number, uploader, logger)
        if not new_tx_hash:
            logger.error("Could not find emit tx. Aborting — registry and Qdrant not updated.")
            return False

        new_timestamp = get_block_timestamp(rpc_url, block_number, logger)
        if new_timestamp:
            logger.info(f"  Block timestamp: {new_timestamp}")

    # ── Step 2: Update registry ───────────────────────────────────────────────
    if not qdrant_only:
        if dry_run:
            logger.info(f"[DRY] Would update registry [{chain}].tx_hash -> {new_tx_hash}")
        else:
            reg_data, reg_index = load_registry()
            if doc_id not in reg_index:
                logger.error("doc_id not in registry. Skipping registry update.")
                return False

            entry = reg_data["documents"][reg_index[doc_id]]
            record = entry.get(chain, {})

            old_tx = record.get("tx_hash", "MISSING")
            record["tx_hash"] = new_tx_hash
            if new_timestamp:
                record["block_timestamp"] = new_timestamp
            record["chain_id"] = CHAIN_ID
            record["source"] = "retro_lookup"

            entry[chain] = record
            save_registry(reg_data)
            logger.info(f"  Registry updated: tx_hash {old_tx} -> {new_tx_hash}")

    # ── Step 3: Update Qdrant ────────────────────────────────────────────────
    if not registry_only:
        if dry_run:
            logger.info(f"[DRY] Would update Qdrant for doc_id={doc_id[:20]}... with tx_hash={new_tx_hash}")
        else:
            client = QdrantClient(url="http://127.0.0.1:6333")
            collections = qdrant_collections_for_doc(client, doc_id)
            if not collections:
                logger.warning("  Doc not found in any Qdrant collection. Nothing to update.")
            else:
                total = update_qdrant_tx_hash(client, doc_id, collections, new_tx_hash, logger)
                logger.info(f"  Qdrant updated: {total} chunks across {collections}")

    return True


def retro_lookup_from_registry(
    rpc_url: str,
    logger: logging.Logger,
    dry_run: bool,
    registry_only: bool,
    qdrant_only: bool,
    batch_all: bool = False,
) -> tuple[int, int]:
    """
    Scan registry for emitted docs with missing/unknown tx_hash and retro-look up each.

    Returns (success_count, fail_count).
    """
    reg_data, reg_index = load_registry()
    docs = get_emitted_docs_with_missing_tx(reg_data)

    if not docs:
        logger.info("No emitted docs with missing tx_hash found in registry.")
        return 0, 0

    if batch_all:
        logger.info(f"Found {len(docs)} emitted docs with missing tx_hash (batch-all mode)")
    else:
        logger.info(f"Found {len(docs)} emitted docs with unknown tx_hash:")

    success = 0
    failures = 0

    for doc in docs:
        doc_id = doc["doc_id"]
        block_number = doc["block_number"]

        if not batch_all:
            logger.info(f"  {doc_id[:20]}... | block={block_number} | current tx={doc['tx_hash']}")

        if not block_number:
            logger.warning(f"  Skipping {doc_id[:20]}... — no block_number in registry")
            failures += 1
            continue

        ok = retro_lookup_single(
            doc_id=doc_id,
            block_number=int(block_number),
            rpc_url=rpc_url,
            logger=logger,
            dry_run=dry_run,
            registry_only=registry_only,
            qdrant_only=qdrant_only,
            chain=doc["chain"],
            uploader=doc["uploader"],
        )
        if ok:
            success += 1
        else:
            failures += 1

        # Rate limit RPC calls
        if not dry_run:
            time.sleep(0.25)

    return success, failures


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        description="Retroactively look up missing EVM tx hashes and update registry + Qdrant.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--doc-id", help="Doc ID to retro-look up (requires --block)")
    p.add_argument("--block", type=int, help="Block number to search (required with --doc-id)")
    p.add_argument("--uploader", default=EMITTER_ADDR,
                   help=f"Uploader address that sent the emit tx (default: {EMITTER_ADDR})")
    p.add_argument("--chain", default="emitted_testnet",
                   help="Registry key: emitted_testnet (default) or emitted_mainnet")
    p.add_argument("--rpc-url", default=DEFAULT_RPC,
                   help=f"JSON-RPC URL (default: {DEFAULT_RPC})")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be done without making changes")
    p.add_argument("--batch-unknown", action="store_true",
                   help="Process all docs in registry with tx_hash=unknown or missing")
    p.add_argument("--batch-all", action="store_true",
                   help="Process all emitted docs regardless of tx_hash (verify mode)")
    p.add_argument("--registry-only", action="store_true",
                   help="Only update registry, skip Qdrant")
    p.add_argument("--qdrant-only", action="store_true",
                   help="Only update Qdrant, skip registry")
    return p


def main():
    args = main.__dict__  # will be set by parser
    p = build_parser()
    args = p.parse_args()

    logger = setup_logging(args.dry_run)

    # ── Validation ────────────────────────────────────────────────────────────
    if args.doc_id and not args.block:
        p.error("--block is required when --doc-id is specified")
    if args.block and not args.doc_id:
        p.error("--block is required with --doc-id")
    if args.batch_unknown and args.batch_all:
        p.error("Cannot use --batch-unknown and --batch-all together")
    if args.doc_id and (args.batch_unknown or args.batch_all):
        p.error("--doc-id cannot be used with --batch-* modes")

    if args.registry_only and args.qdrant_only:
        p.error("Cannot use --registry-only and --qdrant-only together")

    # ── Dispatch ────────────────────────────────────────────────────────────────
    try:
        if args.doc_id and args.block:
            ok = retro_lookup_single(
                doc_id=args.doc_id,
                block_number=args.block,
                rpc_url=args.rpc_url,
                logger=logger,
                dry_run=args.dry_run,
                registry_only=args.registry_only,
                qdrant_only=args.qdrant_only,
                chain=args.chain,
                uploader=args.uploader,
            )
            sys.exit(0 if ok else 1)

        elif args.batch_unknown or args.batch_all:
            success, failures = retro_lookup_from_registry(
                rpc_url=args.rpc_url,
                logger=logger,
                dry_run=args.dry_run,
                registry_only=args.registry_only,
                qdrant_only=args.qdrant_only,
                batch_all=args.batch_all,
            )
            logger.info(f"=== Done: {success} succeeded, {failures} failed ===")
            sys.exit(0 if failures == 0 else 1)

        else:
            p.print_help()
            sys.exit(2)

    except KeyboardInterrupt:
        logger.info("Interrupted.")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
