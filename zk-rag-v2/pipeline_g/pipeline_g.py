#!/usr/bin/env python3
"""
pipeline_g.py -- Qdrant upsert with Merkle metadata + EVM provenance.

Reads chunk data (Pipeline D), Merkle tree metadata (Pipeline E), and EVM
emission records (Pipeline F from registry), then upserts all chunk vectors
with full metadata to Qdrant.

One Qdrant collection per branch. Pipeline G is the sole write point to Qdrant.

Usage:
    python3 pipeline_g.py --dry-run
    python3 pipeline_g.py --batch
    python3 pipeline_g.py --doc-id <doc_id>
    python3 pipeline_g.py --doc-id <doc_id> --dry-run

Exit codes:
    0  - success
    1  - one or more documents failed (batch mode only)
"""

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

# ── Query cache invalidation ──────────────────────────────────────────────────
_API_BASE = "http://127.0.0.1:8100"
_QUERY_CACHE_INVALIDATE_ENDPOINT = "/api/cache/query"


def _invalidate_query_cache_for_collection(collection: str) -> bool:
    """Invalidate the API query result cache for the given collection.

    Called after pipeline_g successfully upserts documents to Qdrant,
    so that subsequent /api/query calls return fresh results.

    Returns True if invalidation succeeded, False otherwise.
    """
    try:
        resp = httpx.delete(
            f"{_API_BASE}{_QUERY_CACHE_INVALIDATE_ENDPOINT}",
            params={"collection": collection},
            timeout=10.0,
        )
        if resp.status_code == 200:
            logger.info("Query cache invalidated for collection '%s' (%s)", collection, resp.json())
            return True
        else:
            logger.warning("Query cache invalidation for '%s' returned %s: %s", collection, resp.status_code, resp.text)
            return False
    except Exception as e:
        logger.warning("Query cache invalidation for '%s' failed: %s", collection, e)
        return False

