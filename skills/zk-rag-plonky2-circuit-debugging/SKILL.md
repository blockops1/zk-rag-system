---
name: zk-rag-plonky2-circuit-debugging
description: Debug plonky2 ZK circuit issues in the ZK-RAG system — version conflicts, wire conflicts, serialization, field mismatches, and the RandomAccessGate constant-generator pairing bug.
category: zk-rag
---

# ZK-RAG plonky2 Circuit Debugging

Comprehensive guide for diagnosing and fixing plonky2 circuit issues in the ZK-RAG proving system.

## Quick Decision Tree

```
Proof generation fails?
├── "wire set twice" error → WIRE CONFLICT (§3)
├── Version/trait bound error → VERSION CONFLICT (§1)
├── Proof verification fails → FIELD MISMATCH (§4)
├── Circuit build slow → SERIALIZATION (§2)
└── "set twice" on constant wires → RANDOMACCESSGATE BUG (§5)
```

## §1 — plonky2 Version Conflict: zk-circuit vs plonky2-verifier

**Symptom:** `error[E0277]: the trait bound GoldilocksField: plonky2::hash::hash_types::RichField is not satisfied`

**Root cause:** `plonky2-verifier` v0.2.1 (from zkVerify) internally uses a **forked plonky2** from `github.com/zkVerify/plonky2` at tag `v0.1.0`. Our `zk-circuit` crate uses `plonky2 v0.2.2` from crates.io. These are completely separate crate instances — same struct names, incompatible types.

### Fix

**Rule: First eliminate the duplicate. Do not use subprocess isolation until the duplicate is ruled out.**

```bash
# Step 1 — Find what's causing the dual version
cargo tree -p plonky2 2>&1 | grep -E "^plonky2|^plonky2_field|^plonky2_maybe"

# Step 2 — Find the culprit
cargo tree -p plonky2 --duplicates 2>&1
cargo tree -p plonky2 -i 2>&1 | head -30  # shows what depends on each instance
```

Common offender: `plonky2-ed25519` or any crate depending on a different plonky2 version.

### Step 3 — Remove the duplicate (preferred)

If the transitive crate isn't essential, remove it from `Cargo.toml`:
```bash
cargo build -p zk-circuit 2>&1 | grep -i "multiple\|different versions"
```
If only ONE plonky2 appears, the conflict is resolved. `ZKVerifyGateSerializer` becomes accessible directly.

### When subprocess isolation IS still needed

If the duplicate cannot be removed, fall back to JSON-based subprocess handoff:
1. `prove-bin` generates proof using `zk-circuit`
2. Serialize proof + common data to JSON (base64 bytes)
3. Spawn a thin verification process that calls `deserialize_vk` + `verify`

---

## §2 — plonky2 Circuit Serialization: Pre-Build for Fast Proving

**Context:** `prove-bin` was calling `builder.build::<C>()` on every invocation — rebuilding the circuit from scratch each time instead of loading pre-built `CircuitData`. This was the bottleneck, not `data.prove(pw)` (~11-273ms).

### How plonky2 CircuitData Serialization Works

```rust
use plonky2::util::serialization::{DefaultGateSerializer, DefaultGeneratorSerializer};
use plonky2::plonk::circuit_data::CircuitData;

type C = plonky2::plonk::config::PoseidonGoldilocksConfig;
const D: usize = 2;

// ── Build once + serialize ──────────────────────────────────────
let config = CircuitConfig::standard_recursion_config();
let mut builder = CircuitBuilder::<F, D>::new(config);
let targets = build_merkle_proof_circuit_targets(&mut builder, depth);
let data: CircuitData<F, C, D> = builder.build::<C>();

let gate_serializer = DefaultGateSerializer;
let generator_serializer = DefaultGeneratorSerializer::<C, D>::default();

let circuit_bytes = data.to_bytes(&gate_serializer, &generator_serializer)?;
std::fs::write(format!("circuit_depth{depth}.bin"), &circuit_bytes)?;

// ── Load + use on subsequent calls ────────────────────────────────
let circuit_bytes = std::fs::read(format!("circuit_depth{depth}.bin"))?;
let data = CircuitData::from_bytes(
    &circuit_bytes,
    &gate_serializer,
    &generator_serializer,
)?;
// Then just: data.prove(pw) — no circuit rebuild
```

### Architecture Decision: Commit .bin vs. Generate on First Run

