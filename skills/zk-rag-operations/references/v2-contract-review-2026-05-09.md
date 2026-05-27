# V2 Contract Review — 2026-05-09

**Reviewed:** `MerkleRootRegistryV2.sol` (274 lines) at `<REPO>pipeline_f/contracts/MerkleRootRegistryV2.sol`

## Verdict: V2 Logic — Fully Compatible, No Code Changes Needed

The contract stores raw `bytes32` merkle roots with no encoding assumptions. The new chunking algorithm (NFKC normalization + 8-byte Goldilocks packing → PoseidonHash) produces different Merkle roots than the old chunking — this is a **data** incompatibility, not a **code** incompatibility. The contract would accept the new roots perfectly well.

## Key Findings

### `_appendRoot` logic (lines 133–153)
- Deduplicates by `merkleRoot` alone via `require(!rootEmitted[merkleRoot], ...)`.
- If the same Merkle root is submitted twice, only the first succeeds.
- **Edge case:** If two different PDFs produced the same `merkleRoot` (astronomically unlikely), only the first would be accepted. The `RootAppended` event includes `docId` so on-chain tracing is possible.
- `rootHistory[docId]` is append-only — each emission appends a new entry with full metadata.

### Stored fields per emission
`RootEntry` contains: docId, merkleRoot, pdfHash, chunkCount, treeDepth, paddedLeafCount, block, timestamp, uploader. All fields are populated from Pipeline E output and Forge script calldata.

### Document hash field (`documentHash`)
Matches the circuit's Poseidon(doc_id_bytes) approach — same PoseidonHash with 8-byte Goldilocks field packing. Confirmed compatible.

## Why V3 Contract (Data Reason, Not Code Reason)

Old Merkle roots (from prior chunking) are live on V2. New roots (from new NFKC-chunked trees) would overwrite `latestRoot[docId]` if submitted to V2 — breaking the ZK proof chain that references the old roots. Deploying V3 keeps V2 roots as historical record and gives the new roots a clean contract.

## Contract Addresses

| Network | V2 (existing) | V3 (deploy fresh) |
|---------|---------------|-------------------|
| Testnet | `0x83166A340c0A61bc836BD6383aD4acB23a3E3176` | deploy new |
| Mainnet | `0x462fc86E28c07798BD4656451611FE4E0A6D7760` | deploy new |

## Before Pipeline F — V3 Deployment Prerequisites

1. Deploy V3 `MerkleRootRegistry` from `<REPO>pipeline_f/contracts/MerkleRootRegistryV2.sol`
2. Record new V3 addresses
3. Confirm `DEPLOYER_KEY` has authorization on V3 (`owner` or `allowlist`)
4. Run Pipeline F with V3 address
