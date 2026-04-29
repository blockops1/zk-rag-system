# RAG Rebuild — Project Plan v8

|**Last updated:** 2026-04-17 (21:05 ET)
|**Git commit:** Phase A Rust prove-from-proof-path complete; git commit TBD
|**Status:** A+B+C+D+E done. F (EVM emit) not yet written. G (Qdrant upsert) not yet written. **Phase A (Rust prove-from-proof-path) complete 2026-04-17.**

---

## What Changed Since v4

### Git Repository Moved to Live Scripts Directory
- **Old:** Git working copy at `/tmp/document_rag_system/`, live scripts at `$REPO_DIR/scripts/` (not a git repo)
- **New:** `$REPO_DIR/` IS the git working copy. All git operations happen directly there.
- **Force-pushed** full history to GitHub (`git@github.com:youruser1/document_rag_system.git`)
- `.gitignore` excludes: venv/, venv-docling/, venv-pymupdf4llm/, qdrant_data/, pdfs/, ingested/, chunks/, embeddings/, images/, uploads/, logs/, __pycache__/, *.gguf, *.db, *.log

### Key Scripts Now in Git
| Script | Pipeline | Qdrant? |
|--------|----------|---------|
| `scripts/api_server.py` | RAG API server (port 8100) | Reads |
| `scripts/batch_ingest_branch.py` | A (--pass 1) and B (--pass 2) | NO |
| `scripts/batch_image_describe.py` | C — SmolVLM2 vision | NO |
| `scripts/pipeline_d.py` | D — chunk + embed | NO |
| `scripts/build_merkle_trees` (Rust) | E — Merkle tree builder | NO |
| `scripts/pipeline_f.py` | F — Qdrant upsert (**NOT YET WRITTEN**) | YES (only) |
| `scripts/pdf_processing.py` | Shared module for A + B | NO |
| `scripts/build_new_registry.py` | Builds/scans v2 registry | NO |
| `scripts/run_pipeline_a.sh` | A runner | — |
| `scripts/run_pipeline_b.sh` | B daemon (while-loop) | — |
| `scripts/run_pipeline_c.sh` | C runner | — |
| `scripts/run_pipeline_d.sh` | D runner | — |
| `scripts/run_pipeline_e.sh` | E runner | — |
| `scripts/run_pipeline_f.sh` | F runner (**TBD**) | — |

---

## Directory Structure

### Live Working Scripts (git working copy)
```
$REPO_DIR/                ← git init + remote origin (git@github.com:youruser1/document_rag_system.git)
  scripts/                         ← Active pipeline scripts (git-tracked)
    api_server.py                  ← FastAPI server (port 8100)
    batch_ingest_branch.py         ← Pipeline A + B (--pass 1 / --pass 2)
    batch_image_describe.py        ← Pipeline C (SmolVLM2)
    pipeline_d.py                  ← Pipeline D (Qdrant write — NOT TESTED)
    pdf_processing.py              ← Shared ingest/docling/image module
    build_new_registry.py          ← Registry scanner
    run_pipeline_a.sh              ← A runner
    run_pipeline_b.sh             ← B runner (daemon)
    run_pipeline_c.sh             ← C runner
    run_pipeline_d.sh             ← D runner
    _log.py                        ← Logging utility
    ingest_pdf.py                  ← Standalone PDF ingestion
    chunk_document.py              ← Page JSON → chunks
    embed_chunks.py                ← Chunks → embeddings
    build_index.py                 ← Qdrant + BM25 builder
    verify_ingest.py               ← Per-doc verification
    push_to_vps.sh                 ← Push to production VPS
    rag_healthcheck.sh             ← Cron health monitor
    rollback_on_failure.sh         ← Emergency rollback
    harvest.sh                     ← Harvester entry
    ...
  harvester/                       ← Internet Archive harvest scripts
  logs/                            ← Pipeline and API logs
  venv/                            ← Python env (fastembed, qdrant, fitz)
  venv-docling/                    ← Separate venv for docling OCR
  venv-pymupdf4llm/                ← Old venv (archived)
```

