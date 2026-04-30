# ZK-RAG — Zero-Knowledge Retrieval-Augmented Generation

A production-grade RAG system with cryptographic ZK proofs of provenance, built on Horizen EVM (Zendoo) with plonky2 zero-knowledge circuits.

> **This is the public scaffold.** It contains all pipeline scripts, contracts, circuits, and frontend — everything needed to run your own ZK-RAG system. Sample data directories are included but empty; point the pipelines at your own document corpus.

---

## What Does It Do?

1. **Ingest** PDFs through a processing pipeline (text extraction, OCR, vision descriptions, chunking)
2. **Embed** document chunks as vectors and store them in Qdrant
3. **Prove** every chunk with a ZK proof that cryptographically verifies it belongs to a document whose Merkle root is committed on-chain
4. **Query** — semantic search returns results with a verifiable ZK proof of provenance

The result: anyone can verify that a search result actually came from the committed document corpus — without revealing the documents themselves.

---

## Architecture

```
PDF ──► A (fitz) ──► B (docling OCR) ──► C (SmolVLM2 vision) ──► D (chunk + embed + Qdrant)
                                                                              │
                                                                              ▼
                                                               E (Poseidon Merkle tree)
                                                                              │
                                                                              ▼
                                                          F (commit root on Horizen EVM)
                                                                              │
                                                                              ▼
                                                     G (Qdrant payload + ZK metadata)
                                                                              │
                                                                              ▼
                                                    Query API + ZK proof generation
```

| Stage | What happens |
|--------|--------------|
| **A** | Extract raw text from each PDF page via PyMuPDF (fitz) |
| **B** | Run docling OCR on low-density pages to recover text from scans |
| **C** | Generate vision captions for figure/photo pages via SmolVLM2 |
| **D** | Split into overlapping chunks, embed with Qwen3-Embedding-0.6B, upsert to Qdrant |
| **E** | Build a Poseidon Merkle tree over all chunks (plonky2) |
| **F** | Commit the Merkle root to the Horizen EVM MerkleRootRegistry contract |
| **G** | Store Merkle proof path + ZK circuit metadata in Qdrant alongside each chunk |

---

## Smart Contracts

| Network | Contract | Address |
|---------|----------|---------|
| Horizen Testnet | MerkleRootRegistry V1 | `0x83166A340c0A61bc836BD6383aD4acB23a3E3176` |
| Horizen Mainnet | MerkleRootRegistry V2 | `0x462fc86E28c07798BD4656451611FE4E0A6D7760` |

