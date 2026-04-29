# PRD-zk-circuit-02c: ZK Proof API Endpoints — Wiring Phase C

**Status:** DRAFT — Ready for review
**Author:** Fred
**Date:** 2026-04-19
**Parent:** SECTION-zk-circuit-02-implementation.md (Phase C)

---

## 1. Problem Statement

Phase B (`zk_bridge.py`) produced a working Python module with `submit_proof_task()`, `get_proof_status()`, `list_proofs()`, and supporting dataclasses. The module is correct and fully tested.

The RAG API server (`api_server.py`) is the HTTP entry point for the entire system. Phase C wires three proof endpoints into it so clients can:
1. Submit a proof generation task and get an immediate `proof_id` (non-blocking)
2. Poll for the result of an in-flight proof
3. List all in-flight proofs with their statuses and ages

This is the last piece before Phase D (limb validation against real Qdrant chunks) and Phase E (full E2E on R730).

---

## 2. Design Decisions

### 2.1 Where This Code Lives

**File to modify:** `$REPO_DIR/shared/api_server.py`

**No new files created** — all code is added to `api_server.py`.

### 2.2 Import Path

`zk_bridge.py` lives at `shared/zk_bridge.py`. `api_server.py` lives at `shared/api_server.py`. The correct import is:

```python
from zk_bridge import (
    submit_proof_task,
    get_proof_status,
    list_proofs,
    ProofCapacityExceeded,
    ChunkInput,
    ProofRecord,
)
```

This is confirmed correct per PRD-zk-circuit-02b Section 2.1.

### 2.3 Pydantic Models

Three request/response models are needed:

```python
class ChunkInputAPI(BaseModel):
    """Chunk data from API request body."""
    doc_id: str
    text: str
    sorted_index: int
    siblings: list[str]  # 4 hex strings

class ProveRequest(BaseModel):
    """Request body for POST /api/prove."""
    chunks: list[ChunkInputAPI]
    merkle_root: str  # single hex string
    llm_input_text: str
    llm_output_text: str

class ProveResponse(BaseModel):
    """Response for POST /api/prove (202 Accepted)."""
    proof_id: str
    status: str  # "pending"
    poll_url: str

class ProofStatusResponse(BaseModel):
    """Response for GET /api/proof/{proof_id}."""
    proof_id: str
    status: str  # "pending" | "generated" | "error"
    public_inputs: Optional[dict] = None
    circuit_data: Optional[str] = None
    verifier_data: Optional[str] = None
    error: Optional[str] = None

class ProofListItem(BaseModel):
    """Single item in GET /api/proofs list."""
    proof_id: str
    status: str
    age_seconds: float

class ProofListResponse(BaseModel):
    """Response for GET /api/proofs."""
    count: int
    proofs: list[ProofListItem]
```

### 2.4 Exception Handling

`ProofCapacityExceeded` from `zk_bridge` must be caught and converted to HTTP 503:

```python
from zk_bridge import ProofCapacityExceeded

@app.exception_handler(ProofCapacityExceeded)
async def proof_capacity_handler(request, exc):
    return JSONResponse(
        status_code=503,
        content={"error": "Proof generation capacity reached. Try again shortly."}
    )
```

### 2.5 Error Responses

| Error | HTTP Status | Body |
|-------|-------------|------|
| Malformed / missing fields | 400 | `{"detail": "..."}` |
| Semaphore at capacity | 503 | `{"error": "Proof generation capacity reached. Try again shortly."}` |
| Proof not found | 404 | `{"detail": "Proof not found: {proof_id}"}` |

### 2.6 Endpoint Summaries

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/prove` | Submit proof generation task |
| GET | `/api/proof/{proof_id}` | Poll for proof result |
| GET | `/api/proofs` | List all in-flight proofs |

---

## 3. API Contract

### 3.1 POST /api/prove

**Purpose:** Start an async ZK proof generation task. Returns immediately with a `proof_id`.

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
  "proof_id": "a1b2c3d4e5f67890abcdEF1234567890",
  "status": "pending",
  "poll_url": "/api/proof/a1b2c3d4e5f67890abcdEF1234567890"
}
```

**Validation rules:**
- `chunks` must be non-empty
- `merkle_root` must be a non-empty hex string
- Each chunk's `siblings` must have exactly 4 entries
- `text` must be non-empty string

**Error responses:**
- `400 Bad Request` — malformed input, missing fields, empty chunks array
- `503 Service Unavailable` — proof generation capacity reached

