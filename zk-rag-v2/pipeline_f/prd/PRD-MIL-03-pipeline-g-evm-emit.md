# PRD-MIL-03: Pipeline G — EVM Merkle Root Emission

**Status:** Draft
**Author:** Fred (data backbone)
**Date:** 2026-04-02
**Pipeline:** G (EVM Emit)
**Depends On:** PRD-MIL-02 (Pipeline E Merkle tree)
**Runs After:** Pipeline F (Qdrant upsert) — must complete before G fires
**Git repo:** `$REPO_DIR/scripts/emit_merkle_roots.py`

---

## 1. Problem Statement

Once a document's Merkle tree is computed (Pipeline E), the root must be published to an EVM-compatible blockchain so it becomes an immutable, publicly verifiable commitment. The Horizen (ZEN) blockchain has EVM compatibility (via their EVM sidechain), making it the target chain.

Pipeline G reads tree JSON files, retrieves the original PDF hash from the registry, and calls a smart contract to append the Merkle root.

---

## 2. Goals

- Emit each document's Merkle root to the Horizen EVM sidechain
- One on-chain transaction per document (append-only — roots are never deleted)
- Record: `doc_id`, `merkle_root`, `pdf_hash`, `chunk_count`, `timestamp (block)`, `uploader`
- Support re-running without duplicate emissions
- Be independent of Qdrant state — if Qdrant is corrupted, on-chain roots remain valid

---

## 3. Input

```
/data/rag/merkle_trees/{doc_id}_tree.json
/data/rag/mil-docs-staging/new-unified-registry-v2.json
```

**From merkle tree JSON:**
```json
{
  "doc_id": "05f9cb1d...",
  "merkle_root": "0xabcd...1234",
  "chunk_count": 1433,
  "computed_at": "2026-04-02T12:00:00Z"
}
```

**From registry (pdf_hash):**
```json
{
  "doc_id": "05f9cb1d...",
  "sha256": "abc123...",       // original PDF hash
  "title": "US Army Survival Manual",
  "branch": "army"
}
```

---

## 4. Smart Contract Design

**Target chain:** Horizen EVM sidechain (ZEN)
**Language:** Solidity (compatible with EVM)

### 4.1 Contract: `MerkleRootRegistry`

**Canonical source:** `/Users/jack/zk-rag/contracts/MerkleRootRegistry.sol`

**Key design decisions (updated 2026-04-02):**

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Hash function | Poseidon (Goldilocks field) | Matches plonky2 v0.2.2 circuit — hash compatibility by definition |
| Field | Goldilocks (p = 2^64 - 2^32 + 1) | NOT BN254 — plonky2 native field |
| On-chain storage | Full MerkleCap (16 × bytes32 = 512 bytes) | Gas is cheap on Horizen L3; one-time expense |
| Doc ID type | bytes32 | Gas-efficient vs string; UUIDs/hashes convert naturally |
| Access control | Owner + allowlist | Only authorized addresses can append |
| Batch support | Yes — `batchAppendRoots()` | Reduces overhead for 983+ doc corpus |
| Dedup | keccak256(flattened cap) | Prevents duplicate emissions |

**Contract functions:**

| Function | Access | Purpose |
|----------|--------|---------|
| `appendRoot(docId, merkleCap[16], pdfHash, chunkCount)` | Owner/allowlist | Single document |
| `batchAppendRoots(docIds[], merkleCaps[], pdfHashes[], chunkCounts[])` | Owner/allowlist | Bulk emission |
| `getRootEntry(docId, index)` | Public | Read specific historical entry |
| `getLatestCapHash(docId)` | Public | Quick lookup |
| `isCapEmitted(capHash)` | Public | Dedup check |
| `getDocIds(offset, limit)` | Public | Paginated enumeration |
| `getRootCount(docId)` | Public | History length |
| `setAllowlist(account, allowed)` | Owner only | Manage uploaders |
| `transferOwnership(newOwner)` | Owner only | Transfer control |

**On-chain data per entry:**
```
bytes32[16] merkleCap    — full Poseidon MerkleCap (512 bytes)
bytes32     pdfHash      — SHA256 of original PDF
uint32      chunkCount   — real chunks before padding
uint40      blockNumber  — when recorded
uint40      timestamp    — block timestamp
address     uploader     — who submitted
```

### 4.2 Deployment

- **Chain:** Horizen Mainnet (Base) — Chain ID 26514
- **RPC:** https://horizen.calderachain.xyz/http
- **Block Explorer:** https://horizen.calderaexplorer.xyz/
- **Bridge:** https://horizen.hub.caldera.xyz/
- **Gas symbol:** ETH (for gas) / ZEN (token)
- **Deployer:** Mr. V (owner wallet)
- **Gas estimation:** ~150k gas per `appendRoot` call (conservative); batch reduces per-doc overhead
- **Estimated cost:** Low — Horizen L3 gas is very cheap; this is a one-time expense per document

### 4.3 Security Considerations

