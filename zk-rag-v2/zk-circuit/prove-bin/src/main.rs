//! `prove` binary — generate a ZK proof for a Merkle proof.
//!
//! # Input format (JSON):
//! ```json
//! {
//!   "chunk_text": "The text of the chunk being proven...",
//!   "document_hash": "0x...",
//!   "merkle_root": "0x...",
//!   "leaf_index": 12,
//!   "depth": 8,
//!   "siblings": ["0x...", "0x...", ...]
//! }
//! ```
//!
//! # Output (JSON):
//! ```json
//! {
//!   "proof_b64": "<base64-encoded proof>",
//!   "public_inputs": { "document_hash": "0x...", "merkle_root": "0x..." }
//! }
//! ```
//!
//! # Kurier/zkVerify integration
//! Proof submission to Kurier is handled by the standalone `kurier_submit.py` script.
//! Generate the proof with this binary first, then submit separately with `kurier_submit.py`.
//!
//! # Logging
//! Structured logs go to stderr (human-readable).
//! Use `RUST_LOG` env var to control verbosity (e.g., RUST_LOG=debug).
//! Stdout is reserved for the machine-readable proof JSON only.

use base64::Engine;
use plonky2::iop::witness::PartialWitness;
use plonky2::plonk::circuit_builder::CircuitBuilder;
use plonky2::plonk::circuit_data::{CircuitConfig, CircuitData};
use plonky2::plonk::config::GenericConfig;
use plonky2::plonk::proof::ProofWithPublicInputs;
use plonky2::util::serialization::{
    DefaultGateSerializer, DefaultGeneratorSerializer, Write as P2Write,
};
use plonky2_verifier::ZKVerifyGateSerializer;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{BufWriter, Write};
use std::path::PathBuf;
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use tracing::{error, info, warn};
use tracing_subscriber::{
    fmt::{self, format::FmtSpan},
    layer::SubscriberExt,
    util::SubscriberInitExt,
    EnvFilter,
};

use zk_circuit::{
    build_merkle_proof_circuit_targets, fill_merkle_proof_witness, hash_to_hex, parse_hash, C, D,
};

type PLiF = <C as GenericConfig<D>>::F;

// ─────────────────────────────────────────────────────────────────────────────
// CLI + Logging Setup
// ─────────────────────────────────────────────────────────────────────────────

struct Opts {
    input_path: String,
    log_file: Option<PathBuf>,
    build_circuit_depth: Option<usize>, // If set, build circuit and exit
    circuit_output_path: Option<PathBuf>, // Where to save the built circuit
}

fn parse_args() -> Opts {
    let mut args = std::env::args_os().skip(1);
    let mut opts = Opts {
        input_path: String::new(),
        log_file: None,
        build_circuit_depth: None,
        circuit_output_path: None,
    };

    while let Some(arg) = args.next() {
        let arg = arg.to_string_lossy();
        match &*arg {
            "--log-file" => {
                opts.log_file = Some(args.next().expect("--log-file requires a path").into());
            }
            "--build-circuit" => {
                let depth: usize = args
                    .next()
                    .expect("--build-circuit requires a depth")
                    .to_string_lossy()
                    .parse()
                    .expect("--build-circuit depth must be a number");
                opts.build_circuit_depth = Some(depth);
            }
            "--circuit-output" => {
                opts.circuit_output_path = Some(
                    args.next()
                        .expect("--circuit-output requires a path")
                        .into(),
                );
            }
            _ if !arg.starts_with('-') => {
                opts.input_path = arg.to_string();
            }
            _ => {
                eprintln!("Unknown flag: {}", arg);
                print_usage();
                std::process::exit(1);
            }
        }
    }

    // ── Build-circuit mode: bypass input validation ──────────────────────────────
    // Set a dummy input_path so parse_args() doesn't exit early.
    // The actual build-circuit logic lives in main().
    if opts.build_circuit_depth.is_some() {
        opts.input_path = "<build-circuit>".to_string();
        return opts;
    }

    if opts.input_path.is_empty() {
        print_usage();
        std::process::exit(1);
    }

    opts
}

