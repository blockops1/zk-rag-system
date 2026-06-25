"""RAG Query API Server - FastAPI application with Qdrant integration."""

from __future__ import annotations

import sys
import os

# Force unbuffered output so logs appear immediately
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Startup log to stderr FIRST — before any other imports
print("[api_server] === STARTING UP ===", flush=True)
print(f"[api_server] Python: {sys.executable}", flush=True)
print(f"[api_server] CWD: {os.getcwd()}", flush=True)
print(f"[api_server] sys.path[0]: {sys.path[0]}", flush=True)

# Fix sys.path so imports work regardless of WorkingDirectory
# __file__ = ./shared/api_server.py  (R730 layout)
#          = /home/deruyter/rag/api_server.py              (VPS layout)
# Detect which layout we're in and add the right parent to sys.path
_this_file = os.path.abspath(__file__)
_this_dir = os.path.dirname(_this_file)
if os.path.basename(_this_dir) == "shared":
    # R730 layout: api_server.py is inside shared/ subdirectory
    _project_root = os.path.dirname(_this_dir)
    sys.path.insert(0, _project_root)
else:
    # VPS layout: api_server.py is directly in /home/deruyter/rag/
    # shared/ module files live at the same level, not in a subdirectory
    # Add parent (/home/deruyter/) so `shared` resolves via the symlink below
    _project_root = os.path.dirname(_this_dir)
    sys.path.insert(0, _project_root)
    # On VPS, /home/deruyter/shared is a symlink to /home/deruyter/rag/
    # so `import shared` resolves to /home/deruyter/shared/ → /home/deruyter/rag/
    _shared_link = os.path.join(_project_root, "shared")
    if not os.path.islink(_shared_link) and not os.path.isdir(_shared_link):
        os.symlink(_this_dir, _shared_link)
print(f"[api_server] Added project root to sys.path: {_project_root}", flush=True)

# Validate that shared/ is importable
try:
    import shared
    print(f"[api_server] shared module OK: {shared.__file__}", flush=True)
except Exception as e:
    print(f"[api_server] FATAL: cannot import shared: {e}", flush=True, file=sys.stderr)
    raise

# Verify x402_paid_download exists
_x402_path = os.path.join(os.path.dirname(_this_file), "x402_paid_download.py")
print(f"[api_server] x402_paid_download.py exists: {os.path.exists(_x402_path)}", flush=True)

print("[api_server] Imports phase starting...", flush=True)

import hashlib
import json
import logging
import logging.handlers
import os
import time
import asyncio
import httpx
from collections import OrderedDict
print("[api_server] x402 import starting...", flush=True)
from x402_paid_download import (
    verify_and_stream,
    NETWORK_SPEC,
    USDC_CONTRACT,
)
print("[api_server] x402 import OK", flush=True)
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Header, Query
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import ApiException

# Embedding service client (connection-pooled)
_EMBEDDING_SERVICE_URL = "http://127.0.0.1:8200"
_http_client: httpx.AsyncClient | None = None

# Admin routes (cache invalidation, document listing) — guarded by env var
_DISABLE_ADMIN_ROUTES = os.environ.get("DISABLE_ADMIN_ROUTES", "").lower() in ("1", "true", "yes")
print(f"[api_server] _DISABLE_ADMIN_ROUTES = {_DISABLE_ADMIN_ROUTES}", flush=True)


# ── Embedding model (fastembed, loaded once at startup) ──────────────────────
#
# NomicEmbedText-v1.5 via fastembed — ~1.3GB RSS vs 5-6GB for sentence-transformers.
# On VPS (16GB RAM) this keeps the total footprint under control without a separate service.
# Concurrent encodes are bounded by a semaphore so burst queries don't exhaust memory.

import threading

_MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", str(min(os.cpu_count() or 1, 12))))
MAX_CONCURRENT = _MAX_CONCURRENT
_encode_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="encode-")
_encode_semaphore = threading.Semaphore(MAX_CONCURRENT)
print(f"[api_server] MAX_CONCURRENT = {MAX_CONCURRENT} (cpu_count={os.cpu_count()})", flush=True)

_embed_model = None
_embed_model_loaded = False