### Data Directories (git-ignored)
```
/data/rag/
  mil-docs-staging/               ← Registry + project docs + uploads
    new-unified-registry-v2.json  ← Authoritative registry (741 docs)
    uploads/                      ← Source PDFs (741 PDFs, 126 missing files)
  ingested/                        ← Pipeline A/B output: per-doc page JSONs (674 dirs)
  ingested-vision/                 ← Pipeline C output: pages with vision descriptions (430 dirs)
  images/                          ← Extracted images from PDFs
  chunks/                          ← Pipeline D output: Per-doc chunk JSONL (Jill → NFS write)
  embeddings/                      ← Pipeline D output: Per-doc embedding numpy (4096-dim, Jill → NFS)
  merkle_trees/                    ← Pipeline E output: Per-doc Merkle tree JSON (R730)
  emit_output/                    ← Pipeline F output: Per-doc EVM tx results JSON (R730)
  pdfs/                           ← Short-term PDF storage
  qdrant_data/                    ← Qdrant vector DB (Pipeline G output only)
  qdrant_config/                  ← Qdrant server config
  docs/                           ← Static web files served by OpenResty
  scripts/                        ← Symlink or copy of $REPO_DIR/scripts/

### Jill's NFS Mount (exports from R730)
```
R730 exports: 192.168.1.x:/data/rag/ingested          → ro
R730 exports: 192.168.1.x:/data/rag/ingested-vision   → ro
R730 exports: 192.168.1.x:/data/rag/chunks             → rw
R730 exports: 192.168.1.x:/data/rag/embeddings          → rw
```

### Archive Directories (safe to ignore)
```
/data/rag/archive_20260401/
/data/rag/qdrant_data_clean_20260324-183830/
/data/rag/qdrant_data_presnap/
/data/rag/qdrant_data_wipe_backup_20260324-181144/
/data/rag/qdrant_data.archive_20260326_*/     (multiple)
/data/rag/chunks.archive_20260326_*/
/data/rag/embeddings.archive_20260326_*/
/data/rag/images.archive_20260326_*/
$REPO_DIR/scripts_v2/
$REPO_DIR/scripts_v2_archived_20260331/
$REPO_DIR/scripts/archive/
```

---

## Seven-Pipeline Architecture

**HARD RULE: Only Pipeline G touches Qdrant. All other pipelines are completely offline.**

```
PDF (in uploads/)
    │
    ├─── Pipeline A: fitz extract  (batch_ingest_branch.py --pass 1)
    │       ├─ Dedup gate L1: SHA256 if status∈{extracted,ingested} (v2 registry)
    │       ├─ High-density (avg_chars >= 300): pages → ingested/{doc_id}/ → registry=extracted
    │       ├─ Low-density (avg_chars < 300): added to extraction_queue.json → Pipeline B
    │       └─ NO Qdrant writes
    │
    ├─── Pipeline B: docling OCR  (batch_ingest_branch.py --pass 2, daemon)
    │       ├─ Processes extraction_queue.json
    │       ├─ write_docling_pages(): docling → page JSONs → ingested/
    │       └─ Registry updated to status=extracted
    │
    ├─── Pipeline C: SmolVLM2 vision  (batch_image_describe.py, cron)
    │       ├─ Copy-first: ingested/ → ingested-vision/
    │       ├─ SmolVLM2-2.2B describes figure_only pages
    │       └─ Writes vision_description to COPY (ingested-vision/)
    │
    ├─── Pipeline D: Chunk + Embed  (Jill's Mac Mini — Qwen3-Embedding-8B, MLX)
    │       ├─ Reads from ingested-vision/ (preferred if exists) or ingested/ over NFS
    │       ├─ Hierarchical + semantic chunking → chunks.jsonl → /data/rag/chunks/
    │       ├─ Qwen3-Embedding-8B (MLX) → embeddings.npy → /data/rag/embeddings/ (4096-dim)
    │       └─ NO Qdrant writes. Completely offline. Jill writes results to NFS mount.
    │
    ├─── Pipeline E: Build Merkle Trees  (Rust binary: build_merkle_trees)
    │       ├─ Reads chunks.jsonl from Pipeline D output
    │       ├─ Builds Merkle tree per document
    │       ├─ Output: merkle_tree.json → /data/rag/merkle_trees/
    │       └─ NO Qdrant writes. Completely offline.
    │
    ├─── Pipeline F: EVM Emit  (NOT YET IMPLEMENTED)
    │       ├─ Reads Merkle roots from Pipeline E output
    │       ├─ Calls MerkleRootRegistry.appendRoot() on Horizen
    │       └─ Writes on-chain tx hash + block number to emit_output/{doc_id}.json
    │
    └─── Pipeline G: Qdrant Upsert  (NOT YET WRITTEN)
            ├─ Reads chunks + embeddings from Pipeline D
            ├─ Reads Merkle trees from Pipeline E
            ├─ Reads emit_output/{doc_id}.json from Pipeline F
            ├─ Upserts everything to Qdrant (vectors + metadata + Merkle fields + on-chain data)
            ├─ Registry updated to status=ingested
            └─ THE ONLY QDRANT WRITE POINT
```

### Pipeline Script Ownership

| Script | Pipeline(s) | Touches Qdrant? | Status |
|--------|-------------|-----------------|--------|
| `batch_ingest_branch.py --pass 1` | A | NO | Done |
| `batch_ingest_branch.py --pass 2` | B | NO | Done |
| `batch_image_describe.py` | C | NO | Done |
| Jill's pipeline_d.py | D | NO | Done (Jill's Mac Mini) |
| `build_merkle_trees` (Rust) | E | NO | Done — 697 trees built 2026-04-14 |
| `emit_merkle_roots.py` | F | NO (blockchain write) | **NOT YET WRITTEN** |
| `pipeline_g.py` | G | YES — only one | **NOT YET WRITTEN** |

---

## Queue and State Files

| File | Location | Purpose |
|------|----------|---------|
| `extraction_queue.json` | `/data/rag/extraction_queue.json` | Docs needing Pipeline B (low-density) |
| `extraction_queue_done.json` | `/data/rag/extraction_queue_done.json` | Docs completed by Pipeline B |
| `ingest_failed_retry.log` | `/data/rag/ingest_failed_retry.log` | Failed docs with error_type |
| `batch_ingest_branch_done.json` | `/data/rag/batch_ingest_branch_done.json` | Docs completed by Pipeline A |
| `needs_docling_queue` | `/data/rag/batch_ingest_branch_needs_docling.json` | **DEPRECATED** — file no longer used |
| `new-unified-registry-v2.json` | `/data/rag/mil-docs-staging/` | Authoritative registry (741 docs) |
| `deduped_doc_ids.json` | `/data/rag/mil-docs-staging/deduped_doc_ids.json` | Dedup skip list |

**Queue file mismatch — FIXED 2026-04-02:** `run_pipeline_b.sh` `queue_count()` now reads `extraction_queue.json`.

---

## Current Pipeline Status (2026-04-14 09:55 ET)

- **Pipeline A:** DONE — 614 extracted from uploads/. 126 docs have no PDF files (missing from uploads/).
- **Pipeline B:** IDLE — extraction queue empty.
- **Pipeline C:** DONE — 697 dirs processed by SmolVLM2.
- **Pipeline D:** COMPLETE — 697 docs chunked, 205K chunks, embeddings produced.
- **Pipeline E:** COMPLETE — 697 Merkle trees built (2026-04-14, 09:38–09:41). Trees use doc_id-first-leaf format (leaf[0] = Poseidon hash of doc_id bytes). Satisfies `merkleCap[0] == docId` contract requirement.
- **Pipeline F:** NOT YET WRITTEN — EVM emit (`emit_merkle_roots.py` does not exist yet). Reads 697 tree JSONs, calls `MerkleRootRegistry.appendRoot()` on Horizen, writes `emit_output/{doc_id}.json`.
- **Pipeline G:** NOT YET WRITTEN — Qdrant upsert (only write point). Needs Pipeline F output first.

**Contract deployment:** `MerkleRootRegistry.sol` fully tested (28 Foundry tests passing, Slither 0 issues). NOT yet deployed to any network. Mr. V to deploy to Horizen testnet.

Check services:
```bash
ps aux | grep "batch_image|batch_ingest" | grep -v grep
curl http://127.0.0.1:8100/health
sudo systemctl status qdrant
```

---

## Key Commands

```bash
# Activate venv
cd $REPO_DIR && source venv/bin/activate

# Check pipeline status
ps aux | grep "batch_image\|batch_ingest" | grep -v grep
python3 /tmp/check_c_progress.py   # Pipeline C progress

# Start Pipeline A (200 docs)
cd $REPO_DIR && ./scripts/run_pipeline_a.sh

# Start Pipeline B (daemon)
cd $REPO_DIR && ./scripts/run_pipeline_b.sh

# Start Pipeline C
cd $REPO_DIR && nohup ./scripts/run_pipeline_c.sh > logs/pipeline_c_latest.log 2>&1 &

# Check API health
curl http://127.0.0.1:8100/health

# Check collections
curl http://127.0.0.1:8100/collections

# Query
curl -X POST http://127.0.0.1:8100/query \
  -H "Content-Type: application/json" \
  -d '{"query": "enemy prisoner of war handling", "top_k": 5}'

# Git operations
cd $REPO_DIR && git status && git add . && git commit -m "message" && git push origin main
```

---

## What's Changed Since v6

### MerkleRootRegistry Security Hardening (2026-04-14)
**Commits on main:** `8da0231` (Pipeline E docId-first-leaf), `b9000e5` (28 Foundry tests), `fb5d803` (Slither fixes), `f21745f` (OZ NatSpec), `681286c` (zero-address guard), `5e808be` (security hardening)

**Contract changes** (`scripts/zk-rag/contracts/MerkleRootRegistry.sol`):
- **zero pdfHash rejected** - `require(pdfHash != bytes32(0), ...)`
- **docId embedded as merkleCap[0]** - validation: `merkleCap[0] == bytes32(docId)` in `_appendRoot`. Prevents cross-docID Merkle proof poisoning.
- `MAX_CHUNK_COUNT = 65535`
- `getDocIds` pagination fixed (memory variable, not storage)
- `offset >= len` guard clause added
- `abi.encode` replaces `abi.encodePacked` (prevents hash collision)
- `onlyAuthorized` on `appendRoot` - wallet key needed for emit transactions

**Pipeline E:** Updated and re-run 2026-04-14. Trees now prepend `PoseidonHash(doc_id_bytes)` as leaf[0]. 697 trees generated with `doc_id_leaf_index: 0` confirmed. Contract requirement satisfied.

**Pipeline F (`emit_merkle_roots.py`): NOT YET WRITTEN.**

---

### Security Audit & Hardening (Grok Recommendations — 2026-04-14)
**Stage: In progress — fixes being applied one at a time**

**Current gaps identified:**
1. `[FIX 1]` `setAllowlist` missing zero-address check — could accidentally set `address(0)`
2. `[FIX 2]` Compiler pinned with `^0.8.24` — should be exact `0.8.24` for reproducible builds
3. `[FIX 3]` No Foundry test suite — no unit tests covering happy path + revert paths
4. `[FIX 4]` NatSpec incomplete — `_appendRoot`, `setAllowlist`, `transferOwnership` lack `@param`/`@return`
5. `[FIX 5]` No static analysis run — Slither/Aderyn not yet executed

**Not applicable (by design):** upgradeable proxy, ERC20/721, reentrancy guards, flash-loan/oracle scenarios — contract is simple append-only with no external calls.

**Audit workflow (Grok-recommended):**
```
1. Write Foundry tests (Fix 3)
2. Run Slither/Aderyn (Fix 5)
3. Fix zero-address on setAllowlist (Fix 1)
4. Pin compiler exact (Fix 2)
5. Complete NatSpec (Fix 4)
6. Internal review
7. Professional audit (Code4rena / OpenZeppelin / Trail of Bits)
8. Deploy to Horizen testnet
9. Mainnet with multisig + timelock
```

---

## What's Pending
1. **[CRITICAL - Pipeline F]** Write `emit_merkle_roots.py`. Reads 697 Merkle tree JSONs, calls `MerkleRootRegistry.appendRoot()` on Horizen, writes `emit_output/{doc_id}.json`. Requires testnet contract address first.
2. **[CRITICAL - Contract Deployment]** Mr. V to deploy `MerkleRootRegistry.sol` to Horizen testnet (Chain ID: 26514, RPC: `https://horizen.calderachain.xyz/http`). Private key in env var `ZKEVM_PRIVATE_KEY`.
3. **[CRITICAL - Pipeline G]** Write Qdrant upsert. Reads D+E+F output, upserts to Qdrant (only write point), updates registry to ingested. Needs Pipeline F complete first.
4. **[HIGH]** Fuzz + invariant tests for MerkleRootRegistry (Echidna or Halmos)
5. **[HIGH]** Aderyn + Mythril static analysis (Slither done, Aderyn/Mythril not yet run)
6. **[HIGH]** Professional audit (Code4rena / OpenZeppelin / Consensys Diligence)
7. **[HIGH]** Deploy to mainnet with multisig wallet
8. **[MEDIUM]** VPS push — registry path mismatch fix needed before re-enabling.
9. **[MEDIUM]** Confirm Qdrant collection dimension: current 4096-dim (Pipeline D), G writes 384-dim BAAI/bge-small-en-v1.5. May need recreation.

---

## PROJ Doc History

| Version | Date | Key Changes |
|---------|------|-------------|
| v1 | 2026-03-27 | Initial rebuild plan |
| v2 | 2026-03-30 | Three-pipeline architecture (A/B/C), OpenResty, new registry |
| v3 | 2026-03-31 | pdf_processing.py module, offline Pipeline A |
| v4 | 2026-04-02 | Status-aware SHA256 dedup fix, Pipeline D design |
| v6 | 2026-04-14 | MerkleRootRegistry security hardening, docId as cap[0] requirement, Pipeline E re-run needed, Pipeline F+G pending |
| v7 | 2026-04-17 | Phase A Rust complete — `prove_from_proof_path()` + `ChunkProofInput` + `fill_zk_rag_witness_from_path()` + 3 unit tests passing; SECTION-zk-circuit-02 updated |
