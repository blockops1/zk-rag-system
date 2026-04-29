# PRD-MIL-05: Pipeline F — Sync Block Metadata to Registry

**Status:** Draft
**Author:** Fred (data backbone)
**Date:** 2026-04-15
**Pipeline:** F (Block Metadata Sync)
**Depends On:** Pipeline F emit_all.py (Merkle root emission to chain)
**Runs After:** Pipeline G has already emitted roots to the contract
**Script:** `pipeline_f/sync_block_metadata_to_registry.py`

---

## 1. Problem Statement

After Pipeline G emits Merkle roots to the `MerkleRootRegistry` contract on Horizen testnet, the registry needs to store the **block number** and **block timestamp** of each emission transaction. This data is required for:

- Auditing: knowing exactly when each document's Merkle root was committed on-chain
- Pipeline G verification: confirming that on-chain block data matches what the registry claims
- Ordering: establishing the temporal sequence of emissions for documents emitted more than once

The contract stores this data at emission time (`block.number`, `block.timestamp`), but the registry never captured it.

---

## 2. Current State

### Registry Schema

Each document has an `emitted_testnet` field in one of two formats:

**New format (dict — for future emissions):**
```json
"emitted_testnet": {
  "status": "emitted",
  "tx_hash": "0xabc...",
  "chain_id": 2651420,
  "emitted_at": "2026-04-15T21:30:45.662036+00:00",
  "block_number": null,
  "block_timestamp": null
}
```

**Old format (boolean — the 19 existing emissions):**
```json
"emitted_testnet": true
```

### Broken Script

`sync_block_metadata_to_registry.py` exists and is well-structured (344 lines). It correctly:
- Connects to the ETH RPC
- Fetches `eth_getTransactionReceipt` and `eth_getBlockByNumber`
- Writes `block_number` and `block_timestamp` to the registry

**Why it fails:** The script iterates documents looking for `emitted_testnet.tx_hash` values. The 19 existing emissions use the old boolean format (`emitted_testnet: true`) and have **no `tx_hash`** stored anywhere in the registry or state file. The script finds nothing to work with and exits immediately.

### Root Cause

The original `emit_all.py` recorded `emitted_testnet: true` (boolean) but never stored the transaction hash returned by the forge broadcast. The broadcast artifacts (`pipeline_f/broadcast/AppendRoot.s.sol/2651420/run-*.json`) store tx hashes, but the script that wrote the registry did not capture or store them.

---

## 3. Solution Approach

Since the 19 doc_ids ARE stored on the contract (`getDocIds(0, 19)` returns all of them), and the contract stores `block_number`, `block_timestamp`, and `uploader` in each `RootEntry`, the script must:

1. **Read doc_ids directly from the contract** using `getDocIds(offset, limit)` — no tx_hash needed
2. **For each doc_id, call `getRootEntry(docId, 0)`** to get block number, timestamp, and uploader
   - **Index 0 = oldest entry.** The contract stores `rootHistory[docId][]` as an append-only array. Index 0 is the first (oldest) entry. For the 19 existing docs, each has exactly 1 entry at index 0. If a doc was re-emitted, index 0 returns the original entry — not the latest.
   - **Block number type: `uint40`.** The `eth_call` response is hex-encoded. Convert with `int(block_hex, 16)` to get the integer value.
3. **Match on-chain data back to the registry** using doc_id as the key
4. **Write block_number (int) and block_timestamp (ISO8601 string)** into the registry
5. **Migrate the old boolean `emitted_testnet: true` entries** to the new dict format

This approach works even when the registry has no tx_hash — it reads directly from the authoritative on-chain source.

---

## 4. Registry Schema Changes

### For old-format docs (19 docs with `emitted_testnet: true`):

Migrate to:
```json
"emitted_testnet": {
  "status": "emitted",
  "tx_hash": null,
  "chain_id": 2651420,
  "emitted_at": null,
  "block_number": 14696786,
  "block_timestamp": "2026-04-14T17:12:16Z",
  "uploader": "YOUR_WALLET_ADDRESS",
  "source": "contract"
}
```

Notes:
- `emitted_at` is left `null` for migrated entries (original local emission time not recoverable; `block_timestamp` is the authoritative on-chain timestamp)
- `tx_hash` is `null` because it was not captured at emission time
- `source: "contract"` indicates the data came from the chain, not from emit_all.py output
- `uploader` is read from `getRootEntry` on-chain — stores the EOAs that submitted each root

### For new-format docs already with tx_hash but missing block data:

