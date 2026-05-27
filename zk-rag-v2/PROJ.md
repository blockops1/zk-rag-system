# ZK-RAG v2 — Project Plan

**Date**: 2026-05-19
**Status**: COMPLETE — 527 docs on-chain, ingested, all titles approved
**Canonical directories**:
- Scripts: `./` — all pipeline scripts, tests, contracts
- Production data + logs: `.<DATA>/` — chunks, embeddings, merkleTrees, registry, logs

---

## Current Pipeline Status

| Pipeline | Name | Script Location | Status | Notes |
|----------|------|----------------|--------|-------|
| A | fitz extract | `pipeline_a/` | ✅ DONE | Single-threaded fitz, one PNG/page, upright rendering. |
| B | docling OCR | `pipeline_b/` | ✅ DONE | |
| C | SmolVLM2 vision | `pipeline_c/` | ✅ DONE | Vision descriptions written in-place to page JSONs. |
| D | Chunk + Embed | `pipeline_d/` | ✅ DONE | D1: LlamaIndex HierarchicalNodeParser. D2: fastembed + NomicEmbedText-v1.5 (768d). |
| E | Merkle trees | `pipeline_e/` | ✅ DONE | Single-root PoseidonHash. |
| F | EVM emit | `pipeline_f/` | ✅ DONE — ALL 527 ON-CHAIN | V3 mainnet `0x462fc86E28c07798BD4656451611FE4E0A6D7760`. |
| G | Qdrant upsert | `pipeline_g/` | ✅ DONE — 527/527 INGESTED | Fresh Qdrant DB. Batched upserts. |

**Registry**: 527 total | 527 ingested | 527 emitted_mainnet | all pipeline_e complete | all has_embeddings | all title_status=approved

---

## `.env` Network Configuration

**Location:** `./.env`

```bash
ACTIVE_NETWORK=mainnet          # flip to "testnet" for testnet

# Testnet (chain 2651420)
TESTNET_CHAIN_ID=2651420
TESTNET_RPC_URL=https://horizen-testnet.rpc.caldera.xyz
TESTNET_CONTRACT_ADDRESS=0x83166A340c0A61bc836BD6383aD4acB23a3E3176
TESTNET_EXPLORER=https://horizen-testnet.explorer.caldera.xyz

# Mainnet (chain 26514)
MAINNET_CHAIN_ID=26514
MAINNET_RPC_URL=https://horizen.calderachain.xyz/http
MAINNET_CONTRACT_ADDRESS=0x462fc86E28c07798BD4656451611FE4E0A6D7760
MAINNET_EXPLORER=https://horizen.calderaexplorer.xyz
```

**Usage:** `source ./.env && python3 emit_all.py --batch --limit N`

The script reads `ACTIVE_NETWORK` and auto-selects `CHAIN_ID`, `RPC_URL`, `CONTRACT_ADDRESS`, `EXPLORER_URL`. No per-run env var passing needed.

---

## Architecture

```
PDF (sourcePDF/)
    │
    ├─── Pipeline A: fitz text extraction
    │       Output: extracted/{doc_id}/pages/*.json
    │
    ├─── Pipeline B: docling OCR (low-density pages)
    │       Output: extracted/{doc_id}/pages/*.json (overwrites A)
    │
    ├─── Pipeline C: SmolVLM2 vision (figure pages)
    │       Output: extracted-vision/{doc_id}/pages/*.json
    │
    ├─── Pipeline D: Chunk + Embed
    │       Output: chunks/{doc_id}/chunks.jsonl
    │              embeddings/{doc_id}/embeddings.npy (1024-dim, Qwen3-Embedding-0.6B)
    │
    ├─── Pipeline E: Merkle trees (Rust) — SINGLE ROOT
    │       Output: merkleTrees/{doc_id}_tree.json
    │       Note: per-document Merkle tree with single Poseidon root (not cap)
    │
||    ├─── Pipeline F: EVM emit — SINGLE ROOT
||    │       Input: merkleTrees/
||    │       Output: emit_output/{doc_id}.json (on-chain tx)
||    │       Mainnet V2: `0x462fc86E28c07798BD4656451611FE4E0A6D7760` — 604/604 docs emitted ✅
    │
|    ├─── Pipeline G: Qdrant upsert — SINGLE ROOT
    │       Input: chunks/ + embeddings/ + merkleTrees/ + emit_output/
    │       Output: Qdrant collections (army, navy, marines, other)
    │       HARD RULE: Only Pipeline G touches Qdrant.
    │       Note: Fresh Qdrant DB built 2026-05-18 with 527 docs.
    │
    └─── Query Services (two-process design)
            │
            ├─── Embedding Service (port 8200)
            │       Owns Qwen/Qwen3-Embedding-0.6B model exclusively
            │       Bounded semaphore (max N concurrent encodes) — controls memory
            │       Memory limit via systemd MemoryMax= — hard OOM ceiling
            │       Called by RAG API over HTTP; connection-pooled
            │
            └─── RAG API Server (port 8100)
                    FastAPI: /api/query, /api/collections, /health, etc.
                    Calls embedding service for vectorization; does BM25 + RRF locally
                    Serves static files (/images/)
```

**Why split embedding service?**
- The embedding model is memory-heavy and must always stay warm
- Separating it lets us enforce hard memory limits (systemd `MemoryMax=`) on the VPS
- The RAG API can be lightweight and stateless; scales independently
- Both are copied together to the VPS as systemd units

---

## Pending Tasks

### ZK-Integrated Query — Phase Q + R Update (DONE 2026-04-22)

**Phase Q ✅ — Qdrant siblings re-ingest (DONE):**
- All 4 Qdrant collections (army, navy, marines, other) have `merkle_siblings[]` and `poseidon_doc_id_hash` in every point
- Verified: army sample has 10 siblings / depth 10, marines has 9, other has 8

**Phase R ✅ — Provenance API wired to Qdrant (DONE 2026-04-22):**
- `shared/provenance.py`: `get_chunk_metadata()` now reads all proof inputs from Qdrant payload (matching `test-from-qdrant.py` design)
- Previously read from disk tree JSONs — now uses `merkle_leaf_hash`, `merkle_leaf_index`, `merkle_tree_depth`, `merkle_siblings[]`, `merkle_root`, `poseidon_doc_id_hash`, `evm_block_number`, `evm_block_timestamp` directly from Qdrant
- Added `ingestion_timestamp` and `ingestion_block` fields to `ChunkMetadata` dataclass and `generate_proof()` input JSON
- `generate_proof()` now passes `ingestion_timestamp` + `ingestion_block` to the Rust prove binary
- API server (PID 1068587) needs restart to pick up changes: `sudo systemctl restart zk-rag-api`
- `test-from-qdrant.py` (2026-04-22) is the canonical reference implementation for Qdrant-based proof generation

**Phase R status:** Endpoints (`/api/provenance/{chunk_id}`, `/status`, `/manifest`) are all wired and working. The background flow was confirmed working 2026-04-22 00:47-00:48 (logs show `Finalized` status on zkVerify for multiple chunks). Only the data source was updated — all other machinery unchanged.

**To activate updated code:**
```bash
sudo systemctl restart zk-rag-api
```

### Infrastructure (in progress — PROJ-infrastructure-migration.md)

