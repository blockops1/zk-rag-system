# Military Documents RAG — Verifiable Pipeline Project Plan

**Last updated:** 2026-04-02
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
  ├── F: Qdrant upsert         (upsert_to_qdrant.py)                  → Qdrant
  └── G: EVM emit              (emit_merkle_roots.py)                → Horizen EVM
```

**Hard rule:** Pipeline F is the only pipeline that touches Qdrant. All other pipelines are offline-only.

---

## Pipeline Summary

| Pipeline | Script | Input | Output | Touches Qdrant? | Touches EVM? |
|----------|--------|-------|--------|-----------------|--------------|
| A | batch_ingest_branch.py --pass 1 | PDFs | ingested/ pages | No | No |
| B | batch_ingest_branch.py --pass 2 | extraction_queue.json | ingested/ pages | No | No |
| C | batch_image_describe.py | ingested/ | ingested-vision/ | No | No |
| D | chunk_document.py | ingested-vision/ | chunks/ | No | No |
| E | build_merkle_trees.py | chunks/ | merkle_trees/ | No | No |
| F | upsert_to_qdrant.py | chunks/ + merkle_trees/ | Qdrant | **YES** | No |
| G | emit_merkle_roots.py | merkle_trees/ + registry | Horizen EVM | No | **YES** |

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

Key design decisions:
- Poseidon (variant, not original Poseidon) over BN254 field
- Binary arity (arity=2), depth=13, max_leaves=8192 (zero-padded)
- All intermediate nodes stored in JSON
- Per-leaf Merkle paths stored for ZK circuit use
- Cross-validation test required: Python Poseidon output must match Plonky2 reference

**CLI:** `python build_merkle_trees.py --doc-id <id>`

**Runs after:** Pipeline D fully working, full corpus chunked, Qdrant serving queries (no rework after E is built)

---

### Pipeline F — Qdrant Upsert
**PRD:** (embedded in this document — no separate PRD needed)

Read chunk text + Merkle tree data, upsert vectors + Merkle metadata to Qdrant.

Key design decisions:
- Reads `chunks.jsonl` + `merkle_tree_{doc_id}.json`
- Per-point payload additions:
  - `merkle_leaf_hash`: Poseidon hash of chunk text
  - `merkle_leaf_index`: position in Merkle tree
  - `merkle_path`: array of sibling hashes at each level
- Per-document payload additions:
  - `merkle_root`: root of the Poseidon Merkle tree
  - `tree_depth`: 13
  - `chunk_count`: number of real chunks
- Uses existing `pipeline_d.py` upsert logic as starting point
- Dedup: SHA256 exact match against existing Qdrant doc_ids

**CLI:** `python upsert_to_qdrant.py --doc-id <id>`

---

### Pipeline G — EVM Emit
**PRD:** `PRD-MIL-03-pipeline-g-evm-emit.md`

Emit Merkle roots to Horizen EVM `MerkleRootRegistry` smart contract.

Key design decisions:
- Smart contract: `MerkleRootRegistry.sol` — append-only root storage
- One `appendRoot()` tx per document
- Records: doc_id, merkle_root, pdf_hash (SHA256 from registry), chunk_count, block_number, timestamp, uploader
- Emitted state tracked in `emitted_roots.json` for idempotency/re-run safety
- Runs after Pipeline F (F must complete before G fires — on-chain root must match Qdrant state)

**CLI:** `python emit_merkle_roots.py --doc-id <id>`

---

## ZK Circuit (Future — Not in This PRD Series)

**Purpose:** At query time, prove that returned chunks are in the committed Merkle tree.

Public inputs: on-chain Merkle root
Private witness: chunk texts + Merkle paths
Output: ZK proof (Plonky2/batch)

This is covered by separate ZK design work. Not started until Pipelines D-G are working.

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
/data/rag/
  mil-docs-staging/
    new-unified-registry-v2.json
    uploads/                    ← source PDFs
  ingested/                    ← Pipeline A+B output
  ingested-vision/             ← Pipeline C output
  chunks/                     ← Pipeline D output (DELETE existing before Pipeline D)
  merkle_trees/               ← Pipeline E output (NEW dir)
    {doc_id}_tree.json
    emitted_roots.json         ← Pipeline G state
  embeddings/                  ← Pipeline F intermediate (embeddings.npy per doc)
  qdrant_data/                ← Pipeline F output
  config/
    evm_config.yaml            ← Horizen RPC + contract address (NEW)

$REPO_DIR/scripts/
  chunk_document.py           ← Pipeline D
  build_merkle_trees.py       ← Pipeline E
  upsert_to_qdrant.py         ← Pipeline F
  emit_merkle_roots.py        ← Pipeline G
  MerkleRootRegistry.sol      ← Smart contract source
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

Phase 4: Pipeline F Qdrant upsert (after E validated)
  ├── Review /data/rag/embeddings/* (stale data?)
  ├── Run upsert_to_qdrant.py on all 983 docs
  └── Validate: Qdrant query returns results with Merkle metadata

Phase 5: Pipeline G EVM emit (after F validated)
  ├── Deploy MerkleRootRegistry.sol to Horizen EVM
  ├── Provide RPC URL + contract address
  ├── Run emit_merkle_roots.py --batch
  └── Validate: on-chain root matches Qdrant metadata
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
| Delete existing chunks before Pipeline D | Mr. V | Pending approval |
| Pipeline C must complete (currently running) | — | Running |
| Poseidon Python library validation (cross-test with Plonky2) | Ralph | Not started |
| Horizen EVM RPC URL | Mr. V | Pending |
| MerkleRootRegistry deployment | Mr. V | Not started |
