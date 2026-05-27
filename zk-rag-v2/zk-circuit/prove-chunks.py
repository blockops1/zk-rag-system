#!/usr/bin/env python3
"""
prove-chunks.py — Generate a ZK proof for a document chunk.

Usage:
    python3 prove-chunks.py <doc_id> <chunk_index> [--output <path>] [--prove-bin <path>]

Example:
    python3 prove-chunks.py 00c8a75d605f10359503c9a25fa1255f8a8946e0dd9026646bf69750639b4669 40

Input data layout (per doc_id):
    ../data/chunks/<doc_id>/
        chunks.jsonl      — one JSON per line: {"chunk_id", "doc_id", "text"}
        chunk_ids.json    — list of chunk_id strings, index N = chunk N
    ../data/merkleTrees/<doc_id>_tree.json
        merkle_root, leaf_hashes, paths{<chunk_index>: {leaf_hash, siblings, leaf_index}}

Outputs:
    <ZK_PROOFS_DIR>/<doc_id>_<chunk_index>.json  — zkVerify-compatible proof JSON (same format as prove-bin)
    <ZK_PROOFS_DIR>/prove-chunks.log  — structured audit log
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from qdrant_client import QdrantClient

# ── Paths ────────────────────────────────────────────────────────────────────

CHUNKS_DIR = Path("../data/chunks")
MERKLE_TREES_DIR = Path("../data/merkleTrees")
ZK_PROOFS_DIR = Path("../data/zk_proofs")
DEFAULT_PROVE_BIN = Path("./zk-circuit/target/release/prove-bin")
LOG_DIR = ZK_PROOFS_DIR  # logs live alongside outputs for discoverability
LOG_FILE = LOG_DIR / "prove-chunks.log"

# ── Logging ────────────────────────────────────────────────────────────────────

def log(level: str, msg: str, **fields):
    """Write a structured JSON line to the audit log and print to stderr."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "script": "prove-chunks",
        "message": msg,
        **fields,
    }
    line = json.dumps(entry)

    # Always write to log file
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
        f.flush()

    # Also print to stderr so Telegram shows it immediately
    print(f"[{level.upper()}] {msg}", file=sys.stderr)
    if fields:
        print(f"    {fields}", file=sys.stderr)


def log_info(msg: str, **fields):
    log("INFO", msg, **fields)


def log_error(msg: str, **fields):
    log("ERROR", msg, **fields)


def log_warn(msg: str, **fields):
    log("WARN", msg, **fields)


# ── Load data for a doc ──────────────────────────────────────────────────────


def get_doc_ingestion(doc_id: str) -> tuple[int, int]:
    """
    Query Qdrant for a document's on-chain ingestion metadata.

    Returns (ingestion_timestamp, ingestion_block):
      - ingestion_timestamp: Unix timestamp from evm_block_timestamp
      - ingestion_block:    Block number from evm_block_number

    Qdrant stores these per-chunk from Pipeline G (all chunks of the same doc
    share the same values). We query the first available chunk and take its values.

    Returns (0, 0) if the doc is not found in Qdrant yet.
    """
    client = QdrantClient(url="http://127.0.0.1:6333")

    # Try each collection (docs are split by branch)
    for collection in ["army", "navy", "marines", "other"]:
        try:
            results = client.scroll(
                collection_name=collection,
                scroll_filter={"must": [{"key": "doc_id", "match": {"value": doc_id}}]},
                limit=1,
                with_payload=True,
            )
            points = results[0]
            if points:
                payload = points[0].payload
                ts_str = payload.get("evm_block_timestamp", "")
                block_num = payload.get("evm_block_number", 0)

                # Parse ISO timestamp → Unix epoch
                if ts_str:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    ts = int(dt.timestamp())
                else:
                    ts = 0

                log_info(
                    "Fetched ingestion metadata from Qdrant",
                    doc_id=doc_id,
                    collection=collection,
                    ingestion_timestamp=ts,
                    ingestion_block=block_num,
                )
                return ts, block_num
        except Exception:
            # Collection might not exist or other error — try next
            continue

    log_warn("Doc not found in Qdrant, using placeholder 0/0", doc_id=doc_id)
    return 0, 0


