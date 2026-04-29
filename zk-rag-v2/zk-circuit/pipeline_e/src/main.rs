//! Pipeline E: Build Poseidon Merkle Trees from chunked documents
//!
//! Reads chunks.jsonl from Pipeline D, builds a Goldilocks-field Poseidon Merkle tree
//! with a single root, and writes a JSON manifest. The tree format matches the
//! zk-circuit (Goldilocks + 8-byte packing via PoseidonHash::hash_or_noop).
//!
//! Usage:
//!   cargo run -p pipeline_e -- --doc-id <id> [--chunks-dir /data/...] [--out-dir /data/...]
//!   cargo run -p pipeline_e -- --batch [--chunks-dir /data/...] [--out-dir /data/...]

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use chrono::Utc;
use clap::Parser;
use plonky2::field::goldilocks_field::GoldilocksField;
use plonky2::field::types::{Field, PrimeField64};
use plonky2::hash::hash_types::HashOut;
use plonky2::hash::poseidon::PoseidonHash;
use plonky2::plonk::config::Hasher;
use serde::{Deserialize, Serialize};
use unicode_normalization::UnicodeNormalization;
use zk_circuit::merkle_tree::MerkleTree;

type F = GoldilocksField;

// ─── CLI ─────────────────────────────────────────────────────────────────────

#[derive(Parser, Debug)]
#[command(name = "pipeline_e")]
#[command(about = "Build Poseidon Merkle trees from chunked documents (Pipeline E)")]
struct Cli {
    /// Process a single document by ID.
    #[arg(long)]
    doc_id: Option<String>,

    /// Process all documents in chunks-dir.
    #[arg(long, default_value_t = false)]
    batch: bool,

    /// Directory containing chunk subdirectories (one per doc_id).
    #[arg(long, default_value = "/data/military-documents/chunks")]
    chunks_dir: PathBuf,

    /// Output directory for Merkle tree JSON files.
    #[arg(long, default_value = "/data/military-documents/merkle_trees")]
    out_dir: PathBuf,

    /// Force re-build even if output already exists.
    #[arg(long, default_value_t = false)]
    force: bool,

    /// Dry run — show what would be computed without writing files.
    #[arg(long, default_value_t = false)]
    dry_run: bool,
}

// ─── Input ────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct ChunkRecord {
    chunk_id: String,
    doc_id: String,
    text: String,
    chunk_index: usize,
}

// ─── Output ──────────────────────────────────────────────────────────────────

#[derive(Debug, Serialize)]
struct MerkleTreeOutput {
    doc_id: String,
    /// Single Poseidon Merkle root (hex string).
    merkle_root: String,
    poseidon_params: PoseidonParams,
    tree_config: TreeConfig,
    chunk_count: usize,
    doc_id_leaf_index: usize,
    padded_leaf_count: usize,
    leaf_hashes: Vec<String>,
    paths: HashMap<String, LeafPath>,
    computed_at: String,
}

#[derive(Debug, Serialize)]
struct PoseidonParams {
    variant: String,
    field: String,
}

#[derive(Debug, Serialize)]
struct TreeConfig {
    arity: usize,
    depth: usize,
    max_leaves: usize,
    padding: String,
}

#[derive(Debug, Serialize)]
struct LeafPath {
    leaf_index: usize,
    chunk_id: String,
    leaf_hash: String,
    siblings: Vec<SiblingEntry>,
}

#[derive(Debug, Serialize)]
struct SiblingEntry {
    hash: String,
    at_depth: usize,
}

// ─── Hashing ─────────────────────────────────────────────────────────────────

/// Convert bytes to Goldilocks field elements using 8-byte little-endian packing.
/// This matches plonky2's PoseidonHash::hash_or_noop for ≤4 field elements:
/// the bytes are laid out as 8-byte LE words directly into the HashOut.
fn bytes_to_field_elements(bytes: &[u8]) -> Vec<F> {
    bytes
        .chunks(8)
        .map(|chunk| {
            let mut word = [0u8; 8];
            word[..chunk.len()].copy_from_slice(chunk);
            F::from_canonical_u64(u64::from_le_bytes(word))
        })
        .collect()
}

