# SECTION-api-server-01: RAG Query API Server

**Parent:** PROJ.md
**Status:** In Discussion — decisions made, PRD being finalized
**Date:** 2026-04-22 (updated 2026-04-22)

This section covers the design, implementation, and deployment of the RAG Query API Server.

---

## Current Status

**Status (2026-04-22):** Local dev server fully working ✅

Website served from git-managed source (`$REPO_DIR/website/`) via OpenResty on port 80. All API endpoints verified working:
- `GET /` → HTML with correct local API URLs ✅
- `POST /api/query` (vector + hybrid) ✅
- `GET /api/images/{doc_id}/{page}` ✅
- `GET /api/context` ✅
- CORS middleware added to `api_server.py` ✅
- Auth: `apiKey = null` for local dev (conditional headers) ✅
- Stale hardcoded doc count removed ✅

**OpenResty config fixes (2026-04-22):**
- Fixed `military-manuals-local.conf`: `root` → `$REPO_DIR/website/`, removed invalid `more_clear_headers`
- Fixed `rag-api.conf`: commented out broken `access_by_lua_block` (was causing 2026-04-14 failure)
- Fixed nginx worker permissions: `chmod o+x /home/youruser` + `$REPO_DIR`
- Reloaded via `sudo systemctl reload openresty`

**Problem resolved — website path migration:**
- Old: `$DATA_DIR/website/docs/` (not git-managed, stale)
- New: `$REPO_DIR/website/` (git-managed, canonical)
- Old site archived: `/data/archive/military-documents-website-20260422_081104`

1. **Embedding service** (`shared/embedding_service.py`, port 8200) ✅
   - Qwen/Qwen3-Embedding-0.6B loads eagerly at startup — no 15s first-query penalty
   - Bounded semaphore: max 2 concurrent encodes (controls active memory)
   - `GET /health` → `{"status":"ok","model":"Qwen/Qwen3-Embedding-0.6B","concurrent_limit":2}`
   - `POST /encode` → returns 1024-dim vectors
   - systemd unit: `embedding-service.service` with `MemoryMax=8G`

2. **RAG API updated** (`shared/api_server.py`, port 8100) ✅
   - Replaced local `load_embedding_model()` with httpx calls to embedding service (port 8200)
   - Connection-pooled: `httpx.AsyncClient(max_connections=10)`
   - `health` endpoint now checks embedding service reachability
   - BM25 + vector + RRF all working end-to-end

3. **Tested end-to-end** ✅
   - `GET /health` → ✅
   - `POST /api/query` (vector) → ✅ (2 results, score 0.57)
   - `POST /api/query` (hybrid) → ✅ (2 results, Merkle metadata present)

4. **Pipeline G batch run — 2026-04-17** ✅
   - 14/14 docs ingested successfully
   - All 19 emitted docs now in Qdrant across 4 collections: army (8), navy (6), marines (2), other (3)
   - BM25 index built incrementally
   - Registry updated to `status: "ingested"` for all 19

5. **Bug fixed: scroll_filter=** ✅
   - Two `client.scroll()` calls in `api_server.py` used `filter=` param — qdrant_client in venv expects `scroll_filter=`
   - Fixed and restarted `zk-rag-api.service`

6. **Local dev server — 2026-04-17** ✅
   - Website copied from VPS: `$DATA_DIR/website/docs/`
   - OpenResty configured to serve static site + proxy `/api/` → port 8100
   - Website `index.html` updated: hardcoded `militarymanuals.ai` URLs → relative paths, collection `army-docs` → `navy`
   - Full stack tested: `GET /` → HTML, `POST /api/query` → results, `GET /api/context` → chunk windows

---

**[2026-04-17] Design decision: Two-process architecture**

Embedding service split from RAG API server to enable memory control on the VPS:

- **Embedding service** (port 8200) — owns the model, bounded semaphore for concurrency, `MemoryMax=` hard limit via systemd
- **RAG API server** (port 8100) — calls embedding service over HTTP, does BM25 + RRF, serves results

Rationale: VPS is memory-limited. The embedding model must stay warm but needs hard memory guardrails. Separate processes = independent scaling + independent OOM handling.

**PRD:** `docs/PRDs/PRD-api-server-01-query-server.md`

---

## Resolved Decisions

All open items resolved 2026-04-16.

