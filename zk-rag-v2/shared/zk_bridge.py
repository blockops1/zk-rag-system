"""
zk_bridge.py — Python async wrapper for the ZK proof binary.

Provides:
- Proof submission (async, non-blocking)
- Background proof generation with semaphore concurrency control
- Polling API for proof status
- LRU-cached Merkle tree JSON loading

Binary: $REPO_DIR/zk-circuit/target/release/prove
Tree JSONs: $DATA_DIR/merkle_trees/{doc_id}_tree.json
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Ensure temp directory exists on import
Path("/tmp/zk_proofs").mkdir(parents=True, exist_ok=True)


# ─── Constants ────────────────────────────────────────────────────────────────

PROVE_BINARY = Path("$REPO_DIR/zk-circuit/target/release/prove")
PROOF_TIMEOUT = 600  # seconds
MAX_CONCURRENT_PROOFS = 2
PROOF_TTL = 3600  # completed proofs expire after 1 hour (seconds)


# ─── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class ChunkInput:
    doc_id: str
    text: str
    sorted_index: int
    siblings: list[str]           # variable-length hex strings, ordered leaf → root
    merkle_root: str = ""        # single merkle root hex string (V2 format)


@dataclass
class ProofResult:
    proof_id: str
    status: str  # "generated" | "error"
    public_inputs: Optional[dict] = None
    proof: Optional[str] = None  # JSON-serialized STARK proof (for on-chain verification)
    circuit_data: Optional[str] = None  # common circuit data
    verifier_data: Optional[str] = None  # verifier-only data
    error: Optional[str] = None


@dataclass
class ProofRecord:
    proof_id: str
    status: str  # "pending" | "generated" | "error"
    created_at: float
    completed_at: Optional[float] = None
    chunks: list[ChunkInput] = field(default_factory=list)
    merkle_root: str = ""        # single merkle root hex string (V2 format)
    llm_input_text: str = ""
    llm_output_text: str = ""
    result: Optional[ProofResult] = None
    error: Optional[str] = None


# ─── Exceptions ───────────────────────────────────────────────────────────────

class ProofCapacityExceeded(Exception):
    """Raised when the proof generation semaphore is at capacity."""
    pass


# ─── Module-Level State ───────────────────────────────────────────────────────

_proof_semaphore: asyncio.Semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROOFS)
proofs: dict[str, ProofRecord] = {}  # in-memory proof storage
_proofs_lock: asyncio.Lock = asyncio.Lock()  # async context — for use inside async functions
_sync_proofs_lock: threading.Lock = threading.Lock()  # sync context — for use in non-async functions


# ─── Merkle Tree JSON Loader ──────────────────────────────────────────────────

@lru_cache(maxsize=50)
def load_tree_json(doc_id: str) -> dict:
    """Load a Merkle tree JSON from disk.

    Path: $DATA_DIR/merkle_trees/{doc_id}_tree.json

    Cached via LRU (maxsize=50). Cache survives across proof generations
    within the same process lifetime.

    Raises:
        FileNotFoundError: if the tree JSON does not exist.
    """
    path = Path(f"$DATA_DIR/merkle_trees/{doc_id}_tree.json")
    if not path.exists():
        raise FileNotFoundError(f"Merkle tree not found: {path}")
    with open(path) as f:
        return json.load(f)


# ─── Input Validation ─────────────────────────────────────────────────────────

def build_proof_input(
    chunks: list[ChunkInput],
    merkle_root: str,
    llm_input_text: str,
    llm_output_text: str,
) -> dict:
    """Build the JSON input dict for the prove binary (single-root mode).

    Validates input structure. Raises ValueError with descriptive messages on
    validation failure.

    Returns:
        dict with keys: mode, merkle_root, chunks (single-root format)

    Raises:
        ValueError: if validation fails.
    """
    if not chunks:
        raise ValueError("chunks list cannot be empty")

    if not merkle_root:
        raise ValueError("merkle_root cannot be empty")

    # Variable sibling length validation: siblings length must match tree depth
    # (tree depth is determined by sibling count for a given chunk)
    for i, chunk in enumerate(chunks):
        if len(chunk.siblings) == 0:
            raise ValueError(
                f"chunk {i} must have at least 1 sibling, got {len(chunk.siblings)}"
            )

    return {
        "mode": "single-root",
        "merkle_root": merkle_root,
        "chunks": [
            {
                "text": c.text,
                "sorted_index": c.sorted_index,
                "siblings": c.siblings,
            }
            for c in chunks
        ],
        "llm_input_text": llm_input_text,
        "llm_output_text": llm_output_text,
    }


# ─── Proof Generation ──────────────────────────────────────────────────────────

async def generate_zk_proof(
    chunks: list[ChunkInput],
    merkle_root: str,
    llm_input_text: str,
    llm_output_text: str,
    timeout: int = PROOF_TIMEOUT,
) -> ProofResult:
    """Generate a ZK proof by calling the prove binary (async, non-blocking).

    1. Build input JSON via build_proof_input()
    2. Write to /tmp/zk_proofs/{proof_id}_input.json
    3. Spawn: prove {input_path}
    4. await subprocess with timeout
    5. On non-zero exit: return ProofResult(status='error', error=stderr)
    6. On timeout: return ProofResult(status='error', error=f'timeout after {timeout}s')
    7. Read output JSON, extract fields
    8. Return ProofResult(status='generated', ...)
    9. Delete temp files in finally block

    The proof_id is generated inside this function and returned as part of
    ProofResult.

    Args:
        chunks: list of ChunkInput with text, sorted_index, siblings
        merkle_root: single hex string (the Merkle root)
        llm_input_text: full LLM input prompt text
        llm_output_text: LLM output text
        timeout: seconds before killing the subprocess (default 600)

    Returns:
        ProofResult with proof_id, status, and either result fields or error
    """
    proof_id = uuid.uuid4().hex

    input_path = Path(f"/tmp/zk_proofs/{proof_id}_input.json")
    output_path = Path(f"/tmp/zk_proofs/{proof_id}_output.json")

    input_data = build_proof_input(chunks, merkle_root, llm_input_text, llm_output_text)

    try:
        with open(input_path, "w") as f:
            json.dump(input_data, f)

        # Build env manually — asyncio.subprocess.Environment is Python 3.13+
        env = os.environ.copy()
        env["RUST_BACKTRACE"] = "1"
        env["RUST_LOG"] = "debug"
        env["CIRCUIT_DIR"] = "$REPO_DIR/zk-circuit"

        proc = await asyncio.create_subprocess_exec(
            str(PROVE_BINARY),
            str(input_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ProofResult(
                proof_id=proof_id,
                status="error",
                error=f"Proof generation timed out after {timeout}s",
            )

        if proc.returncode != 0:
            # Capture full stderr — don't truncate; plonky2 panics produce long backtraces
            stderr_text = stderr.decode(errors="replace").strip()
            return ProofResult(
                proof_id=proof_id,
                status="error",
                error=f"prove binary failed (exit {proc.returncode}): {stderr_text}",
            )

        if not output_path.exists():
            return ProofResult(
                proof_id=proof_id,
                status="error",
                error="prove binary produced no output file",
            )

        with open(output_path) as f:
            output = json.load(f)

        status = "generated"
        return ProofResult(
            proof_id=proof_id,
            status=status,
            public_inputs=output.get("public_inputs"),
            proof=output.get("proof"),
            circuit_data=output.get("circuit_data"),
            verifier_data=output.get("verifier_data"),
        )

    except FileNotFoundError:
        return ProofResult(
            proof_id=proof_id,
            status="error",
            error=f"prove binary not found at {PROVE_BINARY}",
        )

    finally:
        # Only delete temp files on success — on error, preserve for post-mortem
        # Note: 'status' is set in the return statements above; if we reach here via
        # exception (not a return), status may be undefined — guard against that.
        try:
            if status == "generated":
                for p in (input_path, output_path):
                    if p.exists():
                        p.unlink(missing_ok=True)
        except NameError:
            # status was never set — preserve files for debugging
            pass


# ─── Background Task Runner ───────────────────────────────────────────────────

async def _run_proof(
    proof_id: str,
    chunks: list[ChunkInput],
    merkle_root: str,
    llm_input_text: str,
    llm_output_text: str,
) -> None:
    """Background task: run proof generation and update the shared ProofRecord.

    Called via asyncio.create_task() from submit_proof_task().
    Updates the ProofRecord in-place and handles semaphore release.
    """
    try:
        result = await generate_zk_proof(
            chunks=chunks,
            merkle_root=merkle_root,
            llm_input_text=llm_input_text,
            llm_output_text=llm_output_text,
        )

        async with _proofs_lock:
            if proof_id in proofs:
                proofs[proof_id].result = result
                proofs[proof_id].status = result.status
                proofs[proof_id].completed_at = time.time()
                if result.error:
                    proofs[proof_id].error = result.error

    except Exception as exc:  # pragma: no cover — broad catch for unexpected errors
        async with _proofs_lock:
            if proof_id in proofs:
                proofs[proof_id].status = "error"
                proofs[proof_id].error = f"unexpected error: {exc}"
                proofs[proof_id].completed_at = time.time()

    finally:
        _proof_semaphore.release()

        # Schedule cleanup of completed proofs after PROOF_TTL
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(PROOF_TTL, _cleanup_proof, proof_id)
        except Exception:
            # No running loop — skip scheduling (e.g., during shutdown)
            pass


def _cleanup_proof(proof_id: str) -> None:
    """Remove a completed proof from the in-memory dict.

    Only removes if status is 'generated' or 'error' (not 'pending', which
    means it's still running or was restarted).
    """
    if proof_id not in proofs:
        return
    record = proofs[proof_id]
    if record.status in ("generated", "error"):
        del proofs[proof_id]


# ─── Proof Submission ─────────────────────────────────────────────────────────

async def submit_proof_task(
    chunks: list[ChunkInput],
    merkle_root: str,
    llm_input_text: str,
    llm_output_text: str,
) -> str:
    """Submit a ZK proof generation task.

    Validates input, acquires the concurrency semaphore, stores a ProofRecord
    in the in-memory dict, and spawns a background task to run the proof.

    Returns:
        proof_id (hex string) — poll GET /api/proof/{proof_id}

    Raises:
        ProofCapacityExceeded: if the semaphore is at capacity (2 concurrent).
        ValueError: if input validation fails.
    """
    # Validate input first before any state changes
    build_proof_input(chunks, merkle_root, llm_input_text, llm_output_text)

    # Try to acquire semaphore without blocking
    try:
        # non-blocking acquire — returns immediately if unavailable
        await asyncio.wait_for(_proof_semaphore.acquire(), timeout=0)
    except asyncio.TimeoutError:
        raise ProofCapacityExceeded(
            "Proof generation capacity reached. Try again shortly."
        )

    proof_id = uuid.uuid4().hex
    record = ProofRecord(
        proof_id=proof_id,
        status="pending",
        created_at=time.time(),
        chunks=chunks,
        merkle_root=merkle_root,
        llm_input_text=llm_input_text,
        llm_output_text=llm_output_text,
    )

    async with _proofs_lock:
        proofs[proof_id] = record

    # Spawn background task (fire and forget)
    asyncio.create_task(
        _run_proof(
            proof_id=proof_id,
            chunks=chunks,
            merkle_root=merkle_root,
            llm_input_text=llm_input_text,
            llm_output_text=llm_output_text,
        )
    )

    return proof_id


# ─── Status / Query API ──────────────────────────────────────────────────────

def get_proof_status(proof_id: str) -> ProofRecord:
    """Look up a proof record by ID.

    Args:
        proof_id: hex string

    Returns:
        ProofRecord

    Raises:
        KeyError: if proof_id is not in the dict (never existed, expired,
                  or server restarted mid-proof).
    """
    with _sync_proofs_lock:
        if proof_id not in proofs:
            raise KeyError(f"Proof not found: {proof_id}")
        return proofs[proof_id]


def list_proofs() -> list[ProofRecord]:
    """Return all proof records, sorted oldest-first.

    Returns:
        list of ProofRecord
    """
    with _sync_proofs_lock:
        return sorted(proofs.values(), key=lambda r: r.created_at)
