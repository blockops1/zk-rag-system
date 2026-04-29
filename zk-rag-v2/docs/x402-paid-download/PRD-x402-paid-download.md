# PRD: Paid PDF Download via X402 Protocol

## 1. Overview

**Project:** Paid Document Download — X402 Protocol Implementation
**Date:** 2026-04-28
**Status:** Draft
**Stack:** Python/FastAPI (api_server.py) + Vanilla JS (website)

### Problem Statement

Users (human and agent) searching military doctrine chunks currently have no way to access the full source PDF. When a chunk is relevant, the natural next step is to read the complete document. There is currently no mechanism to provide paid access to the source files.

### Solution

A new API endpoint `/api/source/{doc_id}` that streams PDF documents only when a valid X402 payment proof is presented. The website integrates this as a "$0.10 — Download PDF" button accessible from both the catalog page and individual passage cards in search results.

### Business Model

- **Price:** $0.10 per document download (flat fee)
- **Purpose:** Hosting and bandwidth cost recovery — not a profit center
- **Payment protocol:** X402 (exact scheme, EVM-compatible chains)
- **Target users:** Agents and humans who discover relevant doctrine via search and want the full document

---

## 2. Technical Specification

### 2.1 X402 Protocol Reference

**Spec location:** https://github.com/x402-foundation/x402

**Key concepts:**
- `resource server` — our API server
- `facilitator` — third-party service that verifies and settles payments
- `scheme: exact` — client pays an exact amount to unlock a resource

**Typical flow:**
1. Client requests `/api/source/{doc_id}` without payment headers
2. Server returns `402 Payment Required` with `PAYMENT-REQUIRED` header (base64-encoded JSON)
3. Client selects payment method from `accepted` array, builds `PaymentPayload`
4. Client retries with `PAYMENT-SIGNATURE` header (base64-encoded `PaymentPayload`)
5. Server (or facilitator) verifies `PaymentPayload`
6. Server returns 200 with PDF stream + `PAYMENT-RESPONSE` header (settlement receipt)

### 2.2 X402 Headers

**Server → Client (402 response):**
```
HTTP/1.1 402 Payment Required
PAYMENT-REQUIRED: <base64-encoded PaymentRequired JSON>
```

**Client → Server (retry with payment):**
```
PAYMENT-SIGNATURE: <base64-encoded PaymentPayload JSON>
```

**Server → Client (200 response):**
```
PAYMENT-RESPONSE: <base64-encoded SettlementResponse JSON>
```

### 2.3 PaymentRequired Object (server → client)

```json
{
  "resource": {
    "url": "https://api.militarymanuals.ai/api/source/{doc_id}",
    "description": "Full PDF download: {document_title}",
    "mimeType": "application/pdf"
  },
  "accepted": [
    {
      "scheme": "exact",
      "network": "eip155:8453",
      "amount": "100000",
      "asset": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
      "payTo": "{payment_receiving_address}",
      "maxTimeoutSeconds": 300,
      "extra": {
        "assetTransferMethod": "eip3009",
        "name": "USDC",
        "version": "2"
      }
    }
  ]
}
```

**Notes:**
- `amount: "100000"` = $0.10 in USDC micro-units (USDC has 6 decimals)
- `network: "eip155:8453"` — Base mainnet (chain ID 8453)
- `asset: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"` — USDC canonical contract (same address on Ethereum, Base, and most EVM chains)
- First implementation: self-verified (no facilitator). Facilitator integration is Phase 2.

### 2.4 PaymentPayload Object (client → server)

```json
{
  "x402Version": 2,
  "resource": {
    "url": "https://api.militarymanuals.ai/api/source/{doc_id}",
    "description": "Full PDF download: {document_title}",
    "mimeType": "application/pdf"
  },
  "accepted": {
    "scheme": "exact",
    "network": "eip155:8453",
    "amount": "100000",
    "asset": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "payTo": "{payment_receiving_address}",
    "maxTimeoutSeconds": 300,
    "extra": {
      "assetTransferMethod": "eip3009",
      "name": "USDC",
      "version": "2"
    }
  },
  "payload": {
    "signature": "0x...",
    "authorization": {
      "from": "0x...",
      "to": "{payment_receiving_address}",
      "value": "100000",
      "validAfter": "1745800000",
      "validBefore": "1745800300",
      "nonce": "0x..."
    }
  }
}
```

### 2.5 API Endpoints

#### `GET /api/source/{doc_id}`

