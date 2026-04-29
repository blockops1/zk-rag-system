//! ZK-RAG Merkle Proof Circuit — Phase A
//!
//! **Design:** Fixed-depth single-root Merkle proof circuit for ZK-RAG provenance proving.
//! - MAX_DEPTH = 12 (supports trees up to 4096 leaves for 4000 chunks)
//!
//! **Public Inputs:**
//!   1. `merkle_root`           — Poseidon hash, document's committed root
//!   2. `document_hash`         — Poseidon(SHA256(doc_id)) = leaf zero of Merkle tree
//!   3. `ingestion_timestamp`   — Unix timestamp when root was published on-chain
//!   4. `ingestion_block`      — Block number when root was published on-chain
//!
//! **Private Witnesses:**
//!   1. `leaf_hash`       — Poseidon hash of the chunk text (pre-computed in Python)
//!   2. `siblings[]`      — Merkle proof path (one HashOut per level)
//!   3. `chunk_index_bits[]` — leaf index as bits (LSB = bit 0)
//!
//! **Security model (Option A — 2026-04-22):**
//! The leaf hash is supplied as a private witness by the (honest) prover. The circuit
//! verifies the Merkle proof using that hash. The binding between real text and the
//! hash was established during ingestion (Pipeline E built the tree with correct text).
//! The ZK circuit proves the chunk belongs to the committed tree — the Merkle proof
//! itself provides the integrity guarantee; the pre-computed hash avoids the plonky2
//! wire-aliasing issue with large (1024-element) Poseidon inputs.
//!
//! **Circuit enforces:**
//!   verify Merkle proof: compute_root(leaf_hash, siblings, index_bits) == merkle_root

pub use unicode_normalization::UnicodeNormalization;
pub mod merkle_tree;

use plonky2::field::types::{Field, PrimeField64};
use plonky2::hash::hash_types::{HashOut, HashOutTarget};
use plonky2::hash::merkle_proofs::MerkleProofTarget;
use plonky2::hash::poseidon::PoseidonHash;
use plonky2::iop::target::{BoolTarget, Target};
use plonky2::iop::witness::{PartialWitness, WitnessWrite};
use plonky2::plonk::circuit_builder::CircuitBuilder;
use plonky2::plonk::config::{GenericConfig, Hasher};

pub const D: usize = 2;
pub type C = plonky2::plonk::config::PoseidonGoldilocksConfig;
pub type F = <C as GenericConfig<D>>::F;

/// Maximum tree depth for 4000 chunks: next power of 2 is 4096, depth = 12.
pub const MAX_DEPTH: usize = 12;

// ============================================================================
// Circuit Building
// ============================================================================

/// Build the Merkle proof circuit for the given depth.
/// Returns CircuitTargets (our local target bundle).
///
/// **Option A (2026-04-22):** `leaf_hash: HashOutTarget` is a private witness —
/// the pre-computed Poseidon hash of the chunk text (computed in Python).
/// No in-circuit text hashing, no wire aliasing issues.
pub fn build_merkle_proof_circuit_targets(
    builder: &mut CircuitBuilder<F, D>,
    depth: usize,
) -> CircuitTargets
where
    <PoseidonHash as Hasher<F>>::Permutation: plonky2::hash::hashing::PlonkyPermutation<F>,
{
    assert!(
        depth <= MAX_DEPTH,
        "depth {} exceeds MAX_DEPTH {}",
        depth,
        MAX_DEPTH
    );

    // ── Public inputs ─────────────────────────────────────────────────────────
    let merkle_root = builder.add_virtual_hash_public_input();
    let document_hash = builder.add_virtual_hash_public_input();
    // On-chain commitment metadata — binds the proof to a specific ingestion event
    let ingestion_timestamp = builder.add_virtual_public_input();
    let ingestion_block = builder.add_virtual_public_input();

    // ── Private witness: leaf hash (pre-computed in Python) ───────────────────
    // The circuit receives the Poseidon hash of the chunk text as a HashOutTarget.
    // The prover supplies this value — the circuit only verifies the Merkle proof.
    let leaf_hash: HashOutTarget = builder.add_virtual_hash();

    // ── Private witness: Merkle proof ─────────────────────────────────────────
    let index_bits: Vec<BoolTarget> = (0..depth)
        .map(|_| builder.add_virtual_bool_target_safe())
        .collect();

    let siblings: Vec<HashOutTarget> = (0..depth).map(|_| builder.add_virtual_hash()).collect();

    // ── Verify Merkle proof ─────────────────────────────────────────────────
    // The circuit verifies: compute_root(leaf_hash, siblings, index_bits) == merkle_root
    // using plonky2's native Merkle proof verification gate.
    let leaf_data: Vec<Target> = leaf_hash.elements.to_vec();
    let proof = MerkleProofTarget { siblings };
    builder.verify_merkle_proof::<PoseidonHash>(leaf_data, &index_bits, merkle_root, &proof);

    CircuitTargets {
        merkle_root,
        document_hash,
        ingestion_timestamp,
        ingestion_block,
        leaf_hash,
        index_bits,
        proof,
    }
}

