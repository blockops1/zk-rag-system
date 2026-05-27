#!/usr/bin/env python3
"""
kurier_submit.py — Submit a plonky2 proof to Kurier/zkVerify for on-chain verification.

Usage:
    python3 kurier_submit.py <proof.json> [--api-key <key>] [--chain-id <id>] [--poll-secs <n>] [--max-wait <secs>]

Requirements:
    - Proof JSON from prove-bin (must contain proof_hex, public_inputs_hex, vk_hex)
    - KURIE_API_KEY env var (or --api-key flag)
    - VK must be registered OR vk_hex provided (vk_hex always sent per API spec)

Environment:
    KURIE_API_KEY    Kurier API key (mainnet: https://kurier.xyz)

Output:
    Proof JSON is updated in-place with kurier_job_id and kurier_final_status.
    Logs: ../data/logs/kurier_submit.log (structured JSON + stderr)

Kurier API base: https://api.kurier.xyz/api/v1
"""

import argparse
import json
import os
import struct
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

ZK_PROOFS_DIR = Path("../data/zk_proofs")
LOG_DIR = Path("../data/logs")
LOG_FILE = LOG_DIR / "kurier_submit.log"
MAINNET_API_URL = "https://api.kurier.xyz/api/v1"
TESTNET_API_URL = "https://testnet.kurier.xyz/api/v1"

# Cloudflare bot protection requires a browser-like User-Agent
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ── Logging ───────────────────────────────────────────────────────────────────