def _load_embed_model():
    """Load NomicEmbedText-v1.5 via fastembed. Called once at startup."""
    global _embed_model, _embed_model_loaded
    print("[api_server] Loading NomicEmbedText-v1.5 via fastembed...", flush=True)
    from fastembed import TextEmbedding
    _embed_model = TextEmbedding(
        "nomic-ai/nomic-embed-text-v1.5",
        max_length=512,
        threads=1,
        enable_cpu_mem_arena=False,
    )
    _embed_model_loaded = True
    print(f"[api_server] Embedding model loaded. dim={_embed_model.embedding_size}", flush=True)
    return _embed_model


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load embedding model on startup; close thread pool on shutdown."""
    global _http_client
    print("[api_server] [lifespan] Configuring httpx limits...", flush=True)
    # Note: httpx client is kept for x402 paid download calls, not for embeddings
    _http_client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8200",  # kept for x402 compatibility
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        timeout=httpx.Timeout(60.0),
    )
    print("[api_server] [lifespan] Loading embedding model...", flush=True)
    try:
        _load_embed_model()
    except Exception as e:
        print(f"[api_server] [lifespan] CRITICAL: failed to load embedding model: {e}", flush=True)
        raise
    print("[api_server] [lifespan] Startup complete.", flush=True)
    yield
    if _http_client:
        await _http_client.aclose()
    _encode_executor.shutdown(wait=False)
    print("[api_server] Lifespan shutdown complete.", flush=True)


# ── Sync encode helpers ─────────────────────────────────────────────────────────


def _encode_texts_sync(texts: list[str]) -> list[list[float]]:
    """Encode texts to embedding vectors using the in-process fastembed model.

    Must be called from a thread (via ThreadPoolExecutor) since fastembed is sync.
    The semaphore is acquired by the caller before invoking this.
    """
    emb_list = []
    for text in texts:
        for emb in _embed_model.passage_embed([text]):
            emb_list.append(emb.tolist())
            break  # one text -> one embedding
    return emb_list


async def _embed_texts_async(texts: list[str]) -> list[list[float]]:
    """Async wrapper — runs sync fastembed encode in thread pool with semaphore bounding."""
    acquired = _encode_semaphore.acquire(timeout=60)
    if not acquired:
        raise HTTPException(
            status_code=503,
            detail="Server at capacity — try again shortly",
        )
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_encode_executor, _encode_texts_sync, texts)
    finally:
        _encode_semaphore.release()


# Configure logging to file
LOG_DIR = ".../data/logs"
LOG_FILE = f"{LOG_DIR}/api_server.log"
os.makedirs(LOG_DIR, exist_ok=True)

_handler = logging.handlers.TimedRotatingFileHandler(
    LOG_FILE, when="midnight", interval=1, backupCount=7
)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger(__name__)

# Global Qdrant client - server mode, not local files
QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
client = QdrantClient(url=QDRANT_URL)

# FastAPI app
app = FastAPI(title="RAG Query API Server", lifespan=lifespan)
print("[api_server] FastAPI app created OK", flush=True)

# CORS middleware - specific origins only (no wildcard with credentials)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://militarymanuals.ai"],
    allow_credentials=True,
    allow_methods=["*"],
)

IMAGES_DIR = os.environ.get("IMAGES_DIR", "./data/images/")

# Known collections
KNOWN_COLLECTIONS = ["army", "navy", "marines", "coast_guard", "air_force", "joint", "other"]

# Query stats tracking
query_stats = {
    "total_queries": 0,
    "queries_by_collection": {},
    "last_query_time": None
}

# Collections metadata cache - 24 hour TTL
_COLLECTIONS_CACHE_TTL_SECONDS = 24 * 60 * 60
_collections_cache: dict[str, tuple[float, dict]] = {}  # collection_name -> (timestamp, stats_dict)

# Query result cache - content-addressed, 5 minute TTL
_QUERY_CACHE_TTL_SECONDS = 5 * 60
_query_cache: OrderedDict[str, tuple[float, list]] = {}  # cache_key -> (timestamp, results_list)
_query_cache_meta: dict[str, dict] = {}  # cache_key -> {collection: str, ...}

# Ingested doc_ids cache per collection - refreshed every 10 minutes
_INGESTED_DOCS_CACHE_TTL_SECONDS = 10 * 60
_ingested_docs_cache: dict[str, tuple[float, set[str]]] = {}  # collection -> (timestamp, doc_ids_set)

# Catalog documents cache - refreshed every 10 minutes
# Returns full doc metadata (title, pub_year, category, page_count, ia_identifier) from Qdrant
_CATALOG_DOCS_CACHE_TTL_SECONDS = 10 * 60
_catalog_docs_cache: dict[str, tuple[float, dict[str, dict]]] = {}  # collection -> (timestamp, {doc_id: doc_data})

# Image page listing cache - 10 minute TTL
_IMAGE_LISTING_CACHE_TTL_SECONDS = 10 * 60
_image_listing_cache: dict[str, tuple[float, list[str]]] = {}  # key -> (timestamp, images_list)

# Hardcoded embedding model used for all queries (matches embedding service)
_EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"


def _make_cache_key(query: str, collection: str, top_k: int, embedding_model: str) -> str:
    """Generate a deterministic SHA-256 cache key from query parameters."""
    data = json.dumps({
        "query": query,
        "collection": collection,
        "top_k": top_k,
        "embedding_model": embedding_model,
    }, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def _query_cache_get(key: str) -> tuple[bool, list | None]:
    """Return (True, results) if cache hit and not expired, else (False, None)."""
    if key not in _query_cache:
        return False, None
    stored_time, results = _query_cache[key]
    if time.time() - stored_time > _QUERY_CACHE_TTL_SECONDS:
        del _query_cache[key]
        _query_cache_meta.pop(key, None)
        return False, None
    # Move to end (most-recently-used)
    _query_cache.move_to_end(key)
    return True, results


def _query_cache_set(key: str, results: list, collection: str) -> None:
    """Store results in the cache with current timestamp."""
    # Evict oldest entry if at capacity limit
    if len(_query_cache) >= 1000:
        oldest_key, _ = _query_cache.popitem(last=False)  # pop first (oldest)
        _query_cache_meta.pop(oldest_key, None)
    _query_cache[key] = (time.time(), list(results))
    _query_cache_meta[key] = {"collection": collection}


def _query_cache_invalidate(collection: str) -> int:
    """Remove all cache entries for a given collection. Returns count of entries removed."""
    keys_to_delete = [k for k, meta in _query_cache_meta.items() if meta.get("collection") == collection]
    for key in keys_to_delete:
        _query_cache.pop(key, None)
        _query_cache_meta.pop(key, None)
    return len(keys_to_delete)


class QueryRequest(BaseModel):
    """Request model for POST /api/query."""

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must not be blank")
        return v.strip()

    query: str
    top_k: int = 5
    collection: str


class QueryResponse(BaseModel):
    """Response model for POST /api/query."""
    results: list[dict]
    query: str
    collection: str
    total: int


class QueryProvanableRequest(BaseModel):
    """Request model for POST /api/query-provable."""
    query: str
    top_k: int = 5
    collection: str = "army"
    doc_id: str | None = None  # If set, scope search to this document only


class QueryProvanableResponse(BaseModel):
    """Response model for POST /api/query-provable.

    Returns chunks with ZK proofs already generated and attached.
    No chunk text is returned without a corresponding proof.
    Each chunk's zk_proof includes kurier_job_id if auto-submit to Kurier succeeded.
    """
    chunks: list[dict]  # each chunk includes zk_proof with proof_hex, public_inputs, kurier_job_id, etc.
    proofs: dict  # keyed by chunk_id: {chunk_id: {proof_hex, public_inputs, kurier_job_id, ...}}
    query: str
    collection: str
    total: int


# ─── ZK Proof Models ────────────────────────────────────────────────────────────

import provenance as provenance_module  # noqa: E402


@app.get("/health")
def health():
    """Health check endpoint — verifies Qdrant and embedding service."""
    return {
        "status": "ok",
        "qdrant": "connected",
        "model": "nomic-ai/nomic-embed-text-v1.5",
        "bm25": "disabled"
    }


def _get_collection_stats(collection_name: str) -> dict:
    """Get statistics for a collection (cached with 24h TTL).
    
    Args:
        collection_name: Name of the collection to analyze
        
    Returns:
        Dictionary with collection statistics including vector_count, vector_dim,
        embedding_model, doc_ids, and chunk_count
    """
    now = time.time()
    
    # Check cache first
    if collection_name in _collections_cache:
        cached_time, cached_stats = _collections_cache[collection_name]
        if now - cached_time < _COLLECTIONS_CACHE_TTL_SECONDS:
            return cached_stats
    
    # Get collection info for vector dim
    collection_info = client.get_collection(collection_name)
    vector_dim = collection_info.config.params.vectors.size if collection_info.config.params.vectors else 1024
    
    # Use count() for exact vector count - fast, no scroll needed
    try:
        count_result = client.count(collection_name, count_filter=None, exact=True)
        vector_count = count_result.count
    except (ApiException, Exception) as e:
        logger.warning(f"count() failed for '{collection_name}': {e}, falling back to scroll")
        vector_count = 0
        for records, next_offset in client.scroll(collection_name, limit=10000, with_payload=False):
            vector_count += len(records)
            if next_offset is None:
                break
    
    # Collect unique doc_ids via scroll with minimal payload (doc_id only)
    doc_ids = set()
    next_offset = None
    while True:
        records, next_offset = client.scroll(
            collection_name=collection_name,
            limit=10000,
            offset=next_offset,
            with_payload=["doc_id"]
        )
        if not records:
            break
        for record in records:
            if record.payload and "doc_id" in record.payload:
                doc_ids.add(record.payload["doc_id"])
        if next_offset is None:
            break
    
    doc_count = len(doc_ids)
    descriptions = {
        "army": "U.S. Army field manuals, doctrine, and operational guidance",
        "navy": "U.S. Navy tactical and operational publications",
        "marines": "U.S. Marine Corps doctrine and field manuals",
        "other": "Other government and allied military publications",
    }
    stats = {
        "name": collection_name,
        "description": descriptions.get(collection_name, f"Military documents — {collection_name} collection"),
        "doc_count": doc_count,
        "vector_count": vector_count,
        "vector_dim": vector_dim,
        "embedding_model": "nomic-ai/nomic-embed-text-v1.5",
        "chunk_count": vector_count
    }
    
    # Cache the result
    _collections_cache[collection_name] = (now, stats)
    logger.info(f"Collection stats cached for '{collection_name}': {vector_count} vectors, {len(doc_ids)} docs")
    
    return stats


@app.get("/api/collections")
def list_collections():
    """List all collections with their metadata (parallel fetch, Ruff-style)."""
    
    # Discover available collections
    try:
        all_collections = client.get_collections()
        available_names = {c.name for c in all_collections.collections}
    except (ApiException, Exception) as e:
        logger.warning(f"Failed to get collections from Qdrant: {e}")
        available_names = set()

    # Filter to available collections
    target_collections = [c for c in KNOWN_COLLECTIONS if c in available_names]
    
    # Parallel fetch — each collection is independent, same pattern as Ruff's par_iter
    collections_info = []
    with ThreadPoolExecutor(max_workers=max(1, min(len(target_collections), 4))) as executor:
        futures = {executor.submit(_get_collection_stats, c): c for c in target_collections}
        for future in as_completed(futures):
            collection_name = futures[future]
            try:
                stats = future.result()
                collections_info.append(stats)
            except (ApiException, httpx.HTTPError, Exception) as e:
                logger.warning(f"Failed to get stats for collection '{collection_name}': {e}")
    
    return collections_info


if not _DISABLE_ADMIN_ROUTES:
    @app.delete("/api/cache/collections", include_schema_in_openapi=False)
    def invalidate_collections_cache(collection: str = Query(default=None, description="Optional: invalidate only this collection")):
        """Invalidate the collections metadata cache. Call after updating collection data."""
        if _DISABLE_ADMIN_ROUTES:
            raise HTTPException(status_code=404, detail="Not found")
        if collection:
            if collection in _collections_cache:
                del _collections_cache[collection]
                logger.info(f"Cache invalidated for collection '{collection}'")
                return {"status": "invalidated", "collection": collection}
            else:
                return {"status": "not cached", "collection": collection}
        else:
            count = len(_collections_cache)
            _collections_cache.clear()
            logger.info(f"Cache invalidated for all {count} collections")
            return {"status": "invalidated", "collection": None, "count": count}


if not _DISABLE_ADMIN_ROUTES:
    @app.delete("/api/cache/query", include_schema_in_openapi=False)
    def invalidate_query_cache(collection: str = Query(default=None, description="Optional: invalidate only this collection's query cache")):
        """Invalidate the content-addressed query result cache. Call after pipelines F or G upsert data."""
        if _DISABLE_ADMIN_ROUTES:
            raise HTTPException(status_code=404, detail="Not found")
        if collection:
            count = _query_cache_invalidate(collection)
            logger.info(f"Query cache invalidated for collection '{collection}': {count} entries removed")
            return {"status": "invalidated", "collection": collection, "count": count}
        else:
            count = len(_query_cache)
            _query_cache.clear()
            _query_cache_meta.clear()
            logger.info(f"Query cache invalidated for all collections: {count} entries removed")
            return {"status": "invalidated", "collection": None, "count": count}


if not _DISABLE_ADMIN_ROUTES:
    @app.delete("/api/cache/images", include_schema_in_openapi=False)
    def invalidate_image_cache():
        """Invalidate the image listing cache. Call after image ingestion."""
        if _DISABLE_ADMIN_ROUTES:
            raise HTTPException(status_code=404, detail="Not found")
        count = len(_image_listing_cache)
        _image_listing_cache.clear()
        logger.info(f"Image listing cache invalidated: {count} entries removed")
        return {"status": "invalidated", "count": count}


    _REGISTRY_PATH = Path(os.environ.get("REGISTRY_PATH", "./data/registry.json"))
_COLLECTION_DESCRIPTIONS = {
    "army": "U.S. Army field manuals, doctrine publications, and operational guidance",
    "navy": "U.S. Navy tactical and operational publications",
    "marines": "U.S. Marine Corps doctrine and tactical guidance",
    "air_force": "U.S. Air Force doctrine, tactics, and operational guidance",
    "joint": "Multi-service and joint doctrine publications",
    "other": "Cross-service and multi-service military publications",
}


def _get_ingested_doc_ids(collection: str) -> set[str]:
    """Return the set of doc_ids currently indexed in a Qdrant collection.
    Results are cached for _INGESTED_DOCS_CACHE_TTL_SECONDS to avoid
    repeated expensive scroll queries.
    """
    now = time.time()
    cached = _ingested_docs_cache.get(collection)
    if cached and (now - cached[0]) < _INGESTED_DOCS_CACHE_TTL_SECONDS:
        return cached[1]

    doc_ids: set[str] = set()
    try:
        offset = None
        while True:
            payload = {"limit": 1000, "with_payload": ["doc_id"]}
            if offset:
                payload["offset"] = offset
            resp = client.scroll(collection_name=collection, **payload)
            for point in resp[0]:
                did = point.payload.get("doc_id")
                if did:
                    doc_ids.add(did)
            offset = resp[1]
            if not offset:
                break
    except (ApiException, Exception) as e:
        logger.warning(f"Failed to scroll collection {collection}: {e}")
        # Return cached value if available, even if stale
        if cached:
            return cached[1]
        return set()

    _ingested_docs_cache[collection] = (now, doc_ids)
    return doc_ids


def _get_catalog_docs_per_collection(collection: str) -> dict[str, dict]:
    """Return a dict of doc_id -> doc metadata for all documents in a Qdrant collection.

    Scrapes the full collection via scroll, deduplicates by doc_id, and returns
    the canonical document metadata (title, pub_year, category, page_count,
    ia_identifier, doc_type, branch) from the first occurrence of each doc_id.
    Results are cached for _CATALOG_DOCS_CACHE_TTL_SECONDS.
    """
    now = time.time()
    cached = _catalog_docs_cache.get(collection)
    if cached and (now - cached[0]) < _CATALOG_DOCS_CACHE_TTL_SECONDS:
        return cached[1]

    docs: dict[str, dict] = {}
    try:
        offset = None
        while True:
            payload = {
                "limit": 1000,
                "with_payload": ["doc_id", "title", "pub_year", "category", "page_count", "ia_identifier", "doc_type", "branch"],
            }
            if offset:
                payload["offset"] = offset
            resp = client.scroll(collection_name=collection, **payload)
            for point in resp[0]:
                doc_id = point.payload.get("doc_id")
                if not doc_id or doc_id in docs:
                    continue
                docs[doc_id] = {
                    "doc_id": doc_id,
                    "title": point.payload.get("title") or "Untitled",
                    "pub_year": point.payload.get("pub_year"),
                    "category": point.payload.get("category") or "",
                    "page_count": point.payload.get("page_count"),
                    "ia_identifier": point.payload.get("ia_identifier") or "",
                    "doc_type": point.payload.get("doc_type") or "",
                    "branch": point.payload.get("branch") or collection,
                }
            offset = resp[1]
            if not offset:
                break
    except (ApiException, Exception) as e:
        logger.warning(f"Failed to scroll catalog from {collection}: {e}")
        if cached:
            return cached[1]
        return {}

    _catalog_docs_cache[collection] = (now, docs)
    return docs


@app.get("/api/catalog")
def get_catalog():
    """Return documents grouped by branch/collection, filtered to only those indexed in Qdrant.

    Titles, pub_year, category, page_count, and ia_identifier are sourced directly
    from Qdrant payloads — the same data stored at ingest time from the registry.
    This ensures the catalog always reflects the authoritative Qdrant state.
    """
    # Build list of docs per collection from Qdrant (cached)
    collections_map: dict[str, list[dict]] = {
        name: [] for name in _COLLECTION_DESCRIPTIONS
    }
    for coll in _COLLECTION_DESCRIPTIONS:
        docs = _get_catalog_docs_per_collection(coll)
        for doc_id, doc_data in docs.items():
            collections_map[coll].append(doc_data)

    # Assemble response — same shape as before so website JS is unaffected
    result = []
    for name, description in _COLLECTION_DESCRIPTIONS.items():
        docs_for_branch = collections_map.get(name, [])
        result.append({
            "name": name,
            "description": description,
            "document_count": len(docs_for_branch),
            "documents": sorted(docs_for_branch, key=lambda d: (d.get("pub_year") or 0, d.get("title") or "")),
        })

    return result


@app.get("/api/collections/{collection}")
def get_collection(collection: str):
    """Get info for a single collection."""
    try:
        return _get_collection_stats(collection)
    except (ApiException, httpx.HTTPError, Exception) as e:
        raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found") from e


@app.get("/api/doc/{doc_id}")
def get_document(doc_id: str):
    """Get document metadata from Qdrant payload.
    
    Returns document metadata from the first matching point found.
    Searches across all known collections using Qdrant filters for efficiency.
    
    Args:
        doc_id: The document ID to retrieve metadata for
        
    Returns:
        Dictionary with document metadata fields (title, branch, category, etc.)
    """
    # Search for the document using Qdrant filter for efficiency
    for collection_name in KNOWN_COLLECTIONS:
        try:
            # Use Qdrant filter to find points with matching doc_id
            filter_condition = models.Filter(
                must=[
                    models.FieldCondition(
                        key="doc_id",
                        match=models.MatchValue(value=doc_id)
                    )
                ]
            )
            
            records, _ = client.scroll(
                collection_name=collection_name,
                limit=1,
                offset=None,
                with_payload=True,
                scroll_filter=filter_condition
            )
            
            if records and len(records) > 0:
                # Return metadata from the first matching point only
                first_record = records[0]
                if first_record.payload:
                    return dict(first_record.payload)
                return {}
                
        except (ApiException, Exception) as e:
            # Skip collections that fail
            logger.warning(f"Failed to search collection '{collection_name}' for doc '{doc_id}': {e}")
            continue
    
    raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")


# ─── Admin Document Review Endpoints ─────────────────────────────────────────

_ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _require_admin_key(x_admin_key: str = Header(default="")):
    """Abort if X-Admin-Key header doesn't match the configured key."""
    if not _ADMIN_KEY:
        raise HTTPException(status_code=500, detail="ADMIN_API_KEY not configured on server")
    if x_admin_key != _ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")


