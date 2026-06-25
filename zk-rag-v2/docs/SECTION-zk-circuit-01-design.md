# SECTION-zk-circuit-01: ZK-RAG Circuit Design

**Parent:** PROJ.md
**Status:** Updated for single-root redesign (2026-04-20)
**Date:** 2026-04-16 (revised 2026-04-20)

This section covers the ZK circuit design for proving RAG query provenance.

---

## Purpose

Build a ZK-RAG system that proves—in zero-knowledge—that a language model was grounded in an authentic, untampered document when generating a response. A verifier can check the proof without seeing the document content, the query, or the model weights.

---

## Core Insight

**Per-document single root.** Each document has its own Merkle tree with a single Poseidon root. The root is published to an EVM contract. A ZK proof demonstrates that the retrieved context chunk is a genuine member of that document's committed Merkle tree—without revealing other chunks or the query itself.

---

## Threat Model

---

## Architecture

### High-Level Data Flow

```
[Document]
       │
       v
[Chunk + Embed  ]  -->  [Merkle Tree Build]
       |                      │
       |                      v (single Poseidon root)
       |               [Publish root to Horizen EVM contract]
       |               (plain hash data; no ZK proof at this step)
       │
       v
[Query]
       │
       v
[Vector Search]  -->  [Top-K Chunks]
       │
       v
[LLM Generation]  (uses retrieved chunks as context)
       │
       v
[ZK Proof Generation]  (plonky2 STARK)
  - Prove each top-K chunk is in this document's Merkle tree with this root
  - Prove LLM input includes those chunks
  - Output: (proof, public_inputs)
       │
       v
[On-chain verification] or [Local verification]
```

### Merkle Tree Construction (Per-Document, Single Root)

**Leaf = PoseidonHash(chunk_text_bytes)**
- Each chunk's raw bytes are processed as field elements and hashed with Poseidon
- Chunks are sorted before building the tree (deterministic ordering by leaf hash)
- Tree uses Poseidon permutation internally (plonky2-compatible)

**Root** — single Poseidon HashOut
- One Poseidon hash = 32 bytes = 4 Goldilocks field elements
- The root is the primary public input to the ZK proof
- Published to `MerkleRootRegistry` contract on Horizen

### Circuit Design (plonky2 v0.2.2)

**Primary Circuit: `merkle_proof_circuit`**

Public Inputs (PI):
1. `merkle_root` — single Poseidon HashOut (4 field elements) — the document root commitment
2. `output_hash` — Poseidon hash of the full LLM response text (optional for Phase 1)

Private Inputs (witness):
1. `chunk_data` — bytes of the retrieved chunk
2. `chunk_hash` — Poseidon digest of chunk_data
3. `merkle_proof` — sibling digest path (one hash per tree level)
4. `chunk_index` — integer index of chunk in the Merkle tree

Constraints enforced in circuit:
- Iterative Poseidon hashing upward from leaf: at each level, `hash(left, right)` based on index bit
- Final computed root must equal public input `merkle_root`
- PoseidonHash(chunk_data) == chunk_hash (re-hash to confirm integrity)

**Single-chunk proof (K=1 for Phase 1, up to K=5 later)**
- Phase 1: one chunk per proof — simplest path to a working proof
- Multi-chunk deferred to Phase 2

### Why No Cap (vs. Original Design)

The original design used a MerkleCap (height 4 → 16 entries) for recursion efficiency. This introduced `RandomAccessGate`, which has a plonky2 v0.2.2 constant-generator pairing bug. For non-recursive ZK-RAG proofs, a single root is simpler and correct.

### On-Chain Architecture: Two-Tier Model

ZK-RAG uses **two separate on-chain layers** for two separate jobs:

**Layer 1 — Commitment (Horizen Mainnet L3, at ingestion):**
- Single Poseidon root = 32 bytes per document
- Published once per document to `MerkleRootRegistry` contract on Horizen Mainnet
- The root is plain data — no ZK proof involved, just a `push` to a smart contract
- As corpus grows: append new documents; old proofs remain valid against old roots
- This is the "I commit to this document's chunk set" signal

