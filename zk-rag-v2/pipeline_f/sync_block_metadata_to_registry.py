#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Run with the system python3 that has eth_abi installed:
#   /usr/bin/python3 sync_block_metadata_to_registry.py --batch --dry-run
# or ensure eth_abi is in your PATH python before running.
"""
sync_block_metadata_to_registry.py -- Backfill block number, timestamp, and uploader
for already-emitted documents by reading directly from the MerkleRootRegistry contract.

Approach:
  1. Read all doc_ids from the contract via getDocIds(offset, limit) -- no tx_hash needed
  2. For each doc_id, call getRootEntry(docId, 0) to get block_number, block_timestamp, uploader
  3. Match on-chain doc_ids back to the registry by doc_id
  4. Migrate old-format entries ("emitted_testnet": true) to the new dict schema
  5. Write block_number (int), block_timestamp (ISO8601), uploader (address) into the registry

This works even for the 19 existing emissions that have no tx_hash stored -- they
all have on-chain entries at getRootEntry(docId, 0).

Usage:
    python3 sync_block_metadata_to_registry.py --batch --dry-run
    python3 sync_block_metadata_to_registry.py --batch
    python3 sync_block_metadata_to_registry.py --doc-id <doc_id>
    python3 sync_block_metadata_to_registry.py --batch --limit 5

Registry fields written (inside emitted_testnet):
    block_number:      int   -- EVM block number (uint40)
    block_timestamp:   str   -- ISO8601 UTC timestamp of the block
    uploader:          str   -- EOA address that submitted the root
    source:            str   -- "contract" (data came from chain, not from emit_all.py)

Env vars:
    ETH_RPC_URL   -- RPC endpoint (default: https://horizen-testnet.rpc.caldera.xyz/http)
    (No private key needed -- reads only)

Requires:
    eth_abi (pip install eth-abi)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import eth_abi


# ── Configuration ─────────────────────────────────────────────────────────────

REGISTRY_PATH       = Path("./data/registry.json")
DEFAULT_RPC_URL     = "https://horizen-testnet.rpc.caldera.xyz"
DEFAULT_CONTRACT    = "0x17A6E8AE3f6eb315F4C117630F3AaC8865BD2B15"
CHAIN_ID            = 2651420          # testnet; change to 26514 for mainnet
REQUEST_TIMEOUT     = 30              # seconds per HTTP request
MAX_RETRIES         = 3               # retries on transient RPC errors
RETRY_DELAY         = 5               # seconds between retries
PAGINATION_LIMIT    = 50              # batch size for getDocIds pagination


# ── Contract ABI ───────────────────────────────────────────────────────────────
# NOTE: These selectors are for the V2 contract at 0x17A6E8AE3f6eb315F4C117630F3AaC8865BD2B15
# They were verified via direct RPC calls against the live contract.
# WARNING: The local source code (MerkleRootRegistry.sol) may have DIFFERENT selectors
# if it was modified after deployment. Always probe the live contract, not local artifacts.

# Verified on-chain (2026-04-15):
GET_DOC_COUNT_SELECTOR    = "0x63704e93"  # getDocCount()
GET_DOC_IDS_SELECTOR      = "0x27eca3ca"  # getDocIds(uint256,uint256)
GET_ROOT_ENTRY_SELECTOR   = "0x7bb39237"  # getRootEntry(bytes32,uint256)
GET_LATEST_CAP_HASH       = "0x6db1f5a2"  # getLatestCapHash(bytes32) — not currently used
GET_ROOT_COUNT_SELECTOR   = "0xa20b84c7"  # getRootCount(bytes32) — not currently used


# ── RPC helpers ────────────────────────────────────────────────────────────────

def _rpc(method: str, params: list, rpc_url: str, retries: int = MAX_RETRIES) -> dict:
    """
    Make a single JSON-RPC call. Retries on transient errors.
    Raises RuntimeError after MAX_RETRIES failures.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.post(rpc_url, json=payload, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                raise RuntimeError(f"RPC error: {data['error']}")
            return data.get("result", {})
        except (requests.RequestException, ValueError, KeyError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))

    raise RuntimeError(f"RPC call failed after {retries} attempts: {last_err}")