| Item | Decision | Rationale |
|------|----------|-----------|
| OpenResty proxy (local dev) | Bypass OpenResty; API server binds directly to `127.0.0.1:8100` | Local dev machine has no OpenResty. Clients hit `http://192.168.1.x:8100` directly over HTTP. Production adds OpenResty + HTTPS + auth at the VPS layer. |
| Deployment method | systemd unit | Available on both machines, straightforward logging via `journalctl`, restart-on-failure, no extra tooling |
| CORS | None — handle at proxy layer if needed | API server sits behind OpenResty; no browser-based local dev access anticipated |
| BM25 index build | Inside Pipeline G batch, incremental pickle | Pipeline G already has all chunk text loaded; natural place to build index without a separate process |
| Pipeline G metadata enrichment | Add `category`, `doc_type`, `source`, `file_size_bytes` to Qdrant payload | Useful for filtering and display; all fields already exist in registry, just need to add to `build_payload()` |

### BM25 Implementation Details
- Library: `rank_bm25`
- Index file: `$DATA_DIR/bm25_index.pkl`
- Build at end of each Pipeline G batch run
- Incremental: load existing index → append new docs → re-pickle (do not rebuild from scratch)

### Pipeline G Payload Enrichment
Add these four fields to `build_payload()` in `pipeline_g.py`:

| Field | Source in registry |
|-------|-------------------|
| `category` | `entry.get("category", "")` |
| `doc_type` | `entry.get("doc_type", "")` |
| `source` | `entry.get("source", "")` |
| `file_size_bytes` | `entry.get("file_size_bytes")` |

---

## Architecture Principle

The pipeline and the API server are completely decoupled. Pipelines A-G are offline batch processes that write to Qdrant and exit. The API server is a long-running service that only reads from Qdrant and serves queries. The only shared state is Qdrant itself.

```
[User/Agent]
    ↓ HTTPS
[OpenResty]  ← website + API gateway (rate limit, geo-filter, auth) — production only
    ↓ proxy_pass
[API Server]  ← lightweight service: query Qdrant, return results
    ↓
[Qdrant]     ← vector data (produced by pipeline)
```

**Local dev:** API server binds directly to `127.0.0.1:8100`, no OpenResty in path.
**VPS (production):** OpenResty handles HTTPS + auth, proxies to API server.

---

## Phases

### Phase 1 — Two-Process API Server (Local Dev)

**Goal:** Get the two-process API server working locally, prove query/response, verify ZK-ready metadata in responses. VPS deployment deferred until after ZK proof integration.

**Architecture decisions (resolved):**
- Embedding service (port 8200) + RAG API server (port 8100)
- Embedding service: bounded semaphore + systemd `MemoryMax=` for memory control
- RAG API: calls embedding service over HTTP, connection-pooled via httpx
- Both services: systemd units, copied to VPS together

**Steps:**

**Sub-projects:**

- [x] **A — Embedding Service** (`shared/embedding_service.py` + `embedding-service.service`)
  - [x] Write FastAPI app that owns the embedding model, loads on startup (eager)
  - [x] `POST /encode` — accepts `{"texts": [str]}` → returns embedding vectors
  - [x] Bounded semaphore: max 2 concurrent encode requests (controls memory under load)
  - [x] Write systemd unit with `MemoryMax=` (8G on R730, TBD for VPS)
  - [x] Start service, verify model loads at startup (check logs)
  - [x] Test: hit `/encode` with a query, verify response

- [x] **B — RAG API update** (`shared/api_server.py`)
  - [x] Remove local `embedding.py` loading — replace with httpx call to embedding service
  - [x] Add connection pooling: `httpx.AsyncClient` with limited max connections
  - [x] Update all `encode_fn = load_embedding_model()` → HTTP call to port 8200
  - [x] Restart `zk-rag-api.service`, test `POST /api/query` end-to-end
  - [x] Verify hybrid search still works (BM25 + vector + RRF)

- [x] **C — Batch Pipeline G** (verify existing build still works after splitting)
  - [x] Run `pipeline_g.py` batch on remaining stranded docs — 14/14 ingested 2026-04-17
  - [x] Verify BM25 index built correctly
  - [x] Verify new metadata fields (`category`, `doc_type`, `source`, `file_size_bytes`) in Qdrant

- [ ] **D — Deploy to VPS**
  - [ ] Copy both systemd units + `embedding_service.py` + updated `api_server.py` to VPS
  - [ ] Set `MemoryMax=` on embedding service based on VPS available RAM
  - [ ] Configure OpenResty to proxy HTTPS → port 8100 (RAG API)
  - [ ] Point DNS, verify SSL
  - [ ] Cut over production traffic

- [x] **D-local — Local dev server** (2026-04-17)
  - [x] Website copied from VPS to `$DATA_DIR/website/docs/`
  - [x] OpenResty configured: serves static site + proxies `/api/` → port 8100
  - [x] Website `index.html` updated for local API paths
  - [x] Local dev stack tested and verified working

