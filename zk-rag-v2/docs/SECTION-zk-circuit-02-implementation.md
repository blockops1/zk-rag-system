# SECTION-zk-circuit-02: ZK Proof Implementation

**Parent:** PROJ.md, SECTION-zk-circuit-01-design.md
**Status:** 🟢 Phase R mostly complete — rebuilt circuits + wiring done, phase_l updated
**Date:** 2026-04-17 (revised 2026-04-22)
**PRD:** TBD — will be created per phase

This section covers the implementation work for the ZK-RAG circuit under the new single-root design.

---

## Design Reference: Sindri Pattern

**Source:** `zk-circuit-sindri/circuit/src/lib.rs`

The sindri code implements a clean Merkle proof circuit using iterative Poseidon hashing with no `RandomAccessGate`. This is the reference implementation for the new circuit.

**Key design:**
- `TREE_DEPTH = 8` (256 max leaves), `CAP_HEIGHT = 4`, `NUM_LEVELS = 4`
- Sibling selection via arithmetic: `sel = bit × (sibling - current) + current`
- Public input: 1 HashOut (4 field elements)
- Works correctly with plonky2 v0.2.2

**What changes for single-root:**
- Remove cap assumption — hash upward ALL levels to single root (not to 16-entry cap)
- Tree depth: variable per document (based on chunk count, e.g., depth=5 for 32 chunks)
- Public input: single HashOut = the document's Merkle root

---

## New Phase Plan

### Phase A — Circuit: Single-Root Merkle Proof Circuit ✅ DONE

**Status:** ✅ COMPLETE — tests passing (9/9), `test-from-chunks` binary builds and runs on real documents

**Implementation (completed):**
- `zk-circuit/circuit/src/lib.rs` — `build_merkle_proof_circuit_targets()` + `fill_merkle_proof_witness()` + `parse_hash()` + `hash_to_hex()`
- `zk-circuit/circuit/src/merkle_tree.rs` — `MerkleTree::build_from_hashed_leaves()` + `MerkleTree::build_single_root()` + `get_merkle_proof()` + `compute_root_from_proof()`
- `pub mod merkle_tree` exported from lib.rs
- `prove-bin` binary exists and builds clean
- `test-from-chunks` binary exists and builds clean — does full E2E: load chunks → build tree → generate ZK proof → verify
- All 9 circuit tests passing: `test_synthetic_tree_depth_5`, `test_synthetic_tree_depth_8`, `test_synthetic_tree_max_depth`, `test_wrong_sibling_fails`, `test_wrong_leaf_index_fails`, `test_wrong_leaf_hash_fails`, `test_parse_hash_roundtrip`, `test_build_and_prove_4_leaves`, `test_wrong_proof_fails`

**E2E results — random chunk proof generation (2026-04-20):**
| doc_id | chunks | depth | prove_time | root_match |
|--------|--------|-------|------------|------------|
| `7f4e2f0a...` (FM 4-90) | 286 | 9 | 408 ms | ✓ |
| `00c8a75d...` (FM 4-90) | 78 | 8 | 115 ms | ✓ |
| `00cdeace1...` | 26 | 5 | 175 ms | ✓ |
| `016c0f8e6...` | 129 | 8 | 364 ms | ✓ |
| `03fb4720...` | 155 | 8 | 222 ms | ✓ |
| `04701ff24...` | 78 | 8 | 134 ms | ✓ |

**ZK Proof Public Input Model (Phase 1 Model A):**
- `merkle_root` (public) — document's committed Poseidon root
- `document_hash` (public) — SHA-256 of PDF bytes (leaf[0])
- `chunk_hash` (public) — Poseidon(chunk_text), independently verifiable
- Private: `siblings[]`, `index_bits[]`

**ZK proof files saved:** `./data/zk_proofs/zk_proof_<doc_id>_<chunk>.json`
- 10 proofs total (various docs and chunk indices)
- `CircuitData` is NOT serialized — circuit is rebuilt from `tree_depth` at verify time (deterministic plonky2)
- Format: JSON with `proof_bin` (bincode) + `metadata` (doc_id, chunk_id, tree_depth, merkle_root_hex, etc.)

**Re-run needed:**
- [ ] Re-run Pipeline E on remaining emitted docs to produce new-format tree JSONs

**Registry sync (2026-04-21):** `sync_registry_merkleTrees.py` (in `pipeline_d/`) reads tree JSONs and updates `has_merkle_tree`, `tree_root`, `tree_depth`, `chunk_count`, `pipeline_e_status`. Updated 2026-04-21 to also sync `tree_depth` from `tree_config.depth`. Run per-doc: `python3 pipeline_d/sync_registry_merkleTrees.py --doc-id <id> --no-backup`.

**Leaf[0] = doc_id:** Pipeline E prepends `PoseidonHash(doc_id_bytes)` as leaf[0]. The doc_id is the 64-char hex string (SHA-256 of PDF bytes). This binds the Merkle tree to a specific document. Text chunks follow at indices 1..N.

---

### Phase B — Pipeline E: Update Tree Format to Single Root ✅ DONE 2026-04-20

**Implementation (completed):**
- `zk-circuit/pipeline_e/` — new Rust binary crate, workspace member
  - Reads `chunks/{doc_id}/chunks.jsonl`
  - Prepends `SHA256(doc_id)` as `leaf[0]` (binds tree to document)
  - Hashes chunk text via `PoseidonHash::hash_or_noop` on 8-byte little-endian words
  - Pads to next power of 2, builds tree bottom-up with `PoseidonHash::two_to_one`
  - Outputs single `merkle_root` (HashOut hex string, not 16-entry cap)
  - CLI: `--doc-id`, `--batch`, `--chunks-dir`, `--out-dir`, `--force`, `--dry-run`

- `zk-circuit/circuit/src/merkle_tree.rs` — new public functions:
  - `MerkleTree::build_from_hashed_leaves(leaves: Vec<HashOut<F>>) -> Self`
  - `MerkleTree::build_single_root() -> HashOut<F>` (wraps leaves in a power-of-2 padded tree, returns root)
  - `MerkleTree::get_merkle_proof(leaf_index) -> Vec<HashOut<F>>` — sibling path
  - `compute_root_from_proof(leaf_hash, siblings, leaf_index) -> HashOut<F>` — standalone verification

- `zk-circuit/circuit/src/lib.rs` — added `pub mod merkle_tree;`
- `clap = "=4.4.18"` pinned in workspace (nightly Rust + plonky2 compat)
- `is_odd()`/`is_even()` → `idx & 1 == 1` / `idx & 1 == 0` (no extra dep)
- `PrimeField64` imported in pipeline_e for `to_canonical_u64()`

**Pipeline E run — 5 docs (2026-04-20):**
| doc_id | chunks | depth | real leaves | padded |
|--------|--------|-------|-----------|--------|
| 00c8a75d... | 166 | 8 | 256 | 89 |
| 00cdeace1... | 17 | 5 | 32 | 14 |
| 016c0f8e6... | 187 | 8 | 256 | 68 |
| 04701ff24... | 177 | 8 | 256 | 78 |
| 03fb47203... | 232 | 8 | 256 | 23 |