fn print_usage() {
    eprintln!("Usage: prove <input.json> [flags]");
    eprintln!("       prove --build-circuit <depth> [--circuit-output <path>]");
    eprintln!();
    eprintln!("Modes:");
    eprintln!("  <input.json>           Generate proof (requires input file)");
    eprintln!("  --build-circuit <N>   Build and serialize circuit of depth N (no input needed)");
    eprintln!();
    eprintln!("Proof-generation flags:");
    eprintln!("  --log-file <path>       Append structured audit log to file");
    eprintln!();
    eprintln!("Build-circuit flags:");
    eprintln!("  --build-circuit <depth>  Depth for --build-circuit mode (also accepts --build-circuit <depth>)");
    eprintln!("  --circuit-output <path>  Output path for serialized circuit (default: ./circuit_depth<N>.bin)");
    eprintln!();
    eprintln!("Environment:");
    eprintln!("  CIRCUIT_DIR            Directory to look for pre-built circuit .bin files (default: next to binary)");
    eprintln!();
    eprintln!("Kurier submission: use kurier_submit.py after proof generation.");
}

/// Write a structured JSONL entry to the audit log file.
fn write_audit_log(
    file: &mut BufWriter<std::fs::File>,
    timestamp_secs: f64,
    level: &str,
    target: &str,
    message: &str,
    fields: &[(&str, serde_json::Value)],
) {
    let mut obj = serde_json::Map::new();
    obj.insert("timestamp".to_string(), serde_json::json!(timestamp_secs));
    obj.insert("level".to_string(), serde_json::json!(level));
    obj.insert("target".to_string(), serde_json::json!(target));
    obj.insert("message".to_string(), serde_json::json!(message));
    for (k, v) in fields {
        obj.insert(k.to_string(), v.clone());
    }
    if let Ok(line) = serde_json::to_string(&obj) {
        let _ = writeln!(file, "{}", line);
        let _ = file.flush();
    }
}

macro_rules! audit {
    ($file:expr, $ts:expr, $level:expr, $target:expr, $msg:expr) => {
        write_audit_log($file, $ts, $level, $target, $msg, &[]);
    };
    ($file:expr, $ts:expr, $level:expr, $target:expr, $msg:expr, $($k:ident = $v:expr),*) => {
        write_audit_log($file, $ts, $level, $target, $msg, &[
            $((stringify!($k), serde_json::json!($v))),*
        ]);
    };
}

/// Set up tracing subscriber for human-readable stderr output.
/// Default level: info. Override with RUST_LOG env var.
fn init_tracing() {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));

    let fmt_layer = fmt::layer()
        .with_target(true)
        .with_thread_ids(false)
        .with_file(true)
        .with_line_number(true)
        .with_span_events(FmtSpan::CLOSE)
        .with_writer(std::io::stderr); // logs go to stderr, stdout is reserved for JSON

    tracing_subscriber::registry()
        .with(filter)
        .with(fmt_layer)
        .init();
}

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

