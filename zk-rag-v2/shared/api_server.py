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
# __file__ = $REPO_DIR/shared/api_server.py  (R730 layout)
#          = $ZK_RAG_HOME/rag/api_server.py              (VPS layout)
# Detect which layout we're in and add the right parent to sys.path
_this_file = os.path.abspath(__file__)
_this_dir = os.path.dirname(_this_file)
if os.path.basename(_this_dir) == "shared":
    # R730 layout: api_server.py is inside shared/ subdirectory
    _project_root = os.path.dirname(_this_dir)
    sys.path.insert(0, _project_root)
else:
    # VPS layout: api_server.py is directly in $ZK_RAG_HOME/rag/
    # shared/ module files live at the same level, not in a subdirectory
    # Add parent ($ZK_RAG_HOME/) so `shared` resolves via the symlink below
    _project_root = os.path.dirname(_this_dir)
    sys.path.insert(0, _project_root)
    # On VPS, $ZK_RAG_HOME/shared is a symlink to $ZK_RAG_HOME/rag/
    # so `import shared` resolves to $ZK_RAG_HOME/shared/ → $ZK_RAG_HOME/rag/
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
print("[api_server] x402 import starting...", flush=True)
from x402_paid_download import (
    verify_and_stream,
    PRICE_MICRO_USDC,
    NETWORK_SPEC,
    USDC_CONTRACT,
)
print("[api_server] x402 import OK", flush=True)
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import ApiException