- [x] **E — ZK Metadata Verification** (2026-04-17)
  - [x] Verify API response includes Merkle tree metadata (root, depth, doc_id) needed for ZK proof generation
  - [x] Fix `scroll_filter=` bug — all scroll operations now use correct qdrant_client parameter
  - [x] `/api/context` endpoint working — returns chunk text + Merkle proof data

- [ ] **F — VPS Deployment** (deferred — after ZK proof integration)
  - [ ] Copy both systemd units + `embedding_service.py` + updated `api_server.py` to VPS
  - [ ] Set `MemoryMax=` on embedding service based on VPS available RAM
  - [ ] Configure OpenResty to proxy HTTPS → port 8100 (RAG API)
  - [ ] Point DNS, verify SSL
  - [ ] Cut over production traffic

**Phase 1 Verification:**
- `GET /health` → `{"status":"ok","qdrant":"connected","model":"...","bm25":"loaded"}` ✅
- `POST /api/query` (vector) → results with Merkle metadata ✅
- `POST /api/query` (hybrid) → results ✅
- `GET /api/collections` → lists all 4 collections ✅
- `GET /api/context` → chunk window + Merkle proof data ✅
- Local website at `http://127.0.0.1/` → HTML, queries local API ✅

### Phase 2 — ZK Proof of Provenance

**Status: ✅ DONE — Two-button search design implemented 2026-04-23**
**Nav+ZK fixes: ✅ DONE 2026-04-26**

The ZK provenance flow was refactored into a clean two-button search design:

**Architecture (2026-04-23):**
- **"Search"** button → `POST /api/query` → plain RAG results, no ZK UI
- **"Search with Provenance"** button → `POST /api/query-provable` → RAG results + ZK proof buttons immediately visible (no generating phase)
- ZK proofs are generated **atomically** within `query-provable` — chunk text is only returned if the ZK proof succeeds. Failed chunks are dropped.
- `query-provable` already enforces proof-first at line 1180+ of `api_server.py`

**Removed endpoints (2026-04-23):**
- `POST /api/provenance/generate` — old two-step generate flow
- `GET /api/provenance/{chunk_id}` — old stateful retrieval
- `GET /api/provenance/manifest` — unused
- `GET /api/query_stats` — unused
- `GET /api/provenance/{chunk_id}/status` — old stateful polling
- `_provenance_jobs`, `_provenance_lock` — server-side state removed

**New endpoints (2026-04-23):**
- `POST /api/provenance/submit` — accepts `{ proof_hex, public_inputs_hex, vk_hex }`, submits to Kurier, returns `{ job_id }` immediately (stateless)
- `GET /api/provenance/status/{job_id}` — polls Kurier, returns `{ job_id, status, verified, message, explorer_url }`

**Website changes (2026-04-23):**
- Two search buttons: "Search" (blue) and "Search with Provenance" (amber)
- Provenance search: ZK proof buttons appear immediately on result cards (no async generation)
- "Verify on Chain" → POSTs to `/provenance/submit` → polls `/provenance/status/{job_id}` → enables Results button
- "Results" button: grayed out while `pending`, clickable when Kurier verification completes
- `fireGenerateAll()` and `_generateAndRender()` removed — no longer needed

**Kurier flow:**
1. Website POSTs `{ proof_hex, public_inputs_hex, vk_hex }` to `/provenance/submit`
2. Kurier returns `{ job_id }` immediately (non-blocking)
3. Website polls `/provenance/status/{job_id}` every 2s
4. When status != "pending", Results button enables
5. User clicks Results → modal shows `{ verified, status, message, explorer_url }`

**Canonical reference:** `website/index.html` (search + ZK button logic), `shared/api_server.py` (new endpoints)

**Nav+ZK fixes (2026-04-26):** All three issues resolved:
1. **Verify-on-chain fires from nav cards** — `buildPassageCard` was dispatching `zk-verify` CustomEvents nothing caught; fixed by calling `window._verifyOnChain(chunkId)` directly from submenu handlers (commit `c93af48`)
2. **IA document links in nav cards** — added "View on page N" and "📄 View full document" links to nav card rendering; document title link CSS fixed (commits `6819cc9`, `fbbd7f8`)
3. **Auto-submit to Kurier on proof generation** — `AUTO_SUBMIT_PROOF = true` in `app.js` line ~193; Playwright confirmed end-to-end Kurier job submission (commits `8a44c1d`, `10fb2b4`)

---

### Phase 3 — X402 Payment for Information (after ZK + VPS)

**Goal:** Charge for API access using X402 (Stripe's HTTP payment protocol).

**Steps:**
- [ ] Add X402 payment header handling to OpenResty
- [ ] Configure payment-gated endpoints
- [ ] Link subscription/credits to API key or wallet
- [ ] Free tier: limited queries/day. Paid tier: unlimited.
