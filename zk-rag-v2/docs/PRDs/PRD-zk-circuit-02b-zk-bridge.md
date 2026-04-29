# PRD-zk-circuit-02b: ZK Bridge — Python Wrapper for Proof Generation

**Status:** APPROVED — Implementation Complete (2026-04-19) — API endpoints also wired (2026-04-19)
**Author:** Fred
**Date:** 2026-04-19
**Parent:** SECTION-zk-circuit-02-implementation.md (Phase B)

---

## 1. Problem Statement

Phase A (`prove_from_proof_path`) produced a working Rust binary that accepts pre-computed Merkle proof paths and generates ZK proofs. The binary is correct and all tests pass.

The API server (`api_server.py`) needs to call this binary when serving queries — but it cannot do so directly. The gap is:

1. **No Python interface** to the `prove` binary — API server is Python, binary is Rust
2. **No async wrapper** — synchronous subprocess calls would block the FastAPI event loop
3. **No proof state management** — proofs take 30s+ on CPU; need async polling infrastructure
4. **No Merkle tree JSON loading** — the API server doesn't know how to load tree data

This PRD defines `zk_bridge.py` and the proof management layer that fills these gaps.

---

## 2. Design Decisions

### 2.1 Where This Code Lives

**File:** `$REPO_DIR/shared/zk_bridge.py`

This is a shared module imported by `api_server.py`. It does NOT live in `api_server/` subdirectory because it must be importable as `from shared.zk_bridge import ...` from the parent `shared/` directory.

### 2.2 Proof Binary Path

```
$REPO_DIR/zk-circuit/target/release/prove
```

No configuration flag needed — the path is fixed relative to the repo root.

### 2.3 In-Flight Proof Storage

**Storage:** In-memory `dict[proof_id, ProofRecord]`

```python
@dataclass
class ProofRecord:
    proof_id: str
    status: Literal["pending", "generated", "error"]
    created_at: float          # time.time()
    completed_at: Optional[float]
    result: Optional[ProofResult]  # set when status != pending
    error: Optional[str]           # set when status == error
```

**Why in-memory:** Proofs are ephemeral. A proof is requested → generated → returned to client → discarded. There is no requirement to persist proofs across server restarts. Adding Redis or file-based storage introduces new failure modes for no benefit.

**Cleanup policy:**
- Completed proofs (`generated` or `error`) are removed from the dict after 1 hour
- Pending proofs are NOT cleaned up on restart — a restart mid-proof leaves orphaned records; clients must handle `404` on poll

### 2.4 Concurrency Control

**Semaphore:** `asyncio.Semaphore(2)` — maximum 2 concurrent proof generations.

**Why 2:** plonky2 proof generation is both CPU and memory intensive. On the R730 (56 cores, 377GB RAM) with the embedding service already running, 2 concurrent proofs is a safe upper bound that won't cause OOM or thrashing. On the VPS with less RAM, this can be adjusted.

**Behavior when limit is hit:** `POST /api/prove` returns `503 Service Unavailable` with `{"error": "Proof generation capacity reached. Try again shortly."}`

### 2.5 Temp File Handling

```
/tmp/zk_proofs/{proof_id}_input.json   # written before subprocess spawns
/tmp/zk_proofs/{proof_id}_output.json  # written by prove binary
```

- `/tmp/zk_proofs/` is created on module import if it doesn't exist
- Files are deleted in the `finally` block after the result is read
- If the process times out, files are NOT cleaned up (OS will GC /tmp eventually) — acceptable tradeoff for a debugging artifact

### 2.6 Proof Timeout

**10 minutes (600 seconds).**

plonky2 proof generation on CPU for a 5-chunk circuit takes approximately 30-120 seconds on the R730. 10 minutes provides a safe upper bound. If a proof exceeds this, it is marked `error` with message `Proof generation timed out after 600s`.

### 2.7 Merkle Tree JSON Cache

**Cache:** `functools.lru_cache(maxsize=50)` on `load_tree_json(doc_id: str) -> dict`

**Path:** `$DATA_DIR/merkle_trees/{doc_id}_tree.json`

**Cache eviction:** LRU. 50 entries is sufficient — a typical query returns chunks from at most 2-3 documents. The cache survives across multiple proof generations within the same process lifetime.

**Cache miss behavior:** If the file does not exist, raise `FileNotFoundError` with a descriptive message.

---

## 3. API Contract

### 3.1 POST /api/prove

**Purpose:** Start an async ZK proof generation task.