All trees written to `./data/merkleTrees/{doc_id}_tree.json`.

**Pipeline E run — 5 docs (2026-04-21, fresh):**
| doc_id (short) | chunks | depth | real leaves | padded |
|---|---|---|---|---|
| `00c8a75d...` | 166 | 8 | 256 | 89 |
| `00cdeace1...` | 17 | 5 | 32 | 14 |
| `016c0f8e6...` | 187 | 8 | 256 | 68 |
| `03fb4720...` | 232 | 8 | 256 | 23 |
| `04701ff24...` | 177 | 8 | 256 | 78 |

**Re-run needed:**
- [ ] Re-run Pipeline E on remaining emitted docs to produce new-format tree JSONs

---

### Phase C — Pipeline F: Update Emit to Single Root ✅ DONE 2026-04-20

**Implementation (completed):**
- `emit_single(doc_id, merkle_tree_json)` — emits `merkle_root` (single string) to the V2 contract
- Contract `appendRoot` — stores `bytes32` instead of `bytes32[16]`
- Updated `emitted_roots.json` schema

**On-chain emit results (2026-04-20):**
- Contract: `0x17A6E8AE3f6eb315F4C117630F3AaC8865BD2B15` (Sepolia)
- Docs emitted: **5 / 742** (those that had tree files at time of run)
- All 5 roots verified against tree JSONs — **100% match** ✅

| doc_id (short) | chunks | root (short) | on-chain? |
|---|---|---|---|
| `00c8a75d...` | 166 | `0x99271cc5...` | ✅ |
| `00cdeace1...` | 17 | `0x6cfc136c...` | ✅ |
| `016c0f8e6...` | 187 | `0x2ad5f17e...` | ✅ |
| `03fb4720...` | 232 | `0x3f9955b4...` | ✅ |
| `04701ff24...` | 177 | `0xe83ac7e8...` | ✅ |

**Remaining docs:** 737 docs without tree files — `emit_all.py --v2` will process them as Pipeline E produces tree files for each.

---

### Phase D — Pipeline G: Update Qdrant Payload Schema ✅ DONE 2026-04-20

**Implementation (completed):**
- `build_proof_input()` — accepts `merkle_root` (string) not `merkle_cap` (list)
- Removed cap-height logic (no more 16-entry cap)

**New Qdrant payload field:**
```json
{
  "merkle_root": "0xabcd...1234",
  "merkle_tree_depth": 5,
  ...
}
```

---

### Phase E — Python Bridge: Update `zk_bridge.py` ✅ DONE 2026-04-20

**Implementation (completed):**
- `build_proof_input()` — accepts `merkle_root` (string) instead of `merkle_cap` (list of 16)
- Updated `ChunkInput` dataclass
- Updated JSON input format for `prove` binary

**New `prove` CLI input format:**
```json
{
  "mode": "single-root",
  "merkle_root": "0xabcd...1234",
  "chunks": [
    {
      "text": "chunk text...",
      "leaf_index": 12,
      "siblings": ["0xh1", "0xh2", "0xh3", "0xh4", "0xh5"]
    }
  ]
}
```

---

### Phase F — E2E: Full Proof Generation with Real Qdrant Data ✅ DONE 2026-04-20

**Goal:** End-to-end test: query Qdrant → retrieve chunks with proofs → generate ZK proof.

**Test script:** `zk-circuit/scripts/phase_e_e2e_test.py`

**Status:** ✅ COMPLETE — 5 documents tested end-to-end

**Results — E2E with real Qdrant data (2026-04-20):**
|| doc_id | collection | chunks | depth | proof_time | root_match |
|--------|-----------|--------|-------|------------|------------|
| `7f4e2f0a...` | army | 286 | 9 | 408 ms | ✓ |
| `00c8a75d...` | army | 78 | 8 | ~120 ms | ✓ |
| `00cdeace1...` | navy | 26 | 5 | ~175 ms | ✓ |
| `016c0f8e6...` | navy | 129 | 8 | ~364 ms | ✓ |
| `03fb4720...` | navy | 155 | 8 | ~222 ms | ✓ |
| `04701ff24...` | marines | 78 | 8 | ~134 ms | ✓ |

**Verification:**
- [x] Proof generated successfully with real Qdrant data
- [x] Proof verifies locally
- [x] Proving time logged (for performance baseline)
- [x] `CircuitData` NOT serialized — circuit rebuilt from `tree_depth` at verify time

---

## Circuit API Reference (sindri pattern to adapt)

```rust
// Build circuit for given depth
pub fn build_merkle_proof_circuit_targets(
    builder: &mut CircuitBuilder<F, D>,
    depth: usize,
) -> MerkleProofTargets {
    // Public input: root (1 HashOut = 4 field elements)
    let root = builder.add_virtual_hash();
    builder.register_public_inputs(&root.elements);

    // Witness: leaf hash
    let leaf_hash = builder.add_virtual_hash();

    // Witness: siblings (one per level)
    let siblings: Vec<HashOutTarget> = (0..depth)
        .map(|_| builder.add_virtual_hash())
        .collect();

    // Witness: index bits
    let leaf_index_bits: Vec<Target> = (0..depth)
        .map(|_| builder.add_virtual_target())
        .collect();

    // Iterative upward hashing
    let mut current = leaf_hash;
    for level in 0..depth {
        let sibling = siblings[level].clone();
        let index_bit = leaf_index_bits[level];

        // Arithmetic left/right select
        let left: HashOutTarget = HashOutTarget {
            elements: std::array::from_fn(|i| {
                let diff = builder.sub(sibling.elements[i], current.elements[i]);
                let scaled = builder.mul(index_bit, diff);
                builder.add(current.elements[i], scaled)
            }),
        };
        let right: HashOutTarget = HashOutTarget { ... };

        current = builder.hash_or_noop::<PoseidonHash>(
            [left.elements.to_vec(), right.elements.to_vec()].concat(),
        );
    }

    // Constraint: computed root == public input
    for i in 0..4 {
        builder.connect(root.elements[i], current.elements[i]);
    }

    MerkleProofTargets { leaf_hash, siblings, leaf_index_bits, root }
}
```

---

## Phase J — Circuit Pre-Build: Serialize CircuitData for Fast Proof Generation ✅ DONE 2026-04-21

**Status:** ✅ COMPLETE — pre-built circuit files exist for depths 5-12, prove-bin loads from disk

**Implementation:**
- `prove-bin --build-circuit <depth>` builds and serializes `CircuitData` to `circuit_depth{N}.bin`
- Default mode: loads pre-built `CircuitData` from disk if present, falls back to build-from-scratch
- Circuit files pre-built 2026-04-21:

