//! verify-zk-proof — Standalone ZK proof verifier
//!
//! Loads a proof JSON from prove-bin and verifies the proof using plonky2-verifier's
//! public verify API (the same verification that zkVerify's settlementPlonky2Pallet runs).
//!
//! Usage:
//!   cargo run -p verify-zk-proof -- <path-to-proof.json>
//!   cargo run -p verify-zk-proof -- ./data/zk_proofs/<doc_id>_<chunk>.json
//!
//! Logs: structured JSON to ./data/zk_proofs/verify-zk-proof.log

use base64::Engine;
use plonky2_verifier::{verify, Proof, Vk};
use serde::Deserialize;
use std::io::Write as IoWrite;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

const B64_ENGINE: base64::engine::general_purpose::GeneralPurpose =
    base64::engine::general_purpose::STANDARD;

// ─── Logging ─────────────────────────────────────────────────────────────────

const LOG_FILE: &str = "./data/zk_proofs/verify-zk-proof.log";
const LOG_DIR: &str = "./data/zk_proofs";

fn log(level: &str, msg: &str, fields: &[(&str, serde_json::Value)]) {
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);

    let mut obj = serde_json::Map::new();
    obj.insert("timestamp".to_string(), serde_json::json!(ts));
    obj.insert("level".to_string(), serde_json::json!(level));
    obj.insert("script".to_string(), serde_json::json!("verify-zk-proof"));
    obj.insert("message".to_string(), serde_json::json!(msg));
    for (k, v) in fields {
        obj.insert(k.to_string(), v.clone());
    }

    if let Ok(line) = serde_json::to_string(&obj) {
        let _ = std::fs::create_dir_all(LOG_DIR);
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(LOG_FILE)
        {
            let _ = writeln!(f, "{}", line);
            let _ = f.flush();
        }
    }

    eprintln!("[{}] {}", level, msg);
    for (k, v) in fields {
        eprintln!("    {}: {}", k, v);
    }
}

fn log_info(msg: &str, fields: &[(&str, serde_json::Value)]) {
    log("INFO", msg, fields)
}
fn log_error(msg: &str, fields: &[(&str, serde_json::Value)]) {
    log("ERROR", msg, fields)
}

// ─── Proof format produced by prove-bin ─────────────────────────────────────

#[derive(Deserialize)]
struct PublicInputs {
    #[allow(dead_code)]
    leaf_hash: String,
    #[allow(dead_code)]
    document_hash: String,
    merkle_root: String,
}

#[derive(Deserialize)]
struct ZkProofPackage {
    proof_b64: String,
    /// 0x-prefixed hex proof bytes — for Kurier submission
    #[allow(dead_code)]
    #[serde(default)]
    proof_hex: Option<String>,
    #[allow(dead_code)]
    common_circuit_data_b64: String,
    #[allow(dead_code)]
    verifier_only_b64: String,
    /// 0x-prefixed hex VK — for Kurier VK registration
    vk_hex: String,
    public_inputs: PublicInputs,
    /// Base64-encoded public inputs bytes — not used by verifier (kept for compatibility)
    #[allow(dead_code)]
    #[serde(default)]
    public_inputs_b64: Option<String>,
    /// 0x-prefixed hex public inputs bytes (write_usize || write_field_vec)
    public_inputs_hex: String,
    #[allow(dead_code)]
    kurier_job_id: Option<String>,
    #[allow(dead_code)]
    kurier_final_status: Option<String>,
}

