# ZK-RAG Serving Improvements — Ruff-Inspired

## What's NOT happening
- BGE embedding model — decided against it; everything is embedded with Qwen
- Monorepo crate split — wrong language (Python vs Rust)
- Major architectural refactor of api_server.py

---

## Phase 1 — Low Risk, No API Changes

### 1. Parallel collection stats scroll
**File:** `shared/api_server.py`, `_get_collection_stats`

**Current:** Sequential loop over collections, calling Qdrant one at a time.

**Change:** Replace sequential loop with `ThreadPoolExecutor` + `as_completed`. Each collection is independent — parallelize safely.

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _get_collection_stats(collection_name: str) -> dict:
    # ... existing cache check ...

    def _fetch_one(name: str) -> tuple[str, dict]:
        client = QdrantClient.from_config(...)
        info = client.get_collection(name)
        # ... build stats dict ...
        return (name, stats)

    with ThreadPoolExecutor(max_workers=min(len(collections), 4)) as executor:
        futures = {executor.submit(_fetch_one, c) for c in collections}
        for future in as_completed(futures):
            name, stats = future.result()
            all_stats[name] = stats
```

Ruff's pattern: `min(num_cpus, 4)` worker cap prevents oversubscription.

---

### 2. Embedding service worker cap
**File:** `embedding_service.py`

**Current:** Configurable workers, default 1.

**Change:** Apply Ruff's `min(num_cpus, 4)` cap to the default. Workers are already configurable — just set a smarter default.

---

## Phase 2 — New Feature, Medium Effort ✅ (Completed 2026-04-25)

### 3. Content-addressed query result cache ✅
**File:** `shared/api_server.py`

**Implemented:** In-memory cache with 5-minute TTL (not disk-backed as originally specced).
- Cache key: `SHA256(json.dumps({query, collection, top_k, embedding_model}, sort_keys=True))`
- `_make_cache_key(query, collection, top_k, embedding_model)` → 64-char hex
- `_query_cache_get(key)` → `(bool, list|None)` — bool=True on hit
- `_query_cache_set(key, results, collection)` — stores results + collection metadata
- `_query_cache_invalidate(collection)` → removes all entries for that collection
- `DELETE /api/cache/query` endpoint — invalidates by collection or all
- Cache check happens **before** embedding service call (short-circuits both embedding + Qdrant)
- Cache store happens **after** ZK proofs are generated (results already include zk_proof)
- `_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"` constant used in cache key
- Tests: `tests/test_query_cache.py` — 13 tests, all pass

**Cache key composition:**
```python
_cache_key = SHA256(json.dumps({
    "query": "armor doctrine",
    "collection": "army",
    "top_k": 5,
    "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
}, sort_keys=True))
```

---

### 4. Auto-invalidate cache from pipelines ✅
**Files:** `pipeline_g/pipeline_g.py`, `pipeline_g/__init__.py`

**Implemented:** After successful Qdrant upsert in `main()` loop, call `DELETE /api/cache/query?collection=<branch>`.
- `_invalidate_query_cache_for_collection(collection)` — calls API server with httpx
- `_API_BASE = "http://127.0.0.1:8100"`
- `_QUERY_CACHE_INVALIDATE_ENDPOINT = "/api/cache/query"`
- Only called when `success=True` and `not args.dry_run` (dry-run skips invalidation)
- Pipeline F (EVM emit) does NOT write to Qdrant — no invalidation needed
- Tests: `pipeline_g/tests/test_cache_invalidation.py` — 4 tests, all pass

---

## Phase 3 — Future (Out of Scope for Now)

- Full `rag_engine/` extraction — pure query logic, no HTTP
- Monorepo crate split — Python project, not applicable
- Incremental document updates via LSP-style `textDocument/didChange` pattern