def _eth_call(to: str, data: str, rpc_url: str) -> str:
    """
    Make an eth_call and return the raw result hex string (without 0x prefix).
    """
    result = _rpc("eth_call", [{"to": to, "data": data}], rpc_url)
    if result is None:
        raise RuntimeError(f"eth_call returned null for data={data[:20]}...")
    # Strip leading 0x if present
    return result[2:] if result.startswith("0x") else result


# ── On-chain data fetchers ─────────────────────────────────────────────────────

def get_doc_count(rpc_url: str, contract_address: str) -> int:
    """
    Call getDocCount() on the contract.
    Returns total number of doc_ids registered on-chain.
    """
    raw = _eth_call(contract_address, GET_DOC_COUNT_SELECTOR, rpc_url)
    return int(raw, 16)


def get_onchain_doc_ids(rpc_url: str, contract_address: str) -> list[str]:
    """
    Fetch ALL doc_ids from the contract by reading getDocIds in paginated batches.
    Returns a list of doc_id strings (0x-prefixed hex).
    """
    count = get_doc_count(rpc_url, contract_address)
    if count == 0:
        return []

    doc_ids = []
    offset = 0

    while True:
        # Build calldata: selector (4 bytes) + offset (32 bytes) + limit (32 bytes)
        offset_hex = format(offset, "064x")
        limit_hex  = format(PAGINATION_LIMIT, "064x")
        calldata   = GET_DOC_IDS_SELECTOR + offset_hex + limit_hex

        raw = _eth_call(contract_address, calldata, rpc_url)

        if not raw or len(raw) < 8:
            break

        # Decode bytes32[] — eth_abi.decode expects tuples for dynamic arrays
        # Result layout: offset-to-data (32) + length (32) + N×bytes32 items
        try:
            decoded = eth_abi.decode(["bytes32[]"], bytes.fromhex(raw))
            batch = [bytes32.hex() for bytes32 in decoded[0]]
        except Exception as e:
            raise RuntimeError(f"Failed to decode getDocIds batch at offset {offset}: {e}")

        if not batch:
            break

        doc_ids.extend(batch)
        print(f"  Fetched getDocIds offset={offset} count={len(batch)}  total_so_far={len(doc_ids)}")

        if len(batch) < PAGINATION_LIMIT:
            # Last batch -- fewer items than limit means we're done
            break

        offset += PAGINATION_LIMIT

    return doc_ids


def get_root_entry(doc_id: str, rpc_url: str, contract_address: str) -> dict:
    """
    Call getRootEntry(docId, 0) on the contract.

    Returns dict:
        block_number:      int    -- EVM block number (uint40)
        block_timestamp:   str    -- ISO8601 UTC
        uploader:          str    -- EOA address
        chunk_count:       int
    """
    # Build calldata: selector (4 bytes) + doc_id (32 bytes) + index (32 bytes)
    doc_id_padded = doc_id.replace("0x", "").rjust(64, "0")
    index_hex     = format(0, "064x")   # index 0 = oldest (first) entry
    calldata      = GET_ROOT_ENTRY_SELECTOR + doc_id_padded + index_hex

    raw = _eth_call(contract_address, calldata, rpc_url)

    if not raw or raw == "0" * 8:
        raise RuntimeError(f"getRootEntry({doc_id[:16]}..., 0) returned empty -- no on-chain entry")

    # Decode struct (V2 -- flat, NOT nested array like V1):
    # (
    #   bytes32  merkleRoot,
    #   bytes32  pdfHash,
    #   uint32   chunkCount,
    #   uint40   blockNumber,
    #   uint40   blockTimestamp,
    #   address  uploader
    # )
    try:
        decoded = eth_abi.decode(
            ["(bytes32,bytes32,uint32,uint40,uint40,address)"],
            bytes.fromhex(raw)
        )
    except Exception as e:
        raise RuntimeError(f"Failed to decode getRootEntry for {doc_id[:16]}...: {e}")

    (merkle_root, _pdf_hash, chunk_count, block_number, block_ts, uploader) = decoded[0]

    # uint40 block_number comes back as int
    block_number_int = int(block_number)

    # uint40 block_timestamp -- convert unix epoch to ISO8601
    block_ts_int     = int(block_ts)
    block_timestamp  = datetime.fromtimestamp(block_ts_int, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "block_number":    block_number_int,
        "block_timestamp": block_timestamp,
        "uploader":        uploader,
        "chunk_count":     int(chunk_count),
    }