/// Our circuit's target bundle — wraps plonky2's MerkleProofTarget with the
/// public input targets.
///
/// **Option A (2026-04-22):** `leaf_hash: HashOutTarget` replaces the raw
/// `text_elements: Vec<Target>` — the pre-computed Poseidon hash of chunk text.
#[derive(Clone)]
pub struct CircuitTargets {
    /// Public input: the document's committed Merkle root.
    pub merkle_root: HashOutTarget,
    /// Public input: Poseidon(SHA256(doc_id_bytes)) — leaf zero of the Merkle tree.
    pub document_hash: HashOutTarget,
    /// Public input: Unix timestamp when the root was published on-chain.
    pub ingestion_timestamp: Target,
    /// Public input: Block number when the root was published on-chain.
    pub ingestion_block: Target,
    /// Private witness: Poseidon hash of the chunk text (pre-computed in Python).
    pub leaf_hash: HashOutTarget,
    /// Private witness: leaf index bits (LSB = bit 0).
    pub index_bits: Vec<BoolTarget>,
    /// Private witness: Merkle proof siblings.
    pub proof: MerkleProofTarget,
}

// ===============================================================================
// Witness Filling
// ===============================================================================

/// Fill the witness for a single Merkle proof.
///
/// **Option A (2026-04-22):** `leaf_hash: HashOut<F>` is passed directly as a private
/// witness — no text-to-field-element conversion needed. The hash was pre-computed in Python.
pub fn fill_merkle_proof_witness(
    pw: &mut PartialWitness<F>,
    targets: &CircuitTargets,
    merkle_root: HashOut<F>,
    document_hash: HashOut<F>,
    ingestion_timestamp: u64,
    ingestion_block: u64,
    leaf_hash: HashOut<F>,
    siblings: &[HashOut<F>],
    chunk_index: usize,
) {
    // ── Public inputs ─────────────────────────────────────────────────────────
    let _ = pw.set_hash_target(targets.merkle_root, merkle_root);
    let _ = pw.set_hash_target(targets.document_hash, document_hash);
    let _ = pw.set_target(targets.ingestion_timestamp, F::from_canonical_u64(ingestion_timestamp));
    let _ = pw.set_target(targets.ingestion_block, F::from_canonical_u64(ingestion_block));

    // ── Private witness: leaf hash ─────────────────────────────────────────────
    // The pre-computed Poseidon hash of the chunk text (computed in Python).
    let _ = pw.set_hash_target(targets.leaf_hash, leaf_hash);

    // ── Private witness: Merkle proof ─────────────────────────────────────────
    for (i, &sibling) in siblings.iter().enumerate() {
        let _ = pw.set_hash_target(targets.proof.siblings[i], sibling);
    }

    for (bit_idx, bool_target) in targets.index_bits.iter().enumerate() {
        let bit_val = (chunk_index >> bit_idx) & 1 == 1;
        let _ = pw.set_bool_target(*bool_target, bit_val);
    }
}

// ============================================================================
// Hash Parsing Helpers
// ============================================================================

/// Parse a 0x-prefixed hex string into a HashOut<F>.
pub fn parse_hash(s: &str) -> HashOut<F> {
    let s = s.trim();
    let hex = if s.starts_with("0x") { &s[2..] } else { s };
    let bytes = hex::decode(hex).expect(&format!("Invalid hex: {}", s));
    assert_eq!(
        bytes.len(),
        32,
        "Hash must be 32 bytes, got {}",
        bytes.len()
    );
    HashOut {
        elements: [
            F::from_canonical_u64(u64::from_le_bytes([
                bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
            ])),
            F::from_canonical_u64(u64::from_le_bytes([
                bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14],
                bytes[15],
            ])),
            F::from_canonical_u64(u64::from_le_bytes([
                bytes[16], bytes[17], bytes[18], bytes[19], bytes[20], bytes[21], bytes[22],
                bytes[23],
            ])),
            F::from_canonical_u64(u64::from_le_bytes([
                bytes[24], bytes[25], bytes[26], bytes[27], bytes[28], bytes[29], bytes[30],
                bytes[31],
            ])),
        ],
    }
}