Qdrant has been moved to `/data/qdrant/database/`. Pipeline log migration is incomplete.

- [ ] **T-10**: Fix `run_pipeline_b.sh` LOG_DIR: `./rag/logs` → `.<DATA>/logs`
- [ ] **T-11**: Fix `run_pipeline_c.sh` LOG_DIR: `./rag/logs` → `.<DATA>/logs`
- [ ] **T-12**: Fix `run_pipeline_e.sh` LOG_DIR: `./rag/logs` → `.<DATA>/logs` (+ fix BINARY and OUT_DIR paths)
- [ ] **T-13**: Find or create `run_pipeline_d.sh` (currently only in `archive/`)
- [ ] **T-14**: Find or create `run_pipeline_a.sh`
- [ ] **T-16**: Create `run_pipeline_g.sh` with `LOG_DIR=".<DATA>/logs"`
- [ ] **T-19**: Archive stale `/data/rag/qdrant_data/` after confirming nothing needed
- [ ] **T-20**: Archive stale `./rag/qdrant_data/`
- [ ] **T-21**: Update `docs/admin.md` with correct paths

### Pipeline C — Vision Image Extraction Review (TODO: 2026-04-22)

**Risk identified:** Image-to-page association may be broken — could require full re-extraction of all images.

**What Pipeline C does:**
1. `extract_images.py` — runs once, extracts images from PDFs via PyMuPDF, writes to `.<DATA>/images/{doc_id}/manifest.json` + `page_NNNN_img_00.{png|jpg}`
2. `batch_image_describe.py` — runs per-doc, copies from `extracted/` to `extracted-vision/`, finds images via `page_{page_num:04d}_img_00.png` (hardcoded), calls SmolVLM2
3. `reingest_vision.py` — reads from `extracted-vision/`, writes descriptions back into page JSONs, re-chunks and re-embeds

**The core risk — `extract_images.py` does not store image xref or position:**

`extract_images.py` extracts images with only `doc_id`, `page_num` (1-indexed), `img_idx`, `filename`. It uses `page.get_images(full=True)` which returns a list per page. The filename is `page_{page_num:04d}_img_{img_idx:02d}.{ext}` — **sorted by `img_idx`**, not by spatial position on page.

`batch_image_describe.py` looks up images as `page_{page_num:04d}_img_00.png` — **always `img_idx=0`**, assuming the first image returned by PyMuPDF is the main figure. This is unreliable:
- If a page has multiple images, `img_idx=0` might be a logo/header, not the main figure
- PyMuPDF returns images in creation order, not spatial order
- Some pages may have 0 images extracted but still be `figure_only=true`

**Questions to answer before the review:**
1. Does the `manifest.json` image count match the `figure_only=true` page count across the corpus?
2. Do pages with `figure_only=true` but zero extracted images exist?
3. Are multi-image pages being handled correctly, or is `img_idx=0` always the wrong image?
4. Does the re-ingest step (`reingest_vision.py`) correctly associate the described image back to the original PDF page?
5. Are `page_num` values in the image manifest and page JSONs actually in sync?

**Review steps:**
- [ ] Audit: for all docs with `figure_only=true` pages, cross-reference `images/{doc_id}/manifest.json` against `extracted/{doc_id}/pages/*.json` with `figure_only=true`
- [ ] Check for pages flagged `figure_only=true` that have zero images in the manifest
- [ ] Check for pages with multiple images (manifest `img_idx > 0` entries)
- [ ] Verify `reingest_vision.py` correctly reads from `extracted-vision/` (copy-first) and that the page JSON `page` field matches the original PDF page number
- [ ] If images are misaligned: **all image extraction must be redone** — `extract_images.py` needs to store spatial position (bbox) per image and name files accordingly

### Pipeline E — Merkle Tree (single-root redesign ✅ DONE 2026-04-20)

**Completed:**
- [x] Rebuilt Pipeline E as `zk-circuit/pipeline_e/` Rust binary — reads chunks.jsonl, builds Poseidon Merkle tree, outputs single `merkle_root`
- [x] `build_from_hashed_leaves()` + `build_single_root()` + `get_merkle_proof()` + `compute_root_from_proof()` added to `circuit/src/merkle_tree.rs`
- [x] `pub mod merkle_tree` exported from `circuit/src/lib.rs`
- [x] Binary rebuilt: `zk-circuit/target/debug/pipeline_e`
- [x] Ran on 5 docs (2026-04-20): trees written to `.<DATA>/merkleTrees/`

**ZK proof generation — COMPLETE 2026-04-20:**
- [x] `test-from-chunks` binary built — loads real chunks, rebuilds identical Merkle tree, generates plonky2 ZK proof, verifies it
- [x] Hashing aligned with Pipeline E: `hash_leaf_text()` uses NFKC normalization + 8-byte packing (same as Pipeline E)
- [x] `hash_doc_id()` for leaf[0] = Poseidon(SHA256(doc_id_bytes)) (same as Pipeline E)
- [x] Shared `MerkleTree::build_from_hashed_leaves()` used by both Pipeline E and test-from-chunks
- [x] `chunk_hash` is a PUBLIC INPUT — verifier can independently confirm chunk matches
- [x] All 5 documents tested: roots and depths MATCH Pipeline E trees exactly
- [x] ZK proofs saved to `.<DATA>/zk_proofs/zk_proof_<doc_id>_<chunk>.json`

**Pre-built circuits — COMPLETE 2026-04-21 (Phase J):**
- [x] `prove-bin --build-circuit <depth>` serializes `CircuitData` to `circuit_depth{N}.bin`
- [x] Pre-built files for depths 5-12 exist at `zk-circuit/target/release/`
- [x] `prove-bin` loads from disk in ~1ms; proof generation ~24ms total

**Re-run needed:**
- [ ] Re-run Pipeline E on remaining emitted docs to produce new-format tree JSONs

| Pipeline G — Qdrant Upsert

**Status (2026-04-27):** Full dataset re-ingest complete. R730 Qdrant holds 172,624 total points across 4 collections: army (84,654), navy (50,709), marines (9,728), other (12,592). VPS Qdrant rebuilt separately with ~594 docs.

**All 604 docs emitted to mainnet V2.** Qdrant is now in sync with emitted docs.

- [x] Qdrant moved to `.<DATA>/qdrant/` (fresh directory, 2026-04-17)
- [x] Logging added to pipeline_g.py (`.<DATA>/logs/pipeline_g.log`)
- [x] API server updated to use `QdrantClient(path=".<DATA>/qdrant")`
- [x] **Pipeline G re-ingest complete** — full 172,624-point dataset in R730 Qdrant

### Title Review — COMPLETED 2026-05-19 ✅

**Final state**: 527 approved documents. 18 documents deleted during review (blank/garbled OCR, non-military content, duplicates). Pipeline H title review complete — no further batches.

**Data sources**:
- Current title → **Qdrant** (payload `title` field)
- Chunk 0 text → **Local disk** at `.<DATA>/chunks/{doc_id}/chunks.jsonl` (first line = chunk_index 0)

**Status (2026-05-XX)**:
- Batch 1 (docs 1-50) → `./title_review_batch_1.md` — **PENDING REVIEW**
- Remaining: ~536 docs in batches of 50 (~11 more batches)

