# ZK-RAG — Zero-Knowledge Retrieval-Augmented Generation

A production-grade RAG system with on-chain ZK proof of provenance for military documents.
Built on Horizen EVM (Zendoo) with plonky2 zero-knowledge circuits.

**This is a scaffolded public version.** See [docs/README.md](zk-rag-v2/docs/README.md)
for the full operator guide.

---

## Quick Start

```bash
# 1. Clone / extract this archive
cd ~/zk-rag-public

# 2. Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt        # or pyproject.toml if using Poetry

# 3. Set up environment
cp .env.example .env
# Edit .env — fill in your keys (see .env.example for values to request)

# 4. Build the Rust circuit (one-time)
cd zk-rag-v2/zk-circuit
cargo build --release

# 5. Start Qdrant
#    See docs/dependency-map.md for Qdrant installation
#    Default config: http://127.0.0.1:6333

# 6. Run the API server
cd ../
source venv/bin/activate
python3 shared/api_server.py
# API at http://127.0.0.1:8100/

# 7. Run the embedding service (separate terminal)
python3 shared/embedding_service.py
```

---

## What's Included

| Directory | Contents |
|-----------|----------|
| `zk-rag-v2/pipeline_a/` | PDF ingestion — fitz + docling OCR |
| `zk-rag-v2/pipeline_b/` | Document layout processing |
| `zk-rag-v2/pipeline_c/` | Vision model image descriptions |
| `zk-rag-v2/pipeline_d/` | Chunking + embedding + Qdrant upsert |
| `zk-rag-v2/pipeline_e/` | Merkle tree builder (plonky2/Poseidon) |
| `zk-rag-v2/pipeline_f/` | On-chain Merkle root emission (Foundry) |
| `zk-rag-v2/pipeline_g/` | Qdrant upsert with ZK metadata |
| `zk-rag-v2/shared/` | API server, embedding service, provenance |
| `zk-rag-v2/zk-circuit/` | plonky2 ZK circuits + prove/verify binaries |
| `zk-rag-v2/website/` | Frontend (static HTML/JS) |
| `data/` | Sample document data (chunks, embeddings, trees) |

---

## System Architecture

```
PDF → A (fitz) → B (docling) → C (vision) → D (Qdrant + embed)
                                                         ↓
                                              E (Merkle tree, Poseidon)
                                                         ↓
                                              F (emit root on-chain)
                                                         ↓
                                              G (Qdrant with ZK metadata)
                                                         ↓
                                         Query API + ZK proof of provenance
```

- **Pipeline D** chunks documents and stores vector embeddings in Qdrant
- **Pipeline E** builds Poseidon Merkle trees over chunks (plonky2)
- **Pipeline F** commits Merkle roots on Horizen EVM (V2 contract)
- **Pipeline G** upserts to Qdrant with Merkle proof metadata
- **ZK proofs** (plonky2) prove a chunk belongs to the committed Merkle tree

---

## Smart Contracts

| Network | Address | Description |
|---------|---------|-------------|
| Horizen Testnet | `0x83166A340c0A61bc836BD6383aD4acB23a3E3176` | V1 MerkleRootRegistry |
| Horizen Mainnet | `0x462fc86E28c07798BD4656451611FE4E0A6D7760` | V2 MerkleRootRegistry |

Both are publicly verifiable on the Horizen block explorers.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `DEPLOYER_KEY` | Private key for on-chain emission (testnet/mainnet) |
| `KURIE_API_KEY` | API key from [Kurier](https://kurier.xyz) for ZK proof submission |
| `RPC_URL` | EVM RPC endpoint (testnet or mainnet) |
| `CONTRACT_ADDRESS` | MerkleRootRegistry contract address |
| `ACTIVE_NETWORK` | `testnet` or `mainnet` |

---

## Customizing for Your Data

1. **Add your PDFs:** Place them in a directory and point Pipeline A at it
2. **Update the registry:** Add entries for your documents (see `data/registry.json` for format)
3. **Run pipelines:** A → B → C → D → E → F → G in order
4. **Deploy the contract:** Use the Foundry scripts in `pipeline_f/`

See `docs/admin.md` for the full operator guide.

---

## Key Files

- `zk-rag-v2/docs/README.md` — Architecture and pipeline overview
- `zk-rag-v2/docs/admin.md` — Full operator guide
- `zk-rag-v2/docs/dependency-map.md` — System dependencies
- `zk-rag-v2/PROJ.md` — Project status and history
- `zk-rag-v2/zk-circuit/src/circuits/zk_rag.rs` — ZK circuit design

---

*Last scaffolded: 2026-04-29*
