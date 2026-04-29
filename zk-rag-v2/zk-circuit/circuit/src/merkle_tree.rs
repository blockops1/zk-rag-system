//! Merkle Tree data structures and utilities.
//!
//! **Source:** Adapted from Sindri-Labs/sindri-resources merkle_tree tutorial.
//! We keep this for local test use (building trees and generating proofs in tests).
//! The circuit itself does NOT use this — it only operates on hashes.

use plonky2::field::goldilocks_field::GoldilocksField;
use plonky2::field::types::{Field, PrimeField64};
use plonky2::hash::hash_types::HashOut;
use plonky2::hash::poseidon::PoseidonHash;
use plonky2::plonk::config::Hasher;
use plonky2::util::log2_strict;
use unicode_normalization::UnicodeNormalization;

// ─── Hashing utilities ────────────────────────────────────────────────────────
// These match Pipeline E exactly. Both pipeline_e and test-from-chunks use these
// so that trees built in tests are identical to trees built by Pipeline E.

/// Maximum chunk size in bytes — fixed size for plonky2 circuit input.
/// 8192 bytes ≈ ~2048 tokens, covers all current document chunks with margin.
pub const MAX_CHUNK_BYTES: usize = 8192;

/// Convert bytes to Goldilocks field elements using 8-byte little-endian packing.
/// Matches plonky2's PoseidonHash::hash_or_noop for variable-length inputs.
pub fn bytes_to_field_elements(bytes: &[u8]) -> Vec<GoldilocksField> {
    bytes
        .chunks(8)
        .map(|chunk| {
            let mut word = [0u8; 8];
            word[..chunk.len()].copy_from_slice(chunk);
            GoldilocksField::from_canonical_u64(u64::from_le_bytes(word))
        })
        .collect()
}

/// Hash text bytes to a Poseidon leaf hash (HashOut<GoldilocksField>).
/// Uses NFKC normalization + 8-byte packing — identical to Pipeline E.
/// This is the witness-side hashing function; the circuit hashes identically.
pub fn hash_leaf_text(text: &str) -> HashOut<GoldilocksField> {
    let normalized = text.nfkc().collect::<String>();
    let elems = bytes_to_field_elements(normalized.as_bytes());
    PoseidonHash::hash_or_noop(&elems)
}

/// Hash raw chunk bytes to a Poseidon leaf hash.
/// Alias of `hash_leaf_text` for the Phase P circuit where raw text is the witness.
/// NFKC normalization is applied before hashing, matching Pipeline E.
pub fn hash_chunk_text(text: &str) -> HashOut<GoldilocksField> {
    hash_leaf_text(text)
}

/// Number of field elements for a chunk of `num_bytes` bytes.
/// Each field element holds 8 bytes.
pub fn num_field_elements(num_bytes: usize) -> usize {
    (num_bytes + 7) / 8
}

/// Compute the number of field elements for MAX_CHUNK_BYTES.
pub const NUM_CHUNK_FIELD_ELEMENTS: usize = (MAX_CHUNK_BYTES + 7) / 8;

/// Hash a doc_id (raw bytes) to a Poseidon leaf hash.
/// doc_id is the SHA-256 hex string (64 hex chars = 32 bytes) as raw bytes.
pub fn hash_doc_id(doc_id: &[u8]) -> HashOut<GoldilocksField> {
    let elems = bytes_to_field_elements(doc_id);
    PoseidonHash::hash_or_noop(&elems)
}

/// Serialize a HashOut<GoldilocksField> to 0x-prefixed lowercase hex (32 bytes).
pub fn hash_to_hex(h: &HashOut<GoldilocksField>) -> String {
    let mut bytes = Vec::with_capacity(32);
    for e in &h.elements {
        bytes.extend_from_slice(&e.to_canonical_u64().to_le_bytes());
    }
    format!("0x{}", hex::encode(bytes))
}

/// A binary Merkle tree built from field-element leaves.
#[derive(Debug, Clone)]
pub struct MerkleTree {
    /// Number of levels from leaves to root (not counting leaf-hashing level 0).
    pub count_levels: usize,
    /// `levels[0]` = hashed leaves, `levels[1..=count_levels]` = intermediate nodes.
    pub levels: Vec<Vec<HashOut<GoldilocksField>>>,
    /// The root hash.
    pub root: HashOut<GoldilocksField>,
}