**Hard rules**:
- Never bulk-sync titles — only scroll-by-doc_id + `set_payload` per doc
- Apply DELETES before title updates
- Full doc_id in all output (no truncation)
- Delete criterion: page 1 blank/cancelled/placeholder AND title is garbage

**Apply script pattern**: see `zk-rag-registry-title-fix` skill.

### Contract — V2 Rewrite with ZK Fields (DEPLOYED TESTNET 2026-04-24)

**What was wrong with the old V2:**
- No `treeDepth` or `paddedLeafCount` — ZK circuit cannot verify proofs without these
- `EnumerableSet.add/remove` return values unchecked in `setAllowlist()` (H-1 bug)
- Single doc per tx — 39 failed emissions out of 604

**V2 rewrite (current, deployed to testnet):**

| Feature | Old V2 | New V2 |
|---------|--------|--------|
| `treeDepth` field | ❌ | ✅ `uint8` in `RootEntry` |
| `paddedLeafCount` field | ❌ | ✅ `uint32` in `RootEntry` |
| Batch emit | 1 doc/tx | ✅ `batchAppendRoots()` — 200 docs per tx |
| `pdfHash` indexed in event | ❌ | ✅ (Aderyn L-4 fix) |
| `renounceOwnership` visibility | `public` | `external` (Aderyn L-3 fix) |
| EnumerableSet return checks | unchecked | ✅ `require(add/remove(...))` (H-1 fix) |
| `VERSION` constant | none | `2` |

**Deployment:**
|| Network | Chain ID | Contract | Status |
|---------|----------|----------|--------|
| Horizen Testnet | 2651420 | `0x83166A340c0A61bc836BD6383aD4acB23a3E3176` | ✅ DEPLOYED 2026-04-24 |
| Horizen Mainnet | 26514 | `0x462fc86E28c07798BD4656451611FE4E0A6D7760` | ✅ DEPLOYED 2026-04-24 — 604/604 docs on-chain |

- Owner: `0xBABc60eD17e6387AEDab112E80744aA19EFCb723`
- Batch size: 200 docs per `batchAppendRoots()` call → 4 txs for 604 docs
- Old V2 testnet (`0x2E276196d...`): DEPRECATED
- Old V2 mainnet (`0x2E276196d...`): DEPRECATED

**RPC URLs:**
- Mainnet: `https://horizen.calderachain.xyz/http`
- Testnet: `https://horizen-testnet.rpc.caldera.xyz`

**Files:**
- `pipeline_f/contracts/MerkleRootRegistryV2.sol` — current contract (10,616 bytes)
- `pipeline_f/script/DeployV2.s.sol` — deployment script
- `pipeline_f/script/CommitBatchV2.s.sol` — batch emission script
- `pipeline_f/tests/MerkleRootRegistryV2.t.sol` — 18 unit tests (all passing)
- `pipeline_f/tests/MerkleRootRegistryV2Invariants.t.sol` — 2 Halmos invariant tests

**Security scan results:**
| Scanner | Result |
|---------|--------|
| Slither | 0 HIGH/Med — 5 informational (OZ library) |
| Aderyn | 0 HIGH — 5 LOW (all cosmetic) |
| Halmos | 2/2 invariant tests passed |

---

## Canonical Directory Map

```
./          ← Scripts (git repo)
  pipeline_a/                      ← A: harvest, ingest, pdf processing
  pipeline_b/                      ← B: docling loop
  pipeline_c/                      ← C: SmolVLM2 vision
  pipeline_d/                      ← D: chunk + embed
      chunk_document.py            ← chunking (Jill's, from rag-chunker-embedder/)
      embed_docs_cpu.py            ← embedding (CPU, sentence-transformers)
      run_pipeline_d.sh            ← MISSING — needs to be created
  pipeline_e/                      ← E: Merkle trees (Rust binary)
  pipeline_f/                      ← F: EVM emit
      emit_all.py                  ← batch emit script (READY)
      contracts/                   ← MerkleRootRegistry.sol
  pipeline_g/                       ← G: Qdrant upsert
      pipeline_g.py                ← Qdrant upsert script (WORKS)
      run_pipeline_g.sh            ← MISSING — needs to be created
  shared/                          ← api_server.py, batch_ingest_branch.py, etc.
  zk-circuit/                     ← ZK circuit (Rust)

.<DATA>/           ← Production data
  sourcePDF/                     ← Source PDFs
  extracted/                       ← A + B output (page JSONs)
  extracted-vision/                ← C output (page JSONs + vision_description)
  chunks/                          ← D output: chunks.jsonl per doc
  embeddings/                      ← D output: embeddings.npy per doc (1024-dim)
  merkleTrees/                    ← E output: *_tree.json per doc
  registry.json                    ← Registry (527 docs)
  logs/                            ← All pipeline logs

.<DATA>/qdrant/     ← Qdrant vector DB (fresh, 2026-04-17)
.<DATA>/logs/       ← All pipeline logs
```

---

## API Server — Query Service

**Section plan:** `docs/SECTION-api-server-01-query-server.md`

**Current state (2026-04-28):**
- `zk-rag-api.service` running on port 8100 ✅
- `GET /health` ✅ `{"status":"ok","qdrant":"connected","model":"Qwen/Qwen3-Embedding-0.6B","bm25":"disabled"}`
- `GET /api/context` ✅ — restored 2026-04-28 (was accidentally deleted during X402 rewrite)
- `POST /api/query` (vector + hybrid) ✅
- **Provenance endpoints**: emit_tx read from registry ✅, ZK proof generation ~11ms ✅
- **Full dataset in R730 Qdrant**: 172,624 points across 4 collections (army 84,654, navy 50,709, marines 9,728, other 12,592)
- **Auto-submit to Kurier on prove** ✅ — `kurier_job_id` returned in prove response
- **Auto-poll from website** ✅ — both nav buttons and expand button trigger polling
- **VPS Qdrant**: ~594 docs rebuilt from R730 data

**Phase ordering:**
1. Phase 1 — Local dev server ✅ (done)
2. Phase 2 — ZK Proof of Provenance (in progress)
3. Phase 3 — VPS Deployment ✅ (2026-04-27 — clean nginx deployed, rate limiting removed, full dataset synced)
4. Phase 4 — X402 Payment

**Phase 2: ZK Proof of Provenance — IN PROGRESS**
- Build ZK bridge: Rust prove binary + Python wrapper ✅
- Merkle proof retrieval from `merkleTrees/` JSON files ✅
- Pre-built circuit files (Phase J ✅) — provenance uses them (~11ms per proof) ✅
- Kurier submission ✅ (tested end-to-end)
- Emit tx lookup from registry ✅ (604/604 emitted docs have tx_hash in registry)

**Phase 2.1: Auto-Submit to Kurier — DONE 2026-04-28**
- `POST /api/provenance/prove` auto-submits to Kurier immediately after proof generation
- `kurier_job_id` persisted to disk (`PROOFS_DIR/{chunk_id}.json`) and returned in API response
- Kurier failures are non-critical — endpoint still returns proof with `kurier_job_id: null`
- Manual submission still available via `kurier_submit.py`