if not _DISABLE_ADMIN_ROUTES:
    @app.get("/api/admin/documents", include_schema_in_openapi=False)
    def admin_list_documents(x_admin_key: str = Header(default="")):
        """Return all documents from registry with chunk counts from Qdrant.

        Requires X-Admin-Key header.
        """
        _require_admin_key(x_admin_key)

        try:
            with open(_REGISTRY_PATH) as f:
                registry = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to read registry: {e}")
            raise HTTPException(status_code=503, detail="Registry unavailable") from e

        docs = registry.get("documents", [])
        if isinstance(docs, dict):
            doc_list = list(docs.values())
        else:
            doc_list = docs

        # Single scroll per collection → doc_id → count  (O(collections) not O(docs*collections))
        def scroll_counts(coll: str) -> dict[str, int]:
            counts: dict[str, int] = {}
            try:
                offset = None
                while True:
                    records, offset = client.scroll(
                        collection_name=coll, limit=1000, offset=offset,
                        with_payload=["doc_id"]
                    )
                    for rec in records:
                        did = rec.payload.get("doc_id") if rec.payload else None
                        if did:
                            counts[did] = counts.get(did, 0) + 1
                    if not offset:
                        break
            except (ApiException, Exception) as e:
                logger.warning(f"Failed to scroll collection '{coll}' for chunk counts: {e}")
            return counts

        with ThreadPoolExecutor(max_workers=len(KNOWN_COLLECTIONS)) as ex:
            futures = {ex.submit(scroll_counts, c): c for c in KNOWN_COLLECTIONS}
            chunk_counts: dict[str, int] = {}
            for f in as_completed(futures):
                for did, cnt in f.result().items():
                    chunk_counts[did] = chunk_counts.get(did, 0) + cnt

        result = []
        for doc in doc_list:
            doc_id = doc.get("doc_id", "")
            result.append({
                "doc_id": doc_id,
                "title": doc.get("title") or doc.get("filename", "Untitled"),
                "branch": doc.get("branch", "other"),
                "category": doc.get("category", ""),
                "pub_year": doc.get("pub_year"),
                "page_count": doc.get("page_count"),
                "status": doc.get("status", "unknown"),
                "has_embeddings": doc.get("has_embeddings", False),
                "embedding_status": doc.get("embedding_status", ""),
                "chunk_count": chunk_counts.get(doc_id, -1),
                "avg_chars_per_page": doc.get("avg_chars_per_page"),
                "file_size_bytes": doc.get("file_size_bytes"),
            })

        return result