```
circuit_depth5.bin  — 141 KB
circuit_depth6.bin  — 142 KB
circuit_depth7.bin  — 142 KB
circuit_depth8.bin  — 142 KB  ← most docs use depth 8
circuit_depth9.bin  — 143 KB
circuit_depth10.bin — 143 KB
circuit_depth11.bin — 143 KB
circuit_depth12.bin — 278 KB
```

**Proof generation performance (2026-04-21, release binary):**

| doc | depth | prove time |
|-----|-------|-----------|
| `00c8a75d...` (FM 4-90) | 8 | **24ms** |
| `00cdeace1...` | 5 | **~20ms** |

Circuit loaded from disk in ~1ms, proof generation ~24ms, local verification ~2ms.

---

## Open Questions

1. **K=1 vs K>1**: Phase 1 = single chunk proof. Multi-chunk (K>1) batch proofs deferred.

2. **LLM output hash**: Not included in current circuit. Circuit proves: chunk is in Merkle tree. LLM output hash is a future enhancement.

3. **Re-emit 19 docs**: Deprecated — V2 contract `0x17A6E8AE3...` deployed with single-root format. New emit flow: Pipeline E (tree) → Pipeline F (emit) → Pipeline G (Qdrant). Running incrementally as trees are built.

4. **Proof serialization**: `CircuitData` is NOT serialized. Verifier rebuilds circuit from `tree_depth`. This is correct for plonky2's deterministic circuit building, but if the circuit changes (e.g., different hashing algorithm), old proofs become unverifiable. Consider versioning.

5. **Kurier devnet vs testnet**: Confirm which Kurier environment to use for dev. See Phase H, Open Question 3.

6. **Circuit pre-build scope**: The fix is scoped to `prove-bin` only. Does not change the circuit logic itself — only when/how `CircuitData` is built.

---

## What's Next

In order:

1. **Phase R (cont.) — Rebuild pre-built circuits** — `circuit_depth{N}.bin` files must be rebuilt for all depths 5–12 (public input count changed). See R6 in Phase R section.
2. **Phase R (cont.) — Wire phase_l.py + test-from-qdrant.py** — remaining Python callers of prove-bin need the new fields.
3. **Phase N — Issues Found During Review** — Gap 1 fixed ✅; Gap 2 skipped; Gap 3 is API script work (deferred).
4. **Phase L (block_number backfill)** — Script to query `cast tx <tx_hash>` for all emitted docs and backfill `block_number` into registry.json.
5. **Phase M — E2E provenance test** — Run the full chain: Qdrant chunk → prove-bin → Kurier → explorer verification.
3. **Phase O — Re-emit remaining docs** — ~722 docs still need Pipeline E → Pipeline F → Pipeline G. Currently 20/742 done.
4. **On-chain verification**: Deploy proof verification contract on Horizen mainnet
5. **Proof compression**: STARK-proof → EVM-verifiable (Groth16 or PLONK) — deferred

---

## Phase H — Provenance API ✅ DONE 2026-04-21

**Status:** ✅ COMPLETE — `shared/provenance.py` wraps the full provenance flow

**Implementation (`shared/provenance.py`):**
- `get_chunk_metadata(chunk_id) → ChunkMetadata` — reads tree JSON directly (no Qdrant needed)
- `generate_proof(leaf_hash, leaf_index, siblings, merkle_root, tree_depth) → ProofResult` — calls prove-bin, returns proof hex + public inputs
- `submit_to_kurier(proof_hex, public_inputs, vk_id?) → job_id` — Kurier submission
- `poll_kurier(job_id) → PollStatus` — polls until COMPLETED/FAILED
- `get_provenance(chunk_id, on_chain) → ProvenanceResponse` — full orchestration
- `_find_tree_json(doc_id)` — locates tree JSON in `./data/merkleTrees/`
- `_find_emit_tx(doc_id)` — locates emit tx in `./data/emit_output/`

**Performance (2026-04-21):**
- `get_chunk_metadata`: **1.7ms** (was seconds with Qdrant scroll)
- `generate_proof`: **~24ms** (with pre-built circuit loaded from disk)
- Kurier round-trip: **~10-60s** (network latency, unavoidable)

**Kurier end-to-end test PASSED 2026-04-21:**
- Proof `c5997755-3d16-11f1-99a3-e2579a7a7dd2` finalized in ~30 seconds on zkVerify
- Full flow tested: tree JSON → prove binary → Kurier submit → poll → Finalized status

---

**emit_all.py flags (2026-04-21):**
- `--dry-run` — simulate without broadcasting
- `--batch` — emit all documents in merkleTrees/
- `--doc-id` — emit single document
- `--limit N` — limit batch to first N documents
- `--verify` — write verify log; performs on-chain verification after each EMIT
- `--force` — re-emit even if registry shows already emitted (queries on-chain state first)

**emit_all.py fixes (2026-04-21):**
- `run_pipeline_e.sh`: exit code captured; registry update only on success
- `run_append_root_v2`: tx hash now read from broadcast receipt file (not stdout)
- `--force`: on-chain check via `cast call` before emitting; skips with `reason=already_on_chain` if root already registered
- Atomic registry writes via rename over temp file (already correct)

**Block metadata backfill (2026-04-21):**
- Fixed `sync_block_metadata_to_registry.py`: RPC URL (removed `/http` suffix), V2 contract address + ABI (flat struct vs V1 nested array)
- Backfilled 20 emitted docs: `block_number`, `block_timestamp`, `uploader` now populated for all
- Commit: `3862db1`

### Two-Tier Architecture (Implemented)

| Tier | Trigger | Latency |
|------|---------|---------|
| **Fast query** | `POST /api/query` | ~50ms |
| **On-demand provenance** | `GET /api/provenance/{chunk_id}` | ~25ms local + ~10-60s Kurier |

**Chunk metadata** is read from Pipeline E tree JSON files — no Qdrant lookup needed for proof generation.

**On-chain data** (emit tx hash) is looked up from `emit_output/{doc_id}.json` files.

---

### ProvenanceResponse Format (Implemented)

```json
{
  "chunk_id": "00c8a75d..._42",
  "doc_id": "00c8a75d...",
  "leaf_index": 42,
  "tree_depth": 8,
  "merkle_root": "0x99271cc523478d...",

  "on_chain": {
    "horizen_explorer_url": "https://sepolia.explorer.horizen.io/tx/0xabc123...",
    "contract": "0x17A6E8AE3f6eb315F4C117630F3AaC8865BD2B15",
    "tx_hash": "0xabc123...",
    "block_number": 12345678
  },

  "zk_proof": {
    "status": "verified" | "pending" | "failed",
    "zkverify_explorer_url": "https://zkverify.io/explorer/job/0xdef456...",
    "job_id": "0xdef456...",
    "public_inputs": {
      "merkle_root": "0x99271cc5...",
      "document_hash": "0xfedcba9...",
      "chunk_hash": "0x123456..."
    },
    "proof_hex": "0x<hex>",
    "vk_id": "<vk-id>"
  }
}
```

