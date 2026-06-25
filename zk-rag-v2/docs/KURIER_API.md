# Kurier API Documentation

## Networks

| Environment | API Base URL | Portal |
|---|---|---|
| **Mainnet** | `https://api.kurier.xyz/api/v1` | https://kurier.xyz |
| **Testnet** | `https://api-testnet.kurier.xyz/api/v1` | https://testnet.kurier.xyz |

**Important:** API keys are network-specific. Mainnet keys ≠ testnet keys.

## Authentication

Kurier uses **x402 micropayment headers** — not API keys in the URL path.

### Submit Proof
```
POST /api/v1/submit-proof/
Headers:
  Content-Type: application/json
  PAYMENT-SIGNATURE: <signed payment authorization>
  X-Api-Key: <api_key>              (alternative, testnet free tier)
```

### Job Status
```
GET /api/v1/job-status/{job_id}/
Headers:
  X-Api-Key: <api_key>
```

## Submit Proof Body
```json
{
  "proofData": {
    "proof": "0x...",
    "publicSignals": "0x...",
    "vk": "0x..."
  },
  "proofType": "plonky2",
  "proofOptions": { "hashFunction": "poseidon" },
  "vkRegistered": true,
  "submissionMode": "attestation"
}
```

Supported proof types: `groth16`, `fflonk`, `plonky2`, `risc0`, `sp1`, `ultrahonk`, `ultraplonk`, `proof of SQL`

## Job Status Polling
```
GET /api/v1/job-status/{job_id}/
```

Terminal statuses: `completed`, `successful`, `done`, `verified`, `failed`, `rejected`, `invalid`

## zkVerify Explorer
- Mainnet: https://zkverify.io/explorer
- Testnet: https://testnet.zkverify.io/explorer

## Support
- Discord: discord.gg/zkverify
- Email: kurier-support@horizenlabs.io

## Migration: Testnet → Mainnet
1. Switch API base: `https://api-testnet.kurier.xyz/api/v1` → `https://api.kurier.xyz/api/v1`
2. Generate new API key at kurier.xyz (old testnet key won't work)
3. VKs auto-re-register on mainnet — no action needed
