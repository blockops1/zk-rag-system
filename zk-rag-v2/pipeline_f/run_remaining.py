#!/usr/bin/env python3
"""Emit remaining small docs (<=500 chunks) in consecutive runs.
Each consecutive run becomes its own forge batch.
"""
import json
import subprocess
import os
import time
from pathlib import Path

REGISTRY_PATH = Path("../data/registry.json")
MERKLE_DIR    = Path("../data/merkleTrees")
LOG_DIR       = Path("../data/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

today = "20260424"
debug_log = LOG_DIR / f"emit_remaining_{today}.log"
error_log = LOG_DIR / f"emit_remaining_errors_{today}.log"

def log(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = f"{ts}  {msg}"
    print(msg)
    with open(debug_log, "a") as f:
        f.write(line + "\n")

def log_err(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(error_log, "a") as f:
        f.write(json.dumps({"timestamp": ts, "message": msg}) + "\n")

def get_nonce():
    out = subprocess.run(
        ["./foundry-bin/cast", "nonce", "0xBABc60eD17e6387AEDab112E80744aA19EFCb723",
         "--rpc-url", "https://horizen.calderachain.xyz/http"],
        capture_output=True, text=True, timeout=10
    )
    return int(out.stdout.strip()) if out.returncode == 0 else None

# Load registry
with open(REGISTRY_PATH) as f:
    registry = json.load(f)
docs = registry["documents"]

doc_id_index = {d["doc_id"].lower(): i for i, d in enumerate(docs)}
key = "emitted_mainnet"

# Find small, unemitted docs
emitted_ids = {d["doc_id"].lower() for i, d in enumerate(docs)
               if d.get(key, {}).get("status") == "emitted"}

all_unemitted = [(idx, d["doc_id"].lower()) for idx, d in enumerate(docs)
                 if d["doc_id"].lower() not in emitted_ids
                 and d.get("chunk_count", 0) <= 500]
all_unemitted.sort(key=lambda x: x[0])

log(f"Small unemitted: {len(all_unemitted)}")

# Group into consecutive runs
runs = []
current = [all_unemitted[0]]
for i in range(1, len(all_unemitted)):
    if all_unemitted[i][0] == all_unemitted[i-1][0] + 1:
        current.append(all_unemitted[i])
    else:
        runs.append(current)
        current = [all_unemitted[i]]
runs.append(current)

log(f"Consecutive runs: {len(runs)}")

CONTRACT = "0x462fc86E28c07798BD4656451611FE4E0A6D7760"
DEPLOYER_KEY = "0x0007eddd48466def81411fb05c3d32291f17eb833154eb5f16b812ea5d842a82"
RPC = "https://horizen.calderachain.xyz/http"
CHAIN_ID = "26514"

results = {"success": 0, "failed": 0, "failed_runs": []}

for run_idx, run in enumerate(runs):
    reg_start = run[0][0]
    reg_end   = run[-1][0]
    size = len(run)
    doc_ids = [did for _, did in run]

    log(f"\n--- Run {run_idx}: registry indices {reg_start}-{reg_end} ({size} docs) ---")

    # Build forge command
    env = os.environ.copy()
    env["DEPLOYER_KEY"]       = DEPLOYER_KEY
    env["CONTRACT_ADDRESS"]   = CONTRACT
    env["BATCH_OFFSET"]       = str(reg_start)
    env["BATCH_SIZE"]         = str(size)
    env["REGISTRY_PATH"]      = str(REGISTRY_PATH)
    env["TREES_DIR"]          = str(MERKLE_DIR)
    env["RPC_URL"]            = RPC

    cmd = [
        "./foundry-bin/forge", "script",
        "script/CommitBatchV2.s.sol:CommitBatchV2",
        "--rpc-url", RPC,
        "--chain-id", CHAIN_ID,
        "--broadcast",
        "--private-key", DEPLOYER_KEY,
    ]

    with open(debug_log, "a") as f:
        f.write(f"  cmd: {' '.join(cmd)}\n")

    result = subprocess.run(
        cmd, env=env,
        cwd="./pipeline_f",
        capture_output=True, text=True, timeout=300
    )

    output = result.stdout + result.stderr
    with open(debug_log, "a") as f:
        f.write(output + "\n")

    if result.returncode == 0:
        # Parse tx hash from broadcast receipt
        bc_dir = Path("./pipeline_f/broadcast/CommitBatchV2.s.sol/") / CHAIN_ID
        try:
            files = sorted(bc_dir.glob("run-*.json"), key=os.path.getmtime)
            if files:
                with open(files[-1]) as f:
                    bc = json.load(f)
                for tx in bc.get("transactions", []):
                    if tx.get("transactionType") == "CALL":
                        tx_hash = tx.get("hash", "")
                        log(f"  SUCCESS: tx={tx_hash}")
                        break
            else:
                tx_hash = "unknown"
                log("  SUCCESS (no receipt): tx_hash unknown")
        except Exception as e:
            tx_hash = "parse_error"
            log(f"  SUCCESS (parse error): {e}")

        results["success"] += size

        # Update registry for each doc in this run
        import fcntl
        lock_fd = open("../data/registry.lock", "w")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        try:
            reg2 = json.load(open(REGISTRY_PATH))
            for _, did in run:
                idx = doc_id_index[did]
                reg2["documents"][idx]["emitted_mainnet"] = {
                    "status": "emitted",
                    "tx_hash": tx_hash,
                    "block_number": 0,  # will be filled from next block query
                    "chain_id": 26514,
                    "notes": "emitted via run_remaining.py"
                }
            tmp = REGISTRY_PATH.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(reg2, f, indent=2)
            os.replace(tmp, REGISTRY_PATH)
            log(f"  Registry updated for {size} docs")
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()
    else:
        # Check for "already emitted" — these are actually on-chain
        if "already emitted" in output:
            log("  Already on-chain (should not happen) — skipping")
            results["success"] += size  # count as success since on-chain
        else:
            log(f"  FAILED rc={result.returncode}")
            results["failed_runs"].append((run_idx, reg_start, size, output[-500:]))
            results["failed"] += size

    time.sleep(2)  # rate limit

log("\n=== DONE ===")
log(f"Success: {results['success']}")
log(f"Failed:  {results['failed']}")
if results["failed_runs"]:
    log(f"Failed runs: {results['failed_runs']}")