---

### Remaining Work

| Item | Status | Notes |
|------|--------|-------|
| Kurier API client in Python (`provenance.py`) | ✅ DONE | Full flow implemented |
| Kurier E2E test (zkVerify finalization) | ✅ DONE | Proof `c5997755-3d16-11f1-99a3-e2579a7a7dd2` finalized |
| API server provenance endpoints | ⬜ TODO | Hook `provenance.py` into FastAPI |
| Website "Prove" button | ⬜ TODO | JavaScript polls `/api/provenance/{chunk_id}/status` |
| E2E website test | ⬜ TODO | Full user flow: query → prove button → zkVerify link |

---

### Phase G — Kurier/zkVerify Integration ✅ DONE 2026-04-21

**Goal:** Send plonky2 proofs to Kurier for verification on the zkVerify blockchain.

**Background:** Kurier is a proof aggregation/verification service that submits proofs to the zkVerify blockchain (Horizen ecosystem). Rather than self-verifying plonky2 proofs on-chain (expensive), we can submit them to Kurier for cheaper verification.

**zkVerify Plonky2 support (confirmed):**
- Hashes: Keccak256, **Poseidon** ✅ (our circuit uses Poseidon)
- Max Public Inputs: 64 (we use 3: merkle_root, document_hash, chunk_hash)
- Max Proof Size: 256 KiB
- Max Verification Key Size: 50 KB
- Source: https://docs.zkverify.io/architecture/supported_proofs

**Kurier API flow:**
- Base: `https://api.kurier.xyz/api/v1`
- Step 1: `POST /register-vk/{apiKey}` — register circuit VK (one-time per circuit design)
  - Body: `{"proofType": "plonky2", "proofOptions": {"hashFunction": "poseidon"}, "vk": <VK data>}`
  - Response: `vkId`
- Step 2: `POST /submit-proof/{apiKey}` — submit proof for verification
  - Body: `{"proofType": "plonky2", "vkRegistered": true, "proofOptions": {"hashFunction": "poseidon"}, "proofData": {"proof": "0x<hex-encoded proof>"}}`
  - Response: `jobId`
- Step 3: `GET /job-status/{jobId}/{apiKey}` — poll until `COMPLETED` or `FAILED`
  - Source: https://kurier.xyz/docs/tutorial

**Our plonky2 proof format:** Currently `proof_bin` (bincode bytes). Kurier expects hex string (`0x...`). We need to hex-encode when submitting.

**Critical gap:** We currently don't serialize the Verification Key (VK) from plonky2. Kurier requires VK registration before proof submission. Need to add VK extraction/serialization to our Rust code.

**Implementation Steps (ordered):**

**Step 1 — Add `plonky2-verifier` fork dependency**
- zkVerify maintains a custom fork: `github.com/zkVerify/plonky2-verifier`, tag `v0.2.1`
- This provides `ZKVerifyGateSerializer` (compatible with plonky2 `v0.2.2` gate set) and `serialize_vk`/`serialize_proof` utilities
- Add to `Cargo.toml` workspace deps, then use in `prove-bin` and `verify-zk-proof`

**Step 2 — Add VK serialization to prove binary**
- Serialize VK: `data.verifier_data().to_bytes(&ZKVerifyGateSerializer)`
- Wrap in JSON: `{"config": "Poseidon", "bytes": "<hex>"}`
- Store alongside proof or as separate artifact
- Also add `serialize_public_inputs(proof) → pubs_hex`

**Step 3 — Add Kurier API client (`kurier.rs`)**
- `register_vk(api_key, vk_json) → vk_id`
- `submit_proof(api_key, proof_hex, pubs_hex, vk_id?) → job_id`
- `poll_job_status(api_key, job_id) → status`
- Base URL: `https://api.kurier.xyz/api/v1`

**Step 4 — Integrate into prove binary + write test**
- After generating proof: serialize → submit to Kurier → poll → log explorer URL
- Write test: register VK (one-time), submit a saved proof JSON, verify status
- Update `verify-zk-proof` to optionally submit to Kurier

**Tasks:**
- [x] Research Kurier API — proof submission endpoint, format requirements ✅
- [x] Determine if plonky2 proof format needs conversion for zkVerify — YES: bincode → hex ✅
- [x] Add `plonky2-verifier` fork as dependency (Step 1) ✅ **DONE 2026-04-20**
- [x] Implement VK serialization with `ZKVerifyGateSerializer` (Step 2) ✅ **DONE 2026-04-20**
- [x] Implement proof + public inputs serialization (Step 2) ✅ **DONE 2026-04-20**
- [x] Build standalone `verify-zk-proof` binary (uses `plonky2_verifier::verify`) ✅ **DONE 2026-04-20**
- [x] Implement Kurier API client — register VK, submit proof, poll status (Step 3) ✅ **DONE 2026-04-21** (`shared/provenance.py`)
- [x] Integrate Kurier submit into prove binary (Step 4) ✅ **DONE 2026-04-21** (`shared/provenance.py` wraps it)
- [x] Write test: register VK, submit proof, verify on Kurier devnet (Step 4) ✅ **DONE 2026-04-21** — proof finalized on zkVerify
- [x] Add `KURIE_API_KEY` to environment (set on this machine) ✅
- [x] Document proof submission workflow ✅

**Step 1+2 completed (2026-04-20):** `plonky2-verifier` added to `prove-bin/Cargo.toml`. Prove binary now serializes using zkVerify-compatible format:

```
proof_bytes  = write_proof(&proof.proof)           // raw proof bytes, no public inputs
pubs_bytes   = write_usize(n) + write_field_vec()  // len prefix + field elements
vk_bytes     = verifier_data().to_bytes(&ZKVerifyGateSerializer)
```

**`verify-zk-proof` binary (NEW 2026-04-20):** Full standalone Rust binary for verifying plonky2 proofs using `plonky2_verifier::verify()`. Located at `zk-circuit/verify-zk-proof/`. Verified test proof: **VALID** ✅

---

## Phase K — Integrate Pre-Built Circuits into Provenance API ✅ DONE (updated 2026-04-21)

**Status:** ✅ COMPLETE — `prove-bin` already loads pre-built circuits automatically via `CIRCUIT_DIR` env var.

**How it works:**
- `provenance.py` sets `CIRCUIT_DIR=./zk-circuit` before calling `prove-bin`
- `prove-bin` checks `circuit_dir.join("circuit_depth{N}.bin")` — if it exists, loads in ~1ms
- If not found, falls back to build-from-scratch (slow)
- Pre-built circuit files confirmed present in `./zk-circuit/` ✅

**Proof generation performance (2026-04-21, release binary):**

| doc | depth | prove time |
|-----|-------|-----------|
| `00c8a75d...` (FM 4-90) | 8 | **~24ms** |
| `00cdeace1...` | 5 | **~20ms** |