---

### 3.2 GET /api/proof/{proof_id}

**Purpose:** Poll for proof generation result.

**Response (pending):**
```json
{
  "proof_id": "a1b2c3d4e5f67890abcdEF1234567890",
  "status": "pending"
}
```

**Response (generated):**
```json
{
  "proof_id": "a1b2c3d4e5f67890abcdEF1234567890",
  "status": "generated",
  "public_inputs": {
    "cap": ["0x...", "..."],
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
  "proof_id": "a1b2c3d4e5f67890abcdEF1234567890",
  "status": "error",
  "error": "Proof generation timed out after 600s"
}
```

**Error responses:**
- `404 Not Found` — proof_id not found (expired, never existed, or server restarted)

---

### 3.3 GET /api/proofs

**Purpose:** List in-flight proof IDs and their statuses. Useful for debugging.

**Response (200 OK):**
```json
{
  "count": 2,
  "proofs": [
    {
      "proof_id": "a1b2c3d4e5f67890abcdEF1234567890",
      "status": "pending",
      "age_seconds": 12.3
    },
    {
      "proof_id": "e5f6a7b8c9d0011223344556677889900",
      "status": "generated",
      "age_seconds": 145.7
    }
  ]
}
```

---

## 4. Implementation Details

### 4.1 Where to Add Each Endpoint

All three endpoints go into `api_server.py` after the existing endpoints (after `get_query_stats`, before `if __name__ == "__main__"`).

### 4.2 POST /api/prove — Implementation

```python
@app.post("/api/prove", response_model=ProveResponse, status_code=202)
async def prove(request: ProveRequest):
    """Submit a ZK proof generation task.
    
    Returns immediately with a proof_id. Poll GET /api/proof/{proof_id}
    for the result. Maximum 2 concurrent proofs; excess returns 503.
    """
    try:
        # Convert API chunk model to zk_bridge ChunkInput
        chunks = [
            ChunkInput(
                doc_id=c.doc_id,
                text=c.text,
                sorted_index=c.sorted_index,
                siblings=c.siblings,
            )
            for c in request.chunks
        ]
        
        proof_id = await submit_proof_task(
            chunks=chunks,
            merkle_root=request.merkle_root,
            llm_input_text=request.llm_input_text,
            llm_output_text=request.llm_output_text,
        )
        
        return ProveResponse(
            proof_id=proof_id,
            status="pending",
            poll_url=f"/api/proof/{proof_id}",
        )
    except ProofCapacityExceeded:
        raise HTTPException(
            status_code=503,
            detail="Proof generation capacity reached. Try again shortly.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

### 4.3 GET /api/proof/{proof_id} — Implementation

```python
@app.get("/api/proof/{proof_id}", response_model=ProofStatusResponse)
async def get_proof(proof_id: str):
    """Poll for the status of a ZK proof generation task.
    
    Returns pending, generated (with proof data), or error.
    Raises 404 if the proof_id is not found.
    """
    try:
        record = get_proof_status(proof_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Proof not found: {proof_id}",
        )
    
    response = ProofStatusResponse(
        proof_id=record.proof_id,
        status=record.status,
    )
    
    if record.result is not None:
        response.public_inputs = record.result.public_inputs
        response.circuit_data = record.result.circuit_data
        response.verifier_data = record.result.verifier_data
    
    if record.error is not None:
        response.error = record.error
    
    return response
```

### 4.4 GET /api/proofs — Implementation

```python
@app.get("/api/proofs", response_model=ProofListResponse)
async def list_proofs_endpoint():
    """List all in-flight proof generation tasks.
    
    Returns proofs sorted oldest-first with their current status
    and age in seconds since creation.
    """
    import time
    records = list_proofs()
    now = time.time()
    
    return ProofListResponse(
        count=len(records),
        proofs=[
            ProofListItem(
                proof_id=r.proof_id,
                status=r.status,
                age_seconds=round(now - r.created_at, 1),
            )
            for r in records
        ],
    )
```

### 4.5 Exception Handler Registration

Register before the route definitions:

```python
from fastapi import HTTPException
from fastapi.responses import JSONResponse

@app.exception_handler(ProofCapacityExceeded)
async def proof_capacity_handler(request, exc):
    return JSONResponse(
        status_code=503,
        content={"error": "Proof generation capacity reached. Try again shortly."}
    )