Write `block_number` and `block_timestamp` in place (existing script logic, still valid).

---

## 5. Script Design

### Input Sources (in priority order)

1. **On-chain contract** (`MerkleRootRegistry` at `0x2E276196d82252aac48854bf1F044B095468A310` on chain 2651420):
   - `getDocIds(0, N)` → list of all doc_ids that have entries on-chain
   - `getRootEntry(docId, 0)` → block_number, block_timestamp, uploader, merkleCap, pdfHash, chunkCount

2. **Registry** (`$DATA_DIR/registry.json`) — for matching doc_ids to registry entries and writing block data

3. **Legacy state file** (`$DATA_DIR/merkle_trees/emitted_roots.json`) — only for migration completeness (read-only)

### Data Flow

```
1. Load registry
2. For each doc_id in registry where emitted_testnet exists:
   a. If emitted_testnet is bool=true → flag as "needs migration from chain"
   b. If emitted_testnet is dict with tx_hash but no block_number → flag as "needs RPC lookup"
   c. If emitted_testnet is dict with block_number → skip (already done)
3. Fetch all on-chain doc_ids via getDocIds
4. For each on-chain doc_id:
   a. Call getRootEntry(docId, 0) → get block_number, block_timestamp, uploader
   b. Find matching registry entry by doc_id
   c. Write block_number + block_timestamp into registry
5. Save registry
```

### RPC Methods Used

| Method | Purpose |
|--------|---------|
| `eth_call` | `getDocIds(offset, limit)` — enumerate on-chain doc_ids |
| `eth_call` | `getRootEntry(docId, index)` — get block metadata for a doc |
| `eth_call` | `getDocCount()` — know when to stop paginating (call once at offset=0; if count ≤ limit, pagination is done) |

**Pagination termination:** Call `getDocCount()` first to get the total N. Then call `getDocIds(0, N)`. If N is large (hundreds/thousands), paginate in chunks of `limit` until an empty array is returned. Stop when `getDocIds(offset, limit)` returns an empty result — the contract has no more entries beyond that offset.

**ABI decoding:** `getDocIds` returns `bytes32[]` — an ABI-encoded array. Use `eth_abi.decode(['bytes32[]'], bytes.fromhex(raw_hex.removeprefix('0x')))` to decode. `getRootEntry` returns a struct — use `eth_abi.decode([...struct_types...], ...)` with the struct field types from the Solidity contract.

### CLI Interface

```bash
# Dry run — show what would be backfilled without writing
python3 sync_block_metadata_to_registry.py --dry-run --batch

# Batch — fetch and write block metadata for all qualifying docs
python3 sync_block_metadata_to_registry.py --batch

# Single doc — for testing or fixing individual entries
python3 sync_block_metadata_to_registry.py --doc-id <doc_id>

# Limit to N docs — for testing
python3 sync_block_metadata_to_registry.py --batch --limit 10
```

### New Flags

| Flag | Purpose |
|------|---------|
| `--chain-id` | Override chain ID (default: 2651420 testnet, use 26514 for mainnet) |
| `--contract-address` | Override contract address (default: `0x2E276196d82252aac48854bf1F044B095468A310`) |
| `--rpc-url` | Override RPC URL (default: `https://horizen-testnet.rpc.caldera.xyz/http`) |

---

## 6. Error Handling

| Condition | Behavior |
|-----------|----------|
| doc_id not found in registry | Log warning, skip — registry entry is missing entirely |
| `getRootEntry` returns zero block number | Log error, skip — likely a contract bug |
| RPC call fails | Retry up to 3 times with exponential backoff; log error and skip after retries exhausted |
| Registry write fails | Log error, abort batch — atomic write prevents partial state |
| On-chain doc_id already has block data in registry | Skip (idempotent) |

---

## 7. Idempotency

The script is idempotent by design:
- A doc with `block_number` already populated is skipped
- Re-running the batch on already-updated docs produces no changes
- No destructive operations

---

## 8. What Pipeline G Needs

Pipeline G (emit_all.py) will read from the registry:
- `emitted_testnet.block_number` (int) — for on-chain verification
- `emitted_testnet.block_timestamp` (ISO8601 string) — for display/logging
- `emitted_testnet.uploader` (address string) — for audit trail (which EOA submitted the root)