# ── Registry helpers ───────────────────────────────────────────────────────────

def load_registry() -> tuple[dict, dict]:
    """Load full registry. Returns (registry_data, doc_id_index)."""
    with open(REGISTRY_PATH, "r") as f:
        registry_data = json.load(f)

    doc_id_index = {
        doc["doc_id"]: idx
        for idx, doc in enumerate(registry_data["documents"])
    }
    return registry_data, doc_id_index


def save_registry(registry_data: dict) -> None:
    """Atomically write registry."""
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(registry_data, f, indent=2)
    tmp.rename(REGISTRY_PATH)


def migrate_bool_entry(emit_testnet: dict) -> dict:
    """
    Migrate an old-format emitted_testnet entry from:
        {"status": "emitted"}   -- old boolean-true case (no fields)
    or:
        {"status": "emitted", "tx_hash": "unknown", ...}  -- partial new format, no block data
    into the full new-format dict with nulls for missing chain-derived fields.
    """
    # Start from whatever exists, fill in missing keys
    result = {
        "status":         emit_testnet.get("status", "emitted"),
        "tx_hash":        emit_testnet.get("tx_hash", None),
        "chain_id":       emit_testnet.get("chain_id", CHAIN_ID),
        "emitted_at":     emit_testnet.get("emitted_at", None),
        "block_number":   None,
        "block_timestamp": None,
        "uploader":       None,
        "source":         emit_testnet.get("source", "contract"),
    }
    return result


# ── Document classification ────────────────────────────────────────────────────

def classify_docs_needing_work(registry_data: dict, doc_id_index: dict) -> dict:
    """
    Classify every doc in the registry into one of:
      - "needs_migration": old-format (bool or dict without block_number) — needs chain read
      - "needs_rpc_lookup": dict with tx_hash but missing block_number — needs RPC (not implemented here)
      - "already_done": has block_number
      - "not_emitted": no emitted_testnet or status != "emitted"
      - "not_on_chain": doc_id not found in getDocIds result

    Returns dict: {doc_id: classification, ...}
    """
    classification = {}
    for doc_id, idx in doc_id_index.items():
        entry = registry_data["documents"][idx]
        emit  = entry.get("emitted_testnet")

        if not isinstance(emit, dict):
            # old boolean format or missing
            classification[doc_id] = "needs_migration"
            continue

        if emit.get("status") != "emitted":
            classification[doc_id] = "not_emitted"
            continue

        if emit.get("block_number") is not None:
            classification[doc_id] = "already_done"
            continue

        # Has dict but no block_number — could be old-format with no fields
        # or new-format with tx_hash but missing block data
        classification[doc_id] = "needs_migration"

    return classification


# ── Core logic ─────────────────────────────────────────────────────────────────