impl MerkleTree {
    /// Build a complete binary Merkle tree from raw field-element leaves.
    /// Panics if `leaves.len()` is not a power of 2.
    pub fn build(leaves: Vec<GoldilocksField>) -> Self {
        let count_levels = log2_strict(leaves.len());

        // Level 0: hash each leaf individually.
        let level0: Vec<HashOut<GoldilocksField>> = leaves
            .into_iter()
            .map(|leaf| PoseidonHash::hash_or_noop(&[leaf]))
            .collect();

        let mut levels = vec![level0];

        // Build upward `count_levels` times.
        for _ in 0..count_levels {
            let prev = &levels[levels.len() - 1];
            let next: Vec<HashOut<GoldilocksField>> = prev
                .chunks(2)
                .map(|pair| PoseidonHash::two_to_one(pair[0], pair[1]))
                .collect();
            levels.push(next);
        }

        let root = levels.last().unwrap()[0];
        MerkleTree {
            count_levels,
            levels,
            root,
        }
    }

    /// Return the Merkle proof for a leaf at `leaf_index`.
    /// Returns `count_levels` hashes — one sibling per level.
    pub fn get_merkle_proof(&self, leaf_index: usize) -> Vec<HashOut<GoldilocksField>> {
        assert!(
            leaf_index < self.levels[0].len(),
            "leaf_index {} out of bounds for {} leaves",
            leaf_index,
            self.levels[0].len()
        );

        let mut proof = Vec::with_capacity(self.count_levels);
        let mut idx = leaf_index;

        for level in 0..self.count_levels {
            let sibling_idx = if idx & 1 == 1 { idx - 1 } else { idx + 1 };
            let level_len = self.levels[level].len();
            let sibling = if sibling_idx < level_len {
                self.levels[level][sibling_idx]
            } else {
                HashOut::ZERO
            };
            proof.push(sibling);
            idx /= 2;
        }

        proof
    }

    /// Build a binary Merkle tree from pre-hashed leaves (HashOut).
    /// Used by Pipeline E — leaves are already Poseidon-hashed text chunks.
    /// Pads to next power of 2 with HashOut::ZERO.
    pub fn build_from_hashed_leaves(leaf_hashes: Vec<HashOut<GoldilocksField>>) -> Self {
        let n = leaf_hashes.len();
        assert!(n > 0, "Cannot build tree from empty leaves");

        let next_pow2 = n.next_power_of_two();
        let mut padded = leaf_hashes;
        padded.resize(next_pow2, HashOut::ZERO);

        let depth = next_pow2.trailing_zeros() as usize;
        let mut current = padded;
        let mut levels = vec![current.clone()];

        for _ in 0..depth {
            current = current
                .chunks(2)
                .map(|pair| PoseidonHash::two_to_one(pair[0], pair[1]))
                .collect();
            levels.push(current.clone());
        }

        let root = *current.first().unwrap();
        let count_levels = levels.len() - 1;

        MerkleTree {
            count_levels,
            levels,
            root,
        }
    }
}

/// Verify a Merkle proof against an expected root (plaintext, no circuit).
pub fn verify_merkle_proof(
    leaf: GoldilocksField,
    leaf_index: usize,
    root: HashOut<GoldilocksField>,
    siblings: &[HashOut<GoldilocksField>],
) -> bool {
    let mut current: HashOut<GoldilocksField> = PoseidonHash::hash_or_noop(&[leaf]);
    let mut idx = leaf_index;

    for sibling in siblings.iter() {
        current = if idx & 1 == 0 {
            PoseidonHash::two_to_one(current, *sibling)
        } else {
            PoseidonHash::two_to_one(*sibling, current)
        };
        idx /= 2;
    }

    current == root
}

#[cfg(test)]
mod tests {
    use super::*;
    use plonky2::field::types::Field;

    #[test]
    fn test_build_and_prove_4_leaves() {
        let leaves: Vec<GoldilocksField> = vec![
            GoldilocksField::from_canonical_u64(2890852870),
            GoldilocksField::from_canonical_u64(156728478),
            GoldilocksField::from_canonical_u64(2876514289),
            GoldilocksField::from_canonical_u64(984286162),
        ];
        let tree = MerkleTree::build(leaves.clone());

        for i in 0..4 {
            let proof = tree.get_merkle_proof(i);
            assert!(verify_merkle_proof(leaves[i], i, tree.root, &proof));
        }
    }

    #[test]
    fn test_wrong_proof_fails() {
        let leaves: Vec<GoldilocksField> = vec![
            GoldilocksField::from_canonical_u64(2890852870),
            GoldilocksField::from_canonical_u64(156728478),
            GoldilocksField::from_canonical_u64(2876514289),
            GoldilocksField::from_canonical_u64(984286162),
        ];
        let tree = MerkleTree::build(leaves.clone());

        let wrong_leaf = GoldilocksField::from_canonical_u64(999999999);
        let proof = tree.get_merkle_proof(0);
        assert!(!verify_merkle_proof(wrong_leaf, 0, tree.root, &proof));
    }
}