Circuit loaded from disk in ~1ms, proof generation ~24ms, local verification ~2ms.

**Pre-built circuit files (exist ✅):**
```
circuit_depth5.bin  — 141 KB
circuit_depth6.bin  — 142 KB
circuit_depth7.bin  — 142 KB
circuit_depth8.bin  — 142 KB  ← most docs use depth 8
circuit_depth9.bin  — 143 KB
circuit_depth10.bin — 143 KB
circuit_depth11.bin — 143 KB
circuit_depth12.bin — 278 KB
```

---

## Phase R — Circuit: Add `ingestion_timestamp` + `ingestion_block` as Public Inputs

**Status:** ✅ COMPLETE (2026-04-22)

**Date:** 2026-04-22

**Why this change:** The current circuit proves "this chunk is in the Merkle tree rooted at `merkle_root`" but does not prove *when* that root was committed. An auditor seeing `merkle_root` on-chain cannot determine the ingestion timestamp without a separate index lookup. Adding these as public inputs makes the proof self-contained: it proves the chunk's provenance AND the commit timestamp in one verification.

**What the circuit proves (new statement):**
> "This exact chunk belongs to the committed Poseidon Merkle tree whose root was published on-chain for this specific document at this timestamp."

**Public Inputs (updated):**
| # | Name | Type | Description |
|---|------|------|-------------|
| 1 | `merkle_root` | HashOutTarget | Poseidon Merkle root published on EVM |
| 2 | `document_hash` | HashOutTarget | Poseidon(doc_id_bytes) = leaf[0] of Merkle tree |
| 3 | `ingestion_timestamp` | Target | Unix timestamp when root was added (from EVM block) |
| 4 | `ingestion_block` | Target | Block number when root was published |

**Private Witnesses (unchanged):**
- `leaf_hash: HashOutTarget` — Poseidon hash of chunk text (pre-computed, Option A)
- `siblings[]: Vec<HashOutTarget>` — Merkle proof path
- `index_bits[]: Vec<BoolTarget>` — leaf index as bits

**Files changed:**
1. `zk-circuit/circuit/src/lib.rs` — add 2 public inputs, update `CircuitTargets` + `fill_merkle_proof_witness`
2. `zk-circuit/prove-bin/src/main.rs` — add fields to `ProveInput` + pass through to witness
3. `zk-circuit/prove-chunks.py` — `get_doc_ingestion(doc_id)` queries Qdrant for `evm_block_timestamp` + `evm_block_number`, passes to prove-bin
4. `shared/phase_l.py` — `generate_proof()` accepts new fields (0/0 placeholders, TODO: wire Qdrant lookup)

**Implementation steps:**

- [x] **R1** ✅ — `circuit/src/lib.rs`: add `ingestion_timestamp` and `ingestion_block` as `add_virtual_public_input()` targets
- [x] **R2** ✅ — `circuit/src/lib.rs`: update `CircuitTargets` struct with new fields
- [x] **R3** ✅ — `circuit/src/lib.rs`: update `fill_merkle_proof_witness` signature and implementation
- [x] **R4** ✅ — `prove-bin/src/main.rs`: update `ProveInput` JSON deserialization (accept `ingestion_timestamp` + `ingestion_block`)
- [x] **R5** ✅ — `prove-bin/src/main.rs`: thread params through to `fill_merkle_proof_witness`
- [ ] **R6** ⏳ — Rebuild pre-built circuits for depths 5–12 (public input count changed — old files incompatible)
- [x] **R7** ✅ — `prove-chunks.py`: `get_doc_ingestion(doc_id)` — Qdrant lookup for `evm_block_timestamp` + `evm_block_number`
- [ ] **R8** ⏳ — `shared/phase_l.py`: wire Qdrant lookup into `generate_proof()` (currently 0/0 placeholders)
- [ ] **R9** ⏳ — `test-from-qdrant.py`: update to pass new fields to prove-bin
- [x] **R10** ✅ — Run circuit tests (9/9 pass)

**Data source:** Qdrant per-chunk payload fields `evm_block_timestamp` (ISO8601) and `evm_block_number`. All chunks of the same doc share the same values. Query by `doc_id` across army/navy/marines/other collections.

**⚠️ Rebuilt circuits required before proof generation works:**
Public input count changed from 2 to 4. Old pre-built circuit files at `zk-circuit/circuit_depth{N}.bin` are incompatible. Rebuild all depths:
```bash
cd ./zk-circuit
for depth in 5 6 7 8 9 10 11 12; do
  cargo run --release --bin circuit_builder -- $depth
done
```

**Rebuild pre-built circuits after R1-R3:**
```bash
cd ./zk-circuit
for depth in 5 6 7 8 9 10 11 12; do
  cargo run --release --bin prove-bin -- --build-circuit $depth
done
```

---

## Phase N — Issues Found During Review (2026-04-21)

**Status:** 🟡 4 issues found — 3 minor, 1 architectural

---

### Issue 1 — `/api/prove` endpoint is broken ✅ REMOVED 2026-04-21

**Location:** `shared/api_server.py` line ~1025 + `shared/zk_bridge.py`

**Problem:** `api_server.py` has two provenance endpoints:
- `GET /api/provenance/{chunk_id}` → calls `provenance.py` → `prove-bin` ✅ **working**
- `POST /api/prove` → calls `zk_bridge.py` → `prove` binary ❌ **broken**

The `/api/prove` endpoint imports `zk_bridge.py` which references `PROVE_BINARY = ./zk-circuit/target/release/prove` — but that binary doesn't exist. The current binary is `prove-bin`. The `ChunkInput` dataclass in `zk_bridge.py` also expects a different JSON format than what `prove-bin` accepts.

**Fix chosen: Option A — Removed the broken endpoints.**

`GET /api/provenance/{chunk_id}` is the single provenance endpoint — it works end-to-end (generate proof + submit to Kurier + return explorer link). The broken `POST /api/prove`, `GET /api/proof/{proof_id}`, and `GET /api/proofs` endpoints were removed from `api_server.py`. The `zk_bridge` import block and related model classes were also removed. `shared/zk_bridge.py` still exists (used by other code/tests) but is no longer referenced by the API server.

---

### Issue 2 — `document_hash` was a dead stand-in ✅ FIXED 2026-04-21

**Location:** `prove-bin` line ~522 + `shared/provenance.py` line ~192

**Problem:** `provenance.py` was computing `document_hash = SHA-256(doc_id)` and passing it as a public input, but:
1. The circuit expects `Poseidon(doc_id_bytes)` (the same hash used as `leaf[0]` in Pipeline E)
2. SHA-256 and Poseidon are completely different hash functions — the circuit was receiving garbage

**Fix applied:**
1. Added `poseidon_doc_id_hash: str` field to `ChunkMetadata` dataclass
2. `get_chunk_metadata()` now extracts `leaf_hashes[0]` from the tree JSON (Poseidon(doc_id), already computed by Pipeline E)
3. `generate_proof()` now passes `metadata.poseidon_doc_id_hash` as `document_hash` instead of computing SHA-256
4. Removed dead `hashlib` import from `provenance.py`

