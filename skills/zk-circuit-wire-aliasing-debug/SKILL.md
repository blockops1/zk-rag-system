---
name: zk-circuit-wire-aliasing-debug
description: Debug wire aliasing in plonky2 ZK circuits — "wire set twice" errors in Poseidon hash gates with large inputs, and distinguishing panic-before-conflict from genuine test pass.
category: zk-rag
---

# ZK Circuit Wire Aliasing Debug

## Context
When plonky2's `hash_or_noop` (PoseidonHash) is called on a large input vector (e.g., 1024 elements), the internal Poseidon hashing creates intermediate wires at low column indices (0–11) plus round constant wires at column 12+. If `add_virtual_target()` was previously called for the same number of targets, those virtual target wires overlap with the hash gate's internal wires. When `pw.set_target()` is called, plonky2 detects the wire was already set by the gate and throws "wire set twice" error.

## Symptom Pattern
```
Partition containing Wire(Wire { row: N, column: 12 }) was set twice with different values: A != B
```
- Column 12 = plonky2's round constant region for Poseidon hash gate
- Tests with "correct" data fail here; "wrong" data tests pass only because they panic earlier

## Diagnosis Method
1. Run `cargo test` — note which tests pass vs fail
2. **Counterintuitive**: Tests that panic with "wrong" data may be passing for the WRONG reason (panic before reaching the wire conflict, not because the test assertion is correct)
3. Tests with correct data that reach `data.prove(pw)` hit the aliasing
4. Check `fill_merkle_proof_witness` for dead code — `hash_chunk_text()` result was never used, meaning circuit and witness were misaligned

## Two Root Causes Found
1. **Large-input aliasing**: `hash_or_noop` on N targets where N ≈ plonky2's internal column count creates wire overlap
2. **Dead code**: `_computed_leaf = hash_chunk_text(...)` in witness code is computed but never passed to `pw.set_hash_target()` — the circuit and witness were misaligned

## Fix Strategies

### Option A — Pre-computed hash as private witness (recommended)
```rust
// CircuitTargets: replace Vec<Target> with HashOutTarget
pub struct CircuitTargets {
    pub leaf_hash: HashOutTarget,  // pre-computed by witness
    pub index_bits: Vec<BoolTarget>,
    pub siblings: Vec<HashOutTarget>,
}
```
- Witness computes `hash_chunk_text(text)` in Python/Rust
- Circuit receives leaf_hash as a private witness target
- Circuit verifies Merkle proof against `merkle_root` using leaf_hash
- No large `hash_or_noop` call → no wire aliasing
- Security: Merkle proof path binds text to the committed tree root

### Option B — Keep in-circuit hashing, avoid hash_or_noop
- Replace `builder.hash_or_noop::<PoseidonHash>(text_elements.clone())` with manual per-element Poseidon gate calls or iterative hashing with smaller chunks
- More complex but preserves in-circuit text hashing

## plonky2 Poseidon Hash Gate Internals
- Columns 0–11: state elements for Poseidon permutation rounds
- Column 12+: round constants (Poseidon has ~56 rounds for 64-bit field)
- When `hash_n_to_m_no_pad` processes N elements where N ≥ 12, it allocates wires that may alias with `add_virtual_target()` wires
- Safe threshold: keep input size below the gate's internal wire range

## Verification
After fix: `cargo test --release` should show all 9 tests passing.