**Request body:**
```json
{
  "chunks": [
    {
      "doc_id": "0a21e7692759f40c37bdc33b0a2d1f38aa6de9a35efc6f83eac8d4b7f8a3ae1a",
      "text": "actual chunk text from Qdrant payload...",
      "sorted_index": 42,
      "siblings": [
        "0xh1...", "0xh2...", "0xh3...", "0xh4..."
      ]
    }
  ],
  "merkle_root": "0xcc5662e4f4ae16457ea31877e0f0fa38994c5f559ba1f9f9c0e94674e050c1cb",
  "llm_input_text": "Context:\n[chunk texts]\n\nQuery: ...",
  "llm_output_text": "LLM response..."
}
```

**Response (202 Accepted):**
```json
{
  "proof_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending",
  "poll_url": "/api/proof/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**Error responses:**
- `400 Bad Request` — malformed input, missing fields, empty chunks array
- `503 Service Unavailable` — proof generation capacity reached (semaphore full)

### 3.2 GET /api/proof/{proof_id}

**Purpose:** Poll for proof generation result.

**Response (pending):**
```json
{
  "proof_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending"
}
```

**Response (generated):**
```json
{
  "proof_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "generated",
  "public_inputs": {
    "merkle_root": "0x...",
    "llm_input_hash": "0x...",
    "llm_output_hash": "0x..."
  },
  "circuit_data": "base64-encoded-common-circuit-data",
  "verifier_data": "base64-encoded-verifier-only-data"
}
```

**Response (error):**
```json
{
  "proof_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "error",
  "error": "Proof generation timed out after 600s"
}
```

**Error responses:**
- `404 Not Found` — proof_id not found (expired, never existed, or server restarted)

### 3.3 GET /api/proofs

**Purpose:** List in-flight proof IDs and their statuses. Useful for debugging.

**Response (200 OK):**
```json
{
  "count": 2,
  "proofs": [
    {
      "proof_id": "a1b2c3d4-...",
      "status": "pending",
      "age_seconds": 12.3
    },
    {
      "proof_id": "e5f6a7b8-...",
      "status": "generated",
      "age_seconds": 145.7
    }
  ]
}
```

---

## 4. Function Interface

### 4.1 `load_tree_json(doc_id: str) -> dict`

Load a Merkle tree JSON from disk. Cached via LRU.

**Returns:**
```json
{
  "merkle_root": "0x...",
  "paths": {
    "0": {"leaf_hash": "0x...", "siblings": [...]},
    "1": {"leaf_hash": "0x...", "siblings": [...]}
  }
}
```

**Raises:** `FileNotFoundError` if `{doc_id}_tree.json` does not exist.

### 4.2 `build_proof_input(chunks: list[dict], merkle_root: str, llm_input_text: str, llm_output_text: str) -> dict`

Build the JSON input for the `prove` binary from API request data.

- Validates chunk structure
- Validates `merkle_root` is a non-empty string
- Validates each chunk has `text`, `sorted_index`, `siblings` (at least 1 entry)
- Returns the JSON-serializable dict

**Raises:** `ValueError` with descriptive message on validation failure.

### 4.3 `generate_zk_proof(...) -> ProofResult`

**Signature:**
```python
async def generate_zk_proof(
    chunks: list[ChunkInput],
    merkle_root: str,
    llm_input_text: str,
    llm_output_text: str,
    timeout: int = 600,
) -> ProofResult
```

**Behavior:**
1. Generate `proof_id = uuid.uuid4().hex`
2. Write input JSON to `/tmp/zk_proofs/{proof_id}_input.json`
3. Spawn `asyncio.create_subprocess_exec` with `--mode from-path`
4. `await proc.communicate()` with `timeout=600`
5. Read output JSON from `/tmp/zk_proofs/{proof_id}_output.json`
6. Parse and return `ProofResult`
7. Delete temp files in `finally` block

**Returns `ProofResult`:**
```python
@dataclass
class ProofResult:
    proof_id: str
    status: Literal["generated", "error"]
    public_inputs: Optional[dict]   # only when status == "generated"
    circuit_data: Optional[str]     # only when status == "generated" (base64)
    verifier_data: Optional[str]    # only when status == "generated" (base64)
    error: Optional[str]            # only when status == "error"