def backfill_single(
    registry_data: dict,
    doc_id_index: dict,
    doc_id: str,
    rpc_url: str,
    contract_address: str,
    dry_run: bool,
    verbose: bool = False,
) -> tuple[str, bool, str]:
    """
    Fetch block metadata from the contract for one document and write to registry.

    Returns (label, success, message).
    Labels: MIGRATE | SKIP | FAIL
    """
    idx   = doc_id_index[doc_id]
    emit  = registry_data["documents"][idx].get("emitted_testnet", {})

    # Migrate old-format entry if needed
    if not isinstance(emit, dict) or "block_number" not in emit:
        if not isinstance(emit, dict):
            emit = migrate_bool_entry({"status": "emitted"})
        else:
            emit = migrate_bool_entry(emit)
        registry_data["documents"][idx]["emitted_testnet"] = emit

    if emit.get("block_number") is not None:
        return "SKIP", True, f"already has block_number={emit['block_number']}"

    if dry_run:
        return "MIGRATE", True, f"dry-run: would call getRootEntry for {doc_id[:16]}..."

    if verbose:
        print(f"  -> getRootEntry({doc_id[:16]}..., 0) via {rpc_url[:50]}...")

    try:
        entry = get_root_entry(doc_id, rpc_url, contract_address)
    except Exception as e:
        return "FAIL", False, f"getRootEntry error: {str(e)[:100]}"

    # Write block metadata into the in-memory registry entry
    registry_data["documents"][idx]["emitted_testnet"]["block_number"]    = entry["block_number"]
    registry_data["documents"][idx]["emitted_testnet"]["block_timestamp"] = entry["block_timestamp"]
    registry_data["documents"][idx]["emitted_testnet"]["uploader"]     = entry["uploader"]
    registry_data["documents"][idx]["emitted_testnet"]["source"]       = "contract"

    msg = f"block={entry['block_number']} ts={entry['block_timestamp']} uploader={entry['uploader'][:16]}..."
    return "MIGRATE", True, msg


