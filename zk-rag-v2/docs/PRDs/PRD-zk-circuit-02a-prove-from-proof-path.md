# PRD-zk-circuit-02a: ZK Proof Generation from Pre-computed Merkle Proof Paths

**Status:** Draft
**Author:** Fred
**Date:** 2026-04-17
**Parent:** SECTION-zk-circuit-02-implementation.md (Phase A)

---

## 1. Problem Statement

The existing `prove_locally()` function in `prove.rs` generates ZK proofs by loading the full Merkle tree into memory and calling `tree.prove(leaf_index)` at proof time. This requires:
- The complete set of all leaf data in field element form (all 256 leaves for a document)
- A fully constructed `CorpusMerkleTree<F>` object

At query time, the API server has **pre-computed Merkle proof paths** stored alongside each chunk in Qdrant. It also has the chunk text. It does not have — and should not need — the full tree.

**Goal:** Generate valid ZK proofs using pre-computed sibling paths instead of rebuilding them from the tree.

---

## 2. Design Decision: Two Witness-Filling Paths

The circuit (`circuits/zk_rag.rs`) and its `CircuitData` are **unchanged**. The same compiled constraint system is used either way. Only the witness-filling differs.

| Function | Tree Required | Use Case |
|----------|--------------|----------|
| `fill_zk_rag_witness()` (existing) | Yes — calls `tree.prove(index)` | Bulk pre-processing (Pipeline E context) |
| `fill_zk_rag_witness_from_path()` (new) | No — uses pre-computed siblings | Query time |

Both produce **bit-for-bit identical proofs** given the same (chunk_text, chunk_index, siblings, cap) inputs. The constraint system doesn't know or care how the witness was derived.

---

## 3. Data Flow

### Existing path (kept for testing):
```
all_leaves (Vec<Vec<F>>) + tree (CorpusMerkleTree)
  → fill_zk_rag_witness(tree, all_leaves, ...)
  → prove_locally(circuit, tree, all_leaves, ...)
  → STARK proof
```

### New path (Phase A):
```
precomputed_siblings (Vec<HashOut<F>>) + chunk_text (String) + cap (Vec<HashOut<F>>)
  → fill_zk_rag_witness_from_path(cap, chunks_with_siblings, ...)
  → prove_from_proof_path(circuit, cap, chunks_with_siblings, ...)
  → STARK proof
```

---

## 4. New Rust API

### 4.1 `ChunkProofInput<F>` struct — added to `witness.rs`

```rust
/// Input for a single chunk in the from-path witness-filling path.
/// Replaces the need for the full Merkle tree at query time.
#[derive(Debug, Clone)]
pub struct ChunkProofInput<F: RichField> {
    /// Raw chunk text (encoded to field elements inside fill function).
    pub text: String,
    /// Leaf index in the sorted Merkle tree (0 = doc_id leaf, 1..N = chunks).
    pub sorted_index: usize,
    /// Pre-computed Merkle proof siblings — 4 HashOuts for depth-4 cap.
    /// Must be ordered from leaf → cap (depth 0 first).
    pub siblings: Vec<HashOut<F>>,
}
```

### 4.2 `fill_zk_rag_witness_from_path()` — new function in `witness.rs`

```rust
/// Fill circuit witness using pre-computed Merkle proof paths.
/// Does NOT require the full Merkle tree.
///
/// - `pw`: PartialWitness to fill
/// - `targets`: ZkRagCircuitTargets from the compiled circuit
/// - `cap`: 16 HashOuts — the MerkleCap (from tree JSON)
/// - `chunks`: per-chunk data — text, index, pre-computed siblings
/// - `llm_input_text`: full LLM input prompt
/// - `llm_output_text`: LLM output text
pub fn fill_zk_rag_witness_from_path<F: RichField>(
    pw: &mut PartialWitness<F>,
    targets: &ZkRagCircuitTargets,
    cap: &[HashOut<F>],
    chunks: &[ChunkProofInput<F>],
    llm_input_text: &str,
    llm_output_text: &str,
) -> Result<(), WitnessError>
```