# ── Logging setup ─────────────────────────────────────────────────────────────
LOG_DIR = Path("../data/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "pipeline_g.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("pipeline_g")

# ── Configuration ─────────────────────────────────────────────────────────────

REGISTRY_PATH = Path("../data/registry.json")
MERKLE_TREES_DIR = Path("../data/merkleTrees")
CHUNKS_DIR = Path("../data/chunks")
EMBEDDINGS_DIR = Path("../data/embeddings")
QDRANT_PATH = Path("../data/qdrant")
EMBEDDING_DIM = 768  # NomicEmbedText-v1.5 produces 768-dimensional vectors
TEXT_TRUNCATE_LEN = 2000  # chars per chunk — keeps Qdrant payload under 32MB limit for high-chunk-count docs


# ── Branch normalization ───────────────────────────────────────────────────────

# Map from raw registry branch values to Qdrant collection names.
# Order matters - more specific mappings first.
BRANCH_NORMALIZE_RULES = [
    ("army",       "army"),
    ("navy",       "navy"),
    ("marines",    "marines"),
    ("air_force",  "air_force"),
    ("air force",  "air_force"),
    ("joint",      "joint"),
    ("coast guard", "coast_guard"),
]


def normalize_branch(branch: str) -> str:
    """Normalize a registry branch value to a valid Qdrant collection name."""
    b = branch.strip().lower()
    for raw, collection in BRANCH_NORMALIZE_RULES:
        if b == raw:
            return collection
    return "other"


# ── Point ID ─────────────────────────────────────────────────────────────────

def build_point_id(chunk_id: str) -> str:
    """
    Build a deterministic Qdrant point ID from a chunk_id.

    chunk_id format: '{doc_id}-{chunk_index}' (e.g. '0a21e769...-0').
    Uses UUID5 (namespace-based, deterministic) so each chunk gets a unique
    valid UUID. Same chunk_id always produces the same UUID5.
    """
    return str(uuid.uuid5(UUID_NAMESPACE, chunk_id))


UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


# ── Data loading helpers ─────────────────────────────────────────────────────

def load_chunks(chunks_path: Path) -> list[dict]:
    """
    Load chunks from a chunks.jsonl file.

    Returns list of chunk dicts with 'text' truncated to TEXT_TRUNCATE_LEN.
    """
    chunks = []
    with open(chunks_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            # Truncate text (only if limit is set)
            if TEXT_TRUNCATE_LEN > 0:
                chunk["text"] = chunk.get("text", "")[:TEXT_TRUNCATE_LEN]
            # else: keep full text
            chunks.append(chunk)
    return chunks


def load_embeddings(embeddings_path: Path) -> np.ndarray:
    """
    Load embeddings from a single .npy file.

    Returns numpy array of shape (N, 1024), dtype float32.
    """
    return np.load(str(embeddings_path))


def load_merkle_tree(doc_id: str, trees_dir: Path) -> dict:
    """Load the tree JSON for a given doc_id."""
    path = trees_dir / f"{doc_id}_tree.json"
    with open(path, "r") as f:
        return json.load(f)


# ── Payload builders ─────────────────────────────────────────────────────────

def build_merkle_payload(
    doc_id: str,
    chunk_index: int,
    tree: dict,
    tree_depth: str,
) -> dict:
    """
    Extract Merkle tree metadata for a single chunk.

    Leaf index offset: doc_id occupies leaf 0, first chunk is at leaf 1.
    Therefore: paths[str(chunk_index + 1)].
    """
    path_key = str(chunk_index + 1)
    path_entry = tree.get("paths", {}).get(path_key)

    if path_entry is None:
        return {
            "merkle_leaf_index": None,
            "merkle_leaf_hash": None,
            "merkle_path": None,
            "merkle_siblings": None,
            "poseidon_doc_id_hash": None,
            "merkle_tree_depth": tree_depth,
        }

    siblings = path_entry.get("siblings", [])
    merkle_path = [
        {"hash": s["hash"], "at_depth": s["at_depth"]}
        for s in siblings
    ]
    # Flat siblings array: just the hash strings, ordered leaf → root.
    # This is what the ZK circuit expects as the "siblings" private witness.
    merkle_siblings = [s["hash"] for s in siblings]

    # leaf[0] of the Merkle tree = Poseidon(doc_id_bytes).
    # Stored per-chunk so the ZK circuit's public input "document_hash"
    # can be read directly from the Qdrant payload without disk I/O.
    leaf_hashes = tree.get("leaf_hashes", [])
    poseidon_doc_id_hash = leaf_hashes[0] if leaf_hashes else None

    return {
        "merkle_leaf_index": path_entry.get("leaf_index"),
        "merkle_leaf_hash": path_entry.get("leaf_hash"),
        "merkle_path": merkle_path,
        "merkle_siblings": merkle_siblings,
        "poseidon_doc_id_hash": poseidon_doc_id_hash,
        "merkle_tree_depth": tree_depth,
    }


def build_evm_payload(registry_entry: dict) -> dict:
    """
    Extract EVM provenance fields from an emitted registry entry.

    Prefers emitted_testnet; falls back to emitted_mainnet.
    Returns dict with evm_* fields.
    """
    emitted = registry_entry.get("emitted_testnet") or registry_entry.get("emitted_mainnet") or {}

    # Handle old boolean format
    if isinstance(emitted, bool):
        emitted = {}

    return {
        "evm_tx_hash":        emitted.get("tx_hash"),
        "evm_block_number":   emitted.get("block_number"),
        "evm_block_timestamp": emitted.get("block_timestamp"),
        "evm_chain_id":       emitted.get("chain_id"),
        "evm_uploader":      emitted.get("uploader"),
    }


def build_payload(
    doc_id: str,
    chunk: dict,
    embedding: np.ndarray,
    registry_entry: dict,
    tree: dict,
    tree_depth: str,
    merkle_root: str,
) -> dict:
    """
    Build a complete Qdrant payload for a single chunk.

    Combines: chunk text/metadata (Pipeline D),
              Merkle tree metadata (Pipeline E),
              EVM provenance (Pipeline F from registry).
    """
    merkle = build_merkle_payload(doc_id, chunk["chunk_index"], tree, tree_depth)
    evm = build_evm_payload(registry_entry)

    payload = {
        # Chunk metadata
        "doc_id":     doc_id,
        "chunk_id":   chunk["chunk_id"],
        "text":       chunk.get("text", ""),
        "page":       chunk.get("page"),
        "chapter":    chunk.get("chapter"),
        "section":   chunk.get("section"),
        "section_title": chunk.get("section_title"),
        "chunk_index": chunk.get("chunk_index"),
        "vision_description_used": chunk.get("vision_description_used", False),
        # Registry fields
        "branch":     registry_entry.get("branch", ""),
        "title":      registry_entry.get("title", ""),
        "category":   registry_entry.get("category", ""),
        "doc_type":   registry_entry.get("doc_type", ""),
        "source":     registry_entry.get("source", ""),
        "pub_year":   registry_entry.get("pub_year"),
        "file_size_bytes": registry_entry.get("file_size_bytes"),
        "ia_identifier": registry_entry.get("ia_identifier"),
        # Merkle metadata
        "merkle_leaf_hash":   merkle["merkle_leaf_hash"],
        "merkle_leaf_index": merkle["merkle_leaf_index"],
        "merkle_path":       merkle["merkle_path"],
        "merkle_siblings":   merkle["merkle_siblings"],       # flat sibling hashes (ZK circuit input)
        "poseidon_doc_id_hash": merkle["poseidon_doc_id_hash"], # Poseidon(doc_id) = leaf[0]
        "merkle_root":       merkle_root,
        "merkle_tree_depth": merkle["merkle_tree_depth"],
        # EVM provenance
        "evm_tx_hash":         evm["evm_tx_hash"],
        "evm_block_number":    evm["evm_block_number"],
        "evm_block_timestamp":  evm["evm_block_timestamp"],
        "evm_chain_id":        evm["evm_chain_id"],
        "evm_uploader":        evm["evm_uploader"],
    }

    return payload


# ── Eligibility ───────────────────────────────────────────────────────────────

def check_eligibility(
    doc_id: str,
    registry_entry: dict,
    trees_dir: Path,
    chunks_dir: Path,
    emb_dir: Path,
    reingest: bool = False,
) -> dict:
    """
    Check whether a document is eligible for Pipeline G.

    Returns {"eligible": bool, "reason": str|None}.

    When reingest=True, already-ingested docs are NOT rejected — this
    allows Pipeline G to overwrite existing Qdrant points with an updated
    payload schema (e.g. adding merkle_siblings[] and poseidon_doc_id_hash).
    """
    logger = logging.getLogger("pipeline_g")

    # 1. Already ingested — allowed in reingest mode
    if registry_entry.get("status") == "ingested":
        if reingest:
            logger.debug("doc_id=%s reingest mode: proceeding despite already-ingested", doc_id)
        else:
            logger.debug("doc_id=%s skipped: already ingested", doc_id)
            return {"eligible": False, "reason": "already ingested"}

    # 2. Emitted on testnet OR mainnet (accept either; don't let testnet failure block valid mainnet)
    emitted_testnet = registry_entry.get("emitted_testnet", {})
    emitted_mainnet = registry_entry.get("emitted_mainnet", {})
    if isinstance(emitted_testnet, bool):
        emitted_testnet = {}
    if isinstance(emitted_mainnet, bool):
        emitted_mainnet = {}
    testnet_ok = emitted_testnet.get("status") == "emitted"
    mainnet_ok = emitted_mainnet.get("status") == "emitted"
    if not testnet_ok and not mainnet_ok:
        logger.debug("doc_id=%s skipped: not emitted (testnet=%s mainnet=%s)", doc_id, emitted_testnet.get("status"), emitted_mainnet.get("status"))
        return {"eligible": False, "reason": "not emitted on testnet or mainnet"}

    # 3. Chunks exist
    chunks_path = chunks_dir / doc_id / "chunks.jsonl"
    if not chunks_path.exists():
        logger.debug("doc_id=%s skipped: chunks.jsonl not found at %s", doc_id, chunks_path)
        return {"eligible": False, "reason": "chunks.jsonl not found"}

    # 4. Embeddings exist
    emb_path = emb_dir / doc_id / "embeddings.npy"
    if not emb_path.exists():
        logger.debug("doc_id=%s skipped: embeddings.npy not found at %s", doc_id, emb_path)
        return {"eligible": False, "reason": "embeddings.npy not found"}

    # 5. Tree exists
    tree_path = trees_dir / f"{doc_id}_tree.json"
    if not tree_path.exists():
        logger.debug("doc_id=%s skipped: tree JSON not found at %s", doc_id, tree_path)
        return {"eligible": False, "reason": "tree JSON not found"}

    return {"eligible": True, "reason": None}


def eligible_doc_ids(registry: dict, trees_dir: Path, chunks_dir: Path, emb_dir: Path, reingest: bool = False) -> list[str]:
    """Return list of doc_ids that are eligible for Pipeline G."""
    results = []
    for entry in registry.get("documents", []):
        doc_id = entry["doc_id"]
        elig = check_eligibility(doc_id, entry, trees_dir, chunks_dir, emb_dir, reingest=reingest)
        if elig["eligible"]:
            results.append(doc_id)
    return results


# ── Qdrant helpers ───────────────────────────────────────────────────────────

def get_qdrant() -> QdrantClient:
    """Return a QdrantClient connected to the local Qdrant instance via HTTP."""
    # Use network mode — the running Qdrant server holds a portalocker exclusive
    # lock on the storage directory, so QdrantClient(path=...) would fail with
    # AlreadyLocked. Network mode connects via the HTTP API instead.
    logger.debug("Connecting to Qdrant at http://127.0.0.1:6333")
    client = QdrantClient(url="http://127.0.0.1:6333")
    return client


def ensure_collection(client: QdrantClient, collection: str) -> None:
    """Create collection if it doesn't exist; otherwise verify vector size matches."""
    try:
        client.get_collection(collection_name=collection)
        logger.debug("Collection '%s' already exists", collection)
    except Exception:
        logger.info("Creating collection '%s' with vector size %d", collection, EMBEDDING_DIM)
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        logger.info("Collection '%s' created successfully", collection)


def upsert_doc(
    doc_id: str,
    registry_entry: dict,
    trees_dir: Path,
    chunks_dir: Path,
    emb_dir: Path,
    client: QdrantClient,
    dry_run: bool = False,
) -> tuple[str, bool, str]:
    """
    Upsert all chunks for a single document into Qdrant.

    Returns (label, success, message).
    Labels: INGEST, SKIP, FAIL
    """
    chunks_path = chunks_dir / doc_id / "chunks.jsonl"
    emb_path = emb_dir / doc_id / "embeddings.npy"
    tree_path = trees_dir / f"{doc_id}_tree.json"

    # ── Load data ─────────────────────────────────────────────────────────────
    logger.debug("Loading chunks from %s", chunks_path)
    try:
        chunks = load_chunks(chunks_path)
        logger.debug("Loaded %d chunks from %s", len(chunks), chunks_path)
    except Exception as e:
        logger.error("Failed to load chunks from %s: %s", chunks_path, e)
        return "FAIL", False, f"error=load_failed: {e}"

    logger.debug("Loading embeddings from %s", emb_path)
    try:
        embeddings = load_embeddings(emb_path)
        logger.debug("Loaded embeddings shape: %s", str(embeddings.shape))
    except Exception as e:
        logger.error("Failed to load embeddings from %s: %s", emb_path, e)
        return "FAIL", False, f"error=load_failed: {e}"

    logger.debug("Loading Merkle tree from %s", tree_path)
    try:
        tree = load_merkle_tree(doc_id, trees_dir)
        logger.debug("Loaded Merkle tree with depth=%s", tree.get("depth", "?"))
    except Exception as e:
        logger.error("Failed to load Merkle tree from %s: %s", tree_path, e)
        return "FAIL", False, f"error=load_failed: {e}"

    if len(chunks) == 0:
        logger.warning("Zero chunks for doc_id=%s", doc_id)
        return "FAIL", False, "error=zero_chunks"

    # ── Derive per-chunk fields ────────────────────────────────────────────────
    tree_depth = str(registry_entry.get("tree_depth", ""))
    merkle_root = registry_entry.get("tree_root", "") or ""
    collection = normalize_branch(registry_entry.get("branch", "other"))
    logger.debug("doc_id=%s collection='%s' branch='%s' tree_depth=%s", doc_id, collection, registry_entry.get("branch", ""), tree_depth)

    # ── Build points ───────────────────────────────────────────────────────────
    points = []
    for i, chunk in enumerate(chunks):
        if i >= len(embeddings):
            # Defensive: embeddings and chunks must align
            logger.warning("doc_id=%s chunk_index=%d skipped — embeddings index out of range (embeddings have %d entries)", doc_id, i, len(embeddings))
            break
        embedding = embeddings[i]
        payload = build_payload(
            doc_id=doc_id,
            chunk=chunk,
            embedding=embedding,
            registry_entry=registry_entry,
            tree=tree,
            tree_depth=tree_depth,
            merkle_root=merkle_root,
        )
        point_id = build_point_id(chunk["chunk_id"])
        points.append(PointStruct(
            id=point_id,
            vector=embedding.tolist(),
            payload=payload,
        ))
    logger.debug("Built %d points for doc_id=%s (chunks=%d embeddings=%d)", len(points), doc_id, len(chunks), len(embeddings))

    # ── Dry run ────────────────────────────────────────────────────────────────
    if dry_run:
        logger.info("doc_id=%s dry-run: would upsert %d points to collection '%s'", doc_id, len(points), collection)
        return "INGEST", True, f"dry_run: {len(points)} points to {collection}"

    # ── Ensure collection exists ────────────────────────────────────────────────
    logger.debug("Ensuring collection '%s' exists", collection)
    try:
        ensure_collection(client, collection)
    except Exception as e:
        logger.error("doc_id=%s collection '%s' setup failed: %s", doc_id, collection, e)
        return "FAIL", False, f"error=collection_setup_failed: {e}"

    # ── Upsert (batched to stay under Qdrant's 32MB per-request limit) ──────────
    BATCH_SIZE = 100
    total_batches = (len(points) + BATCH_SIZE - 1) // BATCH_SIZE
    logger.info("doc_id=%s upserting %d points to collection '%s' in %d batches", doc_id, len(points), collection, total_batches)
    try:
        for batch_idx in range(total_batches):
            batch = points[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
            client.upsert(collection_name=collection, points=batch)
            logger.debug("doc_id=%s batch %d/%d upserted (%d points)", doc_id, batch_idx + 1, total_batches, len(batch))
        logger.info("doc_id=%s upsert successful — %d points in collection '%s'", doc_id, len(points), collection)
    except Exception as e:
        logger.error("doc_id=%s upsert failed: %s", doc_id, e)
        return "FAIL", False, f"error=upsert_failed: {e}"

    return "INGEST", True, f"ingested {len(points)} points to '{collection}'"


# ── Registry update ───────────────────────────────────────────────────────────

def apply_ingested_update(registry_entry: dict, chunk_count: int) -> None:
    """
    Mark a registry entry as ingested.

    Writes in-place to the registry entry dict.
    Caller is responsible for saving the registry.
    """
    registry_entry["status"] = "ingested"
    registry_entry["ingested_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")



def main():
    parser = argparse.ArgumentParser(description="Pipeline G - Qdrant upsert with Merkle + EVM metadata")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without writing")
    parser.add_argument("--batch", action="store_true", help="Process all eligible documents")
    parser.add_argument("--doc-id", type=str, help="Process a single document")
    parser.add_argument("--limit", type=int, metavar="N", help="Limit batch to first N documents (testing)")
    parser.add_argument(
        "--reingest",
        action="store_true",
        help="Allow re-ingesting already-ingested documents (overwrites Qdrant points with new payload schema)",
    )
    args = parser.parse_args()

    if not args.batch and not args.doc_id:
        parser.print_help()
        sys.exit(1)

    # ── Load registry ─────────────────────────────────────────────────────────
    registry = json.load(open(REGISTRY_PATH))
    registry_entries = {d["doc_id"]: d for d in registry["documents"]}
    print(f"Loaded {len(registry_entries)} documents from registry")
    logger.info("Loaded %d documents from registry", len(registry_entries))

    client = get_qdrant()

    # ── Determine doc_ids to process ──────────────────────────────────────────
    if args.doc_id:
        doc_ids = [args.doc_id]
    else:
        doc_ids = eligible_doc_ids(registry, MERKLE_TREES_DIR, CHUNKS_DIR, EMBEDDINGS_DIR, reingest=args.reingest)
        print(f"Eligible docs: {len(doc_ids)}")
        logger.info("Found %d eligible documents", len(doc_ids))
        if args.limit:
            doc_ids = doc_ids[: args.limit]

    if args.dry_run:
        print("\n=== DRY RUN MODE ===\n")
        logger.info("Dry-run mode enabled")

    # ── Process each doc ──────────────────────────────────────────────────────
    success_count = 0
    fail_count = 0

    for doc_id in doc_ids:
        if doc_id not in registry_entries:
            print(f"[FAIL] {doc_id[:16]}... - not in registry")
            fail_count += 1
            continue

        entry = registry_entries[doc_id]
        logger.info("Processing doc_id=%s branch=%s", doc_id, entry.get("branch", ""))
        label, success, message = upsert_doc(
            doc_id=doc_id,
            registry_entry=entry,
            trees_dir=MERKLE_TREES_DIR,
            chunks_dir=CHUNKS_DIR,
            emb_dir=EMBEDDINGS_DIR,
            client=client,
            dry_run=args.dry_run,
        )

        print(f"[{label}] {doc_id[:16]}... {message}")
        logger.info("[%s] %s %s", label, doc_id, message)

        if label == "SKIP":
            pass
        elif success:
            success_count += 1
            if not args.dry_run:
                apply_ingested_update(entry, chunk_count=entry.get("chunk_count", 0))
                _invalidate_query_cache_for_collection(entry.get("branch", ""))
        else:
            fail_count += 1

        # Throttle to avoid overwhelming Qdrant
        if not args.dry_run:
            time.sleep(0.5)

    # ── Save registry ──────────────────────────────────────────────────────────
    if not args.dry_run and (success_count > 0 or fail_count == 0):
        logger.info("Saving registry updates...")
        tmp = REGISTRY_PATH.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(registry, f, indent=2)
        tmp.rename(REGISTRY_PATH)
        logger.info("Registry saved to %s", REGISTRY_PATH)

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("=== SUMMARY === total=%d ingested=%d failed=%d", success_count + fail_count, success_count, fail_count)
    print("\n=== SUMMARY ===")
    print(f"Total:   {success_count + fail_count}")
    print(f"Ingested: {success_count}")
    print(f"Failed:  {fail_count}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