Both are publicly verifiable on the [Horizen block explorer](https://horizen.calderaexplorer.xyz).

---

## Quick Start

### 1. Clone and set up the environment

```bash
git clone https://github.com/blockops1/document-rag-with-zk.git
cd document-rag-with-zk

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt          # if a requirements.txt exists
# or install key deps directly:
pip install fastapi uvicorn qdrant-client python-dotenv httpx
```

### 2. Configure environment

```bash
cp zk-rag-v2/.env.example zk-rag-v2/.env
# Edit zk-rag-v2/.env and fill in:
#   DEPLOYER_KEY     — wallet private key for on-chain emission
#   KURIE_API_KEY    — API key from kurier.xyz (for ZK proof submission)
#   RPC_URL          — EVM RPC endpoint
#   CONTRACT_ADDRESS — MerkleRootRegistry contract address
```

### 3. Build the ZK circuits (one-time)

```bash
cd zk-rag-v2/zk-circuit
cargo build --release
# Pre-built circuit binaries (depth 5–12) are included in the repo
```

### 4. Start Qdrant

```bash
# Using Docker:
docker run -d --name qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant

# Or install Qdrant natively — see https://qdrant.tech/documentation/
```

### 5. Start the API server

```bash
cd zk-rag-v2
source venv/bin/activate
export PYTHONPATH=.
python3 shared/api_server.py
# API at http://127.0.0.1:8100/
```

### 6. Start the embedding service (separate terminal)

```bash
cd zk-rag-v2
source venv/bin/activate
export PYTHONPATH=.
python3 shared/embedding_service.py
```

---

## Running the Pipelines

After setup, process your own document corpus:

```bash
# Pipeline A — extract text from PDFs
./zk-rag-v2/pipeline_a/run_pipeline_a.sh

# Pipeline B — OCR on low-density pages
./zk-rag-v2/pipeline_b/run_pipeline_b.sh

# Pipeline C — vision model captions for figure pages
./zk-rag-v2/pipeline_c/run_pipeline_c.sh

# Pipeline D — chunk, embed, upsert to Qdrant
./zk-rag-v2/pipeline_d/run_pipeline_d.sh

# Pipeline E — build Poseidon Merkle trees
./zk-rag-v2/pipeline_e/run_pipeline_e.sh

# Pipeline F — emit Merkle roots on-chain
source zk-rag-v2/.env
cd zk-rag-v2/pipeline_f
python3 emit_all.py --batch --limit 200

# Pipeline G — sync ZK metadata to Qdrant
# (handled automatically by emit_all.py after emission)
```

---

## Query API

```bash
# Semantic search
curl -X POST http://127.0.0.1:8100/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "enemy prisoner of war handling procedures", "top_k": 5}'

# List available collections
curl http://127.0.0.1:8100/api/collections

# Generate ZK proof of provenance for a chunk
curl -X POST http://127.0.0.1:8100/api/provenance/prove \
  -H "Content-Type: application/json" \
  -d '{"chunk_id": "abc123..."}'

# Verify a proof
curl -X POST http://127.0.0.1:8100/api/provenance/verify \
  -H "Content-Type: application/json" \
  -d '{"proof": {...}}'
```

Interactive docs at [http://127.0.0.1:8100/docs](http://127.0.0.1:8100/docs)

---

## Project Structure

```
zk-rag-v2/
├── pipeline_a/          ← PDF text extraction (PyMuPDF/fitz)
├── pipeline_b/          ← OCR (docling)
├── pipeline_c/          ← Vision captions (SmolVLM2)
├── pipeline_d/          ← Chunking + embedding + Qdrant upsert
├── pipeline_e/          ← Poseidon Merkle tree builder (Rust/plonky2)
├── pipeline_f/          ← On-chain Merkle root emission (Foundry)
├── pipeline_g/          ← Qdrant upsert with ZK metadata
├── shared/
│   ├── api_server.py    ← FastAPI query + provenance server
│   ├── embedding_service.py  ← Qwen3 embedding service
│   └── provenance.py    ← ZK proof generation + verification
├── zk-circuit/          ← plonky2 ZK circuits (Rust)
│   ├── prove-bin/       ← Binary for generating ZK proofs
│   └── verify-zk-proof/ ← Binary for verifying proofs on-chain
├── website/             ← Frontend (static HTML/JS)
└── docs/
    ├── README.md        ← This file
    ├── admin.md         ← Full operator guide
    └── dependency-map.md ← System dependencies

data/                    ← Document corpus and runtime data (gitignored)
├── chunks/              ← Per-document chunk JSONL files
├── embeddings/          ← Per-document numpy embedding files
├── merkle_trees/        ← Per-document Merkle tree JSON files
└── zk_proofs/           ← Generated ZK proofs
```

---

## Customizing for Your Data

1. **Add your PDFs** to a directory and point Pipeline A at it
2. **Update the registry** — add entries for your documents (see `data/` for the registry format)
3. **Set `DEPLOYER_KEY`** in `.env` with a wallet funded for the target network
4. **Run pipelines A → B → C → D → E → F in order**
5. **Deploy the contract** using the Foundry scripts in `pipeline_f/script/`

---

## Key Files

| File | Purpose |
|------|---------|
| `zk-rag-v2/shared/api_server.py` | FastAPI server — query + provenance endpoints |
| `zk-rag-v2/shared/embedding_service.py` | HTTP embedding service wrapping Qwen3 |
| `zk-rag-v2/zk-circuit/src/circuits/zk_rag.rs` | ZK circuit design (plonky2) |
| `zk-rag-v2/pipeline_f/contracts/MerkleRootRegistryV2.sol` | On-chain verifier contract |
| `zk-rag-v2/docs/admin.md` | Full operator guide |
| `zk-rag-v2/docs/dependency-map.md` | System dependency guide |
| `zk-rag-v2/PROJ.md` | Project status and history |

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DEPLOYER_KEY` | Private key for on-chain Merkle root emission |
| `KURIE_API_KEY` | API key from [kurier.xyz](https://kurier.xyz) for ZK proof submission |
| `RPC_URL` | EVM RPC endpoint URL |
| `CONTRACT_ADDRESS` | MerkleRootRegistry contract address |
| `ACTIVE_NETWORK` | `testnet` or `mainnet` |
| `ZK_PROOF_PARALLELISM` | Number of parallel ZK proof workers (default: 2) |