**Implementation notes:**
- `cap` targets: set directly from `cap` array (16 `HashOutTarget` → `HashOut<F>`)
- Per chunk:
  - `text_limbs = bytes_to_field_elements(text.as_bytes())` — re-encode text to field elements
  - **Error if `text_limbs.len() > ZK_MAX_CHUNK_LIMBS` (512):** Log `ERROR` with chunk ID and actual limb count. Return `WitnessError::ChunkTooLarge`. Rejecting is correct here — truncating would produce a proof over different bytes than the original leaf hash committed in the Merkle tree, resulting in a proof that fails verification.
  - `hash = PoseidonHash::hash_or_noop(&text_limbs)` — recompute hash in-circuit (matches leaf hash)
  - Set `hash_target` to this computed value
  - Set `index_bits_target` from `sorted_index` (converts to 8 bit targets, little-endian — bit 0 = LSB)
  - Set `sibling_targets` directly from `siblings[i]` array
- `llm_input_hash` / `llm_output_hash`: compute and set from text

**LLM text size checks (also enforced):**
- `llm_input_text`: Error if `limbs.len() > ZK_MAX_LLM_INPUT_LIMBS` (1024) → `WitnessError::LlmInputTooLarge`
- `llm_output_text`: Error if `limbs.len() > ZK_MAX_LLM_OUTPUT_LIMBS` (512) → `WitnessError::LlmOutputTooLarge`
  - Same rationale: truncating produces a hash that doesn't match the circuit's recomputation, causing verification failure

### 4.3 `prove_from_proof_path()` — new function in `prove.rs`

```rust
use plonky2::hash::hash_types::HashOut;
use plonky2::plonk::circuit_data::CircuitData;

use crate::circuits::zk_rag::ZkRagCircuit;
use crate::witness::{ChunkProofInput, fill_zk_rag_witness_from_path};

/// Generate a ZK proof using pre-computed Merkle proof paths.
/// Does not load the full Merkle tree.
pub fn prove_from_proof_path<F: RichField>(
    circuit: &ZkRagCircuit,
    cap: &[HashOut<F>],
    chunks: &[ChunkProofInput<F>],
    llm_input_text: &str,
    llm_output_text: &str,
) -> Result<ProofWithPublicInputs<F, C, D>, ProveError>
where
    C: GenericConfig<D, F = F>,
{
    // Build witness using new path
    let mut pw = PartialWitness::new();
    fill_zk_rag_witness_from_path(
        &mut pw,
        &circuit.targets,
        cap,
        chunks,
        llm_input_text,
        llm_output_text,
    )?;

    // Generate proof with the pre-built circuit
    prove(circuit, pw)
}
```

### 4.4 `prove()` helper — extracted from existing `prove_locally()`

Extract the inner `prove()` call from `prove_locally()` so it can be reused:

```rust
fn prove<F: RichField>(
    circuit: &ZkRagCircuit,
    pw: PartialWitness<F>,
) -> Result<ProofWithPublicInputs<F, C, D>, ProveError>
where
    C: GenericConfig<D, F = F>,
{
    circuit.prove(pw).map_err(ProveError::from)
}
```

Existing `prove_locally()` becomes:
```rust
pub fn prove_locally<F: RichField>(...) -> Result<...> {
    // ... build witness the old way ...
    prove(circuit, pw)  // reuse extracted helper
}
```

---

## 5. CLI Changes (`src/bin/prove.rs`)

### 5.1 New `--mode from-path` flag

```rust
enum ProveMode {
    FromTree,       // existing: --mode tree (default)
    FromProofPath,  // new: --mode from-path
}
```

Default mode remains `FromTree` for backward compatibility with existing tests.

### 5.2 New input JSON format for `from-path` mode

```json
{
  "mode": "from-proof-path",
  "cap": [
    "0xcc5662e4f4ae16457ea31877e0f0fa38994c5f559ba1f9f9c0e94674e050c1cb",
    "0x23d5d07e612ca1be0c555a490922b54fb1a3a4628b0c9c90a359989e626e6ca0",
    ...
  ],
  "chunks": [
    {
      "text": "actual chunk text from Qdrant payload...",
      "sorted_index": 42,
      "siblings": [
        "0xh1...",
        "0xh2...",
        "0xh3...",
        "0xh4..."
      ]
    }
  ],
  "llm_input_text": "Context:\n[chunk texts]\n\nQuery: ...",
  "llm_output_text": "LLM response..."
}
```

`siblings` is ordered depth 0 → depth 3 (leaf-adjacent first). 4 siblings for cap height 4, tree depth 8.

### 5.3 Output JSON format

```json
{
  "proof": "base64-encoded-proof-bytes",
  "public_inputs": {
    "cap": ["0x...", ...],
    "llm_input_hash": "0x...",
    "llm_output_hash": "0x..."
  },
  "circuit_data": "base64-encoded-common-circuit-data",
  "verifier_data": "base64-encoded-verifier-only-data"
}
```