**Option A — Commit to repo:** Pre-build circuits for depths 5-12 and ship `.bin` files in the repo alongside the binary. The `.bin` files are small (~2-5KB) and the build time for each depth is 30-60s.

**Recommendation:** Option A — commit the pre-built circuit data.

### Relevant Serializers

- `DefaultGateSerializer` — from `plonky2::util::serialization`
- `DefaultGeneratorSerializer::<C, D>::default()` — from same module
- `ZKVerifyGateSerializer` — from `plonky2_verifier` (for zkVerify-compatible VK serialization, NOT for full CircuitData)

---

## §3 — Wire Conflict: "wire set twice" Errors

**Symptom:** `Wire(row: N, column: K) was set twice with different values` at `data.prove(pw).unwrap()`

**Root cause:** When building a plonky2 circuit that uses `hash_or_noop::<PoseidonHash>(text_elements.clone())` and then tries to SET those same `text_elements` targets in the witness via `pw.set_target()`, plonky2 allocates **constraints** on those wires as part of the hash circuit. When the witness tries to SET those wires to concrete values, plonky2 detects a conflict.

### Fix

Use **separate target vectors** for the hash input vs. the witness-settable values:

```rust
// WRONG — wire conflict:
let text_hash = builder.hash_or_noop::<PoseidonHash>(text_elements.clone());
// ... later in witness ...
pw.set_target(text_elements[i], F::from_canonical_u8(chunk_text[i]));

// CORRECT — separate targets:
let text_elements_for_hash: Vec<Target> = text_elements.iter().copied().collect();
let text_hash = builder.hash_or_noop::<PoseidonHash>(text_elements_for_hash);
// ... later in witness ...
pw.set_target(text_elements[i], F::from_canonical_u8(chunk_text[i]));
```

**Files affected:** `zk_proofs/src/merkle_tree.rs` — `fill_merkle_proof_witness()` function

### Verification

```bash
cargo test --package zk_proofs
```
All 9 tests should pass (6 positive + 3 negative tests for wrong proof verification).

---

## §4 — Field Mismatch Debugging: Pipeline E (Goldilocks) vs Circuit (BN254)

**Symptom:** Proof verification fails even though roots appear to match.

**Root cause:** Field mismatch between the Python/Pipeline layer and the circuit layer.

| Component | Field | Byte Packing |
|-----------|-------|-------------|
| `zk-circuit` (BN254) | BN254 (`p = 21888...`) | 8 bytes per field element |
| `build_merkle_trees` (Goldilocks) | Goldilocks (`2^64 - 2^32 + 1`) | 7 bytes per field element |

These are **incompatible** — same input bytes → different Poseidon output.

### Discovery Method

Trace the data flow backward from circuit input to pipeline output:
1. Find the circuit's `hash_leaf` / leaf-hashing function (8-byte LE packing for BN254)
2. Find Pipeline E's tree builder (`build_merkle_trees` binary)
3. Find the underlying merkle library (`bytes_to_field_elements`)
4. Compare packing strategy and field type

### Key Files to Check

| Layer | File | What to look at |
|-------|------|----------------|
| Circuit | `zk-circuit/circuit/src/merkle_tree.rs` | `hash_leaf()`, `parse_hash()`, `hash_to_hex()` |
| Circuit | `zk-circuit/prove-bin/src/main.rs` | plonky2 circuit proving logic |
| Tree builder | `zk-circuit/pipeline_e/src/main.rs` | field type, byte packing |
| Merkle lib | `zk-circuit/circuit/src/merkle_tree.rs` | `bytes_to_field_elements()` — Goldilocks field, 7-byte chunks |

### Also Check: Struct Field Mismatches

Before diving into field arithmetic, verify the **struct fields** match between Python and Rust:
- `ChunkInput.merkle_root: str` — must be a hex string, NOT `merkle_cap: list[str]`
- `ProofRecord.merkle_root: str` — same, not `merkle_cap`
- `ChunkInput.mode: "single-root"` — not `"multi-root"` (cap_height=0 in Pipeline F)

### Debugging Tips

- If proof verification fails: compare `hash_to_hex(root_from_pipeline)` vs `hash_to_hex(root_from_circuit)` — if different fields, they will differ
- If roots match but proof still fails: check sibling ordering (left/right selection)
- If roots match but proof still fails: check depth calculation — `depth = trailing_zeros(next_power_of_two(n))`
- If roots match but proof still fails: check `HashOut::ZERO` padding — circuit may expect different zero representation

