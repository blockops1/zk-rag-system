# Military Documents RAG — Verifiable Pipeline Project Plan

**Last updated:** 2026-04-15
**Git repo:** `$REPO_DIR/` (git@github.com:youruser1/document_rag_system.git)

---

## Overview

Four-pipeline document processing and semantic search system. Adding cryptographic verification via Poseidon Merkle trees and EVM smart contracts.

**Core goal:** Every chunk served by the RAG query API can be cryptographically proven to belong to a specific document committed on-chain at a verifiable timestamp.

---

## Pipeline Architecture

```
PDF
  │
  ├── A: fitz extract          (batch_ingest_branch.py --pass 1)     → ingested/
  ├── B: docling OCR           (batch_ingest_branch.py --pass 2)     → ingested/
  ├── C: SmolVLM2 vision       (batch_image_describe.py)             → ingested-vision/
  │
  ├── D: chunking              (chunk_document.py)                   → chunks/
  ├── E: Merkle tree           (build_merkle_trees.py)                → merkle_trees/
  ├── F: EVM emit              (emit_all.py)                         → Horizen EVM
  └── Fb: sync block metadata  (sync_block_metadata_to_registry.py)   → registry
```

**Hard rule:** Pipeline F is the only pipeline that touches the EVM. Pipeline Fb is the only pipeline that reads from the EVM. All other pipelines are offline-only.

---
| Pipeline | Script | Input | Output | Touches Qdrant? | Touches EVM? |
|----------|--------|-------|--------|-----------------|--------------|
| A | batch_ingest_branch.py --pass 1 | PDFs | ingested/ pages | No | No |
| B | batch_ingest_branch.py --pass 2 | extraction_queue.json | ingested/ pages | No | No |
| C | batch_image_describe.py | ingested/ | ingested-vision/ | No | No |
| D | chunk_document.py | ingested-vision/ | chunks/ | No | No |
| E | build_merkle_trees.py | chunks/ | merkle_trees/ | No | No |
| F | emit_all.py | merkle_trees/ + registry | Horizen EVM | No | **YES** |
| Fb | sync_block_metadata_to_registry.py | on-chain contract | registry (block/timestamp) | No | **READ** |

---

## New Pipeline Detail

### Pipeline D — Chunking
**PRD:** `PRD-MIL-01-pipeline-d-chunking.md`

Split page JSONs into overlapping text chunks. Reads from `ingested-vision/`, writes to `chunks/`.

Key design decisions:
- `chunk_size=512`, `overlap=100` characters
- `RecursiveCharacterTextSplitter` with expanded separator list
- Figure-only pages: include `vision_description` as chunk text
- Output: `chunks.jsonl` + `chunk_ids.json` per document
- Registry updated with `chunk_count` after processing

**CLI:** `python chunk_document.py --doc-id <id>`

---

### Pipeline E — Merkle Tree Build
**PRD:** `PRD-MIL-02-pipeline-e-merkle-tree.md`

Build Poseidon Merkle tree from chunks. Reads from `chunks/`, writes to `merkle_trees/`.

Key design decisions (UPDATED 2026-04-02):
- **Rust binary** (not Python) — uses same plonky2 v0.2.2 library as ZK circuit
- **Poseidon hash over Goldilocks field** (NOT BN254/Poseidon2) — matches plonky2 exactly
- **Hash compatibility guaranteed by definition** — no cross-validation test needed
- Binary arity (arity=2), flexible depth (adapts to chunk count)
- Per-leaf Merkle paths + leaf hashes stored in JSON
- 7-byte LE packing for bytes→field elements (same as ZK circuit)

**CLI:** `build_merkle_trees --doc-id <id>` (Rust binary in zk-rag crate)

**Runs after:** Pipeline D fully working, full corpus chunked, Qdrant serving queries (no rework after E is built)

---

### Pipeline F — EVM Emit
**PRD:** `PRD-MIL-03-pipeline-g-evm-emit.md` (repurposed)

Emit MerkleCap commitments to the Horizen EVM `MerkleRootRegistry` smart contract.

Key design decisions:
- Smart contract: `MerkleRootRegistry.sol` — append-only cap storage, no Qdrant involvement
- Reads `merkle_cap` (16-element array) from registry — populated by Pipeline F sync step
- One `appendRoot()` tx per document
- Records: doc_id, merkle_cap, pdf_hash (SHA256), chunk_count, block_number, timestamp, uploader
- Idempotency: checks `emitted_testnet.status == "emitted"` before attempting tx
- Deduplication: contract rejects duplicate caps via `capEmitted` mapping
- After successful tx, updates registry with `tx_hash`, `emitted_at`, `uploader`

**CLI:** `python emit_all.py --batch` (full corpus) or `python emit_all.py --doc-id <id>`

---

### Pipeline Fb — Sync Block Metadata to Registry
Reads back on-chain block/timestamp for emitted documents and writes them into the registry.