These fields must exist for every doc with `status: "emitted"`:
```json
"emitted_testnet": {
  "status": "emitted",
  "tx_hash": <"0x..." or null>,
  "chain_id": 2651420,
  "emitted_at": <ISO8601 or null>,
  "block_number": <int>,
  "block_timestamp": <ISO8601 string>,
  "uploader": <"0x..." address string>,
  "source": <"contract" or "rpc">
}

---

## 9. Script Architecture (Changes to Existing File)

The existing `sync_block_metadata_to_registry.py` (344 lines) must be updated:

**New functions to add:**
- `get_onchain_doc_ids(rpc_url, contract_address) → list[bytes32]` — paginate through `getDocIds` to get all doc_ids
- `get_root_entry_from_chain(doc_id, rpc_url, contract_address) → dict` — call `getRootEntry(docId, 0)` and return parsed dict
- `migrate_bool_entry(registry_entry) → dict` — convert `emitted_testnet: true` to dict format, ready for block data injection
- `match_and_backfill(registry_data, doc_id_index, rpc_url, contract_address, dry_run) → (updated, skipped)`

**Existing functions to modify:**
- `iter_docs_needing_block_data()` — add logic to also yield bool-format entries that need migration
- `main()` — add `--chain-id`, `--contract-address` flags; handle the new "read from contract" flow

**Existing functions to keep:**
- `_rpc()` — unchanged
- `get_block_number_and_timestamp(tx_hash, rpc_url)` — still used for new-format entries that have tx_hash
- `load_registry()`, `save_registry()` — unchanged
- `backfill_single()` — may need update to handle both bool-migration and chain-read paths

---

## 10. Testing

### Manual Verification Steps

1. Run with `--dry-run --batch` — should show 19 docs needing migration
2. Run with `--batch --limit 1` — verify one doc gets updated correctly
3. Read the updated registry entry and verify block data against on-chain:
   ```bash
   export PATH="$HOME/.foundry/bin:$PATH"
   # Get block metadata from chain for comparison
   cast block <block_number> --rpc-url https://horizen-testnet.rpc.caldera.xyz/http
   # Confirm timestamp matches block_timestamp in registry
   ```
   Expected: `cast block` output shows `timestamp: <unix_timestamp>` which converts to the ISO8601 `block_timestamp` stored in registry.
4. Run `--dry-run --batch` again — should show 0 docs needing work
5. Run actual Pipeline G (`emit_all.py --dry-run`) — should still work (idempotency preserved)

### Regression Check

After the update, `emit_all.py` must still function correctly:
- `--batch` mode should skip already-emitted docs (unchanged behavior)
- `--dry-run` should produce the same output as before

---

## 11. File Locations

| File | Location |
|------|----------|
| Script to update | `$REPO_DIR/pipeline_f/sync_block_metadata_to_registry.py` |
| Registry | `$DATA_DIR/registry.json` |
| Contract | `0x2E276196d82252aac48854bf1F044B095468A310` on chain 2651420 |
| Contract source | `$REPO_DIR/pipeline_f/contracts/MerkleRootRegistry.sol` |
| RPC | `https://horizen-testnet.rpc.caldera.xyz/http` |
| This PRD | `$REPO_DIR/pipeline_f/prd/PRD-MIL-05-pipeline-f-sync-block-metadata.md` |

---

## 12. Open Issues

| Issue | Status | Notes |
|-------|--------|-------|
| `tx_hash` for 19 old emissions | Known gap | Data not recoverable — will be `null` in registry; chain data (block, timestamp, uploader) is the authoritative record |
| Script PATH issue (foundry not in PATH) | Not a sync script issue | `sync_block_metadata_to_registry.py` uses `requests` library (HTTP RPC), not `forge`. No PATH dependency for the script itself. Manual verification with `cast block` requires `export PATH="$HOME/.foundry/bin:$PATH"`. |
| `emitted_at` for old-format entries | Resolved | Left as `null` for migrated entries; original local emission time not recoverable; `block_timestamp` is the authoritative on-chain timestamp |
| Zero-chunk doc_ids | Expected skip | 2 docs (`026f2f64364d0e69...` and `016c0f8e60acd72d...`) have `chunk_count = 0` in their tree files — contract reverts `chunkCount must be > 0`; these docs have no on-chain entries and will produce empty `getDocIds` results or zero `getRootCount`; log as "no on-chain entry, skipped" and continue |
| `getDocIds` pagination boundary | Resolved | Call `getDocCount()` first to get total N; if N ≤ limit, single call suffices; otherwise paginate until empty array returned |
| `getDocIds` ABI decoding | Resolved | Returns `bytes32[]` — decode with `eth_abi.decode(['bytes32[]'], bytes.fromhex(raw_hex.removeprefix('0x')))` |
