# ZK-RAG — Zero-Knowledge Retrieval-Augmented Generation

A production-grade RAG system with cryptographic ZK proofs of provenance, built on Horizen EVM (Zendoo) with plonky2 zero-knowledge circuits.

> **This is the public scaffold.** It contains all pipeline scripts, contracts, circuits, and frontend — everything needed to run your own ZK-RAG system. The `data/` directory is empty; point the pipelines at your own document corpus.

---

## What Does It Do?

1. **Ingest** PDFs through a processing pipeline (text extraction, OCR, chunking)
2. **Embed** document chunks as vectors and store them in Qdrant
3. **Prove** every chunk with a ZK proof that cryptographically verifies it belongs to a document whose Merkle root is committed on-chain
4. **Query** — semantic search returns results with a verifiable ZK proof of provenance

The result: anyone can verify that a search result actually came from the committed document corpus — without revealing the documents themselves.

---

## Architecture

```
PDF ──► A (fitz) ──► B (docling OCR) ──► D (chunk + embed + Qdrant)
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
|-------|-------------|
| **A** | Extract raw text from each PDF page via PyMuPDF (fitz) |
| **B** | Run docling OCR on low-density pages to recover text from scans |
| **D** | Split into overlapping chunks, embed with NomicEmbed, upsert to Qdrant |
| **E** | Build a Poseidon Merkle tree over all chunks (plonky2) |
| **F** | Commit the Merkle root to the Horizen EVM MerkleRootRegistry contract |
| **G** | Store Merkle proof path + ZK circuit metadata in Qdrant alongside each chunk |

---

## Smart Contracts

Deploy your own MerkleRootRegistry instance. The contract source is in `zk-rag-v2/pipeline_f/contracts/`.

```bash
cd zk-rag-v2/pipeline_f

# Deploy to testnet
source ../.env
forge script script/DeployV2.s.sol --rpc-url $RPC_URL --broadcast --verify

# Deploy to mainnet
forge script script/DeployV2.s.sol --rpc-url $MAINNET_RPC_URL --broadcast --verify
```

After deployment, update `CONTRACT_ADDRESS` in `.env` with the new contract address.

**Reference deployments (verify on [Horizen block explorer](https://horizen.calderaexplorer.xyz)):**

| Network | Contract | Address |
|---------|----------|---------|
| Horizen Testnet | MerkleRootRegistry V1 | `0x83166A340c0A61bc836BD6383aD4acB23a3E3176` |
| Horizen Mainnet | MerkleRootRegistry V2 | `0x462fc86E28c07798BD4656451611FE4E0A6D7760` |

---

## Quick Start

### 1. Clone and set up the environment

```bash
git clone https://github.com/blockops1/zk-rag-system.git
cd zk-rag-system

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt          # if a root requirements.txt exists
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
# Pre-built circuit binaries (depth 5–12) are included: circuit_depth_5.bin, etc.
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

---

## Running the Pipelines

Process your document corpus through the pipeline stages in order:

```bash
# Pipeline A — extract text from PDFs
./zk-rag-v2/pipeline_a/run_pipeline_a.sh

# Pipeline B — OCR on low-density pages (docling)
./zk-rag-v2/pipeline_b/run_pipeline_b.sh

# Pipeline D — chunk, embed, upsert to Qdrant
./zk-rag-v2/pipeline_d/run_pipeline_d.sh

# Pipeline E — build Poseidon Merkle trees
./zk-rag-v2/pipeline_e/run_pipeline_e.sh

# Pipeline F — emit Merkle roots on-chain
source zk-rag-v2/.env
cd zk-rag-v2/pipeline_f
python3 emit_all.py --batch --limit 200

# Pipeline G — sync ZK metadata to Qdrant (handled automatically after emission)

# Pipeline J — cleanup: remove orphaned artifacts with no registry entry
./zk-rag-v2/pipeline_j/pipeline_j_cleanup.py --dry-run  # review first
./zk-rag-v2/pipeline_j/pipeline_j_cleanup.py            # execute
```

---

## Query API

```bash
# Semantic search
curl -X POST http://127.0.0.1:8100/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "your search query", "top_k": 5}'

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
zk-rag-system/               ← GitHub repo root
├── README.md                 ← This file
├── tools/                    ← Build and audit tools
│   ├── scaffold_zkrag.py     ← Repo scaffolding generator
│   └── scan_leaks.py         ← Credential/path leak scanner
├── skills/                   ← Operator reference (ZK-RAG, Git, Linux Admin, etc.)
└── zk-rag-v2/               ← Main project
    ├── pipeline_a/           ← PDF text extraction (PyMuPDF/fitz)
    ├── pipeline_b/           ← OCR (docling)
    ├── pipeline_d/            ← Chunking + embedding + Qdrant upsert
    ├── pipeline_e/            ← Poseidon Merkle tree builder (Rust/plonky2)
    ├── pipeline_f/            ← On-chain Merkle root emission (Foundry)
    ├── pipeline_g/            ← Qdrant upsert with ZK metadata
    ├── pipeline_j/            ← Orphaned artifact cleanup
    ├── shared/
    │   ├── api_server.py      ← FastAPI query + provenance server
    │   └── provenance.py      ← ZK proof generation + verification
    ├── zk-circuit/           ← plonky2 ZK circuits (Rust)
    │   ├── circuit/           ← Circuit library crate
    │   ├── prove-bin/         ← Binary for generating ZK proofs
    │   ├── verify-zk-proof/   ← Binary for verifying proofs on-chain
    │   └── circuit_depth_*.bin  ← Pre-built circuit binaries (depth 5–12)
    ├── website/               ← Frontend (static HTML/JS)
    └── docs/
        ├── admin.md          ← Full operator guide
        └── dependency-map.md ← System dependencies

data/                         ← Document corpus and runtime data (gitignored)
├── registry.json             ← Document manifest
├── sourcePDF/                ← Source PDFs
├── chunks/                   ← Per-document chunk JSONL files
├── embeddings/               ← Per-document numpy embedding files
├── merkleTrees/              ← Per-document Merkle tree JSON files
├── images/                   ← Extracted page images
├── extracted/                ← PDF text extraction output
├── zk_proofs/               ← Generated ZK proofs
├── qdrant/                   ← Qdrant storage
├── logs/                     ← Pipeline and API logs
├── failed_pdfs/              ← Failed extraction tracking
├── archive/                  ← Archived docs
└── extraction_queue.json     ← Pipeline B work queue
```

---

## Customizing for Your Data

1. **Add your PDFs** to `data/sourcePDF/` and register them in `data/registry.json`
2. **Set `DEPLOYER_KEY`** in `.env` with a wallet funded for the target network
3. **Run pipelines A → B → D → E → F in order** — each stage reads from the previous stage's output
3. **Deploy the MerkleRootRegistry contract** — see Smart Contracts section above

---

## Key Files

| File | Purpose |
|------|---------|
| `zk-rag-v2/shared/api_server.py` | FastAPI server — query + provenance endpoints |
| `zk-rag-v2/shared/provenance.py` | ZK proof generation + zkVerify submission |
| `zk-rag-v2/zk-circuit/prove-bin/` | Binary ZK proof generator (Rust) |
| `zk-rag-v2/zk-circuit/verify-zk-proof/` | On-chain proof verifier binary (Rust) |
| `zk-rag-v2/pipeline_f/contracts/MerkleRootRegistryV2.sol` | On-chain verifier contract |
| `zk-rag-v2/docs/admin.md` | Full operator guide |
| `zk-rag-v2/docs/dependency-map.md` | System dependency guide |

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