def run_batch(
    registry_data: dict,
    doc_id_index: dict,
    onchain_doc_ids: set[str],
    rpc_url: str,
    contract_address: str,
    dry_run: bool,
    limit: int | None = None,
) -> tuple[int, int]:
    """
    Backfill block metadata for all qualifying documents.

    Returns (updated_count, failed_count).
    Skips any doc_id that has no on-chain entry (not in onchain_doc_ids).
    """
    classification = classify_docs_needing_work(registry_data, doc_id_index)

    # Filter to only those needing migration AND present on-chain
    candidates = [
        doc_id for doc_id, cls in classification.items()
        if cls == "needs_migration" and doc_id in onchain_doc_ids
    ]

    # Also report not-on-chain docs (for visibility)
    [
        doc_id for doc_id, cls in classification.items()
        if cls == "needs_migration" and doc_id not in onchain_doc_ids
    ]

    if limit:
        candidates = candidates[:limit]

    if not dry_run and not candidates:
        # Nothing to do -- exit cleanly
        pass

    updated = 0
    failed  = 0

    for doc_id in candidates:
        label, success, msg = backfill_single(
            registry_data, doc_id_index, doc_id,
            rpc_url, contract_address,
            dry_run=dry_run, verbose=False,
        )
        print(f"  [{label}] {doc_id[:16]}... {msg}")

        if label == "SKIP":
            pass
        elif success:
            updated += 1
        else:
            failed += 1

        # Write registry after every successful fetch (atomic)
        if not dry_run and label == "MIGRATE":
            save_registry(registry_data)

        if (updated + failed) % 20 == 0 and (updated + failed) > 0:
            print(f"  Progress: updated={updated}  failed={failed}")

    return updated, failed


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Backfill block_number + block_timestamp + uploader for emitted docs "
                    "by reading from the MerkleRootRegistry contract.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fetched without writing to registry",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Process all documents missing block data",
    )
    parser.add_argument(
        "--doc-id",
        type=str,
        metavar="DOC_ID",
        help="Process a single document (must be 0x-prefixed bytes32)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Limit batch to first N documents (for testing)",
    )
    parser.add_argument(
        "--rpc-url",
        type=str,
        metavar="URL",
        default=os.environ.get("ETH_RPC_URL", DEFAULT_RPC_URL),
        help=f"ETH RPC URL (default: ETH_RPC_URL env or {DEFAULT_RPC_URL})",
    )
    parser.add_argument(
        "--contract-address",
        type=str,
        metavar="ADDRESS",
        default=DEFAULT_CONTRACT,
        help=f"MerkleRootRegistry contract address (default: {DEFAULT_CONTRACT})",
    )
    args = parser.parse_args()

    if not args.batch and not args.doc_id:
        parser.print_help()
        sys.exit(1)

    rpc_url         = args.rpc_url
    contract_addr   = args.contract_address

    print(f"Loading registry from {REGISTRY_PATH}...")
    registry_data, doc_id_index = load_registry()
    print(f"Loaded {len(registry_data['documents'])} documents")

    # ── Single doc mode ────────────────────────────────────────────────────────
    if args.doc_id:
        doc_id = args.doc_id
        if not doc_id.startswith("0x"):
            doc_id = "0x" + doc_id

        if doc_id not in doc_id_index:
            print(f"Error: doc_id not found in registry: {doc_id[:20]}...")
            sys.exit(1)

        doc_entry = registry_data["documents"][doc_id_index[doc_id]]
        emit = doc_entry.get("emitted_testnet", {})

        print(f"\n=== SINGLE DOC: {doc_id[:20]}... ===")
        print(f"  status:         {emit.get('status') if isinstance(emit, dict) else 'N/A'}")
        print(f"  tx_hash:        {emit.get('tx_hash') if isinstance(emit, dict) else 'N/A'}")
        print(f"  block_number:   {emit.get('block_number') if isinstance(emit, dict) else 'N/A'}")
        print(f"  block_timestamp:{emit.get('block_timestamp') if isinstance(emit, dict) else 'N/A'}")
        print(f"  uploader:       {emit.get('uploader') if isinstance(emit, dict) else 'N/A'}")

        label, success, msg = backfill_single(
            registry_data, doc_id_index, doc_id,
            rpc_url, contract_addr,
            dry_run=args.dry_run, verbose=True,
        )
        print(f"[{label}] {msg}")

        if not args.dry_run and label == "MIGRATE":
            save_registry(registry_data)
            print("Registry updated.")
        sys.exit(0)

    # ── Batch mode ─────────────────────────────────────────────────────────────
    if args.batch:
        print(f"\nFetching doc_ids from contract {contract_addr}...")
        try:
            onchain_doc_ids = get_onchain_doc_ids(rpc_url, contract_addr)
            print(f"On-chain doc_id count: {len(onchain_doc_ids)}\n")
        except Exception as e:
            print(f"FATAL: failed to fetch on-chain doc_ids: {e}")
            sys.exit(1)

        if args.dry_run:
            print("=== DRY RUN MODE ===\n")
        else:
            print("=== BATCH BACKFILL MODE ===\n")

        # Classify all docs
        classification = classify_docs_needing_work(registry_data, doc_id_index)
        needs_work = [d for d, c in classification.items() if c == "needs_migration"]
        on_chain   = [d for d in needs_work if d in set(onchain_doc_ids)]
        off_chain  = [d for d in needs_work if d not in set(onchain_doc_ids)]
        done       = [d for d, c in classification.items() if c == "already_done"]

        print(f"Registry docs needing migration:  {len(needs_work)}")
        print(f"  - On-chain (will process):      {len(on_chain)}")
        print(f"  - Off-chain (no contract entry):{len(off_chain)}")
        print(f"Already done:                     {len(done)}\n")

        if off_chain:
            print("Off-chain docs (no on-chain entry, skipping):")
            for d in off_chain:
                print(f"  {d[:32]}...")
            print()

        if not on_chain:
            print("Nothing to do.")
            sys.exit(0)

        updated, failed = run_batch(
            registry_data, doc_id_index, set(onchain_doc_ids),
            rpc_url, contract_addr,
            dry_run=args.dry_run, limit=args.limit,
        )

        print("\n=== SUMMARY ===")
        print(f"Total on-chain candidates: {len(on_chain)}")
        print(f"Updated (MIGRATE):        {updated}")
        print(f"Failed:                    {failed}")
        print(f"Off-chain (skipped):       {len(off_chain)}")
        print(f"Already done:              {len(done)}")

        if failed > 0:
            sys.exit(1)


if __name__ == "__main__":
    main()