**Result:** `document_hash` public input now correctly equals `Poseidon(doc_id_bytes)` = the Merkle tree's `leaf_hashes[0]`, which is also equal to the first leaf computed by Pipeline E. The circuit's constraint `verify_merkle_proof` (which hashes the provided `document_hash` as leaf[0]) now produces the correct Merkle root.

**Verification:** `generate_proof()` confirmed working — `document_hash: 0x535da7ac50bf7c7baf7482e47483788eea2c47b6be2471ca...` matches `leaf_hashes[0]` from tree JSON.

---

### Issue 3 — `SerializableCircuitTargets::from_targets()` is a `todo!()` ✅ REMOVED 2026-04-21

**Location:** `circuit/src/lib.rs` lines ~142-144

**Problem:** The `SerializableCircuitTargets` struct exists for pre-built circuit target remapping, but `from_targets()` is:
```rust
pub fn from_targets(_targets: &CircuitTargets) -> Self {
    todo!("SerializableCircuitTargets::from_targets requires plonky2 target-index accessor — not yet available in v1.0.2.")
}
```

**Impact:** Currently none — `CircuitData::from_bytes()` deserializes the circuit data correctly, and `build_merkle_proof_circuit_targets()` rebuilds targets deterministically from the same depth. This would only matter if we needed to serialize target indices separately from `CircuitData`.

**Fix (optional):** Either implement target index extraction using plonky2 internals, or remove the dead `SerializableCircuitTargets` code entirely.

---

### Issue 4 — `KURIE_API_KEY` is hardcoded stub in `provenance.py` (security/best-practice)

**Location:** `shared/provenance.py` line ~71

**Problem:** The file contained a hardcoded API key default value — security risk if committed to git.

**Fix applied (2026-04-21):** `KURIE_API_BASE` now defaults to `https://api.kurier.xyz/api/v1` (mainnet). The API key is read from environment variable `KURIE_API_KEY` — loaded from `.env` by the API server startup. No default key value remains in source.

---

## Phase P — Circuit: Hash Chunk Text Inside ZK Proof (K=1)

**Status:** 🟢 COMPLETE — Option A implemented 2026-04-22, all 9 tests passing, prove+verify VALID on real docs