**Phase 2.2: Auto-Poll from Website — DONE 2026-04-28**
- `_autoPollKurier(chunkId, jobId)` polls Kurier immediately after proof response
- Nav buttons (← Prev / Next →): `handleNavProvenance` → `_handleNav` → `_autoPollKurier` ✅
- ZK expand button (🔗 ZK Prove): `wireZKButtons` click handler → `_autoPollKurier` ✅
- `pollInFlightKurierJobs()` resumes polling on page reload for any in-flight jobs
- Toast notifications visible without opening ZK submenu (fixed-position `#zk-toast` div)
- `AUTO_SUBMIT_PROOF = true` — both flows auto-poll

**Phase 2.3: Remove Kurier from prove-bin — DONE 2026-04-28**
- All Kurier CLI flags and `KurierClient` removed from Rust binary
- Kurier submission is API-only (`submit_proof_to_zkverify`) or manual (`kurier_submit.py`)

**Phase S.1: ZK Prove UI Refactor — DONE 2026-04-28 (commit `364b6b6`)**
- Root cause fix: `if (!statusEl) return` silently bailed when submenu was closed — status element only existed inside popup DOM
- After: `.zk-status-badge` is an inline `<span>` rendered at card build time, always present in DOM — polling loop can never find a null element
- UI: popup submenu (4-5 items) → single inline badge click → `⏳ Generating…` → `📤 Submitted…` → `🔄 Polling…` → `✅ Verified` (all inline, no popup)
- Modal: results modal + decode modal → single combined "Verification Results" modal (tx hash, public inputs table, full decode/explainer, Download Proof, Verify on zkVerify)
- "How it works": separate modal → always-visible page footer section below search results
- Files: `website/js/renderer.js`, `website/js/app.js`, `website/js/event-handlers.js`, `website/index.html`
- Net: -187 lines across 4 files

**Service Consolidation — DONE 2026-04-28**
- `rag-api.service` (enabled) and `zk-rag-api.service` (disabled) were duplicate unit files (identical `ExecStart`, same port 8100)
- `rag-api.service` was crash-looping (1461 restarts) because `zk-rag-api.service` held port 8100
- Resolution: `zk-rag-api.service` is now canonical (enabled, running). `rag-api.service` disabled and archived to `/data/archive/rag-api.service-20260428170734`
- **Canonical service is now `zk-rag-api.service`** — use `sudo systemctl restart zk-rag-api.service` for restarts

**Running Tests — CORRECT INVOCATION**
Tests require the project's venv Python, NOT the hermes agent venv:
```bash
cd ./zk-rag-v2 && PYTHONPATH=./zk-rag-v2 ./.venv/bin/python3 -m pytest tests/ -v
```
Test files that error on import: `shared/test_amcp.py` (missing `pdf_processing` module), `shared/test_pending_check.py` (requires absent PDF) — predate current project structure, not runnable.

---

## ZK-Integrated Query — Option B: Full Qdrant Sourcing

**Status:** ✅ DONE — `POST /api/query-provable` implemented 2026-04-22
**Date:** 2026-04-22 (implemented)
**Goal:** Every chunk returned by the query endpoint has a pre-computed ZK proof available before the text is displayed. All proof inputs come from Qdrant — no disk I/O during proof generation.

### Architecture — Two Distinct Query Endpoints

| Endpoint | Proofs | Use Case |
|----------|--------|----------|
| `POST /api/query` | None — fast path | API consumers who don't need provenance |
| `POST /api/query-provable` | ZK proof gated — blocks until proof exists | Website, clients requiring full ZK guarantee |

**Key constraint:** No chunk text is returned via `query-provable` unless the ZK proof for that chunk exists. If proof generation fails, the chunk is dropped from results.

### Implementation (2026-04-22)

**`POST /api/query-provable`** (`api_server.py`, `query_provable()`):
- Queries Qdrant normally (vector + optional hybrid BM25)
- `ThreadPoolExecutor(max_workers=ZK_PROOF_PARALLELISM)` generates proofs in parallel
- `ZK_PROOF_PARALLELISM` env var: default 2, set to 4 on R730, 2 on VPS
- Drops any chunk where proof fails (no text without proof)
- Returns: `{chunks[], proofs{}}` — text + proof together, no text leaves server without proof

**`generate_proof_from_payload(chunk_id, payload)`** (`provenance.py`):
- Takes Qdrant payload dict directly — no re-query of Qdrant needed
- Proof data is already in memory from the search result
- All fields parsed from payload: `merkle_leaf_hash`, `merkle_siblings`, `merkle_root`, `merkle_tree_depth`, `poseidon_doc_id_hash`, `evm_block_number`, `evm_block_timestamp`

**ZK_PROOF_PARALLELISM deployment:**
- R730: `ZK_PROOF_PARALLELISM=4` — 56 cores, plenty of headroom
- VPS: `ZK_PROOF_PARALLELISM=2` — 4 cores, embedding model also running

### New Data Flow

```
User query
    │
    ▼
POST /api/query-provable
    │
    ▼
Qdrant lookup ──retrieves──▶ chunks + merkle_root + leaf_hash + tree_depth + siblings[]
    │
    ▼
For each chunk (server-side, before response):
    generate_proof(siblings, leaf_hash, merkle_root, depth)
    │
    ▼
Return: { chunks[], proofs{} }  ← proof generated BEFORE chunk text is shared
```

### Qdrant Payload Change

Each point in Qdrant gains these new fields (stored by Pipeline G):

```json
{
  "merkle_leaf_hash": "0xabcd...1234",     // Poseidon(chunk_text) — already exists
  "merkle_leaf_index": 42,                  // already exists
  "merkle_path": [                          // NEW — was only in tree JSON
    {"hash": "0xh1", "at_depth": 1},
    {"hash": "0xh2", "at_depth": 2},
    ...
  ],
  "merkle_root": "0xabcd...5678",          // already exists
  "merkle_tree_depth": 8,                   // already exists
  "poseidon_doc_id_hash": "0x9876...dcba"   // NEW — Poseidon(doc_id_bytes) = leaf[0]
}
```

**`poseidon_doc_id_hash` is new.** It is `leaf[0]` of the Merkle tree (Poseidon of the doc_id bytes) and is a public input to the ZK circuit. It must be stored per-chunk in Qdrant because the circuit needs it and the chunk's Qdrant payload is the only source.

### Implementation Phases

#### Phase Q — Pipeline G Re-ingest with Siblings
**Status: ✅ DONE 2026-04-22**

All 4 collections (army, navy, marines, other) have `merkle_siblings[]` and `poseidon_doc_id_hash` in every point. Verified via direct Qdrant scroll API — army chunks have 10 siblings at depth 10.

---

#### Phase R — ZK-Integrated Query API Endpoint
**Status: ✅ DONE — DATA SOURCE UPDATED 2026-04-22**

The API endpoints were already fully wired and working. On 2026-04-22 the data source was updated from disk tree JSONs to Qdrant payloads, matching the `test-from-qdrant.py` reference implementation.