def load_merkle_tree(doc_id: str) -> dict:
    tree_path = MERKLE_TREES_DIR / f"{doc_id}_tree.json"
    log_info("Loading merkle tree", path=str(tree_path))
    if not tree_path.exists():
        log_error("Merkle tree file not found", path=str(tree_path))
        raise FileNotFoundError(tree_path)
    with open(tree_path) as f:
        return json.load(f)


def load_chunk(doc_id: str, chunk_index: int) -> tuple[str, str]:
    """
    Load chunk text and chunk_id from chunks.jsonl by line index.
    Returns (chunk_id, text).
    """
    chunk_dir = CHUNKS_DIR / doc_id
    chunk_ids_path = chunk_dir / "chunk_ids.json"
    chunks_path = chunk_dir / "chunks.jsonl"

    if not chunk_ids_path.exists():
        log_error("chunk_ids.json not found", path=str(chunk_ids_path))
        raise FileNotFoundError(chunk_ids_path)

    with open(chunk_ids_path) as f:
        chunk_ids: list[str] = json.load(f)

    if chunk_index < 0 or chunk_index >= len(chunk_ids):
        log_error(
            "chunk_index out of range",
            chunk_index=chunk_index,
            min_index=0,
            max_index=len(chunk_ids) - 1,
        )
        raise ValueError(
            f"chunk_index {chunk_index} out of range [0, {len(chunk_ids) - 1}]"
        )

    chunk_id = chunk_ids[chunk_index]

    with open(chunks_path) as f:
        for i, line in enumerate(f):
            if i == chunk_index:
                obj = json.loads(line)
                assert obj["chunk_id"] == chunk_id
                return chunk_id, obj["text"]

    log_error("Chunk not found in chunks.jsonl", chunk_index=chunk_index, chunk_id=chunk_id)
    raise RuntimeError(f"Chunk {chunk_index} not found in chunks.jsonl")


# ── Prove ─────────────────────────────────────────────────────────────────────


def build_prove_input(doc_id: str, chunk_index: int, tree_data: dict,
                      ingestion_timestamp: int = 0, ingestion_block: int = 0) -> dict:
    """
    Build the JSON input for prove-bin from doc data.

    **Option A (2026-04-22):** The circuit receives leaf_hash as a private witness.
    The pre-computed Poseidon hash of the chunk text is stored in the merkle tree JSON
    (computed during Pipeline E ingestion). We read it directly — no in-circuit hashing.

    ingestion_timestamp / ingestion_block: on-chain commitment metadata.
    Currently defaults to 0 — wire up to actual emit tx data (Phase L / registry lookup)
    once that pipeline is in place.
    """
    path_key = str(chunk_index)
    if path_key not in tree_data.get("paths", {}):
        log_error(
            "No path found for chunk_index",
            chunk_index=chunk_index,
            available_keys=list(tree_data.get("paths", {}).keys()),
        )
        raise ValueError(f"No path found for chunk_index {chunk_index} in tree data")

    path_info = tree_data["paths"][path_key]
    siblings = [s["hash"] for s in path_info["siblings"]]
    depth = len(siblings)
    merkle_root = tree_data["merkle_root"]
    leaf_hash = path_info["leaf_hash"]

    # document_hash: stored as leaf[0] in the merkle tree
    leaf_hashes = tree_data.get("leaf_hashes", [])
    document_hash = leaf_hashes[0] if leaf_hashes else ""

    log_info(
        "Built prove input",
        merkle_root=merkle_root[:20] + "...",
        leaf_hash=leaf_hash[:20] + "...",
        depth=depth,
        leaf_index=chunk_index,
        ingestion_timestamp=ingestion_timestamp,
        ingestion_block=ingestion_block,
    )

    return {
        "leaf_hash": leaf_hash,
        "document_hash": document_hash,
        "merkle_root": merkle_root,
        "leaf_index": chunk_index,
        "depth": depth,
        "siblings": siblings,
        "ingestion_timestamp": ingestion_timestamp,
        "ingestion_block": ingestion_block,
    }