def log(level: str, msg: str, **fields):
    """Structured JSON log to file + real-time stderr."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "script": "kurier_submit",
        "message": msg,
        **fields,
    }
    line = json.dumps(entry)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
        f.flush()

    print(f"[{level.upper()}] {msg}", file=sys.stderr)
    if fields:
        print(f"    {fields}", file=sys.stderr)


def log_info(msg: str, **fields):
    log("INFO", msg, **fields)


def log_warn(msg: str, **fields):
    log("WARN", msg, **fields)


def log_error(msg: str, **fields):
    log("ERROR", msg, **fields)


# ── Proof JSON parsing ────────────────────────────────────────────────────────


def load_proof_package(path: Path) -> dict:
    """Load and validate a proof JSON from prove-bin."""
    log_info("Loading proof package", path=str(path))

    with open(path) as f:
        pkg = json.load(f)

    required = ["proof_hex", "public_inputs_hex", "vk_hex"]
    missing = [k for k in required if k not in pkg or not pkg[k]]
    if missing:
        log_error("Proof package missing required fields", missing=missing)
        raise ValueError(f"Missing required fields: {missing}")

    log_info(
        "Proof package loaded",
        proof_hex_len=len(pkg["proof_hex"]),
        public_inputs_hex_len=len(pkg["public_inputs_hex"]),
        vk_hex_len=len(pkg["vk_hex"]),
    )
    return pkg


def decode_public_inputs_hex(hex_str: str) -> list[str]:
    """
    Decode plonky2 public_inputs_hex wire format to an array of decimal strings.

    Wire format: 0x + 8-byte little-endian usize (count) + N×8-byte field elements.
    Returns list of decimal string values for each field element.
    Kurier expects: array of decimal strings.
    """
    if not hex_str.startswith("0x"):
        raise ValueError(f"public_inputs_hex must start with 0x, got: {hex_str[:20]}")

    raw = bytes.fromhex(hex_str[2:])
    if len(raw) < 8:
        raise ValueError(f"public_inputs_hex too short: need >= 8 bytes for count, got {len(raw)}")

    num_pis = struct.unpack_from("<Q", raw, 0)[0]
    pi_bytes = raw[8:]
    expected = num_pis * 8
    if len(pi_bytes) < expected:
        raise ValueError(
            f"public_inputs_hex: declared {num_pis} PIs but only {len(pi_bytes)} bytes available"
        )

    pis = []
    for i in range(num_pis):
        val = struct.unpack_from("<Q", pi_bytes, i * 8)[0]
        pis.append(str(val))

    log_info(f"Decoded {num_pis} public input field elements")
    return pis


def wrap_vk_for_kurier(vk_hex: str) -> str:
    """
    Return VK as a bare hex string for Kurier.

    The OpenAPI spec says vk accepts both object and string.
    Kurier's plonky2 parser expects a hex string directly, not {"config": "...", "bytes": "..."}.
    Passing it as a plain "0x..." hex string avoids: input.startsWith is not a function.
    """
    if not vk_hex.startswith("0x"):
        vk_hex = "0x" + vk_hex
    return vk_hex


# ── Kurier API ────────────────────────────────────────────────────────────────


class KurierApiError(Exception):
    def __init__(self, code: int, message: str, details=None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"Kurier API error {code}: {message}")


def kurier_post(api_url: str, api_key: str, endpoint: str, body: dict) -> dict:
    """Make an authenticated POST to the Kurier API."""
    url = f"{api_url}/{endpoint.format(api_key=api_key)}"

    log_info("Kurier POST", endpoint=endpoint)

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": BROWSER_USER_AGENT,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_resp = e.read().decode("utf-8")
        try:
            err_body = json.loads(body_resp)
            message = err_body.get("message", err_body.get("error", body_resp))
            details = err_body.get("details", err_body.get("detail", {}))
        except Exception:
            message = body_resp
            details = {}

        log_error(
            "Kurier API error",
            code=e.code,
            message=str(message)[:300],
            details=str(details)[:300],
        )
        raise KurierApiError(e.code, message, details)
    except urllib.error.URLError as e:
        log_error("Kurier network error", error=str(e.reason))
        raise KurierApiError(0, str(e.reason))


def submit_proof(
    api_url: str,
    api_key: str,
    proof_hex: str,
    public_inputs_hex: str,
    vk_hex: str,
    proof_type: str = "plonky2",
    vk_registered: bool = False,  # MUST be False — True causes Kurier HTTP 500
    submission_mode: str = "attestation",
    chain_id: int | None = None,
) -> dict:
    """
    Submit a proof to Kurier.

    Returns: {"jobId": "...", "optimisticVerify": "...", "error": "..."}
    """
    vk_hex = wrap_vk_for_kurier(vk_hex)

    body = {
        "proofData": {
            "proof": proof_hex,
            # plonky2 requires publicSignals as a hex string (plonky2 wire format)
            "publicSignals": public_inputs_hex,
            "vk": vk_hex,
        },
        "proofType": proof_type,
        "proofOptions": {"hashFunction": "poseidon"},
        "vkRegistered": vk_registered,
        "submissionMode": submission_mode,
    }

    # Log body preview (truncate large fields)
    body_log = {
        "proofData": {
            "proof": proof_hex[:20] + "...<{} bytes>".format(len(proof_hex)),
            "publicSignals": public_inputs_hex[:40] + "...",
            "vk": vk_hex[:30] + "...",
        },
        "proofType": proof_type,
        "proofOptions": {"hashFunction": "poseidon"},
        "vkRegistered": vk_registered,
        "submissionMode": submission_mode,
    }
    if chain_id is not None:
        body["chainId"] = chain_id
        body_log["chainId"] = chain_id
    log_info("Request body preview", body=json.dumps(body_log, indent=2))

    return kurier_post(api_url, api_key, "submit-proof/{api_key}", body)


def get_job_status(api_url: str, api_key: str, job_id: str) -> dict:
    """Poll current job status."""
    url = f"{api_url}/job-status/{api_key}/{job_id}"
    log_info("GET job status", job_id=job_id)

    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": BROWSER_USER_AGENT},
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_resp = e.read().decode("utf-8")
        try:
            err_body = json.loads(body_resp)
            message = err_body.get("message", err_body.get("error", body_resp))
        except Exception:
            message = body_resp
        raise KurierApiError(e.code, message)


TERMINAL_STATUSES = {"completed", "successful", "done", "verified", "failed", "rejected", "invalid", "finalized"}


def wait_for_job(
    api_url: str,
    api_key: str,
    job_id: str,
    poll_interval_secs: int = 10,
    max_wait_secs: int = 300,
) -> dict:
    """Poll until terminal state, return final status dict."""
    log_info(
        "Waiting for Kurier job",
        job_id=job_id,
        poll_interval_secs=poll_interval_secs,
        max_wait_secs=max_wait_secs,
    )

    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed > max_wait_secs:
            raise KurierApiError(
                0,
                f"Timeout after {max_wait_secs}s waiting for job {job_id}",
            )

        status_resp = get_job_status(api_url, api_key, job_id)
        state = status_resp.get("status", "").lower()
        zk_status = status_resp.get("zkverifyStatus") or status_resp.get("zkverify_status") or ""
        status_resp.get("errorMessage") or status_resp.get("error_message") or ""

        log_info(
            "Job status",
            job_id=job_id,
            status=status_resp.get("status"),
            zkverify_status=zk_status,
            elapsed_secs=int(elapsed),
        )

        if state in TERMINAL_STATUSES:
            return status_resp

        time.sleep(poll_interval_secs)


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Submit a plonky2 proof to Kurier/zkVerify for verification"
    )
    parser.add_argument(
        "proof_json",
        type=Path,
        help="Path to proof JSON from prove-bin",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("KURIE_API_KEY"),
        help="Kurier API key (or set KURIE_API_KEY env var)",
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=None,
        help="Target chain ID for settlement (default: none, zkVerify-only)",
    )
    parser.add_argument(
        "--poll-secs",
        type=int,
        default=10,
        help="Seconds between status polls (default: 10)",
    )
    parser.add_argument(
        "--max-wait",
        type=int,
        default=300,
        help="Max seconds to wait for verification (default: 300)",
    )
    parser.add_argument(
        "--submission-mode",
        default="attestation",
        choices=["attestation", "direct"],
        help="Kurier submission mode (default: attestation)",
    )
    parser.add_argument(
        "--vk-registered",
        action="store_true",
        default=False,
        help="Whether VK is pre-registered (default: false — Kurier registers on submit)",
    )
    parser.add_argument(
        "--unregistered",
        dest="vk_registered",
        action="store_false",
        help="VK is not pre-registered — will be registered on submit",
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Use testnet API (https://testnet.kurier.xyz) instead of mainnet",
    )

    args = parser.parse_args()

    if not args.api_key:
        log_error("No API key: pass --api-key or set KURIE_API_KEY env var")
        sys.exit(1)

    proof_path = Path(args.proof_json).resolve()
    api_url = TESTNET_API_URL if args.testnet else MAINNET_API_URL
    log_info(
        "=== kurier_submit started ===",
        proof=str(proof_path),
        api_key_preview=args.api_key[:6] + "...",
        api_url=api_url,
    )

    # Load and parse proof package
    pkg = load_proof_package(proof_path)

    # Submit to Kurier (pass public_inputs_hex directly — plonky2 expects hex string)
    job_resp = None
    try:
        job_resp = submit_proof(
            api_url=api_url,
            api_key=args.api_key,
            proof_hex=pkg["proof_hex"],
            public_inputs_hex=pkg["public_inputs_hex"],
            vk_hex=pkg["vk_hex"],
            chain_id=args.chain_id,
            vk_registered=args.vk_registered,
            submission_mode=args.submission_mode,
        )
    except KurierApiError as e:
        log_error("Submit failed", code=e.code, message=e.message)
        sys.exit(1)

    job_id = job_resp.get("jobId")
    optimistic = job_resp.get("optimisticVerify") or job_resp.get("optimistic_verify") or ""
    submit_error = job_resp.get("error") or ""

    log_info(
        "Proof submitted",
        job_id=job_id,
        optimistic_verify=optimistic,
        error=submit_error,
    )

    if not job_id:
        log_error("No jobId in Kurier response", response=str(job_resp))
        sys.exit(1)

    # Update proof JSON with job_id
    pkg["kurier_job_id"] = job_id
    with open(proof_path, "w") as f:
        json.dump(pkg, f, indent=2)
    log_info("Updated proof JSON with kurier_job_id", path=str(proof_path))

    # Wait for verification
    final_status = None
    try:
        result = wait_for_job(
            api_url=api_url,
            api_key=args.api_key,
            job_id=job_id,
            poll_interval_secs=args.poll_secs,
            max_wait_secs=args.max_wait,
        )
        final_status = result.get("status") or result.get("zkverifyStatus") or "unknown"
        zk_status = result.get("zkverifyStatus") or result.get("zkverify_status") or ""
        error_msg = result.get("errorMessage") or result.get("error_message") or ""

        log_info(
            "=== kurier_submit completed ===",
            job_id=job_id,
            final_status=final_status,
            zkverify_status=zk_status,
            error=error_msg,
        )

    except KurierApiError as e:
        log_error("Polling failed", code=e.code, message=e.message)
        final_status = f"ERROR: {e.message}"

    # Update proof JSON with final status
    pkg["kurier_final_status"] = final_status
    with open(proof_path, "w") as f:
        json.dump(pkg, f, indent=2)
    log_info("Updated proof JSON with kurier_final_status", path=str(proof_path))

    print(f"OK: job_id={job_id} status={final_status}", file=sys.stdout)


if __name__ == "__main__":
    main()