# Embedding service client (connection-pooled)
_EMBEDDING_SERVICE_URL = "http://127.0.0.1:8200"
_http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize httpx client on startup; close on shutdown."""
    global _http_client
    print("[api_server] [lifespan] Configuring httpx limits...", flush=True)
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    print("[api_server] [lifespan] Creating httpx AsyncClient...", flush=True)
    _http_client = httpx.AsyncClient(
        base_url=_EMBEDDING_SERVICE_URL,
        limits=limits,
        timeout=httpx.Timeout(60.0),
    )
    print("[api_server] [lifespan] HTTP client initialized OK", flush=True)
    logger.info("RAG API HTTP client initialized")
    yield
    if _http_client:
        await _http_client.aclose()
    logger.info("RAG API HTTP client closed")
    print("[api_server] Lifespan shutdown complete.", flush=True)

# Configure logging to file
LOG_DIR = "$DATA_DIR/logs"
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

IMAGES_DIR = "$DATA_DIR/images/"

# Known collections
KNOWN_COLLECTIONS = ["army", "navy", "marines", "coast_guard", "air_force", "other"]

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
_query_cache: dict[str, tuple[float, list]] = {}  # cache_key -> (timestamp, results_list)
_query_cache_meta: dict[str, dict] = {}  # cache_key -> {collection: str, ...}

# Ingested doc_ids cache per collection - refreshed every 10 minutes
_INGESTED_DOCS_CACHE_TTL_SECONDS = 10 * 60
_ingested_docs_cache: dict[str, tuple[float, set[str]]] = {}  # collection -> (timestamp, doc_ids_set)

# Hardcoded embedding model used for all queries (matches embedding service)
_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"


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
    return True, results


def _query_cache_set(key: str, results: list, collection: str) -> None:
    """Store results in the cache with current timestamp."""
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


class QueryProvanableResponse(BaseModel):
    """Response model for POST /api/query-provable.

    Returns chunks with ZK proofs already generated and attached.
    No chunk text is returned without a corresponding proof.
    """
    chunks: list[dict]  # each chunk includes zk_proof with proof_hex, public_inputs, etc.
    proofs: dict  # keyed by chunk_id: {chunk_id: {proof_hex, public_inputs, ...}}
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
        "model": "Qwen/Qwen3-Embedding-0.6B",
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
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
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
    with ThreadPoolExecutor(max_workers=min(len(target_collections), 4)) as executor:
        futures = {executor.submit(_get_collection_stats, c): c for c in target_collections}
        for future in as_completed(futures):
            collection_name = futures[future]
            try:
                stats = future.result()
                collections_info.append(stats)
            except (ApiException, httpx.HTTPError, Exception) as e:
                logger.warning(f"Failed to get stats for collection '{collection_name}': {e}")
    
    return collections_info


@app.delete("/api/cache/collections")
def invalidate_collections_cache(collection: str = Query(default=None, description="Optional: invalidate only this collection")):
    """Invalidate the collections metadata cache. Call after updating collection data."""
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


@app.delete("/api/cache/query")
def invalidate_query_cache(collection: str = Query(default=None, description="Optional: invalidate only this collection's query cache")):
    """Invalidate the content-addressed query result cache. Call after pipelines F or G upsert data."""
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


_REGISTRY_PATH = Path("$DATA_DIR/registry.json")
_COLLECTION_DESCRIPTIONS = {
    "army": "U.S. Army field manuals, doctrine publications, and operational guidance",
    "navy": "U.S. Navy tactical and operational publications",
    "marines": "U.S. Marine Corps doctrine and tactical guidance",
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


@app.get("/api/catalog")
def get_catalog():
    """Return documents grouped by branch/collection, filtered to only those indexed in Qdrant.

    Reads from the unified registry, cross-references with Qdrant to include only
    ingested documents, and returns document listings per collection.
    Each entry includes: doc_id, title, branch, category, pub_year, page_count.
    """
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

    # Build set of ingested doc_ids per collection (cached)
    ingested_by_collection: dict[str, set[str]] = {}
    for coll in _COLLECTION_DESCRIPTIONS:
        ingested_by_collection[coll] = _get_ingested_doc_ids(coll)

    # Group by branch, filtering to only ingested docs
    collections_map: dict[str, list[dict]] = {
        name: [] for name in _COLLECTION_DESCRIPTIONS
    }
    for doc in doc_list:
        doc_id = doc.get("doc_id")
        branch = doc.get("branch", "other")
        if branch not in collections_map:
            collections_map[branch] = []
        # Only include docs that were actually indexed into this branch's collection
        if doc_id and doc_id in ingested_by_collection.get(branch, set()):
            collections_map[branch].append({
                "doc_id": doc_id,
                "title": doc.get("title") or doc.get("filename", "Untitled"),
                "branch": branch,
                "category": doc.get("category", ""),
                "pub_year": doc.get("pub_year"),
                "page_count": doc.get("page_count"),
                "ia_identifier": doc.get("ia_identifier", ""),
            })

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


@app.get("/api/context")
def get_context(
    doc_id: str = Query(..., description="Document ID"),
    chunk_index: int = Query(..., description="Chunk index (0-based)"),
    collection: str = Query(..., description="Collection name"),
    window: int = Query(1, description="Window size (returns 2*window+1 chunks)")
):
    """Get a window of chunks from a document by position.

    Returns chunks from a single document centered around the specified
    chunk_index. For example, window=1 returns chunks at indices N-1, N, N+1.

    Args:
        doc_id: The document ID
        chunk_index: The center chunk index (0-based)
        collection: The collection name to search in
        window: Window size (returns 2*window+1 chunks centered on chunk_index)

    Returns:
        Dictionary with 'results' list containing the window of chunks
    """
    # Validate collection
    if collection not in KNOWN_COLLECTIONS:
        raise HTTPException(
            status_code=400, detail=f"Invalid collection. Must be one of: {', '.join(KNOWN_COLLECTIONS)}"
        )

    # Validate chunk_index
    if chunk_index < 0:
        raise HTTPException(status_code=400, detail="chunk_index must be non-negative")

    # Validate window
    if window < 0:
        raise HTTPException(status_code=400, detail="window must be non-negative")

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
                collection_name=collection,
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

        # Calculate window bounds
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


@app.get("/api/images/{doc_id}/{page_num}")
def list_images_for_page(doc_id: str, page_num: int):
    """List images for a specific page of a document.
    
    Args:
        doc_id: The document ID
        page_num: The page number to list images for
        
    Returns:
        Dictionary with 'images' list containing image filenames for that page
    """
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
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
        "vector_dimension": 1024
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
    request: QueryRequest
) -> list[dict]:
    """Perform vector-only search on a single collection.
    
    Args:
        collection_name: Name of the collection to search
        query_vector: The query vector for similarity search
        request: QueryRequest with query text, top_k, and collection
        
    Returns:
        List of result dictionaries with payload and score
    """
    # Vector-only search
    try:
        search_results = client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=request.top_k,
            with_payload=True
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

    # Embed the query via embedding service
    global _http_client
    try:
        resp = await _http_client.post(
            "/encode",
            json={"texts": [request.query]},
            timeout=60.0,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Embedding service error: {resp.text}")
        embedding_data = resp.json()
        query_vector = embedding_data["embeddings"][0]
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        logger.error(f"Embedding service call failed: {e}")
        raise HTTPException(status_code=502, detail=f"Embedding service unreachable: {e}") from e
    
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
        log_path = "$DATA_DIR/logs/search_queries.log"
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

    # Validate top_k
    if request.top_k < 1:
        raise HTTPException(status_code=400, detail="top_k must be at least 1")
    if request.top_k > 50:
        raise HTTPException(status_code=400, detail="top_k cannot exceed 50")

    # Validate collection
    if request.collection not in KNOWN_COLLECTIONS and request.collection != "*":
        raise HTTPException(
            status_code=400,
            detail=f"Invalid collection. Must be one of: {', '.join(KNOWN_COLLECTIONS)} or '*' for cross-collection search"
        )

    # Embed query via embedding service
    global _http_client
    try:
        resp = await _http_client.post(
            "/encode",
            json={"texts": [request.query]},
            timeout=60.0,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Embedding service error: {resp.text}")
        embedding_data = resp.json()
        query_vector = embedding_data["embeddings"][0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Embedding service call failed: {e}")
        raise HTTPException(status_code=502, detail=f"Embedding service unreachable: {e}") from e

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
            collection_name=request.collection,
            query_vector=query_vector,
            request=QueryRequest(
                query=request.query,
                top_k=request.top_k,
                collection=request.collection,
            ),
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
            }
            provenanced_chunks.append(result)

    # Log failed chunks (proof generation failed — drop those chunks)
    if failed_chunk_ids:
        logger.warning(f"Dropping {len(failed_chunk_ids)} chunks with failed proofs: {failed_chunk_ids}")

    # ── Log search query ──────────────────────────────────────────────────────────
    try:
        import json
        log_path = "$DATA_DIR/logs/search_queries.log"
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


# ─── X402 Paid Download Endpoints ─────────────────────────────────────────────

@app.get("/api/source/{doc_id}/info")
async def get_source_info(doc_id: str):
    """Return document metadata and price for paid PDF download."""
    import json

    registry_path = Path("$DATA_DIR/registry.json")
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

    return {
        "doc_id": doc_id,
        "title": doc.get("title", doc_id),
        "branch": doc.get("branch", "unknown"),
        "filename": filename,
        "price_usd": f"{PRICE_MICRO_USDC / 1_000_000:.2f}",
        "price_micro_usdc": PRICE_MICRO_USDC,
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
