# ZK-RAG System — Operator Guide

**Last updated:** 2026-05-01
**Git:** `./` (git@github.com:blockops1/zk-rag-system.git)

This document covers deployment and operation of the ZK-RAG system.

---

## System Overview

ZK-RAG is a document processing pipeline that produces verifiable, on-chain registered document embeddings. Source PDFs go in; searchable, ZK-proven vector chunks come out.

**Components:**
- **API server** (`shared/api_server.py`) — FastAPI query server on port 8100
- **Qdrant** — Vector database for embeddings (systemd service)
- **Pipelines A → D → E → F** — Extract → chunk+embed → prove → verify+register

---

## Repository Structure

```
./                          ← Git working copy
  pipeline_a.py              ← PDF text extraction
  pipeline_d.py              ← Chunk + embed + Qdrant upsert
  pipeline_e.py              ← ZK proof generation
  pipeline_f.py              ← ZK proof verification + on-chain registration
  pipeline_j_cleanup.py      ← Document removal from active corpus
  shared/                    ← Shared modules
    api_server.py            ← FastAPI query server
    _log.py                  ← Logging utility
    batch_ingest_branch.py  ← Batch ingestion helper
    embedding_service.py     ← Standalone embedding service
    provenance.py            ← Provenance helpers
  zk-circuit/               ← ZK circuit source + compiled binaries
  docs/                     ← This directory
  data/                     ← Data storage (gitignored)
    registry.json
    sourcePDF/
    chunks/
    embeddings/
    merkleTrees/
    zk_proofs/
    images/
    extracted/
    logs/
    archive/
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

```
DEPLOYER_KEY               ← Private key for contract deployment
RPC_URL                    ← Ethereum RPC URL
CONTRACT_ADDRESS           ← Deployed MerkleRootRegistry address
QDRANT_URL                 ← Qdrant instance URL (default: http://127.0.0.1:6333)
ZK_PROOF_PARALLELISM       ← Number of parallel proof workers
ADMIN_API_KEY              ← API key for admin endpoints
PAID_DOWNLOAD_RECEIVING_ADDRESS  ← X402 payment receiver
ZK_MERKLE_TREES_DIR        ← Path to merkleTrees/ directory
ZK_CHUNKS_DIR              ← Path to chunks/ directory
ZK_PROOFS_DIR              ← Path to zk_proofs/ directory
ZK_REGISTRY_PATH           ← Path to registry.json
```

---

## Service Management

```bash
# API server
python3 -m uvicorn shared.api_server:app --host 0.0.0.0 --port 8100

# Qdrant
sudo systemctl status qdrant
sudo systemctl restart qdrant
```

---

## Git Workflow

```bash
git status
git add <files>
git commit -m "description"
git push origin main
```