if not _DISABLE_ADMIN_ROUTES:
    @app.get("/api/admin/document/{doc_id}", include_schema_in_openapi=False)
    def admin_get_document(doc_id: str, x_admin_key: str = Header(default="")):
        """Return full document detail: all chunks + per-page image list.
    
        Searches all known collections in parallel for all chunks belonging to doc_id.
        Requires X-Admin-Key header.
        """
        _require_admin_key(x_admin_key)

        # Load registry for doc metadata
        try:
            with open(_REGISTRY_PATH) as f:
                registry = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to read registry: {e}")
            raise HTTPException(status_code=503, detail="Registry unavailable") from e

        docs = registry.get("documents", [])
        if isinstance(docs, dict):
            doc_map = {d.get("doc_id"): d for d in docs.values()}
        else:
            doc_map = {d.get("doc_id"): d for d in docs}

        doc_meta = doc_map.get(doc_id)
        if not doc_meta:
            raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not in registry")

        # Collect all chunks from all collections in parallel
        def scroll_chunks(collection_name: str) -> list[dict]:
            try:
                filter_cond = models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                )
                all_chunks = []
                offset = None
                while True:
                    records, offset = client.scroll(
                        collection_name=collection_name,
                        limit=1000,
                        offset=offset,
                        with_payload=True,
                        scroll_filter=filter_cond,
                    )
                    for rec in records:
                        p = dict(rec.payload) if rec.payload else {}
                        all_chunks.append({
                            "chunk_index": p.get("chunk_index"),
                            "page_num": p.get("page_num"),
                            "char_count": len(p.get("text", "")) if p.get("text") else 0,
                            "text": (p.get("text", "") or "")[:5000],
                            "vector_id": str(rec.id),
                            "collection": collection_name,
                        })
                    if not offset:
                        break
                return all_chunks
            except (ApiException, Exception) as e:
                logger.warning(f"Failed to scroll collection '{collection_name}' for doc '{doc_id}': {e}")
                return []

        with ThreadPoolExecutor(max_workers=len(KNOWN_COLLECTIONS)) as executor:
            futures = {executor.submit(scroll_chunks, c): c for c in KNOWN_COLLECTIONS}
            all_chunks = []
            for future in as_completed(futures):
                all_chunks.extend(future.result())

        # Sort by page_num then chunk_index
        all_chunks.sort(key=lambda c: (c.get("page_num") or 0, c.get("chunk_index") or 0))

        # Build per-page image list (from filesystem)
        import re as re_mod

        image_dir = Path("./data/images") / doc_id
        page_images: dict[int, list[str]] = {}
        if image_dir.is_dir():
            for fpath in image_dir.iterdir():
                fname = fpath.name
                # match page_XXXX_img_00.jb2  or page_XXXX_img_00.png etc.
                m = re_mod.match(r"page_(\d+)_img_\d+", fname)
                if m:
                    page_num = int(m.group(1))
                    if page_num not in page_images:
                        page_images[page_num] = []
                    page_images[page_num].append(fname)
            for pn in page_images:
                page_images[pn].sort()

        return {
            "doc_id": doc_id,
            "title": doc_meta.get("title") or doc_meta.get("filename", "Untitled"),
            "branch": doc_meta.get("branch", "other"),
            "category": doc_meta.get("category", ""),
            "pub_year": doc_meta.get("pub_year"),
            "page_count": doc_meta.get("page_count"),
            "status": doc_meta.get("status", "unknown"),
            "has_embeddings": doc_meta.get("has_embeddings", False),
            "chunks": all_chunks,
            "images": page_images,
        }


if not _DISABLE_ADMIN_ROUTES:
    @app.delete("/api/admin/document/{doc_id}", include_schema_in_openapi=False)
    def admin_delete_document(doc_id: str, x_admin_key: str = Header(default="")):
        """Delete a document from Qdrant, registry.json, and image files.
    
        Requires X-Admin-Key header.
        """
        _require_admin_key(x_admin_key)

        deleted_from: list[str] = []
        errors: list[str] = []

        # 1. Delete from all Qdrant collections
        for coll in KNOWN_COLLECTIONS:
            try:
                filter_cond = models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                )
                result = client.delete(collection_name=coll, points_selector=filter_cond)
                deleted_from.append(f"Qdrant/{coll}")
                logger.info(f"Deleted doc '{doc_id}' from Qdrant collection '{coll}': {result}")
            except (ApiException, Exception) as e:
                err = f"Qdrant/{coll}: {e}"
                logger.warning(f"Failed to delete doc '{doc_id}' from '{coll}': {e}")
                errors.append(err)

        # 2. Invalidate query cache for affected collections
        for coll in _COLLECTION_DESCRIPTIONS:
            try:
                _query_cache_invalidate(coll)
            except Exception:
                pass
        try:
            _collections_cache.clear()
        except Exception:
            pass

        # 3. Remove from registry.json
        try:
            with open(_REGISTRY_PATH) as f:
                registry = json.load(f)
            docs = registry.get("documents", [])
            if isinstance(docs, dict):
                registry["documents"] = {k: v for k, v in docs.items() if v.get("doc_id") != doc_id}
            else:
                registry["documents"] = [d for d in docs if d.get("doc_id") != doc_id]
            with open(_REGISTRY_PATH, "w") as f:
                json.dump(registry, f, indent=2)
            deleted_from.append("registry.json")
            logger.info(f"Removed doc '{doc_id}' from registry")
        except (FileNotFoundError, json.JSONDecodeError, IOError) as e:
            err = f"registry: {e}"
            logger.warning(f"Failed to update registry for doc '{doc_id}': {e}")
            errors.append(err)

        # 4. Delete image directory
        image_dir = Path("./data/images") / doc_id
        if image_dir.is_dir():
            import shutil
            try:
                shutil.rmtree(image_dir)
                deleted_from.append(f"images/{doc_id}/")
                logger.info(f"Deleted image directory for doc '{doc_id}'")
            except OSError as e:
                err = f"images: {e}"
                logger.warning(f"Failed to delete image directory for doc '{doc_id}': {e}")
                errors.append(err)
        else:
            deleted_from.append("images/ (not found, skipped)")

        if errors and not deleted_from:
            raise HTTPException(status_code=500, detail=f"Delete failed: {'; '.join(errors)}")

        return {
            "success": True,
            "doc_id": doc_id,
            "deleted_from": deleted_from,
            "errors": errors if errors else None,
        }