/// Hash text bytes to a Poseidon leaf hash (HashOut<GoldilocksField>).
/// Uses 8-byte packing to match the circuit's PoseidonHash::hash_or_noop behavior.
fn hash_leaf(text: &str) -> HashOut<F> {
    let normalized = text.nfkc().collect::<String>().trim().to_string();
    let elems = bytes_to_field_elements(normalized.as_bytes());
    PoseidonHash::hash_or_noop(&elems)
}

/// Hash a doc_id (raw bytes) to a Poseidon leaf hash.
/// doc_id is the SHA-256 hex string (64 hex chars = 32 bytes).
fn hash_doc_id(doc_id: &[u8]) -> HashOut<F> {
    let elems = bytes_to_field_elements(doc_id);
    PoseidonHash::hash_or_noop(&elems)
}

/// Serialize a HashOut<F> to 0x-prefixed lowercase hex string (32 bytes).
fn hash_to_hex(h: &HashOut<F>) -> String {
    let mut bytes = Vec::with_capacity(32);
    for e in &h.elements {
        bytes.extend_from_slice(&e.to_canonical_u64().to_le_bytes());
    }
    format!("0x{}", hex::encode(bytes))
}

// ─── Build ───────────────────────────────────────────────────────────────────

/// Build a Merkle tree for one document.
fn build_tree_for_doc(
    doc_id: &str,
    chunks_dir: &Path,
    out_dir: &Path,
    force: bool,
    dry_run: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let chunks_path = chunks_dir.join(doc_id).join("chunks.jsonl");
    if !chunks_path.exists() {
        return Err(format!("chunks.jsonl not found: {}", chunks_path.display()).into());
    }

    let out_path = out_dir.join(format!("{}_tree.json", doc_id));
    if out_path.exists() && !force {
        eprintln!(
            "[{}] SKIPPED (already exists, use --force to override)",
            doc_id
        );
        return Ok(());
    }

    // Read and parse chunks
    let content = fs::read_to_string(&chunks_path)?;
    let mut chunks: Vec<ChunkRecord> = content
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line))
        .collect::<Result<Vec<_>, _>>()?;

    if chunks.is_empty() {
        return Err("zero chunks".into());
    }

    chunks.sort_by_key(|c| c.chunk_index);
    let chunk_count = chunks.len();
    eprintln!("[{}] {} chunks loaded", doc_id, chunk_count);

    // ── Prepend doc_id as leaf[0] ─────────────────────────────────────────────
    let doc_id_bytes = doc_id.as_bytes();
    let doc_id_hash = hash_doc_id(doc_id_bytes);

    // ── Hash text chunks ───────────────────────────────────────────────────────
    let mut leaf_hashes: Vec<HashOut<F>> = vec![doc_id_hash];
    let mut leaf_infos: Vec<(String, HashOut<F>)> =
        vec![(format!("{}_docid", doc_id), doc_id_hash)];

    for chunk in &chunks {
        let h = hash_leaf(&chunk.text);
        leaf_infos.push((chunk.chunk_id.clone(), h));
        leaf_hashes.push(h);
    }

    // ── Build Merkle tree (single root) ────────────────────────────────────────
    let tree = MerkleTree::build_from_hashed_leaves(leaf_hashes);
    let padded_leaf_count = tree.levels[0].len();
    let depth = tree.count_levels;
    let root = tree.root;

    eprintln!(
        "[{}] Tree: {} real leaves ({} text + 1 doc_id), {} padded, depth {}",
        doc_id,
        padded_leaf_count,
        chunk_count,
        padded_leaf_count - (chunk_count + 1),
        depth
    );

    if dry_run {
        eprintln!("[{}] DRY RUN — root: {}", doc_id, hash_to_hex(&root));
        return Ok(());
    }

    // ── Extract leaf hashes ────────────────────────────────────────────────────
    let leaf_hashes_out: Vec<String> = tree.levels[0].iter().map(|h| hash_to_hex(h)).collect();

    // ── Build per-chunk paths ─────────────────────────────────────────────────
    // Text chunks are at tree index 1..chunk_count+1 (leaf[0] = doc_id)
    let mut paths = HashMap::new();
    for (i, (chunk_id, _)) in leaf_infos.iter().enumerate().skip(1) {
        let tree_index = i;
        let proof = tree.get_merkle_proof(tree_index);
        let leaf_hash = hash_to_hex(&tree.levels[0][tree_index]);
        let siblings: Vec<SiblingEntry> = proof
            .iter()
            .enumerate()
            .map(|(d, sib)| SiblingEntry {
                hash: hash_to_hex(sib),
                at_depth: d,
            })
            .collect();

        paths.insert(
            i.to_string(),
            LeafPath {
                leaf_index: tree_index,
                chunk_id: chunk_id.clone(),
                leaf_hash,
                siblings,
            },
        );
    }

    // ── Write output ──────────────────────────────────────────────────────────
    let output = MerkleTreeOutput {
        doc_id: doc_id.to_string(),
        merkle_root: hash_to_hex(&root),
        poseidon_params: PoseidonParams {
            variant: "poseidon".to_string(),
            field: "goldilocks (p = 2^64 - 2^32 + 1)".to_string(),
        },
        tree_config: TreeConfig {
            arity: 2,
            depth,
            max_leaves: padded_leaf_count,
            padding: "HashOut::ZERO".to_string(),
        },
        chunk_count,
        doc_id_leaf_index: 0,
        padded_leaf_count,
        leaf_hashes: leaf_hashes_out,
        paths,
        computed_at: Utc::now().to_rfc3339(),
    };

    fs::create_dir_all(out_dir)?;
    let json = serde_json::to_string_pretty(&output)?;
    fs::write(&out_path, &json)?;
    eprintln!(
        "[{}] Wrote {} ({} bytes)",
        doc_id,
        out_path.display(),
        json.len()
    );

    Ok(())
}

