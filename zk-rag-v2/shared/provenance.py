"""
provenance.py — ZK-RAG Provenance API module.

Provides on-demand ZK proof generation + zkVerify submission for
any chunk returned by the RAG query endpoint.

Design: Provenance is NEVER computed at query time. It is always on-demand
via explicit API call or button click.

Data source: All proof inputs (siblings, merkle data, EVM ingestion) are
sourced entirely from the Qdrant payload — no disk I/O required.

Single path: generate proof → submit to zkVerify → poll for completion.

Network: Controlled by KURIE_API_KEY. A mainnet key submits to mainnet;
testnet key submits to testnet. No automatic fallback.

Environment:
    KURIE_API_KEY       zkVerify/Kurier API key (mainnet or testnet)
    KURIE_VK_ID         Pre-registered VK ID (auto-registered on first use if absent)
    KURIE_API_BASE      Kurier API base URL (default: https://api.kurier.xyz/api/v1)

Paths:
    PROVE_BINARY   ./zk-circuit/target/release/prove-bin

Qdrant payload fields used for proof inputs:
    merkle_leaf_hash, merkle_leaf_index, merkle_tree_depth, merkle_siblings,
    merkle_root, poseidon_doc_id_hash, evm_block_number, evm_block_timestamp

"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ────────────────────────────────────────────────────────────────────
# All paths are configurable via environment variables so the same code
# works on R730 (defaults below) and VPS (overridden in systemd service file).


PROVE_BINARY      = Path(os.environ.get("ZK_PROVE_BINARY",      "./zk-circuit/target/release/prove-bin"))
MERKLE_TREES_DIR  = Path(os.environ.get("ZK_MERKLE_TREES_DIR",  "../data/merkleTrees"))
CHUNKS_DIR        = Path(os.environ.get("ZK_CHUNKS_DIR",          "../data/chunks"))
PROOFS_TMP_DIR    = Path(os.environ.get("ZK_PROOFS_TMP_DIR",     "/tmp/zk_proofs"))
PROOFS_DIR        = Path(os.environ.get("ZK_PROOFS_DIR",          "../data/zk_proofs"))
LOG_DIR           = Path(os.environ.get("ZK_LOG_DIR",             "../data/logs"))
REGISTRY_PATH     = Path(os.environ.get("ZK_REGISTRY_PATH",      "../data/registry.json"))

PROOFS_TMP_DIR.mkdir(parents=True, exist_ok=True)
PROOFS_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────────

def log(level: str, msg: str, **fields):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "script": "provenance",
        "message": msg,
        **fields,
    }
    line = json.dumps(entry)
    print(f"[{level.upper()}] {msg}", file=sys.stderr)
    if fields:
        print(f"    {fields}", file=sys.stderr)
    # Write to structured log file
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / "provenance.log", "a") as f:
        f.write(line + "\n")
        f.flush()

# ── zkVerify API (Kurier transport) ─────────────────────────────────────────

KURIE_API_BASE = os.environ.get(
    "KURIE_API_BASE", "https://api.kurier.xyz/api/v1"
)
KURIE_API_KEY = os.environ.get("KURIE_API_KEY", "")
KURIE_VK_ID = os.environ.get("KURIE_VK_ID", "")

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

def log_info(msg: str, **fields):  log("INFO", msg, **fields)
def log_warn(msg: str, **fields):  log("WARN", msg, **fields)
def log_error(msg: str, **fields): log("ERROR", msg, **fields)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ChunkMetadata:
    doc_id: str
    chunk_index: int
    chunk_id: str
    merkle_root: str
    tree_depth: int
    siblings: list[str]          # plain hex strings, ordered leaf → root
    leaf_index: int              # position in the Merkle tree
    leaf_hash: str               # Poseidon(chunk_text) — used for verification
    poseidon_doc_id_hash: str    # Poseidon(doc_id_bytes) = leaf[0] of Merkle tree
    chunk_text: str              # raw chunk text — passed to circuit as private witness
    ingestion_timestamp: int = 0  # Unix epoch — EVM block timestamp
    ingestion_block: int = 0      # EVM block number


@dataclass
class ProvenanceResult:
    chunk_id: str
    doc_id: str
    leaf_index: int
    tree_depth: int
    merkle_root: str
    on_chain: dict               # {horizen_explorer_url, contract, tx_hash, block_number}
    zk_proof: dict              # {status, zkverify_explorer_url, job_id, public_inputs, proof_hex, vk_id}
    proof_hex: Optional[str] = None
    vk_hex: Optional[str] = None
    public_inputs_hex: Optional[str] = None


# ── zkVerify API (Kurier transport) ─────────────────────────────────────────

def get_chunk_metadata(chunk_id: str) -> ChunkMetadata:
    """
    Look up a chunk's provenance metadata from Qdrant.

    All proof inputs (siblings, merkle data, EVM ingestion) come from the
    Qdrant payload — no disk I/O required.

    chunk_id format: {doc_id}-{chunk_index}
    e.g. "00c8a75d605f10359503c9a25fa1255f8a8946e0dd9026646bf69750639b4669-42"

    Raises:
        KeyError: if no Qdrant point matches the chunk_id.

    Returns:
        ChunkMetadata with all fields needed to generate a ZK proof.
    """
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    parts = chunk_id.rsplit("-", 1)
    if len(parts) != 2:
        raise KeyError(f"Invalid chunk_id format: {chunk_id}")
    doc_id, chunk_index_str = parts
    try:
        chunk_index = int(chunk_index_str)
    except ValueError:
        raise KeyError(f"Invalid chunk_index in chunk_id: {chunk_id}")

    # ── Qdrant lookup ─────────────────────────────────────────────────────────
    client = QdrantClient(url="http://127.0.0.1:6333")

    # Search all 4 collections for this chunk_id
    point = None
    for col_name in ("army", "navy", "marines", "other"):
        results = client.scroll(
            collection_name=col_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="chunk_id",
                        match=MatchValue(value=chunk_id),
                    )
                ]
            ),
            limit=1,
            with_payload=True,
        )
        pts = results[0] if isinstance(results, tuple) else results
        if pts:
            point = pts[0]
            break

    if point is None:
        raise KeyError(f"No Qdrant point found for chunk_id: {chunk_id}")

    payload = point.payload

    # ── Parse payload fields ──────────────────────────────────────────────────
    depth_val = payload.get("merkle_tree_depth")
    tree_depth = int(depth_val) if depth_val is not None else 0

    leaf_index_val = payload.get("merkle_leaf_index")
    leaf_index = int(leaf_index_val) if leaf_index_val is not None else chunk_index

    leaf_hash = payload.get("merkle_leaf_hash", "")
    poseidon_doc_id_hash = payload.get("poseidon_doc_id_hash", "")
    merkle_root = payload.get("merkle_root", "")
    siblings = payload.get("merkle_siblings", [])
    chunk_text = payload.get("text", "")

    # EVM ingestion metadata
    ingestion_block = int(payload.get("evm_block_number", 0))
    ts_str = payload.get("evm_block_timestamp", "")
    if ts_str:
        ingestion_timestamp = int(
            datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
        )
    else:
        ingestion_timestamp = 0

    return ChunkMetadata(
        doc_id=doc_id,
        chunk_index=chunk_index,
        chunk_id=chunk_id,
        merkle_root=merkle_root,
        tree_depth=tree_depth,
        siblings=siblings,
        leaf_index=leaf_index,
        leaf_hash=leaf_hash,
        poseidon_doc_id_hash=poseidon_doc_id_hash,
        chunk_text=chunk_text,
        ingestion_timestamp=ingestion_timestamp,
        ingestion_block=ingestion_block,
    )


# ── Prove binary wrapper (payload direct) ────────────────────────────────────

def generate_proof_from_payload(chunk_id: str, payload: dict) -> dict:
    """
    Generate a ZK proof for a chunk using Qdrant payload data directly.

    This is the fast path for query-provable: no re-query of Qdrant needed,
    all data is already in memory from the search result.

    Args:
        chunk_id: The chunk ID (e.g. "00c8a75d...-42")
        payload: The Qdrant point payload dict for this chunk

    Returns:
        dict with proof_hex, public_inputs_hex, vk_hex, public_inputs, kurier_job_id

    Raises:
        RuntimeError: if prove binary fails
    """
    parts = chunk_id.rsplit("-", 1)
    if len(parts) != 2:
        raise KeyError(f"Invalid chunk_id format: {chunk_id}")
    doc_id = parts[0]

    # Parse payload fields
    depth_val = payload.get("merkle_tree_depth")
    tree_depth = int(depth_val) if depth_val is not None else 0

    leaf_index_val = payload.get("merkle_leaf_index")
    chunk_index_val = int(parts[1])
    leaf_index = int(leaf_index_val) if leaf_index_val is not None else chunk_index_val

    leaf_hash = payload.get("merkle_leaf_hash", "")
    poseidon_doc_id_hash = payload.get("poseidon_doc_id_hash", "")
    merkle_root = payload.get("merkle_root", "")
    siblings = payload.get("merkle_siblings", [])

    # EVM ingestion metadata
    ingestion_block = int(payload.get("evm_block_number", 0))
    ts_str = payload.get("evm_block_timestamp", "")
    if ts_str:
        ingestion_timestamp = int(
            datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
        )
    else:
        ingestion_timestamp = 0

    input_data = {
        "leaf_hash": leaf_hash,
        "document_hash": poseidon_doc_id_hash,
        "merkle_root": merkle_root,
        "leaf_index": leaf_index,
        "depth": tree_depth,
        "siblings": siblings,
        "ingestion_timestamp": ingestion_timestamp,
        "ingestion_block": ingestion_block,
    }

    proof_id = uuid.uuid4().hex
    input_path = PROOFS_TMP_DIR / f"{proof_id}_input.json"

    with open(input_path, "w") as f:
        json.dump(input_data, f)

    log_info("Calling prove binary", proof_id=proof_id, chunk_id=chunk_id, depth=tree_depth)

    env = os.environ.copy()
    env["RUST_BACKTRACE"] = "1"
    env["CIRCUIT_DIR"] = str(PROVE_BINARY.parent.parent)

    try:
        proc = subprocess.run(
            [str(PROVE_BINARY), str(input_path)],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
    except subprocess.TimeoutExpired:
        log_error("Prove binary timed out after 5 seconds", proof_id=proof_id, chunk_id=chunk_id, depth=tree_depth)
        raise
    finally:
        if input_path.exists():
            input_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise RuntimeError(f"prove binary failed (exit {proc.returncode}): {proc.stderr}")

    output_text = proc.stdout.strip()
    if not output_text:
        raise RuntimeError("prove binary produced no output (empty stdout)")

    try:
        result = json.loads(output_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"prove binary output is not valid JSON: {e}\noutput: {output_text[:500]}")

    # Submit proof to Kurier (zkVerify) — this is what makes the badge go from "not verified" to "submitted"
    kurier_job_id = None
    try:
        kurier_job_id = submit_proof_to_zkverify(
            proof_hex=result.get("proof_hex", ""),
            public_inputs_hex=result.get("public_inputs_hex", ""),
            vk_hex=result.get("vk_hex", ""),
            vk_id=None,
        )
        result["kurier_job_id"] = kurier_job_id
        log_info("Proof submitted to Kurier", chunk_id=chunk_id, kurier_job_id=kurier_job_id)
    except Exception as e:
        log_warn("Failed to submit proof to Kurier", chunk_id=chunk_id, error=str(e))

    # Save proof to disk for audit trail
    try:
        save_proof_payload(chunk_id, doc_id, leaf_index, tree_depth, merkle_root, ingestion_block, result, kurier_job_id=kurier_job_id)
    except Exception as e:
        log_warn("Failed to save proof to disk", chunk_id=chunk_id, error=str(e))

    return {
        "proof_hex": result.get("proof_hex", ""),
        "public_inputs_hex": result.get("public_inputs_hex", ""),
        "vk_hex": result.get("vk_hex", ""),
        "public_inputs": result.get("public_inputs", {}),
        "kurier_job_id": kurier_job_id,
    }


def save_proof_payload(
    chunk_id: str,
    doc_id: str,
    leaf_index: int,
    tree_depth: int,
    merkle_root: str,
    evm_block_number: int,
    proof_data: dict,
    kurier_job_id: str | None = None,
    kurier_status: str | None = None,
) -> None:
    """Persist proof metadata to disk for audit.

   kurier_job_id and kurier_status are written if provided.
    """
    filename = f"zk_proof_{doc_id}_{leaf_index}.json"
    path = PROOFS_DIR / filename
    payload = {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "leaf_index": leaf_index,
        "tree_depth": tree_depth,
        "merkle_root": merkle_root,
        "evm_block_number": evm_block_number,
        "public_inputs": proof_data.get("public_inputs", {}),
        "public_inputs_hex": proof_data.get("public_inputs_hex", ""),
        "proof_hex": proof_data.get("proof_hex", ""),
        "vk_hex": proof_data.get("vk_hex", ""),
    }
    if kurier_job_id is not None:
        payload["kurier_job_id"] = kurier_job_id
    if kurier_status is not None:
        payload["kurier_status"] = kurier_status
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def _proof_path_for_chunk(chunk_id: str) -> tuple[Path, str, int]:
    """Derive the proof file path and parsed fields from a chunk_id.

    Returns (path, doc_id, leaf_index).
    """
    parts = chunk_id.rsplit("-", 1)
    doc_id = parts[0]
    leaf_index = int(parts[1]) if len(parts) == 2 else 0
    path = PROOFS_DIR / f"zk_proof_{doc_id}_{leaf_index}.json"
    return path, doc_id, leaf_index


def check_existing_kurier_job(chunk_id: str) -> str | None:
    """
    Returns existing job_id if a proof for this chunk_id has already been
    submitted to Kurier and is not in a failed state.

    Returns None if no existing record or if the existing job is terminal-failed.
    """
    path, _, _ = _proof_path_for_chunk(chunk_id)
    if not path.exists():
        return None

    with open(path) as f:
        data = json.load(f)

    job_id = data.get("kurier_job_id")
    if not job_id:
        return None

    status = (data.get("kurier_status") or "").lower()
    failed_statuses = {"failed", "rejected", "invalid"}
    if status in failed_statuses:
        return None

    return job_id


def save_kurier_status(
    chunk_id: str,
    job_id: str,
    status: str,
    explorer_url: str = "",
    tx_hash: str = "",
    tx_explorer_url: str = "",
    block_hash: str = "",
    block_explorer_url: str = "",
) -> None:
    """
    Load the existing proof JSON for a chunk and merge in Kurier status fields.
    Creates the file if it doesn't exist (edge case: called before save_proof_payload).
    """
    path, doc_id, leaf_index = _proof_path_for_chunk(chunk_id)

    if path.exists():
        with open(path) as f:
            payload = json.load(f)
    else:
        # Edge case: status saved before proof — create a minimal shell
        payload = {
            "chunk_id": chunk_id,
            "doc_id": doc_id,
            "leaf_index": leaf_index,
        }

    payload["kurier_job_id"] = job_id
    payload["kurier_status"] = status
    if explorer_url:
        payload["kurier_explorer_url"] = explorer_url
    if tx_hash:
        payload["kurier_tx_hash"] = tx_hash
    if tx_explorer_url:
        payload["kurier_tx_explorer_url"] = tx_explorer_url
    if block_hash:
        payload["kurier_block_hash"] = block_hash
    if block_explorer_url:
        payload["kurier_block_explorer_url"] = block_explorer_url

    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def load_proof_from_disk(chunk_id: str) -> dict:
    """Load the saved proof JSON for a chunk from disk."""
    path, _, _ = _proof_path_for_chunk(chunk_id)
    with open(path) as f:
        return json.load(f)


# ── Prove binary wrapper ───────────────────────────────────────────────────────

def generate_proof(metadata: ChunkMetadata) -> dict:
    """
    Call the Rust prove binary to generate a ZK proof for a single chunk.

    Input format (all fields from Qdrant payload):
        {
          "leaf_hash": "0x...",           // Poseidon hash of chunk text
          "document_hash": "0x...",        // Poseidon(doc_id_bytes) = leaf zero of Merkle tree
          "merkle_root": "0x...",
          "leaf_index": N,
          "depth": N,
          "siblings": ["0x...", ...],
          "ingestion_timestamp": N,        // Unix epoch — EVM block timestamp
          "ingestion_block": N,            // EVM block number
        }

    Output: {proof_hex, public_inputs_hex, vk_hex, public_inputs: {leaf_hash, document_hash, merkle_root}}
    """
    # document_hash = Poseidon(doc_id_bytes) = leaf[0] of the Merkle tree
    # This is the value already computed and stored by Pipeline E.
    document_hash = metadata.poseidon_doc_id_hash

    input_data = {
        "leaf_hash": metadata.leaf_hash,
        "document_hash": document_hash,
        "merkle_root": metadata.merkle_root,
        "leaf_index": metadata.leaf_index,
        "depth": metadata.tree_depth,
        "siblings": metadata.siblings,
        "ingestion_timestamp": metadata.ingestion_timestamp,
        "ingestion_block": metadata.ingestion_block,
    }

    proof_id = uuid.uuid4().hex
    input_path = PROOFS_TMP_DIR / f"{proof_id}_input.json"

    with open(input_path, "w") as f:
        json.dump(input_data, f)

    log_info("Calling prove binary",
        proof_id=proof_id,
        chunk_id=metadata.chunk_id,
        depth=metadata.tree_depth,
    )

    env = os.environ.copy()
    env["RUST_BACKTRACE"] = "1"
    env["CIRCUIT_DIR"] = str(PROVE_BINARY.parent.parent)

    try:
        proc = subprocess.run(
            [str(PROVE_BINARY), str(input_path)],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
    except subprocess.TimeoutExpired:
        log_error("Prove binary timed out after 5 seconds", proof_id=proof_id, chunk_id=metadata.chunk_id, depth=metadata.tree_depth)
        raise
    finally:
        # Clean up input
        if input_path.exists():
            input_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise RuntimeError(f"prove binary failed (exit {proc.returncode}): {proc.stderr}")

    # prove binary outputs JSON to stdout
    output_text = proc.stdout.strip()
    if not output_text:
        raise RuntimeError("prove binary produced no output (empty stdout)")

    try:
        result = json.loads(output_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"prove binary output is not valid JSON: {e}\noutput: {output_text[:500]}")

    # Save proof to disk via save_proof_payload (uses leaf_index from parts)
    try:
        doc_id_parts = metadata.chunk_id.rsplit("-", 1)
        doc_id = doc_id_parts[0]
        leaf_index = int(doc_id_parts[1]) if len(doc_id_parts) == 2 else metadata.chunk_index
        save_proof_payload(
            metadata.chunk_id, doc_id, leaf_index,
            metadata.tree_depth, metadata.merkle_root,
            metadata.ingestion_block, result,
        )
    except Exception as e:
        log_warn("Failed to save proof to disk", chunk_id=metadata.chunk_id, error=str(e))

    return {
        "proof_hex": result.get("proof_hex", ""),
        "public_inputs_hex": result.get("public_inputs_hex", ""),
        "vk_hex": result.get("vk_hex", ""),
        "public_inputs": result.get("public_inputs", {}),
    }


# ── zkVerify API (Kurier transport) ─────────────────────────────────────────

class KurierApiError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(f"Kurier API error {code}: {message}")


def _kurier_post(endpoint: str, body: dict) -> dict:
    """Make an authenticated POST to the Kurier API."""
    if not KURIE_API_KEY:
        raise KurierApiError(0, "KURIE_API_KEY not set in environment")

    url = f"{KURIE_API_BASE}/{endpoint.format(api_key=KURIE_API_KEY)}"
    log_info(f"_kurier_post: key_preview={KURIE_API_KEY[:6]}... endpoint={endpoint}")
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": BROWSER_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_resp = e.read().decode("utf-8")
        try:
            err_body = json.loads(body_resp)
            message = err_body.get("message", err_body.get("error", body_resp))
        except Exception:
            message = body_resp
        raise KurierApiError(e.code, message)
    except urllib.error.URLError as e:
        raise KurierApiError(0, str(e.reason))


def _kurier_get(endpoint: str) -> dict:
    """Make an authenticated GET to the Kurier API."""
    if not KURIE_API_KEY:
        raise KurierApiError(0, "KURIE_API_KEY not set in environment")

    url = f"{KURIE_API_BASE}/{endpoint.format(api_key=KURIE_API_KEY)}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": BROWSER_USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_resp = e.read().decode("utf-8")
        try:
            err_body = json.loads(body_resp)
            message = err_body.get("message", err_body.get("error", body_resp))
        except Exception:
            message = body_resp
        raise KurierApiError(e.code, message)
    except urllib.error.URLError as e:
        raise KurierApiError(0, str(e.reason))


def register_vk(vk_hex: str) -> str:
    """
    Register a VK with zkVerify (via Kurier API) and return the vk_id.

    One-time operation per circuit design.
    """
    log_info("Registering VK with zkVerify")
    body = {
        "proofType": "plonky2",
        "proofOptions": {"hashFunction": "poseidon"},
        "vk": {"config": "Poseidon", "bytes": vk_hex},
    }
    result = _kurier_post("register-vk/{api_key}", body)
    vk_id = result.get("vkId") or result.get("vk_id") or result.get("id", "")
    log_info("VK registered", vk_id=vk_id)
    return vk_id


def ensure_vk_id(vk_hex: str) -> str:
    """Return existing KURIE_VK_ID or register a new one and cache it."""
    if KURIE_VK_ID:
        return KURIE_VK_ID
    vk_id = register_vk(vk_hex)
    log_warn(
        "KURIE_VK_ID not set in environment — please add to .env after this run",
        vk_id=vk_id,
    )
    return vk_id


def submit_proof_to_zkverify(
    proof_hex: str,
    public_inputs_hex: str,
    vk_hex: str,
    vk_id: str | None = None,
) -> str:
    """
    Submit a proof to zkVerify (via Kurier API) and return the job_id.

    vkRegistered=False — Kurier registers the VK on-chain during submission.
    vkRegistered=True causes HTTP 500 because the VK is not pre-registered on their system.
    """
    if not vk_hex.startswith("0x"):
        vk_hex = "0x" + vk_hex

    body = {
        "proofData": {
            "proof": proof_hex,
            "publicSignals": public_inputs_hex,
            "vk": vk_hex,
        },
        "proofType": "plonky2",
        "vkRegistered": False,
        "proofOptions": {"hashFunction": "poseidon"},
        "submissionMode": "attestation",
    }
    result = _kurier_post("submit-proof/{api_key}", body)
    job_id = result.get("jobId") or result.get("job_id", "")
    if not job_id:
        raise KurierApiError(0, f"No jobId in zkVerify response: {result}")
    log_info("Proof submitted to zkVerify", job_id=job_id)
    return job_id


ZKVERIFY_MAINNET_EXPLORER = "https://zkverify.io/explorer/job"
ZKVERIFY_TESTNET_EXPLORER = "https://testnet.zkverify.io/explorer/job"

# Kurier job states that mean the job is done (success or failure).
# Excludes transient states: Submitted, Processing, IncludedInBlock, etc.
# "Finalized" = included in block and verified on zkVerify (our primary success state).
# "verified" and "completed" are alternative success states.
_ZKVERIFY_TERMINAL_STATES = {"finalized", "completed", "verified", "failed", "rejected", "invalid"}


def _zkverify_explorer_url(job_id: str) -> str:
    """Return the correct explorer URL based on which API base is configured."""
    if "testnet" in KURIE_API_BASE.lower():
        return f"{ZKVERIFY_TESTNET_EXPLORER}/{job_id}"
    return f"{ZKVERIFY_MAINNET_EXPLORER}/{job_id}"


def poll_zkverify_job(job_id: str, poll_interval: int = 10, max_wait: int = 300) -> dict:
    """
    Poll a zkVerify job until terminal state.

    Returns final status dict with keys: status, zkverify_explorer_url
    """
    log_info("Polling zkVerify job", job_id=job_id, max_wait=max_wait)
    start = time.monotonic()

    while True:
        elapsed = time.monotonic() - start
        if elapsed > max_wait:
            raise KurierApiError(0, f"Timeout after {max_wait}s waiting for job {job_id}")

        result = _kurier_get(f"job-status/{{api_key}}/{job_id}")
        state = result.get("status", "").lower()
        log_info("Job status", job_id=job_id, status=result.get("status"), elapsed=int(elapsed))

        if state in _ZKVERIFY_TERMINAL_STATES:
            return {
                "status": state,
                "zkverify_explorer_url": _zkverify_explorer_url(job_id),
                "tx_hash": result.get("txHash"),
                "tx_explorer_url": result.get("txExplorerUrl"),
                "block_hash": result.get("blockHash"),
                "block_explorer_url": result.get("blockExplorerUrl"),
            }

        time.sleep(poll_interval)


# ── Emit tx lookup ────────────────────────────────────────────────────────────

REGISTRY_PATH = Path("../data/registry.json")

# Contract address on Sepolia (from SECTION-zk-circuit-02-implementation.md)
DEFAULT_CONTRACT = "0x17A6E8AE3f6eb315F4C117630F3AaC8865BD2B15"


def get_emit_tx(doc_id: str) -> dict:
    """
    Look up the on-chain emit transaction for a document from the registry.

    Returns:
        {horizen_explorer_url, contract, tx_hash, block_number}
    """
    if not REGISTRY_PATH.exists():
        raise KeyError(f"Registry not found: {REGISTRY_PATH}")

    with open(REGISTRY_PATH) as f:
        registry = json.load(f)

    doc = None
    for d in registry.get("documents", []):
        if d.get("doc_id", "").lower() == doc_id.lower():
            doc = d
            break

    if not doc:
        raise KeyError(f"doc_id not found in registry: {doc_id}")

    # Get emit record — prefer mainnet, fall back to testnet
    emit = doc.get("emitted_mainnet", {}) or doc.get("emitted_testnet", {})
    if not emit or isinstance(emit, bool):
        raise KeyError(f"Document {doc_id} has not been emitted on-chain yet")

    tx_hash = emit.get("tx_hash", "") or emit.get("tx", "")
    block = emit.get("block_number", "") or emit.get("block", "")

    explorer_base = "https://sepolia.explorer.horizen.io"
    if tx_hash and tx_hash != "unknown":
        # Normalize: ensure 0x prefix
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        explorer_url = f"{explorer_base}/tx/{tx_hash}"
    else:
        # Tx hash not recorded — link to the contract page where user can find the emit
        explorer_url = f"{explorer_base}/address/{DEFAULT_CONTRACT}"

    return {
        "horizen_explorer_url": explorer_url,
        "contract": DEFAULT_CONTRACT,
        "tx_hash": tx_hash if tx_hash and tx_hash != "unknown" else "",
        "block_number": block,
    }


# ── Manifest ─────────────────────────────────────────────────────────────────

def get_manifest() -> list[dict]:
    """
    Return all documents that have tree files (provable chunks).

    Each entry: {doc_id, merkle_root, tree_depth, chunk_count}
    """
    manifests = []
    for tree_path in MERKLE_TREES_DIR.glob("*_tree.json"):
        doc_id = tree_path.name.replace("_tree.json", "")
        with open(tree_path) as f:
            tree = json.load(f)
        tree_config = tree.get("tree_config", {})
        # tree_config.depth is the authoritative depth, fall back to sibling count
        if "depth" in tree_config:
            tree_depth = tree_config["depth"]
        else:
            # Fallback: compute from sibling list length of first path
            paths = tree.get("paths", {})
            if paths:
                first_path = list(paths.values())[0]
                tree_depth = len(first_path.get("siblings", []))
            else:
                tree_depth = 0
        manifests.append({
            "doc_id": doc_id,
            "merkle_root": tree.get("merkle_root", ""),
            "tree_depth": tree_depth,
            "chunk_count": tree.get("chunk_count", 0),
        })
    return manifests


# ── Full provenance orchestration ────────────────────────────────────────────

async def get_provenance(chunk_id: str) -> ProvenanceResult:
    """
    Full provenance flow for a chunk:

    1. Look up chunk metadata from tree JSON
    2. Generate ZK proof via Rust prove binary
    3. Submit proof to zkVerify (via Kurier API)
    4. Poll for completion
    5. Return ProvenanceResult with explorer links

    Note: zkVerify polling is synchronous (blocking). For production use,
    the API endpoint should return immediately and poll in background.
    """
    log_info("Getting provenance", chunk_id=chunk_id)

    # Step 1: metadata
    metadata = get_chunk_metadata(chunk_id)
    log_info("Got chunk metadata", doc_id=metadata.doc_id, tree_depth=metadata.tree_depth)

    # Step 2: emit tx lookup (static, instant)
    try:
        emit_tx = get_emit_tx(metadata.doc_id)
    except KeyError:
        emit_tx = {
            "horizen_explorer_url": "",
            "contract": "0x17A6E8AE3f6eb315F4C117630F3AaC8865BD2B15",
            "tx_hash": "",
            "block_number": "",
        }

    # Step 3: generate ZK proof
    proof_data = generate_proof(metadata)
    log_info("Proof generated", chunk_id=chunk_id, proof_hex=proof_data["proof_hex"][:20] + "...")

    # Step 4: submit to zkVerify
    job_id = submit_proof_to_zkverify(
        proof_hex=proof_data["proof_hex"],
        public_inputs_hex=proof_data["public_inputs_hex"],
        vk_hex=proof_data["vk_hex"],
        vk_id=None,
    )

    # Step 5: poll for completion
    status = poll_zkverify_job(job_id, poll_interval=10, max_wait=300)

    return ProvenanceResult(
        chunk_id=chunk_id,
        doc_id=metadata.doc_id,
        leaf_index=metadata.leaf_index,
        tree_depth=metadata.tree_depth,
        merkle_root=metadata.merkle_root,
        on_chain=emit_tx,
        zk_proof={
            "status": status["status"],
            "zkverify_explorer_url": status["zkverify_explorer_url"],
            "job_id": job_id,
            "public_inputs": proof_data["public_inputs"],
            "proof_hex": proof_data["proof_hex"],
        },
        proof_hex=proof_data["proof_hex"],
        vk_hex=proof_data["vk_hex"],
        public_inputs_hex=proof_data["public_inputs_hex"],
    )