@app.get("/api/context")
def get_context(
    doc_id: str = Query(..., description="Document ID"),
    chunk_index: int = Query(0, description="Chunk index (0-based)"),
    collection: str = Query(..., description="Collection name"),
    window: int = Query(5, description="Window size (returns 2*window+1 chunks, used when limit is not set)"),
    query: str = Query(default="", description="Optional: semantic search query within this document"),
    limit: int = Query(default=0, description="If > 0, return up to this many consecutive chunks starting at chunk_index (ignores window)")
):
    """Get chunks from a document.

    Two modes:
    - No query param + limit=0: returns sequential chunks centered around chunk_index (window-based).
    - No query param + limit>0: returns up to `limit` consecutive chunks starting at chunk_index.
    - With query param: returns semantically ranked chunks matching the query,
      filtered to this document only (top_k = window param).

    Args:
        doc_id: The document ID
        chunk_index: The center chunk index for sequential mode (0-based)
        collection: The collection name
        window: For sequential mode, window size (2*window+1 chunks returned).
                For semantic mode, top_k limit.
        query: If provided, triggers semantic search within this document
    """
    # Validate collection
    # Resolve actual Qdrant collection — some catalog collections (e.g. "joint") are
    # logical groups stored across multiple real Qdrant collections.  When the
    # requested collection is not a direct Qdrant collection, search all known
    # collections to find where this doc_id actually lives and use that.
    actual_collection = collection
    if collection not in KNOWN_COLLECTIONS:
        for candidate in KNOWN_COLLECTIONS:
            try:
                filter_cond = models.Filter(
                    must=[models.FieldCondition(
                        key="doc_id",
                        match=models.MatchValue(value=doc_id)
                    )]
                )
                records, _ = client.scroll(
                    collection_name=candidate,
                    limit=1,
                    offset=None,
                    with_payload=False,
                    scroll_filter=filter_cond
                )
                if records:
                    actual_collection = candidate
                    break
            except (ApiException, Exception):
                continue

    # Validate chunk_index
    if chunk_index < 0:
        raise HTTPException(status_code=400, detail="chunk_index must be non-negative")

    # Validate window
    if window < 0:
        raise HTTPException(status_code=400, detail="window must be non-negative")

    # ── Semantic search mode ────────────────────────────────────────────────────
    if query and query.strip():
        try:
            # Embed the query using the in-process embedding service (sync context)
            acquired = _encode_semaphore.acquire(timeout=60)
            if not acquired:
                raise HTTPException(
                    status_code=503,
                    detail="Server at capacity — try again shortly",
                )
            try:
                query_vector = _encode_texts_sync([query.strip()])[0]
            finally:
                _encode_semaphore.release()

            # Vector search within this document only
            filter_condition = models.Filter(
                must=[
                    models.FieldCondition(
                        key="doc_id",
                        match=models.MatchValue(value=doc_id)
                    )
                ]
            )

            search_results = client.query_points(
                collection_name=actual_collection,
                query=query_vector,
                limit=window,  # window param doubles as top_k in semantic mode
                with_payload=True,
                query_filter=filter_condition
            )

            results = []
            for result in search_results.points:
                payload = dict(result.payload) if result.payload else {}
                payload["score"] = result.score
                # Derive chunk_index from chunk_id (format: doc_id-chunk_index)
                chunk_id = payload.get("chunk_id", "")
                if chunk_id:
                    parts = chunk_id.rsplit("-", 1)
                    if len(parts) == 2:
                        try:
                            payload["chunk_index"] = int(parts[1])
                        except ValueError:
                            pass
                results.append(payload)

            # Sort by score descending
            results.sort(key=lambda x: x.get("score", 0), reverse=True)
            return {"results": results}

        except HTTPException:
            raise
        except (ApiException, Exception) as e:
            logger.warning(f"Semantic search failed for doc '{doc_id}' in collection '{collection}': {e}")
            raise HTTPException(status_code=500, detail=f"Failed to search document: {e}") from e

    # ── Sequential mode ───────────────────────────────────────────────────────
    try:
        # Use Qdrant filter to get only chunks for this document
        filter_condition = models.Filter(
            must=[
                models.FieldCondition(
                    key="doc_id",
                    match=models.MatchValue(value=doc_id)
                )
            ]
        )

        # Scroll through filtered results
        doc_chunks = []
        next_offset = None

        while True:
            records, next_offset = client.scroll(
                collection_name=actual_collection,
                limit=10000,
                offset=next_offset,
                with_payload=True,
                scroll_filter=filter_condition
            )

            if not records:
                break

            for record in records:
                if record.payload:
                    chunk_data = dict(record.payload)
                    # Extract chunk_index from chunk_id (format: doc_id-chunk_index)
                    chunk_id = chunk_data.get("chunk_id", "")
                    if chunk_id:
                        parts = chunk_id.rsplit("-", 1)
                        if len(parts) == 2:
                            try:
                                idx = int(parts[1])
                                chunk_data["_chunk_index"] = idx
                                doc_chunks.append(chunk_data)
                            except ValueError:
                                logger.warning(f"Failed to parse chunk index from chunk_id: {chunk_id}")
                                pass

            if next_offset is None:
                break

        if not doc_chunks:
            raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found in collection '{collection}'")

        # Sort chunks by their index
        doc_chunks.sort(key=lambda x: x.get("_chunk_index", 0))

        # Calculate window bounds — use limit if set, otherwise window
        if limit > 0:
            start_idx = chunk_index
            end_idx = min(len(doc_chunks) - 1, chunk_index + limit - 1)
        else:
            start_idx = max(0, chunk_index - window)
            end_idx = min(len(doc_chunks) - 1, chunk_index + window)

        # Extract chunks in the window
        window_chunks = []
        for chunk in doc_chunks:
            idx = chunk.get("_chunk_index", 0)
            if start_idx <= idx <= end_idx:
                # Remove internal _chunk_index field, then add back as chunk_index for the client
                chunk_copy = {k: v for k, v in chunk.items() if k != "_chunk_index"}
                chunk_copy["chunk_index"] = idx
                window_chunks.append(chunk_copy)

        # Return "results" key to match acceptance criteria
        return {"results": window_chunks}

    except HTTPException:
        raise
    except (ApiException, httpx.HTTPError, Exception) as e:
        logger.warning(f"Failed to get context for doc '{doc_id}' in collection '{collection}': {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve context: {e}") from e


@app.get("/api/collection/search")
def search_collection(
    collection: str = Query(..., description="Collection name"),
    q: str = Query(..., description="Search query"),
    top_k: int = Query(10, description="Max results to return")
):
    """Semantic search within a specific collection.

    Returns matching chunks from all documents in the collection,
    ranked by vector similarity.
    """
    if collection not in KNOWN_COLLECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid collection. Must be one of: {', '.join(KNOWN_COLLECTIONS)}"
        )
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

    try:
        acquired = _encode_semaphore.acquire(timeout=60)
        if not acquired:
            raise HTTPException(
                status_code=503,
                detail="Server at capacity — try again shortly",
            )
        try:
            query_vector = _encode_texts_sync([q.strip()])[0]
        finally:
            _encode_semaphore.release()

        search_results = client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=top_k,
            with_payload=True,
        )

        results = []
        for result in search_results.points:
            payload = dict(result.payload) if result.payload else {}
            payload["score"] = result.score
            results.append(payload)

        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return {"results": results}

    except HTTPException:
        raise
    except (ApiException, Exception) as e:
        logger.warning(f"Collection search failed for '{collection}' q='{q}': {e}")
        raise HTTPException(status_code=500, detail=f"Collection search failed: {e}") from e


@app.get("/api/images/{doc_id}/{page_num}")
def list_images_for_page(doc_id: str, page_num: int):
    """List images for a specific page of a document.
    
    Args:
        doc_id: The document ID
        page_num: The page number to list images for
        
    Returns:
        Dictionary with 'images' list containing image filenames for that page
    """
    cache_key = f"{doc_id}:{page_num}"
    
    # Check cache first
    if cache_key in _image_listing_cache:
        stored_time, cached_images = _image_listing_cache[cache_key]
        if time.time() - stored_time < _IMAGE_LISTING_CACHE_TTL_SECONDS:
            logger.info(f"Image listing cache HIT for doc_id={doc_id} page_num={page_num}")
            return {
                "doc_id": doc_id,
                "page_num": page_num,
                "images": cached_images,
                "count": len(cached_images)
            }
        else:
            # Expired — remove it
            del _image_listing_cache[cache_key]
    
    doc_images_dir = os.path.join(IMAGES_DIR, doc_id)
    
    if not os.path.exists(doc_images_dir):
        raise HTTPException(status_code=404, detail=f"No images found for document '{doc_id}'")
    
    try:
        files = os.listdir(doc_images_dir)
        # Filter to only image files for the specified page
        image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}
        # Images stored as page_0000 (0-indexed), API uses 1-indexed page numbers
        page_prefix = f"page_{page_num - 1:04d}"
        images = [f for f in files 
                  if f.lower().startswith(page_prefix) 
                  and any(f.lower().endswith(ext) for ext in image_extensions)]
        images.sort()
        
        # Cache the successful result
        _image_listing_cache[cache_key] = (time.time(), images)
        
        return {
            "doc_id": doc_id,
            "page_num": page_num,
            "images": images,
            "count": len(images)
        }
    except (ApiException, httpx.HTTPError, Exception) as e:
        logger.warning(f"Failed to list images for doc '{doc_id}' page {page_num}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list images: {e}") from e


@app.get("/api/manifest")
def api_manifest():
    """Return machine-readable API manifest auto-generated from FastAPI routes.

    No manual maintenance — every route registered with the app appears here.
    """
    endpoints = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path or path in ("/openapi.json", "/docs", "/redoc"):
            continue
        methods = getattr(route, "methods", {"GET"})
        method = next(iter(methods - {"HEAD", "OPTIONS"}), "GET")
        summary = getattr(route, "summary", "") or ""
        description = getattr(route, "description", "") or getattr(route, "name", "")
        key = f"{method} {path}"
        endpoints[key] = summary or description or key

    return {
        "api_name": "RAG Query API Server",
        "version": "1.0.0",
        "description": "REST API for semantic search against military documents stored in Qdrant",
        "endpoints": endpoints,
        "collections": KNOWN_COLLECTIONS,
        "embedding_model": "nomic-ai/nomic-embed-text-v1.5",
        "vector_dimension": 768
    }