Key design decisions:
- Uses `cast call` (Foundry) to read `rootHistory(docId, 0)` from the deployed contract
- Extracts `blockNumber`, `blockTimestamp`, `uploader` for each emitted doc
- Updates registry `emitted_testnet` record with full metadata
- Safe to re-run — only overwrites with more authoritative on-chain data

**CLI:** `python sync_block_metadata_to_registry.py`

---

## ZK Circuit (Future — Not in This PRD Series)

**Purpose:** At query time, prove that returned chunks are in the committed Merkle tree.

Public inputs: on-chain Merkle root
Private witness: chunk texts + Merkle paths
Output: ZK proof (Plonky2/batch)

This is covered by separate ZK design work. Not started until Pipelines D, E, F are working.

---

## Existing Chunks — Action Required

**Before Pipeline D runs, existing chunks must be deleted.**

```
/data/rag/chunks/         ← 245 doc dirs, 190MB — INVALID, delete before Pipeline D
/data/rag/embeddings/     ← may contain stale embeddings, review before Pipeline F
```

**Deletion command:**
```bash
rm -rf /data/rag/chunks/*
rm -rf /data/rag/embeddings/*
```

These were created by the old pipeline architecture and do not match the new chunking spec.

---

## Registry Updates

After each pipeline, the registry (`new-unified-registry-v2.json`) is updated:

| Pipeline | Registry Update |
|----------|----------------|
| D (chunking) | `chunk_count` set |
| E (Merkle) | `merkle_tree_computed: true` |
| F (Qdrant) | `status: "ingested"`, `merkle_root` added |
| G (EVM) | `on_chain_tx` added, `on_chain_block` added |

---

## Directory Structure (Updated)

```
$DATA_DIR/        ← canonical data root (was /data/rag/)
  registry.json                  ← central registry (742 docs)
  merkle_trees/
    {doc_id}_tree.json           ← Pipeline E output
    emitted_roots.json           ← Pipeline F legacy state (migration source)
  ingested/                      ← Pipeline A+B output
  ingested-vision/               ← Pipeline C output
  chunks/                        ← Pipeline D output

$REPO_DIR/pipeline_f/
  emit_all.py                    ← Pipeline F
  sync_merkle_cap_to_registry.py ← one-time cap sync (local only)
  sync_block_metadata_to_registry.py ← Pipeline Fb
  contracts/
    MerkleRootRegistry.sol       ← smart contract source
  script/
    Deploy.s.sol                 ← deployment script
    AppendRoot.s.sol             ← per-doc emit script
```

---

## Execution Order

```
Phase 1: Pipelines A, B, C (current — running now)
  └── Process all 983 docs through A, B, C

Phase 2: Pipeline D chunking (after A, B, C complete)
  ├── Delete /data/rag/chunks/* (old invalid chunks)
  ├── Run chunk_document.py on all 983 docs
  └── Validate: chunk counts match expected distribution

Phase 3: Pipeline E Merkle trees (after D validated, Qdrant serving)
  ├── Validate Poseidon cross-validation test passes
  ├── Run build_merkle_trees.py on all 983 docs
  └── Validate: roots reproducible from chunk files

Phase 4: Pipeline F EVM emit (after E validated)
Phase 5: Pipeline Fb sync block metadata (after F — backfill on-chain data into registry)
```

---

## PRDs

| # | Name | File | Status | Depends On |
|---|------|------|--------|-----------|
| MIL-01 | Pipeline D Chunking | PRD-MIL-01-pipeline-d-chunking.md | Draft | C complete |
| MIL-02 | Pipeline E Merkle Tree | PRD-MIL-02-pipeline-e-merkle-tree.md | Draft | D validated, Qdrant working |
| MIL-03 | Pipeline G EVM Emit | PRD-MIL-03-pipeline-g-evm-emit.md | Draft | E complete |

Pipeline F does not have a separate PRD — spec is in this document.

---

## Blocking Issues

| Issue | Owner | Status |
|-------|-------|--------|
| DEPLOYER_KEY env var for emit_all.py | Mr. V | Pending |
| sync_merkle_cap_to_registry.py --write (~45 docs missing merkle_cap) | Fred | Ready |
| emit_all.py --batch (678 docs pending emission) | Fred | Ready — needs DEPLOYER_KEY |
| sync_block_metadata_to_registry.py (backfill 19 emitted docs) | Fred | Ready |
| ~~Delete existing chunks before Pipeline D~~ | — | ✅ Stale — pipeline architecture changed |
| ~~Pipeline C must complete~~ | — | ✅ Done |
| ~~Poseidon Python library validation~~ | — | ✅ Eliminated — Pipeline E is Rust using plonky2 |
| ~~Horizen EVM RPC URL~~ | — | ✅ Resolved: https://horizen-testnet.rpc.caldera.xyz/http |
| ~~MerkleRootRegistry deployment~~ | — | ✅ Deployed at 0x2E276196d82252aac48854bf1F044B095468A310 (chain 2651420) |