**Purpose:** Stream the PDF file for a document.

**Request (no payment):**
```
GET /api/source/980e09dece3ed6794381a81eeb56d8eecc139804a56b947c17c0a6bc307518fb
```

**Response (402 — not paid):**
```
HTTP/1.1 402 Payment Required
PAYMENT-REQUIRED: <base64>
Content-Type: application/json

{"error": "payment_required", "price_usd": "0.10", "doc_id": "...", "title": "..."}
```

**Request (with payment):**
```
GET /api/source/980e09dece3ed6794381a81eeb56d8eecc139804a56b947c17c0a6bc307518fb
PAYMENT-SIGNATURE: <base64>
```

**Response (200 — paid):**
```
HTTP/1.1 200 OK
Content-Type: application/pdf
Content-Disposition: attachment; filename="{title}.pdf"
PAYMENT-RESPONSE: <base64>
[PDF binary stream]
```

**Error responses:**
- `404 Not Found` — doc_id not in registry or not ingested
- `400 Bad Request` — invalid or expired payment proof
- `402 Payment Required` — no payment header present

#### `GET /api/source/{doc_id}/info`

**Purpose:** Return document metadata and price info (for UI to show before clicking).

**Response (200):**
```json
{
  "doc_id": "980e09dece3ed6794381a81eeb56d8eecc139804a56b947c17c0a6bc307518fb",
  "title": "AR 50-5",
  "page_count": 62,
  "file_size_bytes": 206168,
  "price_usd": "0.10",
  "local_path": "$DATA_DIR/source_pdfs/army/ar-50-5-980e09de.pdf"
}
```

---

## 3. Website Integration

### 3.1 Catalog Page (`catalog.html`)

Each document card gets a "Download PDF — $0.10" button in the `doc-links` div.

**Flow:**
1. On page load, call `GET /api/source/{doc_id}/info` for each visible document to get price
2. Display price badge if document has a local_path
3. Clicking "Download PDF" calls `fetchSourceInfo(docId)` then `downloadSourcePdf(docId)`
4. If 402: show "Pay $0.10 to download" modal with price + wallet button
5. On payment success: retry download and trigger browser download

### 3.2 Search Results (`index.html`)

Each passage card (from search results) gets a "Get Full Doc — $0.10" link.

**Flow:**
- Passage cards already show document metadata (doc_id, title, branch)
- Add download button that calls `GET /api/source/{doc_id}/info`
- Same 402 → pay → retry flow as catalog

### 3.3 JavaScript Changes

**`js/api.js` additions:**
- `fetchSourceInfo(docId)` — GET `/api/source/{doc_id}/info`
- `fetchSourcePdf(docId, paymentSignature)` — GET `/api/source/{doc_id}` with optional `PAYMENT-SIGNATURE` header; handles 402 by returning `{ paymentRequired: true, header: "..." }` so caller can parse the header

**`js/app.js` additions:**
- `handleSourceDownload(docId)` — orchestrates fetch → 402 → payment → retry → download

**`js/renderer.js` additions:**
- `buildSourceDownloadHtml(docId, title, price)` — returns HTML for the download button/badge

### 3.4 Payment Flow (Client-Side)

```
1. User clicks "Download PDF — $0.10"
2. fetchSourcePdf(docId) → 402
3. Parse PAYMENT-REQUIRED header (base64 → JSON)
4. Show payment modal to user with amount and wallet options
5. User approves in wallet → wallet constructs PaymentPayload + signs
6. Client sets PAYMENT-SIGNATURE header, retries fetchSourcePdf
7. Server validates, streams PDF
8. Browser triggers file download
```

**Note:** For Phase 1, client-side wallet integration is out of scope. Phase 1 focuses on the API accepting and validating payment headers. The website Phase 1 will show the 402 and display the payment details, but the actual wallet signing flow will be documented and deferred.

---

## 4. Data Model

### 4.1 Registry

The existing registry at `$DATA_DIR/registry.json` already has:
- `local_path` — absolute path to the PDF on disk
- `doc_id` — SHA256 of file content
- `filename` — original filename
- `branch` — army/navy/marines/other

**No schema changes required.**

### 4.2 New Configuration