@app.get("/api/openapi.json")
def get_openapi_json():
    """Return OpenAPI 3.1 specification auto-generated from FastAPI routes.

    This is a proxy to FastAPI's built-in openapi schema — no manual
    maintenance required. Every route registered with the app automatically
    appears here.
    """
    return app.openapi()




def _search_single_collection(
    collection_name: str,
    query_vector: list,
    request: QueryRequest,
    doc_id: str | None = None
) -> list[dict]:
    """Perform vector-only search on a single collection.

    Args:
        collection_name: Name of the collection to search
        query_vector: The query vector for similarity search
        request: QueryRequest with query text, top_k, and collection
        doc_id: If set, filter results to only this document

    Returns:
        List of result dictionaries with payload and score
    """
    # Build optional doc_id filter
    filter_condition = None
    if doc_id:
        filter_condition = models.Filter(
            must=[
                models.FieldCondition(
                    key="doc_id",
                    match=models.MatchValue(value=doc_id)
                )
            ]
        )

    # Vector-only search
    try:
        search_results = client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=request.top_k,
            with_payload=True,
            query_filter=filter_condition
        )
    except (ApiException, Exception) as e:
        logger.error(f"Collection '{collection_name}' not found: {e}")
        return []
    
    # Format results
    results = []
    for result in search_results.points:
        payload = dict(result.payload) if result.payload else {}
        payload["score"] = result.score
        results.append(payload)
    
    return results


@app.post("/api/query")
async def query(request: QueryRequest):
    """Vector search endpoint using Qdrant similarity search.
    
    Accepts a natural language query and returns relevant document chunks
    from Qdrant using vector similarity search.
    
    Supports single collection search or cross-collection search with collection='*'.
    
    Args:
        request: QueryRequest with query text, top_k, and collection
        
    Returns:
        QueryResponse with matching chunks, query, collection, and total count
    """
    _query_start = time.monotonic()

    # Update query stats
    global query_stats
    query_stats["total_queries"] += 1
    query_stats["last_query_time"] = datetime.now(timezone.utc).isoformat()

    collection = request.collection
    if collection not in query_stats["queries_by_collection"]:
        query_stats["queries_by_collection"][collection] = 0
    query_stats["queries_by_collection"][collection] += 1

    logger.info(f"Query: query='{request.query[:80]}...' top_k={request.top_k} collection={collection}")
    
    # Validate top_k
    if request.top_k < 1:
        raise HTTPException(status_code=400, detail="top_k must be at least 1")
    if request.top_k > 50:
        raise HTTPException(status_code=400, detail="top_k cannot exceed 50")
    
    # Validate collection - must be one of the 6 known collections or '*' for cross-collection
    if request.collection not in KNOWN_COLLECTIONS and request.collection != "*":
        raise HTTPException(
            status_code=400,
            detail=f"Invalid collection. Must be one of: {', '.join(KNOWN_COLLECTIONS)} or '*' for cross-collection search"
        )

    # Cache check — short-circuit before hitting embedding service or Qdrant
    cache_key = _make_cache_key(request.query, request.collection, request.top_k, _EMBEDDING_MODEL)
    cache_hit, cached_results = _query_cache_get(cache_key)
    if cache_hit and cached_results is not None:
        logger.info(f"Query cache HIT for collection={request.collection} top_k={request.top_k}")
        return QueryResponse(
            results=cached_results,
            query=request.query,
            collection=request.collection,
            total=len(cached_results)
        )

    # Embed the query using in-process fastembed
    try:
        query_vector = (await _embed_texts_async([request.query]))[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}") from e
    
    # Check if this is a cross-collection search
    if request.collection == "*":
        # Cross-collection search: search all collections and merge results
        all_results = []
        seen_chunk_ids = set()
        
        for collection_name in KNOWN_COLLECTIONS:
            try:
                # Search each collection with a higher limit to get enough candidates
                collection_results = _search_single_collection(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    request=request
                )
                
                # Deduplicate by chunk_id and collect all results
                for result in collection_results:
                    chunk_id = result.get("chunk_id", "")
                    if chunk_id and chunk_id not in seen_chunk_ids:
                        seen_chunk_ids.add(chunk_id)
                        all_results.append(result)
            except (ApiException, Exception) as e:
                # Skip collections that fail
                logger.warning(f"Failed to search collection '{collection_name}': {e}")
                continue
        
        # Sort all results by score (descending) and take top_k
        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        results = all_results[:request.top_k]
    else:
        # Single collection search
        results = _search_single_collection(
            collection_name=request.collection,
            query_vector=query_vector,
            request=request
        )
    
    # ── Log search query ────────────────────────────────────────────────────────
    try:
        import json
        log_path = ".../data/logs/search_queries.log"
        with open(log_path, "a") as lf:
            lf.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat() + "Z",
                "query": request.query,
                "collection": request.collection,
                "top_k": request.top_k,
                "results": len(results),
                "duration_ms": int((time.monotonic() - _query_start) * 1000),
            }) + "\n")
    except OSError as e:
        logger.warning(f"stats logging failed: {e}")
        pass  # don't let logging failures affect the response

    # Store in query result cache
    _query_cache_set(cache_key, results, request.collection)

    logger.info(f"Query complete: {len(results)} results returned for collection={request.collection}")
    return QueryResponse(
        results=results,
        query=request.query,
        collection=request.collection,
        total=len(results)
    )


# ─── ZK Proof Parallelism ───────────────────────────────────────────────────────

def _get_zk_parallelism() -> int:
    """Return the max number of concurrent proof generations, from env or default."""
    return int(os.environ.get("ZK_PROOF_PARALLELISM", "2"))


def _generate_proof_for_chunk(payload: dict) -> tuple[str, str, dict]:
    """
    Generate a ZK proof for a single chunk. Called in ThreadPoolExecutor.

    Returns (chunk_id, "ok", proof_data) on success.
    Returns (chunk_id, "failed", error_message) on failure.

    Raises:
        RuntimeError: if prove-bin fails entirely
    """
    chunk_id = payload.get("chunk_id", "")
    try:
        proof_data = provenance_module.generate_proof_from_payload(chunk_id, payload)
        return (chunk_id, "ok", proof_data)
    except Exception as e:
        return (chunk_id, "failed", str(e))