fn main() {
    // Load .env from project root (one level up from prove-bin/)
    let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    let _ = dotenv::from_path(&project_root.join(".env"));

    let opts = parse_args();
    init_tracing();

    // Default log dir — matches other ZK-RAG / military-documents apps
    let log_dir = PathBuf::from("/data/military-documents/logs");
    let log_file = opts.log_file.unwrap_or_else(|| log_dir.join("prove.log"));

    let mut audit_file: Option<BufWriter<std::fs::File>> = Some({
        // Ensure directory exists
        if let Some(parent) = log_file.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_file)
            .expect("failed to open log file");
        BufWriter::new(file)
    });

    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);

    if let Some(ref mut f) = audit_file {
        audit!(
            f,
            ts,
            "info",
            "prove_bin",
            "prove binary starting",
            input = opts.input_path.clone()
        );
    }

    // ── Build-circuit mode: serialize CircuitData for a given depth ─────────────
    if let Some(depth) = opts.build_circuit_depth {
        let output_path = opts
            .circuit_output_path
            .unwrap_or_else(|| PathBuf::from(format!("circuit_depth{}.bin", depth)));

        info!(
            stage = "build_circuit",
            depth = depth,
            output = %output_path.display(),
            "building and serializing circuit"
        );

        let config = CircuitConfig::standard_recursion_config();
        let mut builder = CircuitBuilder::<PLiF, D>::new(config);
        let _targets = build_merkle_proof_circuit_targets(&mut builder, depth);
        let data = builder.build::<C>();

        let gate_serializer = DefaultGateSerializer;
        let generator_serializer = DefaultGeneratorSerializer::<C, D>::default();
        let circuit_bytes = data
            .to_bytes(&gate_serializer, &generator_serializer)
            .expect("failed to serialize circuit data");

        std::fs::write(&output_path, &circuit_bytes).expect("failed to write circuit file");

        let size_kb = circuit_bytes.len() / 1024;
        info!(
            stage = "circuit_built",
            depth = depth,
            output = %output_path.display(),
            size_kb = size_kb,
            "circuit built and serialized"
        );

        // Write audit log and exit
        if let Some(ref mut f) = audit_file {
            audit!(
                f,
                ts,
                "info",
                "prove_bin",
                "circuit built and serialized",
                depth = depth,
                output = output_path.display().to_string(),
                size_kb = size_kb
            );
        }
        return;
    }

    // ── Read input ─────────────────────────────────────────────────────────
    let json = fs::read_to_string(&opts.input_path).unwrap_or_else(|e| {
        error!(stage = "read", path = opts.input_path, error = %e);
        if let Some(ref mut f) = audit_file {
            let err_str = e.to_string();
            audit!(
                f,
                ts,
                "error",
                "prove_bin",
                "failed to read input file",
                path = opts.input_path,
                error = err_str
            );
        }
        std::process::exit(1);
    });

    let input: ProveInput = match serde_json::from_str(&json) {
        Ok(v) => v,
        Err(e) => {
            error!(stage = "parse", error = %e);
            if let Some(ref mut f) = audit_file {
                let err_str = e.to_string();
                audit!(
                    f,
                    ts,
                    "error",
                    "prove_bin",
                    "failed to parse input JSON",
                    error = err_str
                );
            }
            std::process::exit(1);
        }
    };

    let depth = input.depth as usize;
    if depth > zk_circuit::MAX_DEPTH {
        error!(
            stage = "validate",
            depth = depth,
            max_depth = zk_circuit::MAX_DEPTH,
            "depth exceeds MAX_DEPTH"
        );
        if let Some(ref mut f) = audit_file {
            audit!(
                f,
                ts,
                "error",
                "prove_bin",
                "depth exceeds MAX_DEPTH",
                depth = depth,
                max_depth = zk_circuit::MAX_DEPTH
            );
        }
        std::process::exit(1);
    }

    let document_hash = parse_hash(&input.document_hash);
    let merkle_root = parse_hash(&input.merkle_root);
    let siblings: Vec<_> = input.siblings.iter().map(|s| parse_hash(s)).collect();

    if siblings.len() != depth {
        error!(
            stage = "validate",
            expected = depth,
            got = siblings.len(),
            "sibling count does not match depth"
        );
        if let Some(ref mut f) = audit_file {
            audit!(
                f,
                ts,
                "error",
                "prove_bin",
                "sibling count mismatch",
                expected = depth,
                got = siblings.len()
            );
        }
        std::process::exit(1);
    }

    // ── Load circuit or build from scratch ───────────────────────────────────────
    // Circuit data is pre-built and shipped with the binary for fast proof generation.
    // Default location: next to the binary (../circuit_depth{N}.bin relative to prove-bin).
    // Override with CIRCUIT_DIR env var.
    let circuit_dir = env::var("CIRCUIT_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .to_path_buf()
        });
    let circuit_path = circuit_dir.join(format!("circuit_depth{}.bin", depth));

    // Targets (public input indices) are always rebuilt — they are cheap to compute.
    let config = CircuitConfig::standard_recursion_config();
    let mut builder = CircuitBuilder::<PLiF, D>::new(config);
    let targets = build_merkle_proof_circuit_targets(&mut builder, depth);

    let data = if circuit_path.exists() {
        info!(
            stage = "load_circuit",
            path = %circuit_path.display(),
            "loading pre-built circuit from disk"
        );
        let circuit_bytes = std::fs::read(&circuit_path).expect("failed to read circuit file");
        let gate_serializer = DefaultGateSerializer;
        let generator_serializer = DefaultGeneratorSerializer::<C, D>::default();
        <CircuitData<PLiF, C, D>>::from_bytes(
            &circuit_bytes,
            &gate_serializer,
            &generator_serializer,
        )
        .expect("failed to deserialize circuit data")
    } else {
        warn!(
            stage = "load_circuit",
            path = %circuit_path.display(),
            "circuit file not found, building from scratch (this is slow)"
        );
        info!(
            stage = "build_circuit",
            depth = depth,
            "building circuit from scratch"
        );
        builder.build::<C>()
    };

    let num_gates = data.common.gates.len();
    let num_public_inputs = data.common.num_public_inputs;

    info!(
        stage = "circuit_ready",
        num_gates = num_gates,
        num_public_inputs = num_public_inputs,
        "circuit ready"
    );
    if let Some(ref mut f) = audit_file {
        audit!(
            f,
            ts,
            "info",
            "prove_bin",
            "circuit ready",
            depth = depth,
            num_gates = num_gates,
            cached = circuit_path.exists()
        );
    }

    // ── Parse leaf_hash (pre-computed in Python) ──────────────────────────────
    let leaf_hash = parse_hash(&input.leaf_hash);

    info!(
        stage = "witness_fill",
        merkle_root = %hash_to_hex(&merkle_root),
        document_hash = %hash_to_hex(&document_hash),
        leaf_hash = %hash_to_hex(&leaf_hash),
        leaf_index = input.leaf_index,
        num_siblings = siblings.len(),
        "filling witness"
    );

    // ── Fill witness ───────────────────────────────────────────────────────
    let mut pw = PartialWitness::new();
    fill_merkle_proof_witness(
        &mut pw,
        &targets,
        merkle_root,
        document_hash,
        input.ingestion_timestamp,
        input.ingestion_block,
        leaf_hash,
        &siblings,
        input.leaf_index as usize,
    );

    // ── Generate proof ─────────────────────────────────────────────────────
    info!(
        stage = "prove",
        depth = depth,
        leaf_index = input.leaf_index,
        "starting proof generation"
    );
    let start = Instant::now();
    let proof: ProofWithPublicInputs<PLiF, C, D> = match data.prove(pw) {
        Ok(p) => p,
        Err(e) => {
            error!(stage = "prove", error = %e, "proof generation failed");
            if let Some(ref mut f) = audit_file {
                let err_str = e.to_string();
                audit!(
                    f,
                    ts,
                    "error",
                    "prove_bin",
                    "proof generation failed",
                    error = err_str
                );
            }
            std::process::exit(1);
        }
    };
    let prove_duration_ms = start.elapsed().as_millis() as u64;

    info!(
        stage = "prove_complete",
        duration_ms = prove_duration_ms,
        "proof generated"
    );
    if let Some(ref mut f) = audit_file {
        audit!(
            f,
            ts,
            "info",
            "prove_bin",
            "proof generated",
            depth = depth,
            leaf_index = input.leaf_index,
            duration_ms = prove_duration_ms
        );
    }

    // ── Verify locally ─────────────────────────────────────────────────────
    match data.verify(proof.clone()) {
        Ok(()) => {
            info!(stage = "verify", status = "ok", "proof verified locally");
            if let Some(ref mut f) = audit_file {
                audit!(
                    f,
                    ts,
                    "info",
                    "prove_bin",
                    "proof verified locally",
                    status = "ok"
                );
            }
        }
        Err(e) => {
            warn!(stage = "verify", status = "failed", error = %e,
                "local verification failed");
            if let Some(ref mut f) = audit_file {
                let err_str = e.to_string();
                audit!(
                    f,
                    ts,
                    "warn",
                    "prove_bin",
                    "local verification failed",
                    error = err_str
                );
            }
        }
    }

    // ── Serialize using zkVerify-compatible format ─────────────────────────
    let mut proof_bytes = Vec::new();
    proof_bytes.write_proof(&proof.proof).unwrap_or_else(|e| {
        error!(stage = "serialize", object = "proof", error = %e);
        if let Some(ref mut f) = audit_file {
            let err_str = e.to_string();
            audit!(
                f,
                ts,
                "error",
                "prove_bin",
                "failed to serialize proof",
                error = err_str
            );
        }
        std::process::exit(1);
    });
    let proof_b64 = base64::engine::general_purpose::STANDARD.encode(&proof_bytes);
    let proof_hex = format!("0x{}", hex::encode(&proof_bytes));

    let mut pubs_bytes = Vec::new();
    pubs_bytes
        .write_usize(proof.public_inputs.len())
        .unwrap_or_else(|e| {
            error!(stage = "serialize", object = "public_inputs_len", error = %e);
            std::process::exit(1);
        });
    pubs_bytes
        .write_field_vec(proof.public_inputs.as_slice())
        .unwrap_or_else(|e| {
            error!(stage = "serialize", object = "public_inputs", error = %e);
            std::process::exit(1);
        });
    let public_inputs_b64 = base64::engine::general_purpose::STANDARD.encode(&pubs_bytes);
    let public_inputs_hex = format!("0x{}", hex::encode(&pubs_bytes));

    let vk_bytes = data
        .verifier_data()
        .to_bytes(&ZKVerifyGateSerializer)
        .unwrap_or_else(|e| {
            error!(stage = "serialize", object = "verifier_circuit_data", error = %e);
            if let Some(ref mut f) = audit_file {
                let err_str = e.to_string();
                audit!(
                    f,
                    ts,
                    "error",
                    "prove_bin",
                    "failed to serialize verifier circuit data with ZKVerifyGateSerializer",
                    error = err_str
                );
            }
            std::process::exit(1);
        });
    let verifier_only_b64 = base64::engine::general_purpose::STANDARD.encode(&vk_bytes);
    let vk_hex = format!("0x{}", hex::encode(&vk_bytes));

    let gate_serializer = DefaultGateSerializer;
    let common_bytes = match data.common.to_bytes(&gate_serializer) {
        Ok(b) => b,
        Err(e) => {
            error!(stage = "serialize", object = "common_circuit_data", error = %e);
            if let Some(ref mut f) = audit_file {
                let err_str = e.to_string();
                audit!(
                    f,
                    ts,
                    "error",
                    "prove_bin",
                    "failed to serialize common circuit data",
                    error = err_str
                );
            }
            std::process::exit(1);
        }
    };

    // ── Output JSON ─────────────────────────────────────────────────────────
    let output = ProveOutput {
        proof_b64,
        public_inputs_b64,
        public_inputs: PublicInputs {
            leaf_hash: hash_to_hex(&leaf_hash),
            document_hash: hash_to_hex(&document_hash),
            merkle_root: hash_to_hex(&merkle_root),
            ingestion_timestamp: input.ingestion_timestamp,
            ingestion_block: input.ingestion_block,
        },
        common_circuit_data_b64: base64::engine::general_purpose::STANDARD.encode(&common_bytes),
        verifier_only_b64,
        proof_hex,
        public_inputs_hex,
        vk_hex,
    };

    let json_out = serde_json::to_string_pretty(&output).unwrap();
    println!("{}", json_out);

    info!(
        stage = "done",
        proof_b64_len = output.proof_b64.len(),
        "proof output written to stdout"
    );
    if let Some(ref mut f) = audit_file {
        audit!(
            f,
            ts,
            "info",
            "prove_bin",
            "done",
            proof_b64_len = output.proof_b64.len(),
            public_inputs_b64_len = output.public_inputs_b64.len(),
            common_b64_len = output.common_circuit_data_b64.len(),
            verifier_only_b64_len = output.verifier_only_b64.len()
        );
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

#[derive(serde::Deserialize)]
struct ProveInput {
    /// Pre-computed Poseidon hash of the chunk text (computed in Python).
    /// The circuit verifies the Merkle proof using this hash directly.
    leaf_hash: String,
    document_hash: String,
    merkle_root: String,
    leaf_index: usize,
    depth: usize,
    siblings: Vec<String>,
    /// Unix timestamp when the root was published on-chain.
    ingestion_timestamp: u64,
    /// Block number when the root was published on-chain.
    ingestion_block: u64,
}

#[derive(serde::Serialize)]
struct ProveOutput {
    proof_b64: String,
    public_inputs_b64: String,
    public_inputs: PublicInputs,
    common_circuit_data_b64: String,
    verifier_only_b64: String,
    /// 0x-prefixed hex proof bytes — for Kurier submission
    proof_hex: String,
    /// 0x-prefixed hex public inputs — for Kurier submission
    public_inputs_hex: String,
    /// 0x-prefixed hex VK — for Kurier VK registration
    vk_hex: String,
}

#[derive(serde::Serialize)]
struct PublicInputs {
    /// Poseidon hash of the chunk text (pre-computed in Python).
    leaf_hash: String,
    /// Document hash (SHA-256 of doc_id, as Poseidon hash).
    document_hash: String,
    /// Merkle root of the document tree.
    merkle_root: String,
    /// Unix timestamp when the root was published on-chain.
    ingestion_timestamp: u64,
    /// Block number when the root was published on-chain.
    ingestion_block: u64,
}