// ─── Main ─────────────────────────────────────────────────────────────────────

fn main() {
    let cli = Cli::parse();

    if cli.doc_id.is_none() && !cli.batch {
        eprintln!("Error: specify --doc-id <id> or --batch");
        std::process::exit(1);
    }

    if let Some(ref doc_id) = cli.doc_id {
        if let Err(e) = build_tree_for_doc(
            doc_id,
            &cli.chunks_dir,
            &cli.out_dir,
            cli.force,
            cli.dry_run,
        ) {
            eprintln!("ERROR [{}]: {}", doc_id, e);
            std::process::exit(1);
        }
    } else {
        let entries = match fs::read_dir(&cli.chunks_dir) {
            Ok(e) => e,
            Err(e) => {
                eprintln!(
                    "ERROR reading chunks dir {}: {}",
                    cli.chunks_dir.display(),
                    e
                );
                std::process::exit(1);
            }
        };

        let mut success = 0;
        let mut failed = 0;

        for entry in entries.flatten() {
            if !entry.path().is_dir() {
                continue;
            }
            let doc_id = entry.file_name().to_string_lossy().to_string();
            if !entry.path().join("chunks.jsonl").exists() {
                continue;
            }
            match build_tree_for_doc(
                &doc_id,
                &cli.chunks_dir,
                &cli.out_dir,
                cli.force,
                cli.dry_run,
            ) {
                Ok(()) => success += 1,
                Err(e) => {
                    eprintln!("ERROR [{}]: {}", doc_id, e);
                    failed += 1;
                }
            }
        }

        eprintln!("\nBatch complete: {} succeeded, {} failed", success, failed);
        if failed > 0 {
            std::process::exit(1);
        }
    }
}