```python
# api_server.py — new constants
PAID_DOWNLOAD_PRICE_USD = "0.10"
PAID_DOWNLOAD_ASSET = "USDC"
PAID_DOWNLOAD_USDC_CONTRACT = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # Base mainnet
PAID_DOWNLOAD_NETWORK = "eip155:8453"  # Base (chain ID 8453)
PAID_DOWNLOAD_RECEIVING_ADDRESS = "0xBABc60eD17e6387AEDab112E80744aA19EFCb723"  # Same wallet as Horizen ZK-RAG operations (Base-compatible)
PAID_DOWNLOAD_MAX_TIMEOUT = 300  # 5-minute payment window

### 4.3 USDC on Base

USDC is the canonical `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` on Base (chain ID 8453). This is the same address used on Ethereum mainnet — no need to look up a separate contract.

**Note on receiving address:** The `PAID_DOWNLOAD_RECEIVING_ADDRESS` must be a wallet capable of receiving USDC on Base (EVM-compatible). This can be a hardware wallet, a multisig, or a smart contract wallet. The private key signs the EIP-3009 `transferWithAuthorization` from this address.

---

## 5. Security Considerations

- **Payment proof is one-time use** — each payment proof can only be used once; replaying a used proof must fail
- **Non-repudiation** — the `PaymentPayload` includes the exact resource URL, preventing cross-resource payment reuse
- **Expiry** — payments have a `validBefore` timestamp; server must enforce this
- **File path traversal** — `doc_id` must be validated against the registry before accessing `local_path`
- **Payment receiving key** — private key must never be in the repository; set via environment variable

---

## 6. Out of Scope (Phase 1)

- Facilitator integration (self-verified only — server verifies EIP-3009 signature directly)
- Wallet extension integration (MetaMask/Rabby) — Phase 2
- Receipt/subscription management
- Refunds or disputes
- CDN for file delivery
- Multiple price tiers

---

## 7. Acceptance Criteria

### API
- [ ] `GET /api/source/{doc_id}` returns 402 with `PAYMENT-REQUIRED` header when called without payment
- [ ] `GET /api/source/{doc_id}` returns 200 with PDF binary when called with valid `PAYMENT-SIGNATURE`
- [ ] `GET /api/source/{doc_id}` returns 404 when doc_id is not in the registry
- [ ] `GET /api/source/{doc_id}` returns 400 when payment proof is invalid or expired
- [ ] `GET /api/source/{doc_id}/info` returns document metadata and price
- [ ] EIP-3009 signature verification passes for valid proofs
- [ ] EIP-3009 signature verification fails for tampered/expired proofs
- [ ] Same payment proof cannot be used twice (replay protection)

### Website
- [ ] Catalog page shows "Download PDF — $0.10" button on each document card
- [ ] Clicking download button on catalog triggers 402 flow and shows payment modal
- [ ] Search results passage cards show "Get Full Doc — $0.10" button
- [ ] Successful payment results in browser PDF download

### Security
- [ ] Server cannot access files outside `$DATA_DIR/source_pdfs/`
- [ ] Payment receiving address is read from environment variable, not hardcoded
- [ ] Payment proofs are one-time use (state tracked in memory or disk)

---

## 8. File Changes

### API Server
- `shared/api_server.py` — add `/api/source/{doc_id}` and `/api/source/{doc_id}/info` routes

### Website Frontend
- `website/catalog.html` — add download button to doc cards
- `website/js/api.js` — `fetchSourceInfo()`, `fetchSourcePdf()` functions
- `website/js/app.js` — `handleSourceDownload()` orchestration
- `website/js/renderer.js` — `buildSourceDownloadHtml()` button builder
- `website/index.html` — download button on passage cards (if not already via renderer)

### Documentation
- `docs/x402-paid-download/PRD-x402-paid-download.md` — this document

---

## 9. Configuration Reference

```bash
# Environment variables required for paid downloads
export PAID_DOWNLOAD_RECEIVING_ADDRESS="0x..."   # ZEN Mainnet address to receive USDC
export PAID_DOWNLOAD_USDC_CONTRACT="0x..."       # USDC contract on Horizen EVM
export PAID_DOWNLOAD_ENABLED="true"              # Feature flag
```

---

## 10. Open Questions

1. **Facilitator vs self-verified?** Phase 1 is self-verified (server verifies EIP-3009 directly). Do we want a facilitator in Phase 2?
2. **Replay protection storage?** In-memory is fine for Phase 1 (server restarts clear it). Phase 2 needs persistent storage (Redis or a simple file).
3. **Testnet testing?** Should we implement on Base Sepolia testnet first with faucet USDC to test the full EIP-3009 flow before mainnet?