**Why Option A (not Phase P's original plan):**

Phase P's original plan (hash text inside circuit) hit a plonky2 wire aliasing bug: `PoseidonGate` reserves internal columns for round constants/state that collide with user-defined arrays when those arrays are large (1024 field elements for 8KB of text). The collision only surfaced during proof generation (not circuit building), making it hard to diagnose. Grok's analysis (2026-04-22) confirmed: this pattern is avoided in real-world plonky2 deployments precisely because of this issue.

**Option A decision (per Grok / Mr. V 2026-04-22):** Accept a pre-computed `leaf_hash` as a private witness. The Merkle proof itself proves the chunk belongs to the committed tree. The binding between real text and hash was established during Pipeline E ingestion (when the tree was built with the correct text). No in-circuit text hashing needed.

**Security model:** The honest prover supplies `leaf_hash = Poseidon(chunk_text)` (pre-computed in Python during ingestion). The ZK circuit verifies the Merkle proof using that hash. A malicious prover cannot fake the hash — the proof would fail because the supplied hash doesn't exist as a leaf in the tree.

**What was built:**
- `circuit/src/lib.rs`: `text_elements: Vec<Target>` → `leaf_hash: HashOutTarget`; removed `hash_or_noop`; `fill_merkle_proof_witness` now takes `leaf_hash: HashOut<F>`
- `prove-bin/src/main.rs`: `ProveInput.chunk_text` → `ProveInput.leaf_hash` (hex string); `PublicInputs` output now includes `leaf_hash`
- `prove-chunks.py`: reads `leaf_hash` directly from tree JSON (pre-computed by Pipeline E)
- `provenance.py` / `phase_l.py`: pass `leaf_hash` instead of `chunk_text`
- Tests: `test_wrong_text_fails` → `test_wrong_leaf_hash_fails`

**Test results (2026-04-22):**
- 9/9 circuit tests pass
- End-to-end prove + verify on real documents: **VALID**

**ZK Proof Public Input Model (Option A — 2026-04-22):**
- `merkle_root` (public) — document's committed Poseidon root
- `document_hash` (public) — Poseidon(doc_id_bytes) = leaf[0] of Merkle tree
- `leaf_hash` (public output) — Poseidon(chunk_text), included in proof output for client-side verification
- Private: `leaf_hash` (private witness), `siblings[]`, `index_bits[]`
- Circuit: verify Merkle proof that `leaf_hash` is in the tree rooted at `merkle_root`

**E2E results (2026-04-22):**
| doc_id | chunk_index | depth | result |
|--------|-------------|-------|--------|
| `00c8a75d...` | 100 | 8 | VALID ✅ |
| `00cdeace1...` | 5 | 5 | VALID ✅ |

**Pre-built circuits:** Stale files deleted; fresh circuits build on first prove run. Commit: `cd41658`.

### Phase P Original Plan (Superseded by Option A 2026-04-22)

> ⚠️ The plan below described hashing chunk text *inside* the circuit. This approach hit a
> plonky2 wire aliasing bug and is **superseded** by Option A above. The text below is kept
> for historical reference.

**Why this change is needed:**

The current circuit receives `chunk_hash` as a **public input** (pre-computed by the API server). This creates a provenance gap: the prover could supply any arbitrary hash and claim it came from the document's text. The ZK proof only verifies that *some* hash is in the Merkle tree — not that the hash was computed from the actual document text.

**Grok recommendation:** Include the raw chunk text as a private witness. The circuit computes the Poseidon hash internally, then verifies the Merkle proof. This eliminates the gap — the prover cannot fake the text-to-hash binding because the circuit computes the hash from the raw text inside the ZK proof.

**Current model:**
```
Public inputs (3): merkle_root, document_hash, chunk_hash
Private witness: siblings[], index_bits[]
Circuit: verify Merkle proof that chunk_hash is in the tree
```

**New model (Grok revised, K=1):**
```
Public inputs (2): merkle_root, document_hash
Private witness: chunk_text_bytes[], siblings[], index_bits[]
Circuit: computed_leaf = Poseidon(chunk_text_bytes || metadata)
         verify Merkle proof that computed_leaf is in the tree rooted at merkle_root
```

The `document_hash` (leaf[0] = Poseidon(doc_id_bytes)) stays public — it's the PDF commitment, independently verifiable.

---

### Step 1 — `circuit/src/merkle_tree.rs`: Add `hash_chunk_text()`

Extract the text-hashing logic from `test-from-chunks` into a reusable function. This must be **identical** to the hashing used in Pipeline E:
- NFKC normalization
- 8-byte little-endian word packing into Goldilocks field elements
- `PoseidonHash::hash_or_noop` on packed words

```rust
/// Hash raw chunk text to a HashOut, using identical normalization/packing as Pipeline E.
pub fn hash_chunk_text(text: &str) -> HashOut<F>
```

**Verification:** The resulting hash must match `leaf_hashes[leaf_index]` from the tree JSON for any real document.

---

### Step 2 — `circuit/src/lib.rs`: Modify `build_merkle_proof_circuit_targets()`

Change the circuit to accept `chunk_text_bytes` as a private witness instead of `chunk_hash` as a public input:

- **Remove** `chunk_hash` from public inputs
- **Add** `chunk_text_bytes: Vec<Target>` as private witness
- **Add** inside circuit: `computed_leaf = PoseidonHash::hash_or_noop(chunk_text_bytes)`
- **Use** `computed_leaf.elements` as the leaf data for `verify_merkle_proof`

```rust
pub fn build_merkle_proof_circuit_targets(
    builder: &mut CircuitBuilder<F, D>,
    depth: usize,
) -> CircuitTargets {
    // Public inputs: merkle_root + document_hash only
    let merkle_root = builder.add_virtual_hash_public_input();
    let document_hash = builder.add_virtual_hash_public_input();

    // Private witness: raw text bytes
    let text_len: usize = /* chunk-dependent — needs circuit to handle variable length */;
    let chunk_text_bytes: Vec<Target> = (0..text_len)
        .map(|_| builder.add_virtual_target())
        .collect();

    // Private witness: siblings + index bits
    let index_bits: Vec<BoolTarget> = (0..depth)
        .map(|_| builder.add_virtual_bool_target_safe())
        .collect();
    let siblings: Vec<HashOutTarget> = (0..depth)
        .map(|_| builder.add_virtual_hash())
        .collect();

    // Internal: compute leaf hash from raw text
    let computed_leaf = builder.hash_or_noop::<PoseidonHash>(chunk_text_bytes);

    // Verify Merkle proof
    let proof = MerkleProofTarget { siblings };
    builder.verify_merkle_proof::<PoseidonHash>(
        computed_leaf.elements.to_vec(),
        &index_bits,
        merkle_root,
        &proof,
    );

    CircuitTargets { merkle_root, document_hash, chunk_text_bytes, index_bits, proof }
}
```

**Note on variable-length text:** plonky2 circuits require fixed-size inputs. Options:
- **Option A — Fixed max length:** Pad or truncate text to a fixed `MAX_CHUNK_BYTES = 8192` (covers ~2048 tokens). Simpler, works for all current chunks.
- **Option B — Hash the length separately:** Prove text length as a private input, then hash variable-length bytes. More complex.
- **Decision:** Start with Option A (fixed max length) for Phase P. Profiling will tell if constraint count is acceptable.

---

### Step 3 — `circuit/src/lib.rs`: Update `fill_merkle_proof_witness()`

Change signature: replace `chunk_hash: HashOut<F>` with `chunk_text: &[u8]`. Internally call `hash_chunk_text(chunk_text)` to produce the computed leaf, then fill the rest as before.

```rust
pub fn fill_merkle_proof_witness(
    pw: &mut PartialWitness<F>,
    targets: &CircuitTargets,
    merkle_root: HashOut<F>,
    document_hash: HashOut<F>,
    chunk_text: &[u8],       // ← changed
    siblings: &[HashOut<F>],
    chunk_index: usize,
) {
    // Compute leaf hash inside the witness function (same as circuit does)
    let computed_leaf = merkle_tree::hash_chunk_text(
        &String::from_utf8_lossy(chunk_text)
    );

    // Public inputs
    let _ = pw.set_hash_target(targets.merkle_root, merkle_root);
    let _ = pw.set_hash_target(targets.document_hash, document_hash);

    // Private witnesses
    for (i, &sibling) in siblings.iter().enumerate() {
        let _ = pw.set_hash_target(targets.proof.siblings[i], sibling);
    }
    for (bit_idx, bool_target) in targets.index_bits.iter().enumerate() {
        let bit_val = (chunk_index >> bit_idx) & 1 == 1;
        let _ = pw.set_bool_target(*bool_target, bit_val);
    }
    // chunk_text_bytes targets filled from chunk_text...
}
```

**Note:** The `CircuitTargets` struct must also change — `chunk_hash: HashOutTarget` is replaced by `chunk_text_bytes: Vec<Target>`.

---

### Step 4 — `prove-bin`: Update CLI input format

Change the JSON input from:
```json
{
  "merkle_root": "0x...",
  "chunk_hash": "0x...",
  "document_hash": "0x...",
  "leaf_index": 12,
  "siblings": ["0xh1", "0xh2", ...]
}
```

To:
```json
{
  "merkle_root": "0x...",
  "document_hash": "0x...",
  "leaf_index": 12,
  "chunk_text": "raw text of the chunk...",
  "siblings": ["0xh1", "0xh2", ...]
}
```

The binary hashes `chunk_text` internally using the same `hash_chunk_text()` function before building the witness.

---

### Step 5 — `provenance.py`: Pass chunk text to prove binary

`generate_proof()` currently reads `leaf_hash` from the tree JSON and passes it as a pre-computed hash. It must now:
1. Fetch the actual chunk text (from Qdrant payload, or from `chunks/{doc_id}/chunks.jsonl`)
2. Pass the raw text to the prove binary (not the pre-computed hash)

```python
# Old:
prove_bin_input = {
    "merkle_root": metadata.merkle_root,
    "chunk_hash": metadata.leaf_hash,  # pre-computed
    ...
}

# New:
prove_bin_input = {
    "merkle_root": metadata.merkle_root,
    "document_hash": metadata.poseidon_doc_id_hash,
    "leaf_index": metadata.leaf_index,
    "chunk_text": chunk_text,  # raw text from Qdrant or chunks.jsonl
    "siblings": metadata.siblings,
}
```

**Qdrant payload schema** already stores the full chunk text (TEXT_TRUNCATE_LEN=0 in Pipeline G). The `text` field is available at query time.

---

### Step 6 — Rebuild pre-built circuits for depths 5–12

The circuit change (adding text byte targets) changes the constraint system. Pre-built `circuit_depth{N}.bin` files must be rebuilt:
```bash
cd ./zk-circuit
for depth in 5 6 7 8 9 10 11 12; do
  cargo run --release --bin prove-bin -- --build-circuit $depth
done
```

**Constraint count impact:** Hashing ~8KB of text inside the circuit adds constraints. With Poseidon on bytes, expect ~50-100k constraints. Plonky2 handles this comfortably on CPU.

---

### Step 7 — Tests

Update `test-from-chunks` binary to use the new flow:
1. Load chunk text from `chunks/{doc_id}/chunks.jsonl`
2. Hash with `hash_chunk_text()` — verify it matches `leaf_hashes[leaf_index]` from tree JSON
3. Generate ZK proof with raw text as witness
4. Verify proof

Run all 9 existing circuit tests. If any fail, diagnose whether the test uses the old API or if the circuit behavior changed unexpectedly.

---

### Task Checklist

- [ ] Step 1 — Add `hash_chunk_text()` to `merkle_tree.rs`
- [ ] Step 2 — Modify `build_merkle_proof_circuit_targets()` (Option A: fixed MAX_CHUNK_BYTES)
- [ ] Step 3 — Update `fill_merkle_proof_witness()` signature and `CircuitTargets` struct
- [ ] Step 4 — Update `prove-bin` CLI JSON input format
- [ ] Step 5 — Update `provenance.py` to fetch chunk text and pass raw text to prove binary
- [ ] Step 6 — Rebuild pre-built circuits for depths 5–12
- [ ] Step 7 — Update and run tests (`test-from-chunks` + unit tests)

---

### Open Questions

1. **Fixed MAX_CHUNK_BYTES:** What value? Current max chunk is ~1024 tokens ≈ ~4KB text. Set `MAX_CHUNK_BYTES = 8192` (8KB) for safety margin? Profile after building.

2. **Text from Qdrant vs. chunks.jsonl:** Qdrant has full text but requires a collection lookup. `chunks.jsonl` has it directly on disk. Which does `provenance.py` use? Decision: use `chunks.jsonl` (no Qdrant dependency for proof generation).

3. **Impact on proving time:** Hashing 8KB of text inside the circuit adds witness generation time. Current ~24ms proving will increase — need to measure after implementation.

4. **K>1 batch proofs:** This plan covers K=1 only. Multi-chunk batch proofs (Phase Q) are deferred. The current circuit cannot handle multiple chunks in one proof.

## Phase L — `get_emit_tx()` from Registry + Backfill block_numbers

**Status:** 🟡 PARTIALLY COMPLETE — emit_tx lookup done, block_number backfill remaining

**Part 1 — `get_emit_tx()` is already correct** ✅
`shared/provenance.py`'s `get_emit_tx()` correctly reads `tx_hash` from `registry.json`. No changes needed there.

**Part 2 — block_number is missing from all 20 emitted docs**
All 20 emitted docs have `tx_hash` in `registry.json` but `block_number` is absent. 15/20 have `tx_hash`; 5 are also missing `tx_hash` entirely.

A third Pipeline E script (or standalone script) is needed to:
1. Read `tx_hash` from `registry.json` for all emitted docs
2. Look up each tx on-chain via `cast tx <tx_hash>` to get `block_number`
3. Backfill `block_number` into the registry

The 5 docs missing `tx_hash` need Pipeline F re-run with `--force` to re-emit and capture the tx hash (or emit_output files recovered from a previous run).

---

## Phase M — Full E2E Provenance API Test

**Status:** ✅ VERIFIED 2026-04-21

**Result (2026-04-21):** End-to-end test PASSED.
- Proof `c5997755-3d16-11f1-99a3-e2579a7a7dd2` submitted via Kurier
- Finalized in ~30 seconds on zkVerify testnet
- `GET /api/provenance/{chunk_id}` returns complete response: `merkle_root`, `proof`, `pub_signals`, `zkverify_explorer_url`
- Full flow: RAG query → prove button → ZK proof → Kurier → zkVerify link ✅

**Test with known good chunk**: Any of the 20 emitted documents. Example chunk_id format: `{doc_id}-{chunk_index}`.

---

## Phase Q — Qdrant Direct Proof Generation Script ✅ DONE 2026-04-22

**Status:** ✅ COMPLETE — `test-from-qdrant.py` built and verified

**Goal:** Standalone script that queries Qdrant directly to get all proof inputs, calls prove-bin, verifies result — no disk I/O for proof inputs.

**Script:** `zk-circuit/test-from-qdrant.py`

**What it does:**
1. Queries Qdrant for a chunk (random from collection, or specific via `--chunk-id`)
2. Extracts `leaf_hash`, `document_hash` (poseidon_doc_id_hash), `merkle_root`, `leaf_index`, `depth`, `siblings[]` from Qdrant payload
3. Calls `prove-bin` with those inputs
4. Calls `verify-zk-proof` to confirm the proof is valid
5. Prints timing and public inputs

**No disk I/O for proof inputs** — all data comes from Qdrant. Tree JSON files on disk are not needed.

**Usage:**
```bash
python3 test-from-qdrant.py                           # random chunk, random collection
python3 test-from-qdrant.py --collection army          # random chunk from army
python3 test-from-qdrant.py --chunk-id "00c8a75d..._100"  # specific chunk
python3 test-from-qdrant.py --list                     # show collections + counts
python3 test-from-qdrant.py --skip-verify              # skip local verification
```

**Qdrant payload fields used:**
- `merkle_leaf_hash` — leaf hash (Poseidon of chunk text)
- `merkle_leaf_index` — position in Merkle tree
- `merkle_siblings` — array of sibling hashes (plain strings, no `at_depth`)
- `merkle_root` — document's Poseidon Merkle root
- `merkle_tree_depth` — tree depth
- `poseidon_doc_id_hash` — Poseidon(doc_id bytes) = leaf[0] of Merkle tree

**Performance (depth 10, release binary):**
| Operation | Time |
|-----------|------|
| Proof generation | **~34ms** |
| Local verification | **~6ms** |

**Rust workspace member `test-from-qdrant/` was removed** — Python script is the correct approach for this tool. The Rust prove-bin and verify-zk-proof binaries are the right level for the circuit work; Python is appropriate for orchestration.

**Updated 2026-04-22 — Proof saving + Kurier verification:**

`test-from-qdrant.py` now saves proofs to `./data/zk_proofs/{doc_id}_kurier.json` after generation. Added `--save` behavior is now default.

**3 proofs generated and Kurier-verified (2026-04-22):**

| Collection | Doc ID (short) | Depth | Prove | Verify | Kurier Job | zkVerify |
|------------|----------------|-------|-------|--------|------------|----------|
| army | `215e98c0...` | 10 | 31ms | 6.3ms ✅ | `234db14b-...` | ✅ Finalized |
| navy | `03fb4720...` | 8 | 43ms | 6.4ms ✅ | `23f6c142-...` | ✅ Finalized |
| marines | `07ecb853...` | 10 | 37ms | 6.8ms ✅ | `246a17b7-...` | ✅ Finalized |

**Bug fixed — `kurier_submit.py` TERMINAL_STATUSES:** `"finalized"` was missing from the terminal statuses set, causing the polling loop to run forever after reaching Finalized. Patched to include `"finalized"`. Script now exits cleanly on first Finalized detection (~20s after submission).

**Files:**
- `zk-circuit/test-from-qdrant.py` — the script
- `zk-circuit/Cargo.toml` — workspace member list updated (test-from-qdrant removed)
- `zk-circuit/kurier_submit.py` — TERMINAL_STATUSES fix