**What changed 2026-04-22:**
- `shared/provenance.py`: `get_chunk_metadata()` rewired to read from Qdrant via `client.scroll()` with `Filter(FieldCondition(key="chunk_id", match=MatchValue(value=chunk_id)))`
- `ChunkMetadata` dataclass: added `ingestion_timestamp` and `ingestion_block` fields
- `generate_proof()`: now passes `ingestion_timestamp` and `ingestion_block` to the Rust prove binary
- Qdrant payload fields used: `merkle_leaf_hash`, `merkle_leaf_index`, `merkle_tree_depth`, `merkle_siblings[]`, `merkle_root`, `poseidon_doc_id_hash`, `evm_block_number`, `evm_block_timestamp`
- API server requires restart: `sudo systemctl restart zk-rag-api`

**Canonical reference:** `zk-circuit/test-from-qdrant.py` (2026-04-22) — end-to-end script sourcing all proof inputs from Qdrant.

**Files changed:** `shared/provenance.py`

**New endpoint:**
```
POST /api/query-provable
Body: { query: string, top_k: int, collection: string, hybrid: bool }

Response: {
  chunks: [
    {
      chunk_id, doc_id, text, page, score,
      merkle_root, merkle_leaf_hash, merkle_leaf_index,
      merkle_tree_depth, poseidon_doc_id_hash,
      ...
    }
  ],
  proofs: {
    "<chunk_id>": {
      proof_hex: "0x...",
      public_inputs: { merkle_root, document_hash, chunk_hash },
      merkle_root, document_hash, chunk_hash,
      status: "ready" | "failed",
      error?: string
    },
    ...
  }
}
```

**Implementation (2026-04-22 actual):**
- `provenance.py`: `generate_proof_from_payload(chunk_id, payload)` — reads all proof inputs from the Qdrant payload dict passed directly from the search result (no disk I/O, no re-query of Qdrant)
- `api_server.py`: `POST /api/query-provable` endpoint
  - Queries Qdrant normally (reuses `_search_single_collection`)
  - Collects all result chunks with `merkle_leaf_hash` + `merkle_siblings`
  - Proofs generated in parallel via `ThreadPoolExecutor(max_workers=ZK_PROOF_PARALLELISM)` — env var default 2, R730 uses 4
  - **Only chunks with successful proofs are returned** — any chunk whose proof fails is dropped from results
  - Returns `{chunks[], proofs{}}` — text + proof together, no text leaves server without proof
- Chunks without `merkle_siblings` in Qdrant are silently skipped (not provable yet — would need Pipeline G re-ingest)

**Note:** Keep `POST /api/query` unchanged (no proofs — fast path for non-provable queries).

---

#### Phase S — Website Provenance UI ✅ DONE (2026-04-26)

**All three sub-issues resolved:**

1. **✅ Verify-on-chain fires from nav cards** (commit `c93af48`)
   - Root cause: `buildPassageCard` dispatched `zk-verify` CustomEvents that nothing caught
   - Fix: ZK submenu handlers now call `window._verifyOnChain(chunkId)` etc. directly
   - Prior art: `wireZKButtons` was supposed to handle `zk-verify` but had dual-listener bug (cloneNode replaced original button)

2. **✅ IA document links appear in nav cards** (commit `6819cc9`)
   - `buildPassageCard` now renders "View on page N" and "📄 View full document" links
   - Document title in nav cards is now `<a class="doc-title-link">` with readable color (commit `fbbd7f8`)

3. **✅ Auto-submit to Kurier on proof generation** (commits `8a44c1d`, `10fb2b4`)
   - `AUTO_SUBMIT_PROOF = true` flag in `app.js` line ~193
   - `_handleNav` calls `window._verifyOnChain` immediately after `setZkCache` — no manual button click needed
   - Playwright confirmed: Kurier job `8ccceaed-41c4-11f1-99a3-e2579a7a7a7dd2` submitted automatically end-to-end

**Files changed:**
- `website/js/renderer.js` — ZK submenu → direct window function calls; IA links in nav cards
- `website/js/app.js` — `AUTO_SUBMIT_PROOF = true`; debug console.log in `_handleNav`
- `website/js/event-handlers.js` — provenance button → `handleSearchProvenance`
- `website/index.html` — doc-title-link CSS: `color: #93c5fd`

**Git history (website refactor):**
```
d33df43 fix(website): make doc-title-link readable + add debug logs for auto-submit
10fb2b4 feat(website): enable AUTO_SUBMIT_PROOF — auto-submit proofs to Kurier on generate
8a44c1d feat(website): add AUTO_SUBMIT_PROOF flag — set true to auto-submit proofs to Kurier on generate
6819cc9 fix(website): add IA links to nav cards + link doc title to full document
c93af48 fix(website): wire verify/decode/download directly in buildPassageCard submenu — no more dead CustomEvents
8d86243 fix(website): wire search-with-provenance button and add handleSearchProvenance
9fe8a47 biome+fallow: fix critical _wireZkMenu ref, dedupe nav handlers, export wireZkMenu
d67b70a WEBSITE-007+008: Remove legacy inline script block, preserve backwards compat
4264690 WEBSITE-006: create app.js orchestration layer
1a691c0 WEBSITE-005: extract event listeners into event-handlers.js
6e964f6 WEBSITE-004: prove-on-demand — search returns plain chunks, ZK proofs fetched on button click
e6f880e WEBSITE-003: fix load-more — extracted renderResults(), state.js now authoritative
479fc83 WEBSITE-002: extract state management + HTML builders into js/state.js + js/renderer.js
5d828f3 WEBSITE-001: extract HTTP calls into website/js/api.js ES module
2a84130 Add PRD and prd.json for website modular refactor
e0ad54f Add POST /api/provenance/prove endpoint for single-chunk ZK proof generation
```

**Pending cleanup:**
- [ ] Remove debug `console.log` statements from `app.js` (`_handleNav`, `handleNavProvenance`)
- [ ] Remove debug Playwright scripts (`debug-nav-zk.py`, `debug-nav-zk2.py`, `debug-auto-submit.py`)
- [ ] Two docs need re-ingestion (poor PDF rendering): FM 3-06.11 + Field Hygiene

**Design:**

Each passage card gets a "Prove" button area below the passage text:

```
┌─────────────────────────────────────────────────────────┐
│ [ARMY] FM 4-90                    Page 12 / Chapter 3  │
│ ..."the commander's intent"...                          │
│ Score: 0.87                                            │
├─────────────────────────────────────────────────────────┤
│ [Prove ▾]                                              │
│                                                         │
│  Status: ● Proof ready              [Verify on zkVerify] │
│           ○ Verifying...                                │
│           ○ Verified: Finalized block #12345678         │
│                                                         │
│  Merkle root: 0x9927...               [View on Horizen]│
│  Chunk hash:  0x3f9a...                                │
│  Document ID: 0x00c8...                                │
└─────────────────────────────────────────────────────────┘
```

**Button States:**

| Button | Condition | Action |
|--------|-----------|--------|
| **Verify on zkVerify** | Proof `status === "ready"` | `POST /api/provenance/{chunk_id}/verify` → submits to Kurier → polls → shows Finalized block |
| **Verifying...** (disabled) | During Kurier polling | Button disabled, shows spinner |
| **View on Horizen** | Always available | Opens `https://sepolia.explorer.horizen.io/tx/{evm_tx_hash}` in new tab |
| **View on zkVerify** (grayed out) | `status !== "finalized"` | Grayed out, `cursor: not-allowed` |
| **View on zkVerify** (active) | `status === "finalized"` | Opens `https://zkverify.io/explorer/job/{job_id}` |