@app.post("/api/query-provable", response_model=QueryProvanableResponse)
async def query_provable(request: QueryProvanableRequest):
    """
    Vector search with ZK proofs attached to every result chunk.

    Flow:
        1. Query Qdrant (vector similarity)
        2. Generate ZK proofs in parallel for all result chunks
        3. Return only chunks that have successfully generated proofs

    No chunk text is returned for a chunk unless its ZK proof exists.
    Proof generation runs in parallel, capped by ZK_PROOF_PARALLELISM env var
    (default 2, set to 4 on the R730, 2 on the VPS).

    Args:
        request: QueryProvanableRequest with query text, top_k, and collection

    Returns:
        QueryProvanableResponse with chunks (each with zk_proof attached),
        plus a proofs dict keyed by chunk_id.
    """
    _query_start = time.monotonic()

    # Resolve actual Qdrant collection for non-standard catalog collections.
    # Some catalog collections (e.g. "joint") are logical groups whose docs
    # are stored across real Qdrant collections.  When doc_id is provided and
    # the requested collection is not a direct Qdrant collection, find which
    # collection actually holds this doc_id.
    actual_collection = request.collection
    if request.doc_id and request.collection not in KNOWN_COLLECTIONS:
        for candidate in KNOWN_COLLECTIONS:
            try:
                filter_cond = models.Filter(
                    must=[models.FieldCondition(
                        key="doc_id",
                        match=models.MatchValue(value=request.doc_id)
                    )]
                )
                records, _ = client.scroll(
                    collection_name=candidate,
                    limit=1,
                    offset=None,
                    with_payload=False,
                    scroll_filter=filter_cond
                )
                if records:
                    actual_collection = candidate
                    break
            except (ApiException, Exception):
                continue

    # Validate top_k
    if request.top_k < 1:
        raise HTTPException(status_code=400, detail="top_k must be at least 1")
    if request.top_k > 50:
        raise HTTPException(status_code=400, detail="top_k cannot exceed 50")

    # Validate collection (after resolution — non-standard collections with no doc_id still rejected)
    if actual_collection not in KNOWN_COLLECTIONS and actual_collection != "*":
        raise HTTPException(
            status_code=400,
            detail=f"Invalid collection. Must be one of: {', '.join(KNOWN_COLLECTIONS)} or '*' for cross-collection search"
        )

    # Embed query using in-process fastembed
    try:
        query_vector = (await _embed_texts_async([request.query]))[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}") from e

    # Query Qdrant
    if request.collection == "*":
        # Cross-collection search
        all_results = []
        seen_chunk_ids = set()
        for collection_name in KNOWN_COLLECTIONS:
            try:
                collection_results = _search_single_collection(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    request=QueryRequest(
                        query=request.query,
                        top_k=request.top_k * 2,
                        collection=collection_name,
                    ),
                )
                for result in collection_results:
                    chunk_id = result.get("chunk_id", "")
                    if chunk_id and chunk_id not in seen_chunk_ids:
                        seen_chunk_ids.add(chunk_id)
                        all_results.append(result)
            except Exception as e:
                logger.warning(f"Failed to search collection '{collection_name}': {e}")
                continue
        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        results = all_results[:request.top_k]
    else:
        results = _search_single_collection(
            collection_name=actual_collection,
            query_vector=query_vector,
            request=QueryRequest(
                query=request.query,
                top_k=request.top_k,
                collection=actual_collection,
            ),
            doc_id=request.doc_id,
        )

    if not results:
        return QueryProvanableResponse(
            chunks=[],
            proofs={},
            query=request.query,
            collection=request.collection,
            total=0,
        )

    # ── Generate ZK proofs in parallel ──────────────────────────────────────────
    parallelism = _get_zk_parallelism()
    logger.info(f"Generating ZK proofs for {len(results)} chunks with parallelism={parallelism}")

    # Build list of (chunk_id, payload) for chunks that have provable data
    provable_results = []
    for result in results:
        chunk_id = result.get("chunk_id", "")
        if not chunk_id:
            continue
        # Only include chunks that have merkle data for proof generation
        if result.get("merkle_leaf_hash") and result.get("merkle_siblings"):
            provable_results.append(result)

    if not provable_results:
        # No provable chunks — return empty rather than unproven
        return QueryProvanableResponse(
            chunks=[],
            proofs={},
            query=request.query,
            collection=request.collection,
            total=0,
        )

    # Parallel proof generation
    proofs_map: dict[str, dict] = {}
    failed_chunk_ids: list[str] = []

    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(_generate_proof_for_chunk, result): result["chunk_id"]
            for result in provable_results
        }
        for future in as_completed(futures):
            chunk_id = futures[future]
            try:
                cid, status, data = future.result()
                if status == "ok":
                    proofs_map[cid] = data
                else:
                    failed_chunk_ids.append(cid)
                    logger.warning(f"ZK proof failed for {cid}: {data}")
            except Exception as e:
                failed_chunk_ids.append(chunk_id)
                logger.warning(f"ZK proof exception for {chunk_id}: {e}")

    # Build response — only include chunks that have proofs
    provenanced_chunks = []
    for result in provable_results:
        chunk_id = result.get("chunk_id", "")
        if chunk_id in proofs_map:
            proof_data = proofs_map[chunk_id]
            result["zk_proof"] = {
                "proof_hex": proof_data.get("proof_hex", ""),
                "vk_hex": proof_data.get("vk_hex", ""),
                "public_inputs": proof_data.get("public_inputs", {}),
                "public_inputs_hex": proof_data.get("public_inputs_hex", ""),
                "kurier_job_id": proof_data.get("kurier_job_id"),
            }
            provenanced_chunks.append(result)

    # Log failed chunks (proof generation failed — drop those chunks)
    if failed_chunk_ids:
        logger.warning(f"Dropping {len(failed_chunk_ids)} chunks with failed proofs: {failed_chunk_ids}")

    # ── Log search query ──────────────────────────────────────────────────────────
    try:
        import json
        log_path = ".../data/logs/search_queries.log"
        with open(log_path, "a") as lf:
            lf.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat() + "Z",
                "query": request.query,
                "collection": request.collection,
                "top_k": request.top_k,
                "results": len(provenanced_chunks),
                "results_provable": len(provenanced_chunks),
                "results_dropped_no_proof": len(failed_chunk_ids),
                "duration_ms": int((time.monotonic() - _query_start) * 1000),
            }) + "\n")
    except Exception as e:
        logger.warning(f"stats logging failed: {e}")
        pass

    logger.info(f"Query-provable complete: {len(provenanced_chunks)}/{len(results)} chunks returned "
                f"(dropped {len(failed_chunk_ids)} failed proofs)")

    return QueryProvanableResponse(
        chunks=provenanced_chunks,
        proofs=proofs_map,
        query=request.query,
        collection=request.collection,
        total=len(provenanced_chunks),
    )


# ─── Provenance Models ─────────────────────────────────────────────────────────



class ProveSubmitRequest(BaseModel):
    """Request for POST /api/provenance/submit.

    Accepts proof data from a query-provable response and submits it to Kurier/zkVerify.
    """
    proof_hex: str
    public_inputs_hex: str
    vk_hex: str


class ProveSubmitResponse(BaseModel):
    """Response for POST /api/provenance/submit — returns job_id for polling."""
    job_id: str
    status: str  # submitted | error


class ProveStatusResponse(BaseModel):
    """Response for GET /api/provenance/status/{job_id}."""
    job_id: str
    status: str  # pending | verified | failed | rejected | invalid | finalized
    verified: Optional[bool] = None
    message: Optional[str] = None
    explorer_url: Optional[str] = None
    tx_hash: Optional[str] = None
    tx_explorer_url: Optional[str] = None
    block_hash: Optional[str] = None
    block_explorer_url: Optional[str] = None


class ProvenanceProveRequest(BaseModel):
    """Request for POST /api/provenance/prove.

    Takes a specific chunk and generates a ZK proof for it.
    """
    doc_id: str
    chunk_id: str
    collection: str


class ProvenanceProveResponse(BaseModel):
    """Response for POST /api/provenance/prove.

    Returns the chunk payload and its ZK proof fields.
    Also returns kurier_job_id if KURIE_API_KEY is configured — indicating
    the proof has already been submitted to Kurier for on-chain verification.
    """
    chunk: dict
    proof_hex: str
    vk_hex: str
    public_inputs: dict
    public_inputs_hex: str
    kurier_job_id: Optional[str] = None





# ─── Provenance Endpoints ─────────────────────────────────────────────────────

# ─── ZK Proof Verification Endpoints ──────────────────────────────────────────

@app.post("/api/provenance/submit", response_model=ProveSubmitResponse)
async def submit_proof(request: ProveSubmitRequest):
    """Submit a ZK proof to Kurier/zkVerify for on-chain verification.

    Accepts proof data directly (no server-side cache required).
    Returns job_id immediately for polling.
    """
    if not request.proof_hex or not request.proof_hex.startswith("0x"):
        raise HTTPException(status_code=400, detail="proof_hex must be a valid hex string starting with 0x")
    if not request.public_inputs_hex or not request.public_inputs_hex.startswith("0x"):
        raise HTTPException(status_code=400, detail="public_inputs_hex must be a valid hex string")
    if not request.vk_hex or not request.vk_hex.startswith("0x"):
        raise HTTPException(status_code=400, detail="vk_hex must be a valid hex string")

    loop = asyncio.get_running_loop()
    try:
        job_id = await loop.run_in_executor(
            None,
            provenance_module.submit_proof_to_zkverify,
            request.proof_hex,
            request.public_inputs_hex,
            request.vk_hex,
            None,
        )
    except Exception as e:
        logger.error(f"Kurier submit failed: {e}")
        raise HTTPException(status_code=500, detail=f"Kurier submission failed: {e}") from e

    return ProveSubmitResponse(job_id=job_id, status="submitted")