```

### 4.4 `submit_proof_task(...) -> str`

**Signature:**
```python
async def submit_proof_task(
    chunks: list[ChunkInput],
    merkle_root: str,
    llm_input_text: str,
    llm_output_text: str,
) -> str  # proof_id
```

**Behavior:**
1. Validate input via `build_proof_input()`
2. Attempt to acquire semaphore
3. If semaphore full → raise `ProofCapacityExceeded`
4. Create `ProofRecord` with `status="pending"`
5. Store in global `proofs` dict
6. Spawn background task: `asyncio.create_task(_run_proof(proof_id, ...))`
7. Return `proof_id`

### 4.5 `get_proof_status(proof_id: str) -> ProofRecord`

Look up a proof record. Raises `KeyError` if not found.

### 4.6 `list_proofs() -> list[ProofRecord]`

Return all current proof records. Used by `GET /api/proofs`.

### 4.7 `_run_proof(proof_id: str, ...)` — internal

Background task that:
1. Calls `generate_zk_proof()`
2. Updates the `ProofRecord` in place with result or error
3. Sets `completed_at`
4. Schedules cleanup via `asyncio.get_event_loop().call_later(3600, _cleanup_proof, proof_id)`

---

## 5. Data Types

### ChunkInput
```python
@dataclass
class ChunkInput:
    doc_id: str
    text: str
    sorted_index: int
    siblings: list[str]  # 4 hex strings
```

### ProofRecord (in-memory state)
```python
@dataclass
class ProofRecord:
    proof_id: str
    status: Literal["pending", "generated", "error"]
    created_at: float
    completed_at: Optional[float]
    chunks: list[ChunkInput]
    merkle_root: str
    llm_input_text: str
    llm_output_text: str
    result: Optional[ProofResult] = None
    error: Optional[str] = None
```

---

## 6. Module-Level Globals

```python
# Concurrency control
_proof_semaphore: asyncio.Semaphore = asyncio.Semaphore(2)

# Proof state storage
proofs: dict[str, ProofRecord] = {}
_proofs_lock: asyncio.Lock = asyncio.Lock()

# LRU cache for Merkle tree JSONs (module-level, survives across proof gens)
@lru_cache(maxsize=50)
def load_tree_json(doc_id: str) -> dict: ...

# Binary path
PROVE_BINARY: Path = Path("$REPO_DIR/zk-circuit/target/release/prove")
```

---

## 7. Error Handling

| Error | HTTP Status | Error Message |
|-------|-------------|---------------|
| Malformed request body | 400 | Descriptive validation message |
| Semaphore at capacity | 503 | "Proof generation capacity reached. Try again shortly." |
| Proof not found | 404 | "Proof not found: {proof_id}" |
| Binary not found | 500 | "ZK proof binary not found at {PROVE_BINARY}" |
| Binary non-zero exit | 500 | "Proof generation failed: {stderr excerpt}" |
| Proof timeout | N/A | Stored in record as error |
| Tree JSON not found | 400 | "Merkle tree not found for doc_id: {doc_id}" |

---

## 8. What Does NOT Change

| Component | Change |
|-----------|--------|
| `api_server.py` | New imports only — no endpoint changes in this PRD |
| `prove` binary | No changes |
| `witness.rs` | No changes |
| `prove.rs` | No changes |
| `pipeline_g.py` | No changes |
| Qdrant payload format | No changes |

---

## 9. Test Plan

### 9.1 Unit test: `test_proof_result_dataclass`
Instantiate `ProofResult` with all fields, serialize to JSON, verify round-trip.

### 9.2 Unit test: `test_build_proof_input_valid`
Call `build_proof_input()` with valid data, verify output dict has correct structure.

### 9.3 Unit test: `test_build_proof_input_invalid_merkle_root`
Call with empty merkle_root string, assert `ValueError`.

### 9.4 Unit test: `test_build_proof_input_zero_siblings`
Call with a chunk with 0 siblings, assert `ValueError`.

### 9.5 Unit test: `test_load_tree_json_not_found`
Call with a non-existent doc_id, assert `FileNotFoundError`.

### 9.6 Integration test: `test_generate_zk_proof_timeout`
Submit proof with very short timeout (1s), verify `ProofResult` with `status="error"` and timeout message.

### 9.7 Integration test: `test_submit_and_poll`
Call `submit_proof_task()` → get `proof_id` → poll `get_proof_status()` → verify status transitions `pending` → `generated` or `error`.

---

## 10. Acceptance Criteria

- [ ] `python3 -c "from shared.zk_bridge import generate_zk_proof, ProofResult; print('import ok')"` succeeds
- [ ] `python3 -m py_compile $REPO_DIR/shared/zk_bridge.py` exits 0
- [ ] Unit tests for `build_proof_input` validation (valid, invalid merkle_root, invalid siblings) — each raises correct error
- [ ] `test_load_tree_json_not_found` raises `FileNotFoundError`
- [ ] `test_submit_and_poll` — proof transitions from `pending` to `generated` (or `error`) within 5 minutes
- [ ] `test_generate_zk_proof_timeout` returns `status="error"` with timeout message
- [ ] Semaphore at capacity returns `503` on `POST /api/prove`
- [ ] All temp files deleted after proof completes (input and output)
- [ ] Completed proofs are cleaned from memory after 1 hour