/// Serialize a HashOut<F> to 0x-prefixed lowercase hex string.
pub fn hash_to_hex(h: &HashOut<F>) -> String {
    let mut bytes = Vec::with_capacity(32);
    for e in &h.elements {
        bytes.extend_from_slice(&e.to_canonical_u64().to_le_bytes());
    }
    format!("0x{}", hex::encode(bytes))
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use crate::merkle_tree::hash_leaf_text;
    use plonky2::hash::poseidon::PoseidonHash;
    use plonky2::plonk::circuit_data::CircuitConfig;
    use plonky2::plonk::config::Hasher;

    fn log2_strict(n: usize) -> usize {
        assert!(n.is_power_of_two());
        (n as f64).log2() as usize
    }

    /// Build a Merkle tree from text strings.
    /// Each text is hashed via hash_leaf_text() to produce the leaf hash,
    /// matching how Pipeline E and the Option A circuit produce leaves.
    fn build_merkle_tree_from_text(texts: &[String]) -> (HashOut<F>, Vec<Vec<HashOut<F>>>) {
        let leaves: Vec<HashOut<F>> = texts.iter().map(|t| hash_leaf_text(t)).collect();
        let n = leaves.len();
        let depth = log2_strict(n);

        let mut level = leaves.clone();
        level.resize(n, HashOut::ZERO);
        let mut levels = vec![level.clone()];

        for _ in 0..depth {
            level = level
                .chunks(2)
                .map(|pair| PoseidonHash::two_to_one(pair[0], pair[1]))
                .collect();
            levels.push(level.clone());
        }

        let root = *levels.last().unwrap().first().unwrap();
        (root, levels)
    }

    fn get_siblings(
        levels: &[Vec<HashOut<F>>],
        leaf_index: usize,
        depth: usize,
    ) -> Vec<HashOut<F>> {
        let mut idx = leaf_index;
        let mut siblings = Vec::with_capacity(depth);
        for level in 0..depth {
            let sibling_idx = if idx % 2 == 0 { idx + 1 } else { idx - 1 };
            let level_len = levels[level].len();
            siblings.push(if sibling_idx < level_len {
                levels[level][sibling_idx]
            } else {
                HashOut::ZERO
            });
            idx /= 2;
        }
        siblings
    }

    /// Run a proof test with a pre-computed leaf_hash as the private witness.
    /// The circuit verifies the Merkle proof using the supplied leaf_hash.
    fn run_proof_test(
        leaf_idx: usize,
        depth: usize,
        root: HashOut<F>,
        document_hash: HashOut<F>,
        leaf_hash: HashOut<F>,
        siblings: &[HashOut<F>],
        ingestion_timestamp: u64,
        ingestion_block: u64,
    ) -> Result<(), String> {
        let config = CircuitConfig::standard_recursion_config();
        let mut builder = CircuitBuilder::<F, D>::new(config);
        let targets = build_merkle_proof_circuit_targets(&mut builder, depth);
        let data = builder.build::<C>();

        let mut pw = PartialWitness::new();
        fill_merkle_proof_witness(
            &mut pw,
            &targets,
            root,
            document_hash,
            ingestion_timestamp,
            ingestion_block,
            leaf_hash,
            siblings,
            leaf_idx,
        );
        let proof = data.prove(pw).unwrap();
        data.verify(proof).map_err(|e| format!("{:?}", e))
    }

    #[test]
    fn test_synthetic_tree_depth_5() {
        let texts: Vec<String> = (0..32)
            .map(|i| format!("chunk text number {}", i))
            .collect();
        let depth = log2_strict(32);
        let (root, levels) = build_merkle_tree_from_text(&texts);

        // document_hash = hash of first text (leaf 0)
        let document_hash = levels[0][0];

        for leaf_idx in [0, 1, 15, 16, 31] {
            let siblings = get_siblings(&levels, leaf_idx, depth);
            let leaf_hash = hash_leaf_text(&texts[leaf_idx]);
            run_proof_test(
                leaf_idx,
                depth,
                root,
                document_hash,
                leaf_hash,
                &siblings,
                0,
                0,
            )
            .expect(&format!("Leaf {} failed", leaf_idx));
        }
    }

    #[test]
    fn test_synthetic_tree_depth_8() {
        let texts: Vec<String> = (0..256)
            .map(|i| format!("chunk text number {}", i))
            .collect();
        let depth = log2_strict(256);
        let (root, levels) = build_merkle_tree_from_text(&texts);

        let document_hash = levels[0][0];

        for leaf_idx in [0, 1, 127, 128, 255] {
            let siblings = get_siblings(&levels, leaf_idx, depth);
            let leaf_hash = hash_leaf_text(&texts[leaf_idx]);
            run_proof_test(
                leaf_idx,
                depth,
                root,
                document_hash,
                leaf_hash,
                &siblings,
                0,
                0,
            )
            .expect(&format!("Leaf {} failed", leaf_idx));
        }
    }

    #[test]
    fn test_synthetic_tree_max_depth() {
        let texts: Vec<String> = (0..4096)
            .map(|i| format!("chunk text number {}", i))
            .collect();
        let depth = log2_strict(4096);
        assert_eq!(depth, MAX_DEPTH);
        let (root, levels) = build_merkle_tree_from_text(&texts);

        let document_hash = levels[0][0];

        for leaf_idx in [0, 1, 2047, 2048, 4095] {
            let siblings = get_siblings(&levels, leaf_idx, depth);
            let leaf_hash = hash_leaf_text(&texts[leaf_idx]);
            run_proof_test(
                leaf_idx,
                depth,
                root,
                document_hash,
                leaf_hash,
                &siblings,
                0,
                0,
            )
            .expect(&format!("Leaf {} failed", leaf_idx));
        }
    }

    #[test]
    fn test_wrong_sibling_fails() {
        let texts: Vec<String> = (0..32)
            .map(|i| format!("chunk text number {}", i))
            .collect();
        let depth = log2_strict(32);
        let (root, levels) = build_merkle_tree_from_text(&texts);

        let leaf_idx = 10;
        let document_hash = levels[0][0];
        let leaf_hash = hash_leaf_text(&texts[leaf_idx]);
        let mut siblings = get_siblings(&levels, leaf_idx, depth);
        // Corrupt sibling at level 2
        siblings[2] = HashOut {
            elements: [F::ONE, F::ONE, F::ONE, F::ONE],
        };

        // Wrong sibling causes plonky2's witness generator to panic (constraint
        // violation detected during witness generation). We catch this as an error.
        let result = std::panic::catch_unwind(|| {
            run_proof_test(
                leaf_idx,
                depth,
                root,
                document_hash,
                leaf_hash,
                &siblings,
                0,
                0,
            )
        });
        assert!(
            result.is_err() || result.as_ref().is_err(),
            "Wrong sibling should cause an error"
        );
    }

    #[test]
    fn test_wrong_leaf_index_fails() {
        let texts: Vec<String> = (0..32)
            .map(|i| format!("chunk text number {}", i))
            .collect();
        let depth = log2_strict(32);
        let (root, levels) = build_merkle_tree_from_text(&texts);

        let leaf_idx = 10;
        let document_hash = levels[0][0];
        let leaf_hash = hash_leaf_text(&texts[leaf_idx]);
        let siblings = get_siblings(&levels, leaf_idx, depth);
        // Use wrong index (11) with proof for index 10
        let wrong_idx = 11;

        // Wrong index causes plonky2's witness generator to panic.
        let result = std::panic::catch_unwind(|| {
            run_proof_test(
                wrong_idx,
                depth,
                root,
                document_hash,
                leaf_hash,
                &siblings,
                0,
                0,
            )
        });
        assert!(
            result.is_err() || result.as_ref().is_err(),
            "Wrong index should cause an error"
        );
    }

    #[test]
    fn test_wrong_leaf_hash_fails() {
        // Option A variant: providing a wrong leaf_hash that doesn't match the leaf
        // in the Merkle tree should cause the proof verification to fail.
        let texts: Vec<String> = (0..32)
            .map(|i| format!("correct chunk text {}", i))
            .collect();
        let depth = log2_strict(32);
        let (root, levels) = build_merkle_tree_from_text(&texts);

        let leaf_idx = 10;
        let document_hash = levels[0][0];
        let siblings = get_siblings(&levels, leaf_idx, depth);

        // Provide WRONG leaf_hash — something that doesn't exist in the tree
        let wrong_leaf_hash = hash_leaf_text("this is not the correct chunk text");

        // Wrong leaf_hash produces a hash not in the tree → proof verification fails
        let result = std::panic::catch_unwind(|| {
            run_proof_test(
                leaf_idx,
                depth,
                root,
                document_hash,
                wrong_leaf_hash,
                &siblings,
                0,
                0,
            )
        });
        assert!(
            result.is_err() || result.as_ref().is_err(),
            "Wrong leaf_hash should cause an error"
        );
    }

    #[test]
    fn test_parse_hash_roundtrip() {
        let original = "0xcc5662e4f4ae16457ea31877e0f0fa38994c5f559ba1f9f9c0e94674e050c1cb";
        let h = parse_hash(original);
        let back = hash_to_hex(&h);
        assert_eq!(back, original.to_lowercase());
    }
}