### 5.4 CLI argument changes

```
prove [FLAGS]
    --mode MODE     Mode: "tree" (default) or "from-path"
    --input PATH    Input JSON file path
    --output PATH   Output JSON file path
```

---

## 6. Sibling Ordering Convention

From tree JSON (`paths[leaf_index]["siblings"]`):
```json
[
  {"hash": "0x...", "at_depth": 0},
  {"hash": "0x...", "at_depth": 1},
  {"hash": "0x...", "at_depth": 2},
  {"hash": "0x...", "at_depth": 3}
]
```

The `"at_depth"` indicates sibling's depth in the tree, not its position in the proof chain. All 4 siblings are siblings *along the path* from the leaf to the cap, at increasing depths.

**Ordering:** Siblings are stored depth-0-first in the tree JSON. The plonky2 `MerkleProof::siblings` field is also depth-0-first. So no re-ordering is needed when converting from tree JSON → `ChunkProofInput.siblings`.

The existing `build_merkle_payload()` in `pipeline_g.py` already stores them in this order.

---

## 7. What Doesn't Change

| Component | Change |
|-----------|--------|
| `circuits/zk_rag.rs` | None — constraints untouched |
| `circuits/merkle.rs` | None |
| `witness.rs` `assemble_witness()` | None — still used by bulk pipeline |
| `prove_locally()` | None — kept for backward compatibility + testing |
| `CircuitData` | Identical — same compiled circuit used either way |
| Tree JSON format | None — already stores siblings correctly |
| Qdrant payload format | None — already stores `merkle_path` |

---

## 8. Test Plan

### 8.1 Unit test: `test_prove_from_proof_path_vs_tree()`

**Synthetic data, no real tree needed:**
1. Build a `CorpusMerkleTree` with 16 synthetic leaves
2. Call `prove_locally()` with a single chunk → get `proof_A`
3. Extract the same chunk's: `text`, `sorted_index`, `siblings`, `cap`
4. Call `prove_from_proof_path()` with those values → get `proof_B`
5. Assert `proof_A == proof_B` (public inputs identical, proof bytes identical)

This proves the two paths produce identical output.

### 8.2 Integration test: `test_cli_from_path_mode()`

```bash
# Generate synthetic input
echo '{
  "mode": "from-proof-path",
  "cap": ["0x0000...", ...],
  "chunks": [{"text": "test", "sorted_index": 1, "siblings": [...] }],
  "llm_input_text": "test input",
  "llm_output_text": "test output"
}' > /tmp/test_input.json

./target/release/prove --mode from-path --input /tmp/test_input.json --output /tmp/test_output.json

# Verify output has all required fields
jq -e '.proof' /tmp/test_output.json
jq -e '.public_inputs.cap | length == 16' /tmp/test_output.json
```

### 8.3 Unit test: `test_oversized_chunk_rejected()`

The `ChunkTooLarge` error path is effectively unreachable with the current corpus (max chunk: 798 bytes vs. 3584-byte limit), but must be exercised via synthetic data to confirm the circuit rejects oversized input rather than silently truncating.

1. Create a `ChunkProofInput` with `text = "a".repeat(4000)` (4000 bytes → ~572 limbs, exceeds 512-limb limit)
2. Call `fill_zk_rag_witness_from_path()` with this chunk
3. Assert `Err(WitnessError::ChunkTooLarge(n, 512))` is returned
4. Assert an `ERROR` log line was emitted containing the chunk byte count

### 8.4 Unit test: `test_oversized_llm_input_rejected()`

Same pattern for LLM input text:
1. Call with `llm_input_text = "x".repeat(8000)` (exceeds 1024-limb limit)
2. Assert `Err(WitnessError::LlmInputTooLarge)` returned + log line

---

## 9. Verification Criteria

- [ ] `cargo test -p zk-rag` — all tests pass
- [ ] New unit test `test_prove_from_proof_path_vs_tree` passes
- [ ] `test_cli_from_path_mode` passes
- [ ] `test_oversized_chunk_rejected` passes (synthetic oversized input)
- [ ] `test_oversized_llm_input_rejected` passes (synthetic oversized input)
- [ ] `prove --mode from-path` produces output identical to `prove --mode tree` for the same data
- [ ] Binary builds: `cargo build --release -p zk-rag`
