# MerkleRootRegistryV2 — Specification

## Purpose

On-chain registry of Poseidon Merkle root commitments for the ZK-RAG document provenance system.
Each document's Merkle root is committed to this contract, enabling off-chain ZK proofs to
verify document integrity against an on-chain anchor. The ZK circuit requires `treeDepth`
and `paddedLeafCount` per entry to reconstruct proof paths correctly.

---

## Constants

| Name | Value | Notes |
|------|-------|-------|
| `VERSION` | `2` | |
| `CHAIN_ID` | `26514` | Horizen EVM mainnet |
| `MAX_BATCH_SIZE` | `200` | Max docs per `batchAppendRoots()` call |

---

## Data Structures

### RootEntry (struct)

```
merkleRoot:      bytes32   — Poseidon hash of the Merkle tree root
pdfHash:         bytes32   — SHA-256 of the PDF content
chunkCount:      uint32    — Number of content chunks in the document
treeDepth:       uint8     — Depth of the Merkle tree (for ZK circuit)
paddedLeafCount: uint32    — Number of leaf nodes after zero-padding (power of 2)
blockNumber:     uint40    — Block this entry was committed
timestamp:       uint40    — Block timestamp
uploader:        address   — msg.sender that committed this entry
```

Stored in `mapping(bytes32 docId => RootEntry[]) public rootHistory`.

---

## Storage Layout

```
_owner:           address
_rootHistory:     mapping(bytes32 => RootEntry[])
_totalEntries:    uint256
_allowlist:       EnumerableSet.AddressSet
```

---

## Immutables / Constants

- `VERSION`: hardcoded `2`
- `MAX_BATCH_SIZE`: `200`

---

## Access Control

- `owner()` — OpenZeppelin Ownable, deploy-time constructor argument
- `appendRoot()` / `batchAppendRoots()` — owner OR any address on allowlist
- `setAllowlist()` — owner only
- All other views — anyone

---

## Public View Functions

| Function | Returns | Notes |
|----------|---------|-------|
| `totalEntries()` | `uint256` | Total roots across all docs |
| `rootHistory(docId)` | `RootEntry[]` | All roots for a doc |
| `getRootCount(docId)` | `uint256` | Number of roots per doc |
| `getRootEntry(docId, index)` | `(bytes32,bytes32,uint32,uint8,uint32,uint40,uint40,address)` | 8-tuple: merkleRoot, pdfHash, chunkCount, treeDepth, paddedLeafCount, blockNumber, timestamp, uploader |
| `getLatestRoot(docId)` | `bytes32` | Most recent root for a doc |
| `isOnAllowlist(addr)` | `bool` | |

---

## Permissioned Functions

### appendRoot(docId, merkleRoot, pdfHash, chunkCount, treeDepth, paddedLeafCount)

Append a single root for a document.

- Access: owner OR allowlist
- Emits: `RootAppended(docId, merkleRoot, pdfHash, chunkCount, treeDepth, paddedLeafCount, blockNumber, uploader)`
- Reverts: if `treeDepth == 0` or `treeDepth > 32` or `chunkCount == 0`

### batchAppendRoots(docIds[], merkleRoots[], treeDepths[], paddedLeafCounts[], emitterOnlyDocs[])

Append a batch of roots. All entries validated before any state writes (two-phase).

- Access: owner OR allowlist
- Input: parallel arrays (all must be same length)
- Emits: `RootAppended` for each entry on success
- Reverts (entire tx) if array length mismatch or any entry fails validation
- At most `MAX_BATCH_SIZE` (200) entries per call

### setAllowlist(account, allowed)

Add or remove an address from the emission allowlist.

- Access: owner only
- **FIX (H-1)**: checks `EnumerableSet.add()` / `EnumerableSet.remove()` return value with `require()`
- Emits: `AllowlistUpdated(account, allowed)`

---

## Events

| Event | Fields |
|-------|--------|
| `RootAppended(bytes32 indexed docId, bytes32 indexed pdfHash, bytes32 merkleRoot, uint32 chunkCount, uint8 treeDepth, uint32 paddedLeafCount, uint40 blockNumber, address indexed uploader)` | All entries indexed for off-chain search |
| `AllowlistUpdated(address indexed account, bool allowed)` | |

---

## Invariants (for Halmos)

1. `totalEntries` is monotonically increasing — never decrements
2. Every entry in `rootHistory[docId]` was appended in block-number order (per-doc ordering)
3. `treeDepth` in every entry is 1-32
4. `chunkCount > 0` for every entry
5. `paddedLeafCount >= chunkCount` and is a power of 2
6. `isOnAllowlist(addr)` returns true iff addr is in the EnumerableSet

---

## Design Decisions

1. **No ERC-7201 namespaced storage** — fewer than 10,000 docs expected, flat mapping is fine
2. **`blockNumber` and `timestamp` as `uint40`** — sufficient until year 2365; saves gas vs uint256
3. **`treeDepth` and `paddedLeafCount` per entry** — ZK circuit needs these to verify proofs offline without reloading the full tree
4. **`pdfHash` indexed** — enables off-chain event filtering by document hash
5. **Allowlist over AccessControl** — simpler for this single-purpose contract
6. **Two-phase batch validation** — all entries checked before any state writes; prevents partial commits

---

## Compilation

- Solidity: `0.8.24`
- EVM: Paris
- IR pipeline (`via_ir = true`): required — "stack too deep" otherwise
- Optimizer: `runs = 10000`

---

## Deployment

| Network | Chain ID | Contract Address |
|---------|----------|----------------|
| Horizen Testnet | 2651420 | `0x83166A340c0A61bc836BD6383aD4acB23a3E3176` ✅ DEPLOYED 2026-04-24 |
| Horizen Mainnet | 26514 | NOT YET DEPLOYED |

- Owner: `0xBABc60eD1...`
- Constructor: `constructor(address initialOwner) Ownable(initialOwner)`
- Script: `script/DeployV2.s.sol`

---

## Changes: Old V2 → New V2

| Feature | Old V2 | New V2 |
|---------|--------|--------|
| `treeDepth` field | ❌ | ✅ `uint8` in `RootEntry` |
| `paddedLeafCount` field | ❌ | ✅ `uint32` in `RootEntry` |
| Batch emit | 1 doc/tx | ✅ `batchAppendRoots()` — 200 docs per tx |
| `pdfHash` indexed in event | ❌ | ✅ |
| `renounceOwnership` visibility | `public` | `external` |
| EnumerableSet return checks (H-1) | unchecked | ✅ `require(add/remove(...))` |
| `VERSION` constant | none | `2` |

---

## Files

- `contracts/MerkleRootRegistryV2.sol` — main contract
- `script/DeployV2.s.sol` — deployment script
- `script/CommitBatchV2.s.sol` — batch emit script with `--batch-size N` and `--all` flags
- `tests/MerkleRootRegistryV2.t.sol` — 18 unit tests
- `tests/MerkleRootRegistryV2Invariants.t.sol` — 2 Halmos invariant tests