def run_prove(prove_bin: Path, input_data: dict, output_path: Path) -> dict:
    """Run prove-bin with the given input, write output to file."""
    import tempfile

    input_json = json.dumps(input_data, indent=2)
    log_info("Calling prove-bin", prove_bin=str(prove_bin), input_preview=input_json[:200])

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(input_json)
        tmp_path = f.name

    try:
        # Point prove-bin at the pre-built circuit cache
        os.environ["CIRCUIT_DIR"] = "./zk-circuit"
        result = subprocess.run(
            [str(prove_bin), tmp_path],
            capture_output=True,
            text=True,
            check=False,  # don't raise — handle errors below
            timeout=60,
        )
    finally:
        os.unlink(tmp_path)

    if result.returncode != 0:
        log_error(
            "prove-bin failed",
            returncode=result.returncode,
            stdout=result.stdout[:500] if result.stdout else "",
            stderr=result.stderr[:1000] if result.stderr else "",
        )
        raise RuntimeError(f"prove-bin exited with code {result.returncode}: {result.stderr}")

    # prove-bin outputs JSON to stdout
    try:
        proof_output = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        log_error(
            "prove-bin output is not valid JSON",
            stdout=result.stdout[:1000],
            error=str(e),
        )
        raise

    log_info(
        "Proof generated successfully",
        output=str(output_path),
        proof_b64_preview=proof_output.get("proof_b64", "N/A")[:20] + "...",
    )

    with open(output_path, "w") as f:
        json.dump(proof_output, f, indent=2)

    return proof_output


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Generate ZK proof for a document chunk")
    parser.add_argument("doc_id", help="Document ID (64-char hex)")
    parser.add_argument("chunk_index", type=int, help="Chunk index (0-based)")
    parser.add_argument(
        "--prove-bin",
        type=Path,
        default=DEFAULT_PROVE_BIN,
        help="Path to prove-bin binary",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: <ZK_PROOFS_DIR>/<doc_id>_<chunk_index>.json)",
    )
    parser.add_argument(
        "--ingestion-timestamp",
        type=int,
        default=0,
        help="Unix timestamp of the on-chain root emission (default: 0, placeholder)",
    )
    parser.add_argument(
        "--ingestion-block",
        type=int,
        default=0,
        help="Block number of the on-chain root emission (default: 0, placeholder)",
    )
    args = parser.parse_args()

    doc_id = args.doc_id
    chunk_index = args.chunk_index

    log_info(
        "=== prove-chunks started ===",
        doc_id=doc_id,
        chunk_index=chunk_index,
        prove_bin=str(args.prove_bin),
    )

    # Output path
    if args.output:
        output_path = args.output
    else:
        ZK_PROOFS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = ZK_PROOFS_DIR / f"{doc_id}_{chunk_index}.json"

    log_info(f"Output path: {output_path}")

    try:
        # Load data
        tree_data = load_merkle_tree(doc_id)
        chunk_id, _ = load_chunk(doc_id, chunk_index)
        log_info("Loaded chunk", chunk_id=chunk_id)

        # Fetch on-chain ingestion metadata from Qdrant
        ingestion_timestamp, ingestion_block = get_doc_ingestion(doc_id)

        # Build proof input
        prove_input = build_prove_input(
            doc_id,
            chunk_index,
            tree_data,
            ingestion_timestamp=ingestion_timestamp,
            ingestion_block=ingestion_block,
        )

        # Run prove-bin
        proof = run_prove(args.prove_bin, prove_input, output_path)

        log_info(
            "=== prove-chunks completed ===",
            output=str(output_path),
            proof_size=len(proof.get("proof_b64", "")),
        )

        # Print summary to stdout (not stderr) for normal output
        print(f"OK: {output_path}", file=sys.stdout)

    except Exception as e:
        log_error("prove-chunks failed", error=str(e), error_type=type(e).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()