**Verification flow (new endpoint):**
```
POST /api/provenance/{chunk_id}/verify
  - Reads siblings[] from Qdrant payload (not disk)
  - Calls generate_proof() → submit_to_zkverify() → returns job_id immediately (non-blocking)
  - Response: { job_id, status: "submitted" }

GET /api/provenance/{chunk_id}/verify/status?job_id=xxx
  - Polls Kurier for job status
  - Response: { status: "Queued" | "Submitted" | "IncludedInBlock" | "Finalized" | "Failed", block_number? }
```

**UI implementation:**
1. Change `index.html` to call `POST /api/query-provable` instead of `POST /api/query`
2. Add collapsible "Prove" section per passage card (click to expand)
3. Each section shows: proof status chip, Merkle root, chunk hash, document hash
4. Buttons: "Verify on zkVerify", "View on Horizen", "View on zkVerify"
5. "View on zkVerify" starts grayed; activates when verification completes
6. Polling: JS polls `/api/provenance/{chunk_id}/verify/status?job_id=xxx` every 5s until Finalized

**New API endpoints needed:**
- `POST /api/provenance/{chunk_id}/verify` — non-blocking submit + return job_id
- `GET /api/provenance/{chunk_id}/verify/status?job_id=xxx` — poll Kurier status

**Files changed:** `website/index.html`, `shared/api_server/api_server.py`

---

### End-to-End Flow

```
User types query
    │
    ▼
Browser → POST /api/query-provable
    │
    ▼
API server: for each of top-5 chunks:
    - generate ZK proof (prove-bin, ~24ms each)
    - proof_hex + public_inputs stored in response
    │
    ▼
Browser ← { chunks[], proofs{} }

For each chunk:
    Browser displays passage text
    Browser shows: "● Proof ready"
    Browser enables: [Verify on zkVerify] [View on Horizen]
    [View on zkVerify] is grayed out
    │
    ▼ (user clicks "Verify on zkVerify")
    Browser → POST /api/provenance/{chunk_id}/verify
    │
    ▼
API server:
    - generate_proof_from_qdrant() [already done, could cache]
    - submit_to_zkverify() → job_id
    - return { job_id }
    │
    ▼
Browser ← { job_id }
Browser enables [Verifying...] (disabled button)
Browser polls GET /api/provenance/{chunk_id}/verify/status?job_id=xxx
    │
    ▼ (Kurier Finalizes)
Browser ← { status: "Finalized", block_number: 12345678 }
Browser enables [View on zkVerify] (active)
    │
    ▼ (user clicks "View on zkVerify")
Browser opens: https://zkverify.io/explorer/job/{job_id}
```

---

### What's Changed vs. Old Design

| Aspect | Old | New |
|--------|-----|-----|
| Proof trigger | Manual button click | Automatic on query (server-side) |
| Proof inputs | Tree JSON on disk | Qdrant payload only |
| Verification | Separate API call | Inline button on each chunk |
| zkVerify submit | `get_provenance()` (blocking) | `POST /verify` (non-blocking) |
| Chain explorer | Manual link | Inline button |
| Siblings stored | Tree JSON files only | Qdrant payload + tree JSON |

---

## ZK Proof of Provenance

**New Design (2026-04-20): Single-root per document**

The original design used a MerkleCap (16 cap entries at height 4) with `RandomAccessGate` for cap lookup. This introduced plonky2 bugs and unnecessary complexity. The new design:

- **One document = one Merkle tree = one root** (single HashOut)
- No cap, no `RandomAccessGate`, no recursive proof aggregation
- The ZK circuit proves: "this chunk is in the Merkle tree rooted at this document's single root"
- Simpler, correct, easier to verify

**Pre-built ZK Circuits (Phase J ✅):**
- Circuit files exist for depths 5-12: `circuit_depth{N}.bin`
- `prove-bin` loads pre-built `CircuitData` from disk for fast proof generation (~24ms)
- See `SECTION-zk-circuit-02-implementation.md` Phase J for details

**Provenance uses pre-built circuits** ✅ — `prove-bin` auto-loads `circuit_depth{N}.bin` from `CIRCUIT_DIR`.

**Section plans:**
- `docs/SECTION-zk-circuit-01-design.md` — circuit architecture, single-root Merkle tree, on-chain model (updated 2026-04-20)
- `docs/SECTION-zk-circuit-02-implementation.md` — Phase A-J implementation plan (pending update: pre-circuit integration)

**Phase 2 task breakdown (updated 2026-04-21):**
- [x] **Phase A — Design**: Update SECTION-zk-circuit-01 with single-root architecture ✅ DONE
- [x] **Phase B — Pipeline E update**: Update tree format — emit single `merkle_root` HashOut instead of 16-entry cap ✅ DONE 2026-04-20
- [x] **Phase C — Pipeline F update**: Update `emit_all.py` to extract single `merkle_root` (string) instead of `merkle_cap[0]` ✅ DONE 2026-04-20
- [x] **Phase D — Pipeline G update**: Update Qdrant payload schema (single `merkle_root` string per doc) ✅ DONE 2026-04-20
- [x] **Phase E — Circuit + E2E**: ✅ DONE — `test-from-chunks` binary builds clean, 9/9 tests pass, 6 docs proof-generated and verified
- [x] **Phase G-1 — Verify binary**: ✅ DONE 2026-04-20 — standalone `verify-zk-proof` binary using `plonky2_verifier::verify()`, test proof verified VALID
- [x] **Phase G-2 — Kurier/zkVerify Submission**: ✅ DONE 2026-04-21 — `kurier_submit.py` E2E test PASSED, proof finalized in ~30s
- [x] **Phase H — Provenance API**: ✅ DONE 2026-04-21 — `shared/provenance.py` full orchestration, `GET /api/provenance/{chunk_id}` works end-to-end
- [x] **Phase J — Pre-built circuits**: ✅ DONE 2026-04-21 — `circuit_depth{N}.bin` files for depths 5-12, prove-bin auto-loads
|- [x] **Phase K — Integrate pre-built circuits into Provenance**: ✅ DONE — `prove-bin` auto-loads `circuit_depth{N}.bin`. Broken `/api/prove` endpoint removed.
- [x] **Phase L (emit_tx)**: ✅ DONE — `get_emit_tx()` reads from registry.json
- [x] **Phase Q (Qdrant direct prove script)**: ✅ DONE — `test-from-qdrant.py` queries Qdrant directly, no disk I/O for proof inputs
- [x] **Phase L (block_number backfill)**: ✅ DONE — block_number queried and backfilled for all emitted docs
- [x] **Phase M — E2E Provenance Test**: ✅ DONE 2026-04-21 — full flow RAG query → prove → Kurier → zkVerify link ✅
| [x] **Phase N — Website Provenance Button**: ✅ DONE — prev/next+provenance buttons, ZK proof fetch/submit, Kurier submission all wired in website (2026-04-22)
| [x] **Phase O — Pipeline G Full Re-ingest**: ✅ DONE — full 172,624-point dataset in R730 Qdrant (2026-04-27)