### Canonical Path for Fix

1. Update Pipeline E tree builder to use BN254 field + 8-byte packing
2. Switch `build_merkle_trees` to `cap_height=0` (single root, not 16-entry cap)
3. Re-run Pipeline E on all docs to regenerate trees
4. Verify circuit proof generation uses same tree builder as pipeline

---

## §5 — RandomAccessGate Constant-Generator Pairing Bug

**Symptom:** `assertion failed: Partition containing Wire(row, column) was set twice with different values`

**Root cause:** plonky2 v0.2.2 itself — the constant-to-generator pairing algorithm mismatches `constants_to_targets` (real constants) vs `constant_generators` (gates that write to column 23).

When a gate creates extra constant generators (e.g., `RandomAccessGate` with `num_extra_constants=8`), `constant_generators` can exceed `constants_to_targets` in count. Plonky2 pairs them by sorted index order — extra generators get **garbage constant values** → witness generation fails.

**Confirmed:** Even the hashcloak `merkle_proof_example1.rs` crashes with this error — proving it's a plonky2 issue.

### The Fix

**Replace `register_public_inputs()` with `add_virtual_hash_public_input()` + `builder.connect()`**

```rust
// WRONG — uses constant wires, triggers the bug:
builder.register_public_inputs(&root.elements);

// RIGHT — uses virtual public input wires, bypasses constant mechanism:
let expected_root = builder.add_virtual_hash_public_input();
for i in 0..4 {
    builder.connect(computed_root.elements[i], expected_root.elements[i]);
}
```

This works because `add_virtual_hash_public_input()` creates regular virtual wires (not constant wires). The plonky2 constant-generator pairing bug only affects column 23 (constant wires).

### Verified Working

Fresh build at `<REPO>zk-circuit-fresh/`:
- Hashcloak merkle proof pattern adapted for plonky2 v0.2.2
- Uses `add_virtual_hash_public_input()` + `builder.connect()` instead of `register_public_inputs()`
- Proves and verifies successfully

### The Real Challenge: Cap Height & Tree Depth

**Tree: 32 leaves, `cap_height=4`**

- `depth = log2(32) = 5` levels from leaf (level 0) to root (level 4)
- `cap_height = 4` means top 4 levels are absorbed into the **cap** (a set of 16 root hashes)
- `siblings_to_cap = depth - cap_height = 5 - 4 = 1` — **only 1 level** of iterative hashing below the cap

The circuit design must account for this: only 1 iteration of sibling hashing, then cap entry selection.

### Patterns to Avoid

- `builder.register_public_inputs()` — creates constant wires, triggers the bug
- Any gate that uses `num_extra_constants > 0` unless you control the pairing
- `le_sum` (internally uses problematic gates)

### Patterns That Are Safe

- `builder.hash_or_noop()` — Poseidon hashing
- `builder.add_virtual_hash_public_input()` — public inputs without constant wires
- `builder.connect()` — wire equality constraints
- `builder.select()` / `builder.select_hash()` — conditional selection
- Manual merkle path verification using poseidon hashes + select

### The plonky2 cap_height Semantics

In plonky2 `MerkleTree::new(leaves, cap_height)`:
- `cap_height` is an **exponent** — the cap has `2^cap_height` entries
- `cap_height = 0` → **exactly 1 cap entry** (the single root hash) ✅ what you want for single-root design
- `cap_height = 4` → 16 cap entries (2^4 = 16)
- `cap_height = tree_depth` → 2^tree_depth cap entries (e.g., 256 for depth=8) — NOT a single root

**Common mistake:** Setting `cap_height = tree_depth` hoping for 1 entry. You get 2^tree_depth entries instead.

**How to get a single root:** Use `cap_height = 0`.

Source: `plonky2/src/hash/merkle_tree.rs` lines 152-175.

### Related Skills

Former narrow skills absorbed into this umbrella:
- `zk-rag-plonky2-version-conflict` — §1 above
- `zk-rag-plonky2-circuit-serialization` — §2 above
- `zk-rag-plonky2-wire-conflict-debug` — §3 above
- `zk-rag-field-mismatch-debug` — §4 above
- `zk-rag-randomaccessgate-bug` — §5 above
