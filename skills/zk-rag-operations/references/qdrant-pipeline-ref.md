# ZK-RAG Operations — Qdrant & Pipeline Reference

## Qdrant Storage Backends

- **LMDB** (default since v1.7) — high-performance, concurrent reads/writes, recommended for production
- **SQLite** — single-writer, simpler but poor write concurrency; select with `storage_type: sqlite` in config

**Current production setup uses LMDB** (confirmed 2026-04-21 from `<DATA>qdrant/meta.json`).

## Qdrant SSH Tunnel

```bash
ssh -L 6333:127.0.0.1:6333 user@host
# Then open http://localhost:6333/dashboard
```

## R730 Qdrant
- **Storage:** `<DATA>qdrant/`
- **Admin dashboard:** `http://localhost:6333/dashboard` (ssh tunnel required)
- **Collections:** army (84k pts), navy (50k), marines (9k), other (12k) — as of 2026-04

## VPS Qdrant
- **Storage:** `/var/lib/qdrant/`
- **Service:** `qdrant.service`
- **Port:** 6333 (localhost only)
- **Public:** No — admin dashboard not exposed publicly

## Common Qdrant Operations

```bash
# Check if Qdrant is running
systemctl status qdrant

# Check disk usage
du -sh <DATA>qdrant/

# List collections via API
curl http://localhost:6333/collections

# Get collection info
curl http://localhost:6333/collections/{collection_name}

# Delete collection
curl -X DELETE http://localhost:6333/collections/{collection_name}
```

## Pipeline Reference

### Pipeline A — PDF Text Extraction
- **Script:** `pipeline_a/run_pipeline_a.sh`
- **Input:** PDFs at `$SOURCE_PDF_DIR/`
- **Output:** Extracted text at `$EXTRACTION_DIR/`
- **Env:** `SOURCE_PDF_DIR`, `EXTRACTION_DIR`, `LOG_DIR`, `LOCK_FILE`

### Pipeline B — OCR
- **Script:** `pipeline_b/run_pipeline_b.sh`
- **Input:** `$EXTRACTION_DIR/` (from A)
- **Output:** Updated extraction with OCR text
- **Env:** `EXTRACTION_QUEUE`, `EXTRACTION_DONE`, `DONE_LOG`, `FAILED_RETRY_LOG`

### Pipeline C — Vision Captions
- **Script:** `pipeline_c/run_pipeline_c.sh`
- **Input:** Document images from `$IMAGES_DIR/`
- **Output:** Vision captions added to extraction
- **Env:** `IMAGES_DIR`, `EXTRACTION_QUEUE`, `EXTRACTION_DONE`

### Pipeline D — Chunk + Embed + Qdrant
- **Script:** `pipeline_d/run_pipeline_d.sh`
- **Input:** Extracted text from B/C
- **Output:** Chunks in `$CHUNKS_DIR/`, embeddings in `$EMBEDDINGS_DIR/`, vectors in Qdrant
- **Env:** `CHUNKS_DIR`, `EMBEDDINGS_DIR`, `REGISTRY_PATH`, `EMBEDDING_SERVICE_URL`

### Pipeline E — Merkle Tree
- **Script:** `pipeline_e/run_pipeline_e.sh`
- **Input:** Chunks from D
- **Output:** Merkle trees at `$MERKLE_TREES_DIR/`
- **Env:** `CHUNKS_DIR`, `MERKLE_TREES_DIR`, `ZK_CIRCUIT_DIR`

### Pipeline F — On-Chain Emission
- **Script:** `pipeline_f/emit_all.py`
- **Input:** Merkle trees from E
- **Output:** Merkle root committed to Horizen EVM
- **Env:** `DEPLOYER_KEY`, `RPC_URL`, `CONTRACT_ADDRESS`, `ACTIVE_NETWORK`

### Pipeline G — Qdrant ZK Metadata
- **Script:** `pipeline_g/pipeline_g.py`
- **Input:** Merkle proofs from E
- **Output:** ZK metadata upserted to Qdrant alongside chunk vectors
- **Env:** `REGISTRY_PATH`, `ZK_PROOFS_DIR`, `MERKLE_TREES_DIR`

## R730 vs VPS Path Differences

| Component | R730 | VPS |
|-----------|------|-----|
| Code dir | `<REPO>` | `/home/deruyter/rag/` |
| Data dir | `<DATA>` | `/data/rag/` |
| API service | `rag-api-local.service` | `zk-rag-api.service` |
| Embedding service | `embedding-service-local.service` | `embedding-service.service` |
| Chunks | `<DATA>chunks/` | `/data/rag/chunks/` |
| Merkle trees | `<DATA>merkle_trees/` | `/data/rag/merkle_trees/` |
| Circuit binary | `<REPO>zk-circuit/target/release/prove-bin` | `/data/rag/zk-circuit/prove-bin` |

## Provenance.py Path Overrides

`provenance.py` uses environment variables (with R730 defaults):

| Env Var | R730 Default | VPS Value |
|---------|-------------|-----------|
| `ZK_PROVE_BINARY` | `<HOME>/.../prove-bin` | `/data/rag/zk-circuit/prove-bin` |
| `ZK_MERKLE_TREES_DIR` | `<DATA>merkle_trees` | `/data/rag/merkle_trees` |
| `ZK_CHUNKS_DIR` | `<DATA>chunks` | `/data/rag/chunks` |
| `ZK_PROOFS_DIR` | `<DATA>zk_proofs` | `/data/rag/zk_proofs` |
| `ZK_LOG_DIR` | `<VENV>logs` | `/home/deruyter/rag/logs` |
| `ZK_REGISTRY_PATH` | `<DATA>registry.json` | `/data/rag/registry.json` |