**Phase E results (2026-04-20):**
- `zk-circuit/test-from-chunks/` binary — full E2E: load chunks → build tree (identical to Pipeline E) → generate ZK proof → verify
- All hashing imported from `zk_circuit::merkle_tree` — NFKC normalization, 8-byte packing, Poseidon
- `chunk_hash` is PUBLIC INPUT — verifier can independently confirm chunk matches
- 5/5 docs tested: merkle_root and depth MATCH Pipeline E trees exactly
- ZK proofs saved: `.<DATA>/zk_proofs/zk_proof_<doc_id>_<chunk>.json`

**Phase G-2 — Kurier/zkVerify Submission (COMPLETE 2026-04-21):**
- `zk-circuit/kurier_submit.py` — Python CLI for submitting plonky2 proofs to Kurier/zkVerify
- Proof format validated: `proof` (raw hex), `publicSignals` (plonky2 wire hex string), `vk` (bare hex string)
- URL: `POST /api/v1/submit-proof/{apiKey}` → returns `{jobId, optimisticVerify}`
- Poll: `GET /api/v1/job-status/{apiKey}/{jobId}` — status transitions: Queued → Submitted → IncludedInBlock → Finalized
- End-to-end test PASSED: proof `c5997755-3d16-11f1-99a3-e2579a7a7dd2` finalized in ~30 seconds on zkVerify
- Supports `--unregistered` flag (VK registered on-submit), `--testnet` flag, `--max-wait` for polling
- API key: `KURIE_API_KEY` env var or `--api-key` argument

**ZK Proof Public Input Model (Phase 1 Model A — COMPLETE):**
- `merkle_root` (public) — document's committed Poseidon root
- `document_hash` (public) — SHA-256 of PDF bytes (leaf[0])
- `chunk_hash` (public) — Poseidon(chunk_text), verifiable by API server
- Private: `siblings[]`, `index_bits[]` — the actual Merkle proof

**Next up:** Phase C — update Pipeline F emit to use single `merkle_root` string

---

## Phase 1b: PRD v2 — Completed Context

**Status:** Completed 2026-04-15

Key gaps identified in Phase 1 code that v2 fixes:
- `llm_input_hash` was a public input but the circuit did not constrain LLM input text to produce that hash (v2 circuit fixes this)
- Corpus build hashes unpadded bytes as leaves; circuit hashes padded limbs — both must pad identically (v2 fixes this in `build_corpus_merkle_tree_v2()`)

**Output:** `mil-docs-pipelines/PRD-zk-rag-v2.md` — full v2 specification
**Working codebase:** `mil-docs-pipelines/zk-rag-v2/` (exact copy of Phase 1 working codebase)
**Original codebase:** `./rag/scripts/zk-rag/` — untouched, 39/39 tests passing

---

## Implementation Log

### 2026-04-02 — Phase 1 Complete ✅ (39/39 tests passing)

| Module | File | Tests | Purpose |
|--------|------|-------|---------|
| merkle | src/circuits/merkle.rs | 10 | Merkle tree build/prove/verify + byte encoding |
| corpus | src/corpus.rs | 9 | Chunk loading, Poseidon sort, JSON serialization |
| zk_rag | src/circuits/zk_rag.rs | 5 | plonky2 circuit: K=5 Merkle inclusion + text hashing |
| witness | src/witness.rs | 6 | Witness assembly from RAG pipeline output |
| prove | src/prove.rs | 3 | Circuit build, proof generation, serialization |
| verify | src/verify.rs | 3 | Proof verification (in-memory + JSON roundtrip) |
| e2e | tests/test_e2e_synthetic.rs | 3 | Full pipeline integration test |

**Key technical decisions:**
1. Rust nightly required (plonky2 v0.2.2 uses `#![feature]`)
2. PoseidonHash throughout (Poseidon2 not available in v0.2.2)
3. plonky2 native MerkleTree API (not Hashcloak reimplementation)
4. 7-byte field element packing (56-bit, safe for Goldilocks modulus)
5. Cap height auto-clamping for small trees
6. Reduced limb sizes (512/1024/512) for practical circuit size
7. Leaf padding mandatory — both tree and circuit must hash identical padded data
8. No CircuitData JSON transport — verifier rebuilds deterministic circuit

**plonky2 v0.2.2 API surface confirmed:**
- `MerkleTree::new(leaves, cap_height)` ✅
- `MerkleTree::prove(leaf_index)` → `MerkleProof` ✅
- `verify_merkle_proof_to_cap()` ✅ (standalone fn and CircuitBuilder method)
- `CircuitBuilder::hash_or_noop()` and `hash_n_to_hash_no_pad()` ✅
- `CircuitBuilder::add_virtual_cap(cap_height)` ✅

### 2026-04-21 — Pipeline E Fresh Run + Registry Reconciliation ✅

**Problem:** Registry showed `has_merkle_tree=true` for 691 docs but only 5 tree JSON files existed on disk. Registry was out of sync with actual files.

**Actions:**
1. Archived 5 existing tree JSONs → `/data/archive/merkleTrees/`
2. Reset `has_merkle_tree=false` for all 696 docs in registry
3. Ran Pipeline E fresh on 5 docs → rebuilt trees to disk
4. Ran `sync_registry_merkleTrees.py --doc-id <id> --no-backup` for each → registry correctly synced

**Pipeline E output (5 docs):**

| doc_id (short) | chunks | depth | real leaves | padded |
|---|---|---|---|---|
| `00c8a75d...` | 166 | 8 | 256 | 89 |
| `00cdeace1...` | 17 | 5 | 32 | 14 |
| `016c0f8e6...` | 187 | 8 | 256 | 68 |
| `03fb4720...` | 232 | 8 | 256 | 23 |
| `04701ff24...` | 177 | 8 | 256 | 78 |

**Leaf[0] = doc_id:** Pipeline E prepends `PoseidonHash(doc_id_bytes)` as leaf[0]. The doc_id is the 64-char hex string (SHA-256 of PDF bytes). Text chunks at indices 1..N.

**`sync_registry_merkleTrees.py` updated:** Added `tree_depth` sync from `tree_config.depth` (nested path). Previously missed.

---

### 2026-04-21 — Provenance Script Optimization ✅

**Problem:** `get_chunk_metadata()` was doing a Qdrant scroll query across 6 collections (sequential, slow) to fetch chunk text — but `generate_proof()` never used the chunk text. This added seconds of latency per provenance request.

**Fix:** Removed the unnecessary Qdrant scroll. All data needed for ZK proof generation (leaf_hash, siblings, merkle_root, tree_depth) is already in the Pipeline E tree JSON files. No Qdrant lookup required.

**Results:**

| Step | Before | After |
|------|--------|-------|
| `get_chunk_metadata` | ~seconds (6 collection scroll) | **1.7ms** |
| `generate_proof` | rebuilding circuit each call | **24ms** |
| Total proof generation | ~seconds + circuit rebuild | **~25ms** |

**Changes:**
- `shared/provenance.py`: removed Qdrant scroll loop from `get_chunk_metadata()`
- `ChunkMetadata.text` field removed (was unused)
- `_qdrant_client()` helper removed
- `KNOWN_COLLECTIONS` constant removed
- `dataclasses.field` import removed