**Layer 2 — Proof Verification (zkVerify / Kurier, at query time):**
- plonky2 STARK proof generated at query time
- Proof sent to Kurier → verified on zkVerify smart contract
- Verifier checks: "this chunk exists in the Merkle tree with this root"
- The document root was already published, so the proof only needs to prove membership

**Horizen Mainnet (Base) — Chain Details:**

| Parameter | Value |
|-----------|-------|
| Chain ID | 26514 |
| RPC (HTTPS) | https://horizen.calderachain.xyz/http |
| RPC (WS) | wss://horizen.calderachain.xyz/ws |
| Gas symbol | ETH |
| Token symbol | ZEN |
| Block Explorer | https://horizen.calderaexplorer.xyz/ |
| Bridge | https://horizen.hub.caldera.xyz/ |

**Layer 2 — Proof Verification (zkVerify / Kurier, at query time):**
- plonky2 STARK proof generated at query time
- Proof sent to Kurier → verified on zkVerify smart contract
- Verifier checks: "this chunk exists in the Merkle tree with this root"
- The document root was already published, so the proof only needs to prove membership

**Why this split:**
- Publishing a root on Horizen L3 is cheap (gas is very low on this L3)
- Generating a ZK proof on Horizen EVM would be prohibitively slow/expensive
- Kurier/zkVerify is built exactly for fast on-chain STARK verification
- Two chains, two jobs — each optimized for its task

---

## Technology Stack

| Component | Technology | Version | Notes |
|-----------|-----------|---------|-------|
| ZK Framework | plonky2 | 0.2.2 | Stark proof; Poseidon hash |
| Proof Verification On-chain | Kurier / zkVerify | — | On-chain STARK verification |
| Commitment Storage | Horizen Mainnet L3 (chain 26514) | Caldera | Single root published via MerkleRootRegistry.sol |
| Cloud Proving | Local | CLI | CPU-based plonky2 proving for development |
| Off-chain Verifier | kurier.xyz | API | Lightweight verification endpoint |
| Rust Tooling | cargo, rustc | stable | Local dev and testing |
| Embedding Model | Qwen3-Embedding-0.6B | — | Deployed on R730 as embedding service |
| Vector DB | Qdrant | — | Deployed at `./data/qdrant/` |

### Kurier API Integration

Lightweight proof verification service.

```bash
curl -X POST https://api.kurier.xyz/verify \
  -H "Content-Type: application/json" \
  -d '{"proof": {...}, "public_inputs": [...]}''
```

**OPEN QUESTION**: Kurier base URL and exact endpoint format need confirmation from `docs.kurier.xyz`. The structure above is illustrative.

---

## Security Considerations

1. **Document Immutability**: Once a document's root is published, that document's chunk set is committed. Rebuilding the tree with different chunks produces a different root—old proofs become invalid for the new tree.
2. **Chunk Sorting**: Sort chunks deterministically (by `PoseidonHash(chunk_bytes)`) before building tree. Non-deterministic ordering breaks verification.
3. **Query Privacy**: Query text is NOT hidden from the verifier. Only document contents and the LLM input are private.
4. **Merkle Path Leakage**: Revealing sibling hashes for a chunk does not reveal other chunks in the document—Merkle trees are information-theoretically secure.
5. **No Private Keys**: Pure proving system; no signatures or secret keys involved.
6. **ZK Proof Soundness**: plonky2 with Poseidon is a proof system with extractable witness—anyone with the proof can extract the private inputs. This is standard for STARKs; do not confuse with FHE or secure multi-party computation.

---

## Open Questions (for Mr. V)

1. **Kurier API**: Base URL and `/verify` endpoint format need confirmation from `docs.kurier.xyz`
2. **Phase 1 scope**: K=1 (single chunk proof) vs K=5 (multi-chunk). Decision: start with K=1 for working proof, multi-chunk as Phase 2.
3. **LLM output hash**: Include `output_hash` as public input in Phase 1, or defer to later?
4. **Performance targets**: Acceptable proving time bounds for CPU-based proving?

---

## Ralph Reference (archived — do not edit)

The Ralph-specific implementation guide (detailed Phase 1-5 tasks, plonky2 API patterns, code structure) is archived in `PROJ-zk-rag-ARCHIVED.md` Section 5. Move to a Ralph skill when ready to implement.