```

### 4.6 OpenAPI Manifest Updates

Update `app.openapi()` section in `get_openapi_json()` to include the three new endpoints. Add to the `paths` dict:

```json
"/api/prove": {
  "post": {
    "summary": "Submit ZK proof generation task",
    "requestBody": { ... },
    "responses": {
      "202": { "description": "Proof task accepted" },
      "400": { "description": "Invalid request" },
      "503": { "description": "Capacity reached" }
    }
  }
},
"/api/proof/{proof_id}": {
  "get": {
    "summary": "Get proof status",
    "parameters": [{ "name": "proof_id", "in": "path", "required": true }],
    "responses": {
      "200": { "description": "Proof status" },
      "404": { "description": "Not found" }
    }
  }
},
"/api/proofs": {
  "get": {
    "summary": "List all in-flight proofs",
    "responses": { "200": { "description": "Proof list" } }
  }
}
```

Also update `/api/manifest` endpoint description to include the new routes.

---

## 5. Data Flow

```
Client POST /api/prove
    │
    ▼
api_server.py::prove()
    │
    ├─ Validate request body (Pydantic)
    │
    ├─ Convert ProveRequest → list[ChunkInput]
    │
    ├─ await submit_proof_task()  ──► zk_bridge.py
    │                                    │
    │    [semaphore check]               │
    │    ├─ Acquire slot (or 503)       │
    │    ├─ Store ProofRecord(pending)   │
    │    └─ Spawn asyncio.create_task()   │
    │                                    │
    │    Background task:                │
    │    └─ _run_proof() → generate_zk_proof()
    │           │
    │           └─ subprocess.run(prove binary)
    │
    ▼
return ProveResponse(proof_id, status="pending", poll_url)
    │
    ▼
Client polls GET /api/proof/{proof_id}
    │
    ▼
get_proof_status(proof_id) → ProofRecord
    │
    ▼
return ProofStatusResponse(...)
```

---

## 6. What Does NOT Change

| Component | Change |
|-----------|--------|
| `zk_bridge.py` | No changes — imports only |
| `prove` binary | No changes |
| Existing API endpoints | No changes |
| Qdrant payload format | No changes |
| BM25 / embedding logic | No changes |
| `pipeline_g.py` | No changes |

---

## 7. Test Plan

### 7.1 New Tests

**File:** `shared/tests/test_zk_api.py` (create new)

```python
# Tests for POST /api/prove, GET /api/proof/{proof_id}, GET /api/proofs
# Uses FastAPI TestClient
```

| Test | What it verifies |
|------|----------------|
| `test_prove_endpoint_202` | Valid request → 202 + proof_id + poll_url |
| `test_prove_endpoint_400_invalid_chunks` | Empty chunks → 400 |
| `test_prove_endpoint_400_invalid_cap` | Wrong cap size → 400 |
| `test_prove_endpoint_503_capacity` | Semaphore full → 503 |
| `test_proof_endpoint_404` | Unknown proof_id → 404 |
| `test_proof_endpoint_pending` | Pending proof → status=pending |
| `test_proofs_endpoint_list` | Lists in-flight proofs with age |

### 7.2 Integration

The existing `test_integration_zk_bridge.py` tests `zk_bridge.py` at the module level. The new `test_zk_api.py` tests the HTTP layer on top of it.

---

## 8. Acceptance Criteria

- [ ] `POST /api/prove` with valid body returns 202 and a `proof_id`
- [ ] `POST /api/prove` with empty `chunks` returns 400
- [ ] `POST /api/prove` with empty `merkle_root` returns 400
- [ ] `POST /api/prove` when semaphore is full returns 503
- [ ] `GET /api/proof/{id}` for unknown ID returns 404
- [ ] `GET /api/proof/{id}` for pending proof returns `status: "pending"`
- [ ] `GET /api/proofs` returns list with `count` and `proofs` array, each with `proof_id`, `status`, `age_seconds`
- [ ] `GET /api/manifest` includes the three new endpoints
- [ ] `GET /api/openapi.json` includes the three new endpoints
- [ ] `python3 -m py_compile shared/api_server.py` exits 0
- [ ] All existing API tests still pass

---

## 9. Dependencies

No new dependencies. All required imports are already available:
- `fastapi` / `pydantic` — already used in `api_server.py`
- `asyncio` — already used in `api_server.py`
- `time` — stdlib

---

## 10. File Inventory

| File | Action |
|------|--------|
| `shared/api_server.py` | Modify — add Pydantic models, exception handler, and 3 endpoints |
| `shared/tests/test_zk_api.py` | Create — new test file for API endpoints |