// ─── Main ─────────────────────────────────────────────────────────────────────

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 2 {
        log_error(
            "Usage: cargo run -p verify-zk-proof -- <path-to-proof.json>",
            &[],
        );
        std::process::exit(1);
    }

    let path = PathBuf::from(&args[1]);
    log_info(
        "=== verify-zk-proof started ===",
        &[("path", serde_json::json!(path.display().to_string()))],
    );

    if !path.exists() {
        log_error(
            "File not found",
            &[("path", serde_json::json!(path.display().to_string()))],
        );
        std::process::exit(1);
    }

    // ── Load and parse package ────────────────────────────────────────────────
    let json_content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(e) => {
            log_error(
                "Failed to read file",
                &[
                    ("path", serde_json::json!(path.display().to_string())),
                    ("error", serde_json::json!(e.to_string())),
                ],
            );
            std::process::exit(1);
        }
    };

    let package: ZkProofPackage = match serde_json::from_str(&json_content) {
        Ok(p) => p,
        Err(e) => {
            log_error(
                "Failed to parse JSON",
                &[("error", serde_json::json!(e.to_string()))],
            );
            std::process::exit(1);
        }
    };

    log_info(
        "Loaded proof package",
        &[
            (
                "merkle_root",
                serde_json::json!(
                    &package.public_inputs.merkle_root
                        [..20.min(package.public_inputs.merkle_root.len())]
                ),
            ),
            ("proof_b64_len", serde_json::json!(package.proof_b64.len())),
        ],
    );

    // ── Decode proof bytes ───────────────────────────────────────────────────
    // prove-bin outputs: proof_bytes = write_proof(&proof.proof)
    let proof_bytes = match B64_ENGINE.decode(&package.proof_b64) {
        Ok(b) => b,
        Err(e) => {
            log_error(
                "Failed to base64-decode proof_b64",
                &[("error", serde_json::json!(e.to_string()))],
            );
            std::process::exit(1);
        }
    };

    log_info(
        "Decoded proof",
        &[("bytes", serde_json::json!(proof_bytes.len()))],
    );

    // ── Decode public inputs bytes ───────────────────────────────────────────
    // prove-bin outputs: pubs_bytes = write_usize(len) || write_field_vec(pubs)
    // public_inputs_hex is "0x-prefixed" hex
    let pubs_hex_str = package
        .public_inputs_hex
        .strip_prefix("0x")
        .unwrap_or(&package.public_inputs_hex);
    let pubs_bytes = match hex::decode(pubs_hex_str) {
        Ok(b) => b,
        Err(e) => {
            log_error(
                "Failed to hex-decode public_inputs_hex",
                &[("error", serde_json::json!(e.to_string()))],
            );
            std::process::exit(1);
        }
    };

    log_info(
        "Decoded public inputs",
        &[("bytes", serde_json::json!(pubs_bytes.len()))],
    );

    // ── Build Vk from vk_hex ─────────────────────────────────────────────────
    // prove-bin outputs: vk_hex = "0x" + hex(vk_bytes)
    // where vk_bytes = verifier_data.to_bytes(&ZKVerifyGateSerializer)
    let vk_hex_str = package.vk_hex.strip_prefix("0x").unwrap_or(&package.vk_hex);
    let vk_bytes: Vec<u8> = match hex::decode(vk_hex_str) {
        Ok(b) => b,
        Err(e) => {
            log_error(
                "Failed to hex-decode vk_hex",
                &[("error", serde_json::json!(e.to_string()))],
            );
            std::process::exit(1);
        }
    };

    log_info(
        "Decoded vk",
        &[("bytes", serde_json::json!(vk_bytes.len()))],
    );

    // Build the Vk JSON: { "config": "Poseidon", "bytes": "<hex>" }
    // Our circuit uses PoseidonGoldilocksConfig
    let vk_json = serde_json::json!({
        "config": "Poseidon",
        "bytes": hex::encode(&vk_bytes)
    });
    let vk_json_str = serde_json::to_string(&vk_json).unwrap();
    let vk: Vk = match serde_json::from_str(&vk_json_str) {
        Ok(v) => v,
        Err(e) => {
            log_error(
                "Failed to parse Vk JSON",
                &[
                    ("error", serde_json::json!(e.to_string())),
                    (
                        "vk_json",
                        serde_json::json!(&vk_json_str[..vk_json_str.len().min(100)]),
                    ),
                ],
            );
            std::process::exit(1);
        }
    };

    log_info("Built Vk", &[("config", serde_json::json!("Poseidon"))]);

    // ── Build Proof ───────────────────────────────────────────────────────────
    // prove-bin outputs uncompressed proofs
    let proof = Proof {
        compressed: false,
        bytes: proof_bytes,
    };

    // ── Verify ────────────────────────────────────────────────────────────────
    log_info("Running plonky2-verifier verification ...", &[]);

    match verify(&vk, &proof, &pubs_bytes) {
        Ok(()) => {
            log_info(
                "=== verify-zk-proof completed: VALID ===",
                &[
                    ("result", serde_json::json!("VALID")),
                    ("source", serde_json::json!(path.display().to_string())),
                ],
            );
        }
        Err(e) => {
            log_error(
                "Verification FAILED",
                &[
                    ("result", serde_json::json!("INVALID")),
                    ("source", serde_json::json!(path.display().to_string())),
                    ("error", serde_json::json!(e.to_string())),
                ],
            );
            std::process::exit(1);
        }
    }
}
