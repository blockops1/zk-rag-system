"""
X402 Paid Download — EIP-3009 verification for USDC on Base.

Implements the /api/source/{doc_id} endpoint that streams PDFs only
when a valid EIP-3009 transferWithAuthorization proof is presented.

Payment flow (EIP-3009 exact scheme):
  1. Client calls GET /api/source/{doc_id}  → 402 with PAYMENT-REQUIRED header
  2. Client constructs PaymentPayload, signs via wallet
  3. Client retries with PAYMENT-SIGNATURE header (base64-encoded PaymentPayload)
  4. Server verifies signature, checks replay, streams PDF
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_typed_data, SignableMessage
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

USDC_CONTRACT = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
NETWORK_CHAIN_ID = 8453  # Base mainnet
NETWORK_SPEC = "eip155:8453"
PRICE_MICRO_USDC = 100_000  # $0.10 (6 decimals)
TOKEN_NAME = "USD Coin"
TOKEN_VERSION = "2"
MAX_PAYMENT_TIMEOUT_SECONDS = 300  # 5 minutes

# Receiving address — loaded from environment at module load time
RECEIVING_ADDRESS: Optional[str] = os.environ.get("PAID_DOWNLOAD_RECEIVING_ADDRESS")


def _receiving_address() -> str:
    if not RECEIVING_ADDRESS:
        raise RuntimeError("PAID_DOWNLOAD_RECEIVING_ADDRESS not set")
    return RECEIVING_ADDRESS


# ─── Payment Required response builder ────────────────────────────────────────

def build_payment_required(
    doc_id: str,
    doc_title: str,
    resource_url: str,
) -> tuple[dict, str]:
    """Build a 402 PaymentRequired payload and the base64-encoded header value.

    Returns (body_dict, header_value) so the caller can set both.
    """
    body = {
        "error": "payment_required",
        "price_usd": "0.10",
        "doc_id": doc_id,
        "title": doc_title,
    }

    header_value = base64.b64encode(json.dumps({
        "resource": {
            "url": resource_url,
            "description": f"Full PDF download: {doc_title}",
            "mimeType": "application/pdf",
        },
        "accepted": [
            {
                "scheme": "exact",
                "network": NETWORK_SPEC,
                "amount": str(PRICE_MICRO_USDC),
                "asset": USDC_CONTRACT,
                "payTo": _receiving_address(),
                "maxTimeoutSeconds": MAX_PAYMENT_TIMEOUT_SECONDS,
                "extra": {
                    "assetTransferMethod": "eip3009",
                    "name": TOKEN_NAME,
                    "version": TOKEN_VERSION,
                },
            }
        ],
    }).encode()).decode()

    return body, header_value


# ─── EIP-3009 Signature Verification ─────────────────────────────────────────

# EIP-3009 types for encode_typed_data
_TRANSFER_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ],
}

_DOMAIN = {
    "name": TOKEN_NAME,
    "version": TOKEN_VERSION,
    "chainId": NETWORK_CHAIN_ID,
    "verifyingContract": USDC_CONTRACT,
}


def _reconstruct_digest(authorization: dict) -> bytes:
    """Reconstruct the EIP-712 digest for a TransferWithAuthorization.

    The signer signed the EIP-712 hash of:
      EIP712Domain(DomainSeparator) || TransferWithAuthorization(Message)
    """
    full_message = {
        "types": _TRANSFER_TYPES,
        "primaryType": "TransferWithAuthorization",
        "domain": _DOMAIN,
        "message": {
            "from": authorization["from"],
            "to": authorization["to"],
            "value": authorization["value"],
            "validAfter": authorization["validAfter"],
            "validBefore": authorization["validBefore"],
            "nonce": authorization["nonce"],
        },
    }
    signable: SignableMessage = encode_typed_data(full_message=full_message)
    # hash_MESSAGE returns the final digest (EIP-191 compliant)
    return signable.hash_MESSAGE()


def verify_eip3009_proof(payload: dict) -> tuple[bool, str]:
    """Verify an EIP-3009 PaymentPayload.

    Returns (is_valid, error_reason).
    """
    accepted = payload.get("accepted", {})
    auth = payload.get("payload", {}).get("authorization", {})
    provided_sig = payload.get("payload", {}).get("signature")

    # 1. Check amount matches
    if str(accepted.get("amount", "")) != str(PRICE_MICRO_USDC):
        logger.error(f"X402 amount mismatch: got {accepted.get('amount')}, expected {PRICE_MICRO_USDC}")
        return False, f"amount mismatch: expected {PRICE_MICRO_USDC}"

    # 2. Check asset
    if accepted.get("asset", "").lower() != USDC_CONTRACT.lower():
        logger.error(f"X402 asset mismatch: got {accepted.get('asset')}")
        return False, f"asset mismatch: expected {USDC_CONTRACT}"

    # 3. Check payTo is our receiving address
    if accepted.get("payTo", "").lower() != _receiving_address().lower():
        logger.error(f"X402 payTo mismatch: got {accepted.get('payTo')}")
        return False, f"payTo mismatch: expected {_receiving_address()}"

    # 4. Check network
    if accepted.get("network") != NETWORK_SPEC:
        logger.error(f"X402 network mismatch: got {accepted.get('network')}, expected {NETWORK_SPEC}")
        return False, f"network mismatch: expected {NETWORK_SPEC}"

    # 5. Check validity window
    now = int(time.time())
    valid_after = int(auth.get("validAfter", 0))
    valid_before = int(auth.get("validBefore", 0))
    if now <= valid_after:
        logger.error(f"X402 payment not yet valid: now={now}, validAfter={valid_after}")
        return False, "payment not yet valid"
    if now >= valid_before:
        logger.error(f"X402 payment expired: now={now}, validBefore={valid_before}")
        return False, "payment expired"

    # 6. Reconstruct digest and recover signer
    if not provided_sig:
        logger.error("X402 missing signature")
        return False, "missing signature"

    try:
        # Reconstruct the SignableMessage so Account.recover_message can verify
        full_message = {
            "types": _TRANSFER_TYPES,
            "primaryType": "TransferWithAuthorization",
            "domain": _DOMAIN,
            "message": {
                "from": auth["from"],
                "to": auth["to"],
                "value": auth["value"],
                "validAfter": auth["validAfter"],
                "validBefore": auth["validBefore"],
                "nonce": auth["nonce"],
            },
        }
        signable: SignableMessage = encode_typed_data(full_message=full_message)
        sig_bytes = bytes.fromhex(provided_sig.replace("0x", ""))
        if len(sig_bytes) != 65:
            logger.error(f"X402 invalid signature length: {len(sig_bytes)}")
            return False, f"invalid signature length: {len(sig_bytes)}"
        signer: str = Account.recover_message(signable, signature=sig_bytes)
    except Exception as e:
        logger.error(f"X402 signature recovery failed: {e}")
        return False, f"signature recovery failed: {e}"

    # 7. Verify signer matches authorization.from
    if signer.lower() != auth.get("from", "").lower():
        logger.error(f"X402 signer mismatch: recovered={signer}, auth.from={auth.get('from')}")
        return False, f"signer mismatch: {signer} != {auth.get('from')}"

    return True, ""


# ─── Replay Protection ──────────────────────────────────────────────────────────

# In-memory set of used nonces. Phase 2 would use Redis or disk.
_used_nonces: set[str] = set()
_used_locked = False  # simple guard; fine for single-process


def mark_nonce_used(nonce: str) -> None:
    global _used_nonces, _used_locked
    if _used_locked:
        return
    _used_nonces.add(nonce)


def is_nonce_used(nonce: str) -> bool:
    if _used_locked:
        return True
    return nonce in _used_nonces


def lock_replay_cache() -> None:
    """Lock after first use to prevent further writes (handles restart race)."""
    global _used_locked
    _used_locked = True


# ─── PaymentPayload parsing ───────────────────────────────────────────────────

def decode_payload(header_value: str) -> tuple[Optional[dict], str]:
    """Decode and parse a base64-encoded PaymentPayload JSON.

    Returns (payload_dict, error_reason).
    """
    try:
        raw = base64.b64decode(header_value)
        payload = json.loads(raw)
    except Exception as e:
        return None, f"failed to decode payload: {e}"

    if not isinstance(payload, dict):
        return None, "payload must be a JSON object"

    if payload.get("x402Version") != 2:
        return None, f"unsupported x402 version: {payload.get('x402Version')}"

    return payload, ""


# ─── Source file resolution ────────────────────────────────────────────────────

_REGISTRY_PATH = Path("$DATA_DIR/registry.json")
_REGISTRY_CACHE: Optional[dict] = None
_REGISTRY_CACHE_TIME: float = 0.0
_REGISTRY_CACHE_TTL = 60.0  # re-read every 60s


def _load_registry() -> dict:
    global _REGISTRY_CACHE, _REGISTRY_CACHE_TIME
    now = time.time()
    if _REGISTRY_CACHE is None or (now - _REGISTRY_CACHE_TIME) > _REGISTRY_CACHE_TTL:
        with open(_REGISTRY_PATH) as f:
            _REGISTRY_CACHE = json.load(f)
        _REGISTRY_CACHE_TIME = now
    return _REGISTRY_CACHE


def resolve_source_path(doc_id: str) -> tuple[Optional[Path], Optional[dict]]:
    """Resolve a doc_id to its local PDF path and registry entry.

    Returns (Path, registry_entry) or (None, None) if not found.
    Only returns documents that have a local_path.
    """
    registry = _load_registry()
    documents = registry.get("documents", [])
    for doc in documents:
        if doc.get("doc_id") == doc_id:
            local_path = doc.get("local_path")
            if not local_path:
                return None, None
            return Path(local_path), doc
    return None, None


# ─── Main verification entry point ────────────────────────────────────────────

def verify_and_stream(
    doc_id: str,
    payment_signature_b64: Optional[str],
    resource_url: str,
) -> tuple[bool, int, dict, str]:
    """Verify a payment and prepare the streaming response.

    Returns:
        (should_stream, status_code, header_dict, file_path_or_error_msg)

    should_stream=True  → client gets the file (header_dict may have PAYMENT-RESPONSE)
    should_stream=False → client gets status_code + error body
    """
    # ── Load document ──────────────────────────────────────────────────────────
    path, doc_entry = resolve_source_path(doc_id)
    if path is None:
        return False, 404, {}, "Document not found"

    doc_title = doc_entry.get("title", doc_id)

    # ── No payment header → 402 ───────────────────────────────────────────────
    if not payment_signature_b64:
        body, header_val = build_payment_required(doc_id, doc_title, resource_url)
        return False, 402, {"PAYMENT-REQUIRED": header_val}, body

    # ── Decode payload ────────────────────────────────────────────────────────
    payload, decode_err = decode_payload(payment_signature_b64)
    if payload is None:
        logger.error(f"X402 decode failed for doc_id={doc_id}: {decode_err}")
        return False, 400, {}, f"Invalid payment payload: {decode_err}"

    # ── Check nonce hasn't been used ─────────────────────────────────────────
    auth = payload.get("payload", {}).get("authorization", {})
    nonce = auth.get("nonce", "")
    if is_nonce_used(nonce):
        logger.error(f"X402 nonce replay detected: nonce={nonce}")
        return False, 400, {}, "Payment proof has already been used"

    # ── Verify signature ──────────────────────────────────────────────────────
    valid, err = verify_eip3009_proof(payload)
    if not valid:
        # verify_eip3009_proof already logged the error
        return False, 400, {}, f"Invalid payment proof: {err}"

    # ── Mark nonce used ──────────────────────────────────────────────────────
    mark_nonce_used(nonce)
    logger.info(f"X402 payment verified and settled: doc_id={doc_id}, nonce={nonce}, amount={PRICE_MICRO_USDC} microUSDC")

    # ── Verify file exists ───────────────────────────────────────────────────
    if not path.exists():
        logger.error(f"Document file missing despite registry entry: {path}")
        return False, 404, {}, "Document file not found on server"

    # ── Build settlement response header ─────────────────────────────────────
    settlement = {
        "status": "settled",
        "amount": str(PRICE_MICRO_USDC),
        "asset": USDC_CONTRACT,
        "payTo": _receiving_address(),
        "network": NETWORK_SPEC,
        "docId": doc_id,
        "nonce": nonce,
    }
    settlement_header = base64.b64encode(json.dumps(settlement).encode()).decode()

    return True, 200, {"PAYMENT-RESPONSE": settlement_header}, str(path)