### 2026-04-22 — Dev Server Fixes + Website Path Migration ✅

**Problem:** Website was being served from `.<DATA>/website/docs/` (stale copy not in git) instead of `./website/` (git-managed canonical source).

**Actions:**
1. Fixed nginx root: `.<DATA>/website/docs/` → `./website/`
2. Fixed `api_server.py`: added `CORSMiddleware` (allow `*`)
3. Fixed `index.html` API URLs: `https://militarymanuals.ai` → `http://127.0.0.1:8100`
4. Fixed `API_KEY` → `apiKey = null` (no auth for local dev)
5. Conditional auth headers: all fetch calls now check `if (apiKey)` before adding Bearer token
6. Fixed `fetchContext` URL to use `${API_BASE}/context`
7. Fixed provenance fetch URLs (2 calls) to use `${API_BASE}`
8. Removed stale `269 documents` hardcoded count
9. Archived old website: `/data/archive/military-documents-website-20260422_081104`
10. Fixed nginx permission: `chmod o+x` on `<HOME>` and `./zk-rag-v2`
11. Fixed `rag-api.conf`: commented out `access_by_lua_block` (broken since 2026-04-14)
12. Fixed `military-manuals-local.conf`: removed `more_clear_headers` directive (not compiled into OpenResty)
13. Used `sudo systemctl reload openresty` to apply changes

**Result:** Dev site live at `http://127.0.0.1/`, serving from git-managed source. All API endpoints verified:
- `GET /` → HTML with correct local API URLs ✅
- `POST /api/query` → ranked results ✅
- `GET /api/images/{doc_id}/{page}` → image list ✅
- `GET /api/context` → chunk window ✅
- Static images → 200 ✅

**Known remaining issue:** `rag-api.conf` has commented-out lua blocks; geoIP filtering is bypassed. Not critical for local dev.

---

### 2026-04-21 — Pipeline F emit_all.py Atomicity + Force Flag Fixes ✅

**Three bugs fixed in Pipeline F / emit_all.py:**

**Fix 1 — `run_pipeline_e.sh`: Pipeline E failure no longer triggers registry update**
- Captured Pipeline E exit code; only calls `update_registry.py` if exit code is 0 AND output contains "Wrote "
- Single doc mode now exits with error code on failure (was previously silent)

**Fix 2 — `emit_all.py`: Atomicity between broadcast and registry**
- `save_registry()` already writes atomically (rename over temp file — was correct)
- Broadcast + registry write sequence in `run_batch` now correctly saves after each doc
- `run_append_root_v2` now reads tx hash from broadcast receipt file (not stdout) — fixes "unknown" tx_hash

**Fix 3 — `emit_all.py`: `--force` flag bypasses registry check but verifies on-chain**
- `--force` flag added (CLI: `--force`)
- When `--force` is used, queries contract via `cast call` to check if merkle_root is already on-chain
- If on-chain root matches, skips emit with `reason=already_on_chain`
- No more silent skips — re-running with `--force` re-emits only if actually needed
- `--verify` flag writes verification log after each emit

**Kurier latency is unavoidable:** The remaining ~10-60s in `get_provenance()` is Kurier network submission + polling, not local computation.

---

### 2026-04-20 — RandomAccessGate Bug + Single-Root Redesign

**Bug Root Cause:** `RandomAccessGate` at row 326 calls `verify_merkle_proof_to_cap`, which creates 8 extra constant wires (`extra_constant_wires`). plonky2's constant-to-generator pairing algorithm sorts by (row, col) and maps `constants_to_targets` entries to `constant_generators` by sorted order. With only 2 actual constants but 10 generators, the 10th generator at `Wire(326,1)` gets garbage constant value `13364786019932550950` instead of `F::ZERO`, causing a mismatch with `RandomAccessGenerator`'s expected value `17649723070268885551`.

**Why caps were used:** plonky2 uses caps for recursion efficiency — bounding the "public output surface" when verifying proofs of proofs. But ZK-RAG is non-recursive: we generate one proof, verify it, done. Caps are unnecessary overhead.

**New Design (Mr. V decision 2026-04-20):**
- **Per-document single root** — one Merkle tree per document, one Poseidon root
- **No `RandomAccessGate`** — iterative Poseidon hashing is sufficient
- **Circuit proves**: "this chunk is in the Merkle tree rooted at [doc_root]"
- **Reference**: `zk-circuit-sindri/` — clean sindri pattern (iterative hashing, 4 sibling levels, no problematic gates)
- **Scope**: Single document first. More complex multi-doc / cross-doc proofs deferred.

### 2026-04-15 — Pipeline F Debug + emit_all.py Fix

**Zero-chunk docs bug (fixed):** 9 PDFs with `chunk_count = 0` were causing contract reverts. Added pre-check that skips them as `[SKIP] reason=zero_chunk_count`.

**State sync issue (fixed):** `emitted_roots.json` got out of sync with on-chain state. Manually corrected entries marked `failed` → `emitted` where chain proved they succeeded.

**"not authorized" errors:** Explained — early test runs before deployer key was set as owner. After re-deployment on 2026-04-15: owner = `0xBABc60eD17e6387AEDab112E80744aA19EFCb723` — no longer an issue.

**On-chain state:** 9 entries total (4 before 2026-04-15, 5 emitted that day). ~679 docs remaining to emit.

---

## Smart Contract Operations — MerkleRootRegistryV2

**Contract name:** `MerkleRootRegistryV2`
**Language:** Solidity 0.8.24
**Source:** `./pipeline_f/contracts/MerkleRootRegistryV2.sol`

|**Deployed Addresses:**

|| Chain | Chain ID | Contract Address |
||-------|----------|-----------------|
|| Horizen Testnet | 2651420 | `0x83166A340c0A61bc836BD6383aD4acB23a3E3176` |
|| Horizen Mainnet | 26514 | `0x462fc86E28c07798BD4656451611FE4E0A6D7760` |

**Owner:** `0xBABc60eD17e6387AEDab112E80744aA19EFCb723` (matches DEPLOYER_KEY)
**Access control:** `appendRoot()` / `batchAppendRoots()` — owner OR any address on allowlist

**`.env` config (`./.env`):**
```bash
ACTIVE_NETWORK=testnet
source ./.env && cd ./pipeline_f

# Emit docs
python3 emit_all.py --batch --limit N

# Dry run
python3 emit_all.py --batch --limit N --dry-run
```

**Run Pipeline F:**
```bash
source ./.env && python3 emit_all.py --batch --limit 10
```

**Key paths:**
```
Contract address:  auto-selected from .env ACTIVE_NETWORK
Merkle tree input: .<DATA>/merkleTrees/{doc_id}_tree.json
Registry:          .<DATA>/registry.json
Logs:              .<DATA>/logs/
```
---

## Archived Plans (do not edit — historical only)

These are consolidated into this document:
- `docs/PROJ-rag-rebuild-v5.md` — superseded
- `docs/PROJ-rag-pipeline-fix.md` — superseded
- `PROJ-infrastructure-migration.md` — tasks moved here, file to be archived
- `PROJ-zk-rag-v2-consolidation.md` — structure now realized, file to be archived
- `PROJ-zk-rag.md` — Ralph reference material archived here (2026-04-16); implementation details moved to this document