- **Append-only:** No `removeRoot` or `updateRoot` function — roots are never deleted or modified
- **Duplicate prevention:** `capEmitted` mapping (keyed by keccak256 of flattened cap) prevents re-emitting the same MerkleCap
- **Zero doc ID prevention:** Rejects `docId == 0`
- **Owner + allowlist access control:** Only owner or allowlisted addresses can call `appendRoot` / `batchAppendRoots`. Prevents registry pollution by unauthorized parties.
- **PDF hash included:** On-chain entry includes original PDF SHA256, enabling auditors to independently verify the Merkle tree was built from the correct file
- **Ownership transfer:** Owner can transfer to new address or add/remove allowlisted uploaders

---

## 5. Pipeline G Script Design

### 5.1 Data Flow

```
For each doc_id in merkle_trees/:
    1. Load {doc_id}_tree.json
    2. Load registry → get sha256 (PDF hash) for doc_id
    3. Call contract.appendRoot(doc_id, merkle_root, pdf_hash, chunk_count)
    4. Mark root as emitted (local state file)
    5. Log result
```

### 5.2 Emitted State Tracking

```
/data/rag/merkle_trees/emitted_roots.json
```

```json
{
  "emitted": [
    {
      "doc_id": "05f9cb1d...",
      "merkle_root": "0xabcd...1234",
      "tx_hash": "0xdef...789",
      "emitted_at": "2026-04-02T12:00:00Z",
      "block_number": 1234567
    }
  ],
  "pending": ["doc_id_1", "doc_id_2"]
}
```

### 5.3 Idempotency / Re-run Safety

Before calling `appendRoot`:
1. Check if `merkle_root` is in `emitted_roots.json` (by merkle_root value)
2. If yes, skip (already emitted)
3. If no, proceed with contract call
4. After successful tx, add to `emitted_roots.json`

This means re-running Pipeline G after a partial failure is safe — already-emitted roots are skipped.

### 5.4 RPC Configuration

```yaml
# /data/rag/config/evm_config.yaml
horizen_evm:
  rpc_url: "https://rpc.horizen.io/..."   # Mr. V to provide
  contract_address: "0x..."               # Deployed contract address
  chain_id: 7332                           # Horizen EVM sidechain chain ID (TBC)
  private_key_env: "ZKEVM_PRIVATE_KEY"     # Env var name containing deployer key
```

Private key read from environment variable (never hardcoded). Env var set in `/data/rag/.env` or system environment.

### 5.5 CLI Interface

```bash
python emit_merkle_roots.py \
    --doc-id <doc_id> \
    [--rpc-url <url>] \
    [--contract-address <address>] \
    [--dry-run] \
    [--batch]
```

**Arguments:**
| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--doc-id` | Yes (single) | — | Document ID to emit |
| `--rpc-url` | No | config file | EVM RPC URL |
| `--contract-address` | No | config file | Contract address |
| `--dry-run` | No | false | Show what would be emitted without calling RPC |
| `--batch` | No | false | Emit all docs in `merkle_trees/` that aren't emitted |

---

## 6. Error Handling

| Error | Behavior |
|-------|----------|
| merkle tree JSON not found | Skip, log error, continue to next |
| PDF hash not in registry | Skip, log error, continue; document not emitted |
| RPC call fails (network) | Retry up to 3 times with exponential backoff; after 3 failures, pause and alert |
| Transaction reverts (already emitted) | Add to emitted list, continue (idempotent by design) |
| Invalid merkle root (zero) | Skip, log error, continue |
| Insufficient gas / balance | Pause and alert via Telegram |

---

## 7. Monitoring and Alerts

- **Success:** Log tx hash, block number
- **Failure:** Telegram alert to Mr. V
- **Progress:** Log doc_id + count as emitted, e.g. `Emitted: 45/983`

---

## 8. Testing

### Local / Testnet Testing (Before Mainnet)
1. Deploy `MerkleRootRegistry` to Horizen testnet (or Ethereum Sepolia as proxy)
2. Run Pipeline G in `--dry-run` mode, verify inputs look correct
3. Emit 1-2 docs to testnet, verify tx succeeds
4. Verify `getLatestRoot()` returns correct value from contract
5. Verify `getRootHistory()` returns full history

### Regression / Safety Tests
1. Re-run Pipeline G on already-emitted docs → should skip all (no new txs)
2. Run Pipeline G with `--dry-run` on full corpus → should show ~983 docs to emit

---

## 9. Blocking Issues (Must Resolve Before Proceeding)

1. ~~Horizen EVM RPC URL~~ ✅ Resolved: https://horizen.calderachain.xyz/http (Chain ID 26514)
2. **Deployer private key:** Mr. V to provide the wallet private key (stored in env var, never in code)
3. **Contract deployment:** Deploy `MerkleRootRegistry.sol` to Horizen Mainnet and record contract address
4. ~~Chain ID confirmation~~ ✅ Resolved: 26514

---

## 10. Open Questions

| Question | Decision Needed | Recommendation |
|----------|----------------|----------------|
| Should we include `title` on-chain? | Yes/no | No — title is changeable metadata. PDF hash + doc_id are the authoritative identifiers. |
| Gas / fee payment | Who pays gas? | Mr. V's deployer wallet. Monitor gas costs after first emissions. |
| Rate limiting | Any throttling needed? | Pipeline G should add 1-2 second delay between calls to avoid RPC rate limits |
| Monitoring dashboards | Needed? | Not for initial version — logs + Telegram alerts are sufficient |
| Backup contract on Ethereum? | Yes/no | No — Horizen only for now. Can add Ethereum backup later if needed. |
