#!/usr/bin/env python3
"""
emit_all.py -- Batch emit Merkle roots to MerkleRootRegistry V2 contract.

Reads doc_ids from tree files in ../data/merkleTrees/, looks up
merkle_root and pdf_hash from tree JSON / registry, calls AppendRootV2.s.sol,
and writes emission records back to the registry.

Usage:
    python3 emit_all.py --dry-run --batch
    python3 emit_all.py --batch
    python3 emit_all.py --doc-id <doc_id>
    python3 emit_all.py --batch --verify

Registry emit fields (per doc):
    emitted_testnet:
        status:     "emitted" | "failed"
        tx_hash:    hex string or "unknown"
        emitted_at: ISO8601 timestamp
        chain_id:   2651420 (testnet) | 26514 (mainnet)

Logging:
    Always written to ../data/logs/emit_all_debug_YYYYMMDD.log (every invocation).
    --verify: Also writes on-chain verification results to emit_all_verify_YYYYMMDD.log

Env vars required (for non-dry-run):
    DEPLOYER_KEY -- Private key for contract interaction (NEVER logged)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ── Configuration ─────────────────────────────────────────────────────────────

MERKLE_TREES_DIR = Path("../data/merkleTrees")
REGISTRY_PATH    = Path("../data/registry.json")
LOG_DIR          = Path("../data/logs")

# Active network selection — read from .env ACTIVE_NETWORK (testnet | mainnet)
_ACTIVE_NETWORK = os.environ.get("ACTIVE_NETWORK", "testnet").strip().lower()

if _ACTIVE_NETWORK == "mainnet":
    CHAIN_ID         = int(os.environ.get("MAINNET_CHAIN_ID",  "26514"))
    RPC_URL          = os.environ.get("MAINNET_RPC_URL",       "https://horizen.calderachain.xyz/http")
    CONTRACT_ADDRESS = os.environ.get("MAINNET_CONTRACT_ADDRESS", "")
    EXPLORER_URL    = os.environ.get("MAINNET_EXPLORER",       "https://horizen.calderaexplorer.xyz")
elif _ACTIVE_NETWORK == "testnet":
    CHAIN_ID         = int(os.environ.get("TESTNET_CHAIN_ID",   "2651420"))
    RPC_URL          = os.environ.get("TESTNET_RPC_URL",        "https://horizen-testnet.rpc.caldera.xyz")
    CONTRACT_ADDRESS = os.environ.get("TESTNET_CONTRACT_ADDRESS", "0x83166A340c0A61bc836BD6383aD4acB23a3E3176")
    EXPLORER_URL     = os.environ.get("TESTNET_EXPLORER",       "https://horizen-testnet.explorer.caldera.xyz")
else:
    raise ValueError(f"ACTIVE_NETWORK must be 'testnet' or 'mainnet', got '{_ACTIVE_NETWORK}'")
SCRIPT_V2_PATH   = Path(__file__).parent / "script" / "AppendRootV2.s.sol"
BATCH_SCRIPT_PATH = Path(__file__).parent / "script" / "CommitBatchV2.s.sol"
MAX_BATCH_SIZE   = 200   # max docs per Forge script call (sub-batch)
SUB_BATCH_SIZE   = 15    # max docs per single Forge call (avoids EVM memory OOG)


# ── Logging helpers ────────────────────────────────────────────────────────────

def _sanitized_env_for_log(env: dict) -> dict:
    """Return env dict with sensitive values masked for logging."""
    sanitized = dict(env)
    for key in ["DEPLOYER_KEY", "RPC_URL", "CONTRACT_ADDRESS"]:
        if key in sanitized:
            val = sanitized[key]
            if key == "DEPLOYER_KEY":
                sanitized[key] = "<REDACTED>"
            elif len(str(val)) > 8:
                sanitized[key] = str(val)[:8] + "..."
    return sanitized


def _extract_revert_reason(output: str) -> str:
    """
    Extract the LAST Solidity revert reason from forge trace output.
    The last Revert] is the deepest in the call stack = actual failure reason.
    """
    pattern = r"Revert\]\s*(MerkleRootRegistry:[^\n]+)"
    matches = re.findall(pattern, output)
    if matches:
        return matches[-1].strip()
    revert_pattern = r"Revert\]\s*([^\n]+)"
    revert_matches = re.findall(revert_pattern, output)
    if revert_matches:
        return revert_matches[-1].strip()
    return "unknown_revert"


def _write_error_log(
    doc_id: str,
    error_type: str,
    message: str,
    forge_rc: int,
) -> None:
    """Write one JSON entry to the error log. Never logs DEPLOYER_KEY."""
    today = datetime.now().strftime("%Y%m%d")
    error_log_path = LOG_DIR / f"emit_all_errors_{today}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "doc_id": doc_id,
        "error_type": error_type,
        "message": message,
        "forge_rc": forge_rc,
    }

    with open(error_log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Registry helpers ──────────────────────────────────────────────────────────

def load_registry() -> tuple[dict, dict]:
    """
    Load full registry.

    Returns (registry_data, doc_id_index):
      - registry_data: the full parsed JSON (top-level object with 'documents' list)
      - doc_id_index:  doc_id -> index into registry_data['documents']
    """
    with open(REGISTRY_PATH, "r") as f:
        registry_data = json.load(f)

    doc_id_index = {
        doc["doc_id"]: idx
        for idx, doc in enumerate(registry_data["documents"])
    }
    return registry_data, doc_id_index


LOCK_PATH = Path("../data/registry.lock")

def save_registry(registry_data: dict) -> None:
    """Atomically write registry with exclusive file lock to prevent concurrent writes."""
    import fcntl
    debug_log_path = LOG_DIR / f"emit_all_debug_{datetime.now().strftime('%Y%m%d')}.log"
    with open(debug_log_path, "a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()}  [DEBUG]  save_registry  START  path={REGISTRY_PATH}  docs={len(registry_data.get('documents',[]))}\n")

    # Acquire exclusive lock — blocks until available
    with open(LOCK_PATH, "w") as lock_fd:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        try:
            tmp = REGISTRY_PATH.with_suffix(".json.tmp")
            with open(debug_log_path, "a") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()}  [DEBUG]  save_registry  LOCK_ACQUIRED\n")

            # Step 1: write to tmp
            with open(tmp, "w") as f:
                json.dump(registry_data, f, indent=2)
            tmp_size = tmp.stat().st_size
            with open(debug_log_path, "a") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()}  [DEBUG]  save_registry  TMP_WRITTEN  tmp_size={tmp_size}\n")

            # Step 2: atomic rename
            tmp.rename(REGISTRY_PATH)
            with open(debug_log_path, "a") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()}  [DEBUG]  save_registry  RENAME_DONE\n")

            # Step 3: verify
            written_mtime = REGISTRY_PATH.stat().st_mtime
            with open(REGISTRY_PATH, "r") as f:
                written = json.load(f)
            written_count = len(written.get("documents", []))
            expected_count = len(registry_data.get("documents", []))
            with open(debug_log_path, "a") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()}  [DEBUG]  save_registry  VERIFIED  written_count={written_count}  expected={expected_count}  mtime={written_mtime}\n")
            if written_count != expected_count:
                raise RuntimeError(
                    f"save_registry verification failed: wrote {written_count} docs "
                    f"but expected {expected_count}"
                )
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            with open(debug_log_path, "a") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()}  [DEBUG]  save_registry  LOCK_RELEASED\n")


def safe_save_registry(registry_data: dict, operation: str = "save") -> bool:
    """Save with full exception handling. Returns True on success, False on failure."""
    try:
        save_registry(registry_data)
        return True
    except Exception as e:
        err_log_path = LOG_DIR / f"emit_all_errors_{datetime.now().strftime('%Y%m%d')}.log"
        with open(err_log_path, "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operation": operation,
                "error_type": "save_registry_failed",
                "message": str(e),
            }) + "\n")
        print(f"  [FAIL] Registry {operation} FAILED: {e}")
        return False


def is_already_emitted(registry_data: dict, doc_id_index: dict, doc_id: str) -> bool:
    """
    Check if doc has already been emitted (to testnet or mainnet).

    Handles both the new dict format (emitted_testnet: {status: "emitted", ...})
    and the old boolean format (emitted_testnet: false) that may exist in the
    registry from earlier schema versions.
    """
    if doc_id in doc_id_index:
        entry = registry_data["documents"][doc_id_index[doc_id]]
        for net in ("emitted_testnet", "emitted_mainnet"):
            val = entry.get(net)
            # New format: dict with status field
            if isinstance(val, dict) and val.get("status") == "emitted":
                return True
            # Old format: boolean True means emitted
            if isinstance(val, bool) and val is True:
                return True

    return False


def write_emission_record(
    registry_data: dict,
    doc_id_index: dict,
    doc_id: str,
    status: str,          # "emitted" | "failed"
    tx_hash: str,
    block_number: str = "0",
    error_msg: str | None = None,
) -> None:
    """
    Write an emission record into the registry for this doc.
    Updates the registry in-memory; caller must call save_registry().
    """
    if doc_id not in doc_id_index:
        return

    idx = doc_id_index[doc_id]
    entry = registry_data["documents"][idx]

    record = {
        "status": status,
        "tx_hash": tx_hash,
        "block_number": int(block_number) if block_number and block_number != "" else 0,
        "chain_id": CHAIN_ID,
        "explorer_url": f"{EXPLORER_URL}/tx/0x{tx_hash.lstrip('0x')}" if tx_hash and tx_hash != "unknown" else "",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_msg:
        record["error"] = error_msg

    key = "emitted_testnet" if CHAIN_ID == 2651420 else "emitted_mainnet"
    entry[key] = record


# ── Merkle tree helpers ────────────────────────────────────────────────────────

def get_tree_files() -> list[Path]:
    """Get all _tree.json files from merkle trees directory."""
    return sorted(MERKLE_TREES_DIR.glob("*_tree.json"))


def extract_doc_id(filepath: Path) -> str:
    """Strip '_tree' suffix: 'abc..._tree.json' -> 'abc...'"""
    stem = filepath.stem
    if stem.endswith("_tree"):
        return stem[:-5]
    return stem


# ── On-chain verification helpers ────────────────────────────────────────────────

def check_onchain_root_count(doc_id: str) -> int | None:
    """
    Query the contract to get how many roots are on-chain for this doc_id.
    Returns None on error (transient RPC failure), 0 if doc has no roots on chain.
    """
    cmd = [
        "cast", "call",
        CONTRACT_ADDRESS,
        "getRootCount(bytes32)(uint256)",
        doc_id,
        "--rpc-url", RPC_URL,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return int(result.stdout.strip(), 10)
    except Exception:
        pass
    return None


def check_onchain_root(doc_id: str, index: int) -> str | None:
    """
    Get the root at on-chain index for this doc_id.
    Returns merkle_root hex string or None.
    """
    cmd = [
        "cast", "call",
        CONTRACT_ADDRESS,
        "getRootEntry(bytes32,uint256)((bytes32,bytes32,uint32,uint40,uint40,address))",
        doc_id, str(index),
        "--rpc-url", RPC_URL,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            # Tuple: (padding[0], root, chunkCount, blockNumber, timestamp, uploader)
            fields = [f.strip() for f in result.stdout.strip().lstrip("(").rstrip(")").split(",")]
            if len(fields) >= 2:
                return fields[1]
    except Exception:
        pass
    return None


def is_onchain_emitted(doc_id: str, expected_root: str) -> bool:
    """
    Check if the expected merkle_root is already on-chain for this doc_id.
    Queries getRootCount then iterates getRootEntry to compare roots.
    Returns True if expected_root is found. False if not found or RPC error.
    """
    count = check_onchain_root_count(doc_id)
    if count is None or count == 0:
        return False

    for i in range(count):
        root = check_onchain_root(doc_id, i)
        if root and root.lower() == expected_root.lower():
            return True
    return False


# ── Core emit logic ────────────────────────────────────────────────────────────


def run_append_root_v2(
    doc_id: str,
    pdf_hash: str,
    chunk_count: int,
    merkle_root: str,
    tree_depth: int,
    padded_leaf_count: int,
    dry_run: bool,
    verify: bool = False,
) -> tuple[bool, str, str]:
    """
    Run forge script to append root for a single document (V2: single merkle_root mode).

    Reads merkle_root from the tree JSON already loaded by the caller.
    Passes doc_id, merkle_root, pdf_hash, chunk_count, tree_depth, padded_leaf_count
    to AppendRootV2.s.sol.

    Returns (success: bool, tx_hash: str, block_number: str)
    """
    today = datetime.now().strftime("%Y%m%d")
    debug_log_path   = LOG_DIR / f"emit_all_debug_{today}.log"
    verify_log_path  = LOG_DIR / f"emit_all_verify_{today}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    foundry_bin = os.path.expanduser("~/.foundry/bin")
    if foundry_bin not in env.get("PATH", ""):
        env["PATH"] = foundry_bin + ":" + env.get("PATH", "")

    env["CONTRACT_ADDRESS"] = CONTRACT_ADDRESS
    env["DOC_ID"]           = doc_id
    env["MERKLE_ROOT"]       = merkle_root
    env["PDF_HASH"]          = pdf_hash
    env["CHUNK_COUNT"]       = str(chunk_count)
    env["TREE_DEPTH"]        = str(tree_depth)
    env["PADDED_LEAF_COUNT"] = str(padded_leaf_count)
    env["RPC_URL"]           = RPC_URL

    deployer_key = os.environ.get("DEPLOYER_KEY")
    if not deployer_key:
        msg = "DEPLOYER_KEY environment variable not set"
        _write_error_log(doc_id, "missing_key", msg, -1)
        return False, "", ""

    env["DEPLOYER_KEY"] = deployer_key

    cmd = [
        "forge", "script", str(SCRIPT_V2_PATH),
        "--rpc-url", RPC_URL,
        "-vvv",
        "--private-key", deployer_key,
    ]
    if not dry_run:
        cmd.append("--broadcast")
    # Dry-run: Forge simulates with msg.sender=derived(private_key), no broadcast

    # ── Always write debug log; --debug only controls verbosity to stderr ──
    def _write_debug(label: str, rc: int, out: str) -> None:
        safe_env = _sanitized_env_for_log(env)
        with open(debug_log_path, "a") as f:
            f.write(f"\n===== {datetime.now(timezone.utc).isoformat()} [{label}] =====\n")
            f.write(f"doc_id:            {doc_id}\n")
            f.write(f"merkle_root:       {merkle_root}\n")
            f.write(f"pdf_hash:          {pdf_hash}\n")
            f.write(f"chunk_count:       {chunk_count}\n")
            f.write(f"tree_depth:        {tree_depth}\n")
            f.write(f"padded_leaf_count: {padded_leaf_count}\n")
            f.write(f"dry_run:           {dry_run}\n")
            f.write(f"forge_rc:    {rc}\n")
            f.write(f"env (sanitized): {json.dumps(safe_env, indent=2)}\n")
            f.write("\n--- command ---")
            for part in cmd:
                if part == deployer_key if not dry_run else False:
                    f.write(" <REDACTED_KEY>")
                else:
                    f.write(f" {part}")
            f.write("\n--- forge stdout+stderr ---\n")
            f.write(out)
            f.write("\n")

    # Rate-limit: 100ms pause between Forge invocations to avoid RPC 429s
    time.sleep(0.1)

    try:
        result = subprocess.run(
            cmd,
            env=env,
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = result.stdout + result.stderr

        # Always write debug log (failure details are useless without it)
        _write_debug("V2", result.returncode, output)

        if result.returncode == 0:
            tx_hash = "simulated" if dry_run else "unknown"
            block_number = "0" if dry_run else ""
            if not dry_run:
                # Parse tx hash AND block number from broadcast receipt file
                broadcast_dir = Path(__file__).parent / "broadcast" / SCRIPT_V2_PATH.name / str(CHAIN_ID)
                print(f"  [run_append_root_v2] looking for broadcast receipt in: {broadcast_dir}")
                print(f"  [run_append_root_v2] dry_run={dry_run}  broadcast_dir exists={broadcast_dir.exists()}")
                try:
                    broadcast_files = sorted(broadcast_dir.glob("run-*.json"), key=os.path.getmtime)
                    print(f"  [run_append_root_v2] found {len(broadcast_files)} broadcast files: {[f.name for f in broadcast_files[-3:]]}")
                    if broadcast_files:
                        receipt_file = broadcast_files[-1]
                        print(f"  [run_append_root_v2] reading receipt: {receipt_file}")
                        with open(receipt_file) as bf:
                            data = json.load(bf)
                        receipts = data.get("receipts", [])
                        print(f"  [run_append_root_v2] receipts in file: {len(receipts)}")
                        for receipt in receipts:
                            h = receipt.get("transactionHash", "")
                            bn = receipt.get("blockNumber", "")
                            status = receipt.get("status", "")
                            print(f"    receipt: tx_hash={h[:20] if h else ''}... block={bn} status={status}")
                            # Only treat as success if on-chain status is 1 (confirmed)
                            if h.startswith("0x") and len(h) == 66 and status == "0x1":
                                tx_hash = h
                                block_number = str(int(bn, 16)) if bn else "0"
                                print(f"  [run_append_root_v2] MATCHED tx_hash={tx_hash}  block_number={block_number}")
                                break
                        else:
                            print("  [run_append_root_v2] WARN: no valid receipt with 0x tx_hash found in file")
                    else:
                        print(f"  [run_append_root_v2] WARN: no broadcast files found in {broadcast_dir}")
                except Exception as e:
                    # Log and WARN — but do NOT silently return success with "unknown" tx.
                    # Return failure so caller records it as "failed" rather than "emitted"
                    # with a phantom tx_hash. The on-chain record is already there;
                    # registry update can be re-run manually once the tx hash is known.
                    import traceback
                    err_msg = f"V2 single-doc receipt parse error: {e}"
                    print(f"  [FAIL] {err_msg}")
                    _write_debug("RECEIPT_PARSE_ERROR", -1, err_msg + "\n" + traceback.format_exc())
                    return False, "", ""

            if verify and not dry_run:
                with open(verify_log_path, "a") as f:
                    f.write(f"\n===== {datetime.now(timezone.utc).isoformat()} [V2] =====\n")
                    f.write(f"doc_id: {doc_id}\n")
                    f.write(f"merkle_root: {merkle_root}\n")
                    f.write(f"tx_hash: {tx_hash}\n")
                    f.write(f"block_number: {block_number}\n")
                    f.write("status: EMIT success, on-chain verify pending\n")

            return True, tx_hash, block_number

        else:
            revert_reason = _extract_revert_reason(output)
            error_msg = f"error={revert_reason}"
            _write_error_log(doc_id, "forge_revert", error_msg, result.returncode)
            return False, "", ""

    except subprocess.TimeoutExpired:
        _write_debug("TIMEOUT", -1, "(process timed out after 300s)")
        msg = "error=timeout"
        _write_error_log(doc_id, "timeout", msg, -1)
        return False, "", ""
    except Exception as e:
        _write_debug("EXCEPTION", -1, str(e))
        msg = f"error={str(e)[:200]}"
        _write_error_log(doc_id, "exception", msg, -1)
        return False, "", ""


# ── Batch forge mode — calls CommitBatchV2.s.sol directly ─────────────────────

def _load_emitted_ids(registry_data: dict, doc_id_index: dict) -> set[str]:
    """Return set of doc_ids already emitted for the active chain."""
    key = "emitted_testnet" if CHAIN_ID == 2651420 else "emitted_mainnet"
    emitted = set()
    for doc_id, idx in doc_id_index.items():
        rec = registry_data["documents"][idx].get(key, {})
        if rec and rec.get("status") == "emitted":
            emitted.add(doc_id)
    return emitted

def _get_unemitted_doc_ids(
    registry_data: dict,
    doc_id_index: dict,
    limit: int | None = None,
) -> list[str]:
    """
    Return doc_ids that have not yet been emitted to the active network.
    Respects limit (applied to emission order, not registry order).
    """
    key = "emitted_testnet" if CHAIN_ID == 2651420 else "emitted_mainnet"
    emitted_ids = set()

    for doc_id, idx in doc_id_index.items():
        entry = registry_data["documents"][idx]
        rec = entry.get(key, {})
        if rec and rec.get("status") == "emitted":
            emitted_ids.add(doc_id)

    tree_files = get_tree_files()
    unemitted = [
        extract_doc_id(tf) for tf in tree_files
        if extract_doc_id(tf) not in emitted_ids
    ]
    if limit:
        unemitted = unemitted[:limit]
    return unemitted


def run_batch_forge(
    registry_data: dict,
    doc_id_index: dict,
    dry_run: bool,
    limit: int | None = None,
    start: int = 0,
) -> tuple[int, int]:
    """
    Batch-emit documents using CommitBatchV2.s.sol (one forge call per batch of 200).

    Reads un-emitted docs from registry, calls forge script with BATCH_OFFSET/BATCH_SIZE,
    parses tx hash from broadcast receipt, updates registry for all docs in batch.

    Returns (success_count, fail_count).
    """
    today = datetime.now().strftime("%Y%m%d")
    debug_log_path = LOG_DIR / f"emit_all_debug_{today}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    deployer_key = os.environ.get("DEPLOYER_KEY")
    if not deployer_key:
        print("ERROR: DEPLOYER_KEY environment variable not set")
        return 0, 0

    # Build list of (registry_index, doc_id) for all unemitted docs
    # registry_index = position in registry['documents'] array
    # This is what Forge uses as BATCH_OFFSET (it reads .documents[BATCH_OFFSET + i])
    emitted_ids = _load_emitted_ids(registry_data, doc_id_index)
    all_unemitted = [
        (doc_id_index[doc_id], doc_id)
        for doc_id in doc_id_index
        if doc_id not in emitted_ids
    ]
    # start is a registry index, not a tree index — skip docs whose registry_index < start
    all_unemitted = [(ri, did) for ri, did in all_unemitted if ri >= start]
    all_unemitted.sort(key=lambda x: x[0])  # sort by registry index

    # Filter out large-tree docs (>500 chunks) — they OOM in batch mode, emit individually
    MAX_BATCH_CHUNK_COUNT = 500
    small_docs = []
    large_docs = []
    for ri, did in all_unemitted:
        cc = registry_data["documents"][ri].get("chunk_count", 0)
        if cc > MAX_BATCH_CHUNK_COUNT:
            large_docs.append((ri, did, cc))
        else:
            small_docs.append((ri, did))
    if large_docs:
        print(f"WARNING: {len(large_docs)} docs with >{MAX_BATCH_CHUNK_COUNT} chunks excluded from batch "
              f"(emit individually): {[(ri, cc) for ri, _, cc in large_docs[:10]]}"
              f"{' ...' if len(large_docs) > 10 else ''}")

    batch_docs = small_docs[:limit or len(small_docs)]
    total_needed = len(all_unemitted)
    print(f"Total unemitted from registry idx {start}: {total_needed}")
    print(f"This batch: registry indices {batch_docs[0][0] if batch_docs else 'none'}–{batch_docs[-1][0] if batch_docs else 'none'} ({len(batch_docs)} small docs)")

    if total_needed == 0:
        print("Nothing to emit — all docs already emitted.")
        return 0, 0

    # ── EVM nonce management ───────────────────────────────────────────────────
    # Query the RPC for the sender's current nonce to avoid Foundry nonce-cache
    # issues where Foundry uses a stale cached value and gets "nonce too low".
    def _get_current_nonce() -> int | None:
        """Return the latest-block nonce for the deployer, or None on error."""
        sender = "0xBABc60eD17e6387AEDab112E80744aA19EFCb723"
        cmd = [
            "cast", "rpc", "eth_getTransactionCount",
            sender, "latest",
            "--rpc-url", RPC_URL,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
                env={**os.environ, "PATH": os.environ.get("PATH", "")},
            )
            if result.returncode == 0:
                return int(result.stdout.strip(), 16)
        except Exception:
            pass
        return None

    # ── EVM nonce management ───────────────────────────────────────────────────

    # batch_docs is a list of (registry_index, doc_id) tuples
    success_count = 0
    fail_count = 0
    batch_num = 0

    while batch_docs:
        batch = batch_docs[:MAX_BATCH_SIZE]
        batch[0][0]  # registry array index of first doc in batch

        # Split batch into sub-batches to avoid EVM memory OOG
        sub_batch_num = 0
        while batch:
            sub_batch = batch[:SUB_BATCH_SIZE]
            sub_reg_offset = sub_batch[0][0]
            doc_ids = [doc_id for _, doc_id in sub_batch]

            print(f"\n--- Batch {batch_num}.{sub_batch_num}: registry indices {sub_reg_offset}–{sub_reg_offset + len(sub_batch) - 1} ({len(sub_batch)} docs) ---")

            env = os.environ.copy()
            foundry_bin = os.path.expanduser("~/.foundry/bin")
            if foundry_bin not in env.get("PATH", ""):
                env["PATH"] = foundry_bin + ":" + env.get("PATH", "")

            env["DEPLOYER_KEY"] = deployer_key
            env["CONTRACT_ADDRESS"] = CONTRACT_ADDRESS
            env["BATCH_OFFSET"] = str(sub_reg_offset)
            env["BATCH_SIZE"] = str(len(sub_batch))
            env["REGISTRY_PATH"] = str(REGISTRY_PATH)
            env["TREES_DIR"] = str(MERKLE_TREES_DIR)
            env["RPC_URL"] = RPC_URL

            cmd = [
                "forge", "script", str(BATCH_SCRIPT_PATH),
                "--rpc-url", RPC_URL,
                "--build-info",
                "-vvv",
            ]
            if not dry_run:
                cmd += ["--broadcast", "--private-key", deployer_key]
                # Refresh nonce from RPC before each forge call to avoid stale cache
                nonce = _get_current_nonce()
                if nonce is not None:
                    env["ETH_NONCE"] = str(nonce)

            # Rate-limit: 100ms pause between Forge invocations to avoid RPC 429s
            time.sleep(0.1)

            with open(debug_log_path, "a") as f:
                f.write(f"\n===== {datetime.now(timezone.utc).isoformat()} [BATCH {batch_num}.{sub_batch_num}] =====\n")
                f.write(f"reg_offset: {sub_reg_offset}  size: {len(sub_batch)}\n")
                safe_env = dict(env)
                safe_env["DEPLOYER_KEY"] = "<REDACTED>"
                f.write(f"env: {json.dumps(safe_env, indent=2)}\n")
                f.write(f"command: {' '.join(cmd)}\n")

            result = subprocess.run(
                cmd,
                env=env,
                cwd=Path(__file__).parent,
                capture_output=True,
                text=True,
                timeout=600,
            )
            output = result.stdout + result.stderr

            with open(debug_log_path, "a") as f:
                f.write(f"forge rc: {result.returncode}\n")
                f.write(f"output:\n{output}\n")

            if result.returncode != 0:
                print(f"[FAIL] Batch {batch_num}.{sub_batch_num} forge failed with rc={result.returncode}")
                fail_count += len(sub_batch)
                batch = batch[SUB_BATCH_SIZE:]
                sub_batch_num += 1
                continue

            # Parse tx hash from broadcast receipt
            tx_hash = ""
            block_number = ""
            if not dry_run:
                broadcast_dir = (
                    Path(__file__).parent / "broadcast" / BATCH_SCRIPT_PATH.name / str(CHAIN_ID)
                )
                try:
                    broadcast_files = sorted(
                        broadcast_dir.glob("run-*.json"),
                        key=os.path.getmtime,
                    )
                    if broadcast_files:
                        with open(broadcast_files[-1]) as bf:
                            data = json.load(bf)
                        for receipt in data.get("receipts", []):
                            h = receipt.get("transactionHash", "")
                            # Only treat as success if on-chain status is 1 (confirmed)
                            if h.startswith("0x") and len(h) == 66 and receipt.get("status") == "0x1":
                                tx_hash = h
                                block_number = str(int(receipt.get("blockNumber", "0x0"), 16))
                                break
                except Exception as e:
                    err_msg = f"[FAIL] Batch {batch_num}.{sub_batch_num}: exception parsing broadcast receipt: {e}"
                    print(err_msg)
                    with open(debug_log_path, "a") as f:
                        f.write(f"RECEIPT_PARSE_ERROR: {e}\n")
                    fail_count += len(sub_batch)
                    batch = batch[SUB_BATCH_SIZE:]
                    sub_batch_num += 1
                    continue

            if not dry_run and not tx_hash:
                print(f"[FAIL] Batch {batch_num}.{sub_batch_num}: no tx hash found in broadcast receipt")
                fail_count += len(sub_batch)
                batch = batch[SUB_BATCH_SIZE:]
                sub_batch_num += 1
                continue

            # All docs in this sub-batch succeeded
            explorer_base = EXPLORER_URL.rstrip("/")
            for doc_id in doc_ids:
                write_emission_record(registry_data, doc_id_index, doc_id, "emitted", tx_hash, block_number)
                f"{explorer_base}/tx/0x{tx_hash.lstrip('0x')}"
                print(f"  [EMIT] {doc_id[:16]}... tx={tx_hash[:16]}...")

            if not dry_run:
                if not safe_save_registry(registry_data, "save after batch"):
                    fail_count += len(doc_ids)
                    batch = batch[SUB_BATCH_SIZE:]
                    sub_batch_num += 1
                    continue
                print(f"  [SAVE] Registry saved ({len(doc_ids)} docs)")

            batch = batch[SUB_BATCH_SIZE:]
            sub_batch_num += 1
            success_count += len(sub_batch)

        batch_docs = batch_docs[MAX_BATCH_SIZE:]
        batch_num += 1

    print("\n=== SUMMARY ===")
    print(f"Total:   {total_needed}")
    print(f"Emitted: {success_count}")
    print(f"Failed:  {fail_count}")
    return success_count, fail_count


def process_single_doc(
    doc_id: str,
    registry_data: dict,
    doc_id_index: dict,
    dry_run: bool,
    verify: bool = False,
    force_reemit: bool = False,
) -> tuple[str, bool, str]:
    """
    Process a single document by doc_id.

    Reads merkle_root from tree JSON, pdf_hash from registry,
    chunk_count from tree file. Writes emission record to registry on success.

    Returns (status_label, success, message)
    Labels: EMIT, SKIP, FAIL
    """
    print(f"\n[process_single_doc] START  doc_id={doc_id}")
    print(f"  dry_run={dry_run}  force_reemit={force_reemit}  verify={verify}")

    # ── Idempotency: skip if already emitted ──────────────────────────────────
    if is_already_emitted(registry_data, doc_id_index, doc_id) and not force_reemit:
        print("  [SKIP] already emitted in registry (use --force to override)")
        return "SKIP", True, "reason=already_emitted"

    # ── On-chain verification for --force ────────────────────────────────────
    # If --force is set, we skip the registry check but still verify on-chain.
    # We need merkle_root to check on-chain, so load tree data early.
    if force_reemit:
        print("  [--force] checking on-chain state before emitting...")
        tree_file = MERKLE_TREES_DIR / f"{doc_id}_tree.json"
        if tree_file.exists():
            try:
                with open(tree_file) as f:
                    tree_data = json.load(f)
                merkle_root = tree_data.get("merkle_root", "")
                if merkle_root and is_onchain_emitted(doc_id, merkle_root):
                    print("  [SKIP] already on-chain (--force check)")
                    return "SKIP", True, "reason=already_on_chain"
            except Exception as e:
                print(f"  [--force] on-chain check failed ({e}) — proceeding with emit")

    # ── Locate doc in registry ───────────────────────────────────────────────
    if doc_id not in doc_id_index:
        msg = "error=doc_id_not_in_registry"
        print(f"  [FAIL] {msg}")
        _write_error_log(doc_id, "missing_registry_entry", msg, -1)
        return "FAIL", False, msg

    doc_entry = registry_data["documents"][doc_id_index[doc_id]]
    print(f"  [OK] registry index={doc_id_index[doc_id]}")

    # ── Get pdf_hash from registry ────────────────────────────────────────────
    pdf_hash = doc_entry.get("sha256")
    if not pdf_hash:
        msg = "error=no_sha256_in_registry"
        print(f"  [FAIL] {msg}  (sha256 field missing or empty)")
        _write_error_log(doc_id, "missing_pdf_hash", msg, -1)
        return "FAIL", False, msg
    print(f"  [OK] pdf_hash loaded  sha256={pdf_hash[:20]}...")

    # ── Get tree data ─────────────────────────────────────────────────────────
    tree_file = MERKLE_TREES_DIR / f"{doc_id}_tree.json"
    if not tree_file.exists():
        msg = "error=tree_file_not_found"
        print(f"  [FAIL] tree file not found: {tree_file}")
        _write_error_log(doc_id, "missing_tree", msg, -1)
        return "FAIL", False, msg
    print(f"  [OK] tree file exists: {tree_file}")

    try:
        with open(tree_file, "r") as f:
            tree_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        msg = f"error=invalid_tree_json  ({e})"
        print(f"  [FAIL] {msg}")
        _write_error_log(doc_id, "invalid_json", msg, -1)
        return "FAIL", False, msg

    chunk_count = tree_data.get("chunk_count", 0)
    tree_depth = tree_data.get("tree_config", {}).get("depth", 0)
    padded_leaf_count = tree_data.get("padded_leaf_count", 0)
    if chunk_count <= 0:
        print(f"  [SKIP] chunk_count={chunk_count} (must be > 0)")
        return "SKIP", True, "reason=zero_chunk_count"
    print(f"  [OK] tree loaded  chunks={chunk_count}  depth={tree_depth}  padded={padded_leaf_count}")

    # ── Read merkle_root from tree JSON ────────────────────────────────────────
    merkle_root = tree_data.get("merkle_root")
    if not merkle_root:
        msg = "error=no_merkle_root_in_tree"
        print(f"  [FAIL] {msg}")
        _write_error_log(doc_id, "missing_merkle_root", msg, -1)
        return "FAIL", False, msg
    print(f"  [OK] merkle_root={merkle_root[:30]}...")

    # ── Emit ─────────────────────────────────────────────────────────────────
    print("\n  Calling run_append_root_v2()...")
    success, tx_hash, block_number = run_append_root_v2(
        doc_id, pdf_hash, chunk_count, merkle_root,
        tree_depth, padded_leaf_count,
        dry_run=dry_run, verify=verify,
    )
    print(f"  run_append_root_v2 returned: success={success}  tx_hash={tx_hash[:20] if tx_hash else ''}...  block={block_number}")

    # ── Write result to registry ─────────────────────────────────────────────
    # Guard: if success=True but tx_hash is empty/unknown, the broadcast succeeded but
    # receipt-parsing failed — don't write "emitted" with a phantom tx_hash.
    # Leave the doc for manual re-emission once the real tx hash is recovered.
    if success and tx_hash and tx_hash not in ("", "unknown"):
        write_emission_record(
            registry_data, doc_id_index, doc_id,
            status="emitted",
            tx_hash=tx_hash,
            block_number=block_number,
        )
        print(f"  [OK] registry updated: emitted  tx={tx_hash[:20]}...  block={block_number}")
    elif success and not tx_hash:
        # Broadcast succeeded but we lost the receipt — log and skip registry write
        print("  [WARN] on-chain OK but tx_hash unknown — manual registry update needed")
        print(f"  [DEBUG] doc_id={doc_id} on-chain succeeded, tx_hash unknown")
        print(f"[process_single_doc] DONE  doc_id={doc_id}  label=EMIT_NO_TX")
        return "EMIT_NO_TX", True, "tx_hash=unknown"
    else:
        write_emission_record(
            registry_data, doc_id_index, doc_id,
            status="failed",
            tx_hash=tx_hash or "unknown",
            block_number=block_number or "0",
            error_msg=tx_hash if tx_hash.startswith("error=") else None,
        )
        print(f"  [FAIL] registry updated: failed  tx={tx_hash or 'unknown'}")

    label = "EMIT" if success else "FAIL"
    print(f"[process_single_doc] DONE  doc_id={doc_id}  label={label}")
    return label, success, tx_hash or ""


# ── Batch runner ───────────────────────────────────────────────────────────────

def run_batch(
    registry_data: dict,
    doc_id_index: dict,
    dry_run: bool,
    limit: int | None = None,
    verify: bool = False,
    force_reemit: bool = False,
) -> tuple[int, int]:
    """
    Run batch emit over all tree files.

    Returns (success_count, fail_count)
    """
    tree_files = get_tree_files()
    if limit:
        tree_files = tree_files[:limit]

    success_count = 0
    fail_count = 0
    total = len(tree_files)

    for idx, tree_file in enumerate(tree_files):
        doc_id = extract_doc_id(tree_file)
        label, success, message = process_single_doc(
            doc_id, registry_data, doc_id_index,
            dry_run=dry_run, verify=verify,
            force_reemit=force_reemit,
        )

        print(f"[{label}] {doc_id[:16]}... {message}")

        if label == "SKIP":
            pass
        elif success:
            success_count += 1
        else:
            fail_count += 1

        # Write registry after every doc (atomic)
        if not dry_run:
            if not safe_save_registry(registry_data, "save after per-doc emit"):
                fail_count += 1
                success_count -= 1  # undo the increment above
                continue

        if (idx + 1) % 10 == 0:
            print(f"Progress: {idx + 1}/{total}  emitted={success_count}  failed={fail_count}")

        # 5s delay between every doc to avoid rate limiting on the public RPC
        time.sleep(5)

    return success_count, fail_count


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Batch emit Merkle roots to MerkleRootRegistry")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be emitted without broadcasting")
    parser.add_argument("--batch", action="store_true",
                        help="Emit all documents in merkleTrees/")
    parser.add_argument("--doc-id", type=str,
                        help="Emit a single document by doc_id")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="Maximum number of documents to emit in this run")
    parser.add_argument("--start", type=int, metavar="N", default=0,
                        help="Registry array index to start from (default 0). Skip docs whose registry index < N.")
    parser.add_argument("--verify", action="store_true",
                        help="Write verify log; performs on-chain verification after each EMIT")
    parser.add_argument("--force", action="store_true",
                        help="Re-emit even if registry shows already emitted (checks on-chain state)")
    parser.add_argument("--forge-batch", action="store_true",
                        help="Use CommitBatchV2.s.sol batch mode (200 docs/tx) instead of per-doc forge calls")
    args = parser.parse_args()

    if not args.batch and not args.doc_id:
        print("Error: must specify --batch or --doc-id")
        parser.print_help()
        sys.exit(1)

    # ── Load registry ────────────────────────────────────────────────────────
    print(f"Loading registry from {REGISTRY_PATH}...")
    registry_data, doc_id_index = load_registry()
    print(f"Loaded {len(registry_data['documents'])} documents from registry")

    # ── Comprehensive startup banner — printed to stdout AND written to debug log ──
    # Every run should be reproducible from the printed config alone
    today_str = datetime.now().strftime("%Y%m%d")
    debug_log_path = LOG_DIR / f"emit_all_debug_{today_str}.log"
    error_log_path = LOG_DIR / f"emit_all_errors_{today_str}.log"

    banner_lines = [
        "",
        "=" * 70,
        f" emit_all.py   started at {datetime.now(timezone.utc).isoformat()} UTC",
        f" Debug log:    {debug_log_path}",
        f" Error log:    {error_log_path}",
        "=" * 70,
        f" Network:      ACTIVE_NETWORK={_ACTIVE_NETWORK}  CHAIN_ID={CHAIN_ID}",
        f" RPC:          {RPC_URL}",
        f" Contract:     {CONTRACT_ADDRESS}",
        f" Script (V2):  {SCRIPT_V2_PATH}",
        f" Batch script: {BATCH_SCRIPT_PATH}",
        f" Registry:     {REGISTRY_PATH}  ({len(registry_data['documents'])} docs)",
        f" Trees:        {MERKLE_TREES_DIR}",
        f" DEPLOYER_KEY: {'SET' if os.environ.get('DEPLOYER_KEY') else 'NOT SET — broadcasts WILL FAIL'}",
        f" Dry run:      {args.dry_run}",
        f" Verify:       {args.verify}",
        f" Force:        {args.force}",
    ]
    if args.doc_id:
        banner_lines.append(f" Mode:         SINGLE DOC  doc_id={args.doc_id}")
    elif args.forge_batch:
        banner_lines.append(f" Mode:         FORGE BATCH  start={args.start}  limit={args.limit}")
    else:
        banner_lines.append(f" Mode:         BATCH (per-doc)  start={args.start}  limit={args.limit}")
    banner_lines.append("=" * 70)
    banner_str = "\n".join(banner_lines)

    print(banner_str)

    # Also write banner to debug log so it's captured for every run
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(debug_log_path, "a") as f:
        f.write(banner_str + "\n")

    # ── Single doc mode ──────────────────────────────────────────────────────
    if args.doc_id:
        print(f"\n=== SINGLE DOC MODE: {args.doc_id[:16]}... ===")
        if args.dry_run:
            print("DRY RUN — no transactions will be sent\n")

        label, success, message = process_single_doc(
            args.doc_id, registry_data, doc_id_index,
            dry_run=args.dry_run, verify=args.verify,
            force_reemit=args.force,
        )
        print(f"\n[{label}] {args.doc_id[:16]}... {message}")
        print(f"Explorer: {EXPLORER_URL}/tx/{registry_data['documents'][doc_id_index[args.doc_id]].get('emitted_testnet',{}).get('tx_hash','')}")

        if not args.dry_run:
            if not safe_save_registry(registry_data, "save after single doc"):
                print(f"[FAIL] Registry save failed — see {debug_log_path}")
                print(f"\nDebug log: {debug_log_path}")
                sys.exit(1)
            print(f"Registry saved: {REGISTRY_PATH}")
        print(f"\nDebug log: {debug_log_path}")
        sys.exit(0)

    # ── Batch mode ───────────────────────────────────────────────────────────
    if args.batch:
        print(f"\n=== BATCH MODE ({len(registry_data['documents'])} docs) ===")
        if args.dry_run:
            print("DRY RUN — no transactions will be sent\n")
        elif args.forge_batch:
            print("FORGE BATCH MODE — using CommitBatchV2.s.sol (up to 200 docs/tx)\n")

        if args.forge_batch:
            success_count, fail_count = run_batch_forge(
                registry_data, doc_id_index,
                dry_run=args.dry_run,
                limit=args.limit,
                start=args.start,
            )
        else:
            success_count, fail_count = run_batch(
                registry_data, doc_id_index,
                dry_run=args.dry_run,
                limit=args.limit,
                verify=args.verify,
                force_reemit=args.force,
            )

        print("\n=== SUMMARY ===")
        print(f"Total:   {success_count + fail_count}")
        print(f"Emitted: {success_count}")
        print(f"Failed:  {fail_count}")
        print(f"Registry: {REGISTRY_PATH}")
        print(f"Debug log: {debug_log_path}")
        print(f"Error log: {error_log_path}")
        sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
