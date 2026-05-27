#!/usr/bin/env python3
"""
test-from-qdrant.py

End-to-end ZK proof generation using data sourced entirely from Qdrant.
No disk I/O for proof inputs — siblings[], poseidon_doc_id_hash, leaf_hash,
merkle_root, depth all come from Qdrant payload.

Usage:
    python3 test-from-qdrant.py                          # random chunk from random collection
    python3 test-from-qdrant.py --collection army       # random chunk from a specific collection
    python3 test-from-qdrant.py --chunk-id <id>         # specific chunk by ID
    python3 test-from-qdrant.py --list                  # list collections and exit
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

# Qdrant
from qdrant_client import QdrantClient

ZK_PROOFS_DIR = Path("../data/zk_proofs")

PROVE_BIN = os.environ.get(
    "PROVE_BIN",
    "./zk-circuit/target/release/prove-bin",
)
QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
QDRANT_PATH = None  # Use URL mode (API server); path is for local-only mode


@dataclass
class QdrantChunkProofInput:
    chunk_id: str
    doc_id: str
    leaf_hash: str          # Poseidon(chunk_text)
    document_hash: str      # Poseidon(doc_id) = poseidon_doc_id_hash = leaf[0]
    merkle_root: str
    leaf_index: int
    depth: int
    siblings: list[str]
    text: str               # chunk text (for display only)
    ingestion_timestamp: int  # Unix epoch seconds
    ingestion_block: int     # EVM block number


def parse_qdrant_point(point) -> QdrantChunkProofInput:
    """Extract proof inputs from a Qdrant point payload."""
    payload = point.payload

    # Parse chunk_id from point ID
    chunk_id = str(point.id)

    # Parse doc_id — stored in payload["doc_id"] or derived from chunk_id
    doc_id = payload.get("doc_id", "")

    # Core proof inputs — depth may be stored as string or int
    depth_val = payload.get("merkle_tree_depth")
    depth = int(depth_val) if depth_val is not None else 0

    leaf_index_val = payload.get("merkle_leaf_index")
    leaf_index = int(leaf_index_val) if leaf_index_val is not None else 0

    leaf_hash = payload["merkle_leaf_hash"]
    document_hash = payload["poseidon_doc_id_hash"]
    merkle_root = payload["merkle_root"]
    siblings = payload["merkle_siblings"]

    # Chunk text (for display)
    text = payload.get("text", "")

    # On-chain ingestion metadata
    ingestion_block = int(payload.get("evm_block_number", 0))
    ts_str = payload.get("evm_block_timestamp", "")
    if ts_str:
        # ISO-8601 → Unix epoch
        ingestion_timestamp = int(__import__("datetime").datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())
    else:
        ingestion_timestamp = 0

    return QdrantChunkProofInput(
        chunk_id=chunk_id,
        doc_id=doc_id,
        leaf_hash=leaf_hash,
        document_hash=document_hash,
        merkle_root=merkle_root,
        leaf_index=leaf_index,
        depth=depth,
        siblings=siblings,
        text=text,
        ingestion_timestamp=ingestion_timestamp,
        ingestion_block=ingestion_block,
    )


def prove_input_to_json(inp: QdrantChunkProofInput) -> dict:
    """Build the JSON input for prove-bin."""
    return {
        "leaf_hash": inp.leaf_hash,
        "document_hash": inp.document_hash,
        "merkle_root": inp.merkle_root,
        "leaf_index": inp.leaf_index,
        "depth": inp.depth,
        "siblings": inp.siblings,
        "ingestion_timestamp": inp.ingestion_timestamp,
        "ingestion_block": inp.ingestion_block,
    }


def generate_proof(input_json: dict, prove_bin: str) -> dict:
    """Call prove-bin (input via stdin file, output via stdout) and return parsed JSON."""
    input_file = f"/tmp/prove_input_{uuid.uuid4().hex[:8]}.json"
    try:
        with open(input_file, "w") as f:
            json.dump(input_json, f)

        result = subprocess.run(
            [prove_bin, input_file],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            print(f"[STDERR]\n{result.stderr}", file=sys.stderr)
            raise RuntimeError(f"prove-bin exited with code {result.returncode}")

        return json.loads(result.stdout)
    finally:
        if os.path.exists(input_file):
            os.remove(input_file)


def save_proof(output: dict, doc_id: str) -> Path:
    """Save proof JSON to zk_proofs directory, keyed by doc_id."""
    ZK_PROOFS_DIR.mkdir(parents=True, exist_ok=True)
    path = ZK_PROOFS_DIR / f"{doc_id}_kurier.json"
    with open(path, "w") as f:
        json.dump(output, f, indent=2)
    return path


def verify_proof_locally(output: dict) -> bool:
    """Verify the proof using verify-zk-proof binary."""
    verify_bin = os.environ.get(
        "VERIFY_BIN",
        "./zk-circuit/target/release/verify-zk-proof",
    )
    input_file = f"/tmp/verify_input_{uuid.uuid4().hex[:8]}.json"
    try:
        with open(input_file, "w") as f:
            json.dump(output, f)

        result = subprocess.run(
            [verify_bin, input_file],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0
    finally:
        if os.path.exists(input_file):
            os.remove(input_file)


def list_collections(client: QdrantClient) -> dict[str, int]:
    """List all collections and their point counts."""
    collections = client.get_collections().collections
    result = {}
    for col in collections:
        info = client.get_collection(col.name)
        result[col.name] = info.points_count
    return result


def pick_random_chunk(client: QdrantClient, collection: str) -> QdrantChunkProofInput:
    """Pick a random chunk from a collection by scanning all points."""
    offset = None
    all_points = []
    while True:
        response = client.scroll(
            collection_name=collection,
            scroll_filter=None,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        # scroll returns (points_list, next_page_offset) tuple
        pts, next_offset = response if isinstance(response, tuple) else (response, None)
        all_points.extend(pts)
        if next_offset is None:
            break
        offset = next_offset

    if not all_points:
        raise ValueError(f"No points found in collection '{collection}'")

    chosen = random.choice(all_points)

    return parse_qdrant_point(chosen)


def main():
    parser = argparse.ArgumentParser(description="ZK proof from Qdrant — no disk I/O for proof inputs")
    parser.add_argument("--collection", type=str, default=None,
                        help="Qdrant collection to pick a random chunk from")
    parser.add_argument("--chunk-id", type=str, default=None,
                        help="Specific chunk_id (point ID) to prove")
    parser.add_argument("--list", action="store_true",
                        help="List collections and point counts, then exit")
    parser.add_argument("--prove-bin", type=str, default=PROVE_BIN,
                        help=f"Path to prove-bin (default: {PROVE_BIN})")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip local verification step")
    args = parser.parse_args()

    client = QdrantClient(url=QDRANT_URL)

    # List mode
    if args.list:
        collections = list_collections(client)
        print("Collections:")
        for name, count in sorted(collections.items()):
            print(f"  {name}: {count} points")
        return

    # Load prove binary
    if not os.path.exists(args.prove_bin):
        print(f"ERROR: prove-bin not found at {args.prove_bin}", file=sys.stderr)
        sys.exit(1)

    # Select chunk
    if args.chunk_id:
        # chunk_id is stored in payload, not as point ID — search via scroll filter
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        found = None
        found_collection = None
        for col_name in client.get_collections().collections:
            col_name_str = col_name.name if hasattr(col_name, 'name') else str(col_name)
            results = client.scroll(
                collection_name=col_name_str,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="chunk_id",
                            match=MatchValue(value=args.chunk_id),
                        )
                    ]
                ),
                limit=1,
                with_payload=True,
            )
            pts = results[0] if isinstance(results, tuple) else results
            if pts:
                found = parse_qdrant_point(pts[0])
                found_collection = col_name_str
                break
        if found is None:
            print(f"ERROR: chunk_id '{args.chunk_id}' not found in any collection", file=sys.stderr)
            sys.exit(1)
        chunk = found
        collection = found_collection
    elif args.collection:
        collection = args.collection
        chunk = pick_random_chunk(client, collection)
    else:
        # Random across all collections
        collections = list_collections(client)
        if not collections:
            print("ERROR: No collections found in Qdrant", file=sys.stderr)
            sys.exit(1)
        collection = random.choice(list(collections.keys()))
        chunk = pick_random_chunk(client, collection)

    print(f"Collection : {collection}")
    print(f"Chunk ID   : {chunk.chunk_id}")
    print(f"Doc ID     : {chunk.doc_id}")
    print(f"Depth      : {chunk.depth}")
    print(f"Leaf index : {chunk.leaf_index}")
    print(f"Merkle root: {chunk.merkle_root[:40]}...")
    print(f"Leaf hash  : {chunk.leaf_hash[:40]}...")
    print(f"Doc hash   : {chunk.document_hash[:40]}...")
    print(f"Siblings   : {len(chunk.siblings)} values")
    print(f"Text preview: {chunk.text[:80]!r}...")
    print()

    # Build prove-bin input
    prove_input = prove_input_to_json(chunk)
    print(f"[{time.strftime('%H:%M:%S')}] Calling prove-bin...")
    t0 = time.perf_counter()

    output = generate_proof(prove_input, args.prove_bin)
    prove_ms = (time.perf_counter() - t0) * 1000

    # Save proof JSON to zk_proofs/
    save_path = save_proof(output, chunk.doc_id)
    print(f"  Saved to : {save_path.name}")

    pubs = output.get("public_inputs", {})
    print(f"[{time.strftime('%H:%M:%S')}] Proof generated in {prove_ms:.1f}ms")
    print(f"  Proof size    : {len(output.get('proof_hex', ''))} hex chars")
    print("  Public inputs :")
    print(f"    merkle_root  : {pubs.get('merkle_root', '')}")
    print(f"    document_hash: {pubs.get('document_hash', '')}")
    print(f"    leaf_hash    : {pubs.get('leaf_hash', '')}")
    print(f"    ingestion_ts : {pubs.get('ingestion_timestamp', '')}")
    print(f"    ingestion_blk: {pubs.get('ingestion_block', '')}")
    print()

    # Verify locally
    if args.skip_verify:
        print("[VERIFY] Skipped (--skip-verify)")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] Verifying proof locally...")
        t1 = time.perf_counter()
        valid = verify_proof_locally(output)
        verify_ms = (time.perf_counter() - t1) * 1000
        if valid:
            print(f"[VERIFY] ✅ VALID — {verify_ms:.1f}ms")
        else:
            print(f"[VERIFY] ❌ INVALID — {verify_ms:.1f}ms", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