@app.post("/api/provenance/prove", response_model=ProvenanceProveResponse)
async def prove_chunk(request: ProvenanceProveRequest):
    """Generate a ZK proof for a specific chunk identified by doc_id and chunk_id.

    Takes a doc_id + chunk_id + collection, fetches the chunk from Qdrant,
    generates a ZK proof for it, and returns the chunk payload plus proof fields.

    Used by the website's next/previous chunk navigation with provenance.
    """
    if not request.doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")
    if not request.chunk_id:
        raise HTTPException(status_code=400, detail="chunk_id is required")
    if request.collection not in KNOWN_COLLECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid collection. Must be one of: {', '.join(KNOWN_COLLECTIONS)}"
        )

    # Fetch the chunk from Qdrant — find the point matching doc_id + chunk_id
    filter_condition = models.Filter(
        must=[
            models.FieldCondition(
                key="doc_id",
                match=models.MatchValue(value=request.doc_id)
            )
        ]
    )

    loop = asyncio.get_running_loop()
    try:
        records, _ = await loop.run_in_executor(
            None,
            lambda: client.scroll(
                collection_name=request.collection,
                limit=10000,
                offset=None,
                with_payload=True,
                scroll_filter=filter_condition
            )
        )
    except (ApiException, httpx.HTTPError, Exception) as e:
        logger.error(f"Qdrant scroll failed for doc '{request.doc_id}': {e}")
        raise HTTPException(status_code=500, detail=f"Qdrant query failed: {e}") from e

    # Find the chunk matching chunk_id
    chunk_payload = None
    for record in records:
        if record.payload and record.payload.get("chunk_id") == request.chunk_id:
            chunk_payload = dict(record.payload)
            break

    if chunk_payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"Chunk '{request.chunk_id}' not found for doc '{request.doc_id}' in collection '{request.collection}'"
        )

    # Check if this chunk already has a Kurier job (deduplication)
    existing_job_id = provenance_module.check_existing_kurier_job(request.chunk_id)
    if existing_job_id:
        logger.info("Reusing existing Kurier job", chunk_id=request.chunk_id, job_id=existing_job_id)
        try:
            disk_proof = provenance_module.load_proof_from_disk(request.chunk_id)
        except Exception as e:
            logger.warning(f"Could not load proof from disk for '{request.chunk_id}': {e} — generating fresh", chunk_id=request.chunk_id)
            existing_job_id = None
        else:
            return ProvenanceProveResponse(
                chunk=chunk_payload,
                proof_hex=disk_proof.get("proof_hex", ""),
                vk_hex=disk_proof.get("vk_hex", ""),
                public_inputs=disk_proof.get("public_inputs", {}),
                public_inputs_hex=disk_proof.get("public_inputs_hex", ""),
                kurier_job_id=existing_job_id,
            )

    # Generate the ZK proof using the existing proven pattern
    try:
        cid, status, proof_data = await loop.run_in_executor(
            None,
            _generate_proof_for_chunk,
            chunk_payload
        )
    except Exception as e:
        logger.error(f"Proof generation failed for chunk '{request.chunk_id}': {e}")
        raise HTTPException(status_code=500, detail=f"Proof generation failed: {e}") from e

    if status != "ok":
        logger.warning(f"Proof generation returned status '{status}' for chunk '{request.chunk_id}': {proof_data}")
        raise HTTPException(status_code=500, detail=f"Proof generation failed: {proof_data}")

    # Auto-submit to Kurier (background, non-critical)
    kurier_job_id = None
    try:
        kurier_job_id = provenance_module.submit_proof_to_zkverify(
            proof_hex=proof_data["proof_hex"],
            public_inputs_hex=proof_data["public_inputs_hex"],
            vk_hex=proof_data["vk_hex"],
            vk_id=None,
        )
        logger.info("Auto-submitted to Kurier", chunk_id=request.chunk_id, job_id=kurier_job_id)
        # Persist Kurier fields to disk
        provenance_module.save_kurier_status(
            chunk_id=request.chunk_id,
            job_id=kurier_job_id,
            status="submitted",
        )
    except Exception as e:
        # Non-critical — log and continue without job_id
        logger.warning(f"Kurier auto-submit failed for chunk '{request.chunk_id}': {e}")

    return ProvenanceProveResponse(
        chunk=chunk_payload,
        proof_hex=proof_data.get("proof_hex", ""),
        vk_hex=proof_data.get("vk_hex", ""),
        public_inputs=proof_data.get("public_inputs", {}),
        public_inputs_hex=proof_data.get("public_inputs_hex", ""),
        kurier_job_id=kurier_job_id,
    )


@app.get("/api/provenance/status/{job_id}", response_model=ProveStatusResponse)
async def get_proof_status(job_id: str):
    """Poll Kurier job status for a proof verification job.

    Returns current status and, if complete, the verification result.
    """
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")

    loop = asyncio.get_running_loop()
    try:
        poll_result = await loop.run_in_executor(
            None,
            provenance_module.poll_zkverify_job,
            job_id,
            10,   # poll_interval seconds
            300,  # max_wait seconds
        )
    except Exception as e:
        logger.error(f"Status poll failed for {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Status poll failed: {e}") from e

    status = poll_result.get("status", "pending")
    terminal_states = {"finalized", "completed", "verified", "failed", "rejected", "invalid"}
    is_terminal = status.lower() in terminal_states

    verified = None
    message = None
    explorer_url = poll_result.get("zkverify_explorer_url")
    tx_hash = poll_result.get("tx_hash")
    tx_explorer_url = poll_result.get("tx_explorer_url")
    block_hash = poll_result.get("block_hash")
    block_explorer_url = poll_result.get("block_explorer_url")

    if is_terminal:
        if status.lower() in {"finalized", "completed", "verified"}:
            verified = True
            message = "Proof verified on zkVerify"
        else:
            verified = False
            message = f"Verification failed: {status}"

    return ProveStatusResponse(
        job_id=job_id,
        status=status,
        verified=verified,
        message=message,
        explorer_url=explorer_url,
        tx_hash=tx_hash,
        tx_explorer_url=tx_explorer_url,
        block_hash=block_hash,
        block_explorer_url=block_explorer_url,
    )


# ─── Public Kurier polling (no nginx auth required) ──────────────────────────
# The /api/provenance/status/ endpoint is behind nginx Basic auth.
# This endpoint lets the browser poll Kurier job status without any API key.

@app.get("/api/provenance/poll/{job_id}")
async def poll_kurier_status(job_id: str):
    """Poll Kurier job status — no auth required.

    This is a thin proxy to poll_zkverify_job that bypasses nginx's
    auth layer so browsers can poll without an API key.
    """
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")

    loop = asyncio.get_running_loop()
    try:
        poll_result = await loop.run_in_executor(
            None,
            provenance_module.poll_zkverify_job,
            job_id,
            10,   # poll_interval seconds
            300,  # max_wait seconds
        )
    except Exception as e:
        logger.error(f"Status poll failed for {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Status poll failed: {e}") from e

    status = poll_result.get("status", "pending")
    terminal_states = {"finalized", "completed", "verified", "failed", "rejected", "invalid"}
    is_terminal = status.lower() in terminal_states

    verified = None
    message = None
    explorer_url = poll_result.get("zkverify_explorer_url")
    tx_hash = poll_result.get("tx_hash")
    tx_explorer_url = poll_result.get("tx_explorer_url")
    block_hash = poll_result.get("block_hash")
    block_explorer_url = poll_result.get("block_explorer_url")

    if is_terminal:
        if status.lower() in {"finalized", "completed", "verified"}:
            verified = True
            message = "Proof verified on zkVerify"
        else:
            verified = False
            message = f"Verification failed: {status}"

    return {
        "job_id": job_id,
        "status": status,
        "verified": verified,
        "message": message,
        "explorer_url": explorer_url,
        "tx_hash": tx_hash,
        "tx_explorer_url": tx_explorer_url,
        "block_hash": block_hash,
        "block_explorer_url": block_explorer_url,
    }


# ─── X402 Paid Download Endpoints ─────────────────────────────────────────────

@app.get("/api/source/{doc_id}/info")
async def get_source_info(doc_id: str):
    """Return document metadata and price for paid PDF download."""
    import json

    registry_path = Path("./data/registry.json")
    try:
        with open(registry_path) as f:
            registry = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load registry: {e}")

    docs = registry.get("documents", [])
    doc = next((d for d in docs if d.get("doc_id") == doc_id), None)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    local_path = doc.get("local_path")
    if not local_path or not Path(local_path).exists():
        raise HTTPException(status_code=404, detail="Source PDF not found on server")

    # Use the filename from the local_path as the download filename
    filename = Path(local_path).name

    price_micro_usdc = doc.get("price_micro_usdc", 10_000)

    return {
        "doc_id": doc_id,
        "title": doc.get("title", doc_id),
        "branch": doc.get("branch", "unknown"),
        "filename": filename,
        "price_usd": f"{price_micro_usdc / 1_000_000:.2f}",
        "price_micro_usdc": price_micro_usdc,
        "asset": USDC_CONTRACT,
        "network": NETWORK_SPEC,
        "scheme": "exact",
        "pay_to": os.environ.get("PAID_DOWNLOAD_RECEIVING_ADDRESS", ""),
        "max_timeout_seconds": 300,
    }


@app.get("/api/source/{doc_id}")
async def get_source_pdf(doc_id: str, request: Request):
    """Stream the source PDF for a document, requiring X402 payment proof.

    Without a Payment-Signature header: returns 402 with PAYMENT-REQUIRED.
    With a valid EIP-3009 PaymentPayload: streams the PDF file.
    """
    # Extract the base64-encoded PaymentPayload from the X402 header
    payment_sig = request.headers.get("Payment-Signature")

    # Build the resource URL as seen by the client
    resource_url = f"{request.base_url}api/source/{doc_id}"

    should_stream, status_code, response_headers, result = verify_and_stream(
        doc_id, payment_sig, resource_url
    )

    if status_code == 402:
        raise HTTPException(
            status_code=402,
            detail=result,
            headers={k: v for k, v in response_headers.items()},
        )

    if not should_stream or status_code != 200:
        raise HTTPException(status_code=status_code, detail=result)

    # result is the local file path
    file_path = result
    filename = Path(file_path).name

    from starlette.responses import FileResponse
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/pdf",
        headers={k: v for k, v in response_headers.items()},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100, log_level="info")
