# ZK-RAG Pipeline Reference

## Pipeline Dependency Chain

All pipelines run on R730 only. Never run pipelines on VPS.

| Pipeline | Input | Output | Downstream |
|----------|-------|--------|------------|
| **A** | PDFs | Text chunks + images | B |
| **B** | Text chunks | BM25 index | C |
| **C** | BM25 index | `registry.json` | D |
| **D** | `registry.json` + doc JSON | Embeddings → Qdrant | E |
| **E** | Qdrant | ZK circuit proving | F |
| **F** | Qdrant + proofs | Proved chunks → Qdrant | G |
| **G** | Proved chunks | Website served from `/data` | — |

## Pipeline Runners

Each pipeline has a runner script: `run_pipeline_{a,b,c,d,e,f,g}.sh` in its pipeline directory.

### Critical path notes

- **Pipeline A** → output dir: `<DATA>` (chunks + images)
- **Pipeline B** → output dir: `<DATA>bm25/`
- **Pipeline C** → output: `registry.json` (the canonical document manifest)
- **Pipeline D** → embeddings go to Qdrant (collection per doc or flat); also writes `collection_name` into `registry.json` entries
- **Pipeline E** → reads from Qdrant collection; outputs ZK proofs (Groth16 or PLONK)
- **Pipeline F** → reads E's proofs + Qdrant chunks; writes proved chunk records back to Qdrant (with `has_proof` flag)
- **Pipeline G** → serves the website from `/data/zk-rag-website/` (static HTML/JS/CSS)

## Venv note

Pipeline C uses venv at `<REPO>venv/bin/python3` (not `./venv/bin/python3` from within `pipeline_c/` — no venv exists there).

## Registry.json Structure

```json
{
  "doc_id": {
    "doc_type": "FM",
    "pub_year": "2023",
    "branch": "ATP",
    "title": "Document Title",
    "pages": 42,
    "status": "active",
    "collection_name": "doc_1aa38a2a…983b8550",
    "has_embeddings": true,
    "title_status": "approved"
  }
}
```

## Qdrant Collections

- Collection naming: `doc_<first_16_chars_of_hash>` (e.g. `doc_1aa38a2ab98b5ca6c`)
- Each collection holds chunk vectors + payload (text, page_num, doc_type, etc.)
- Payload fields: `text`, `page_num`, `doc_type`, `pub_year`, `branch`, `page_count`, `doc_id`, `chunk_index`, `source_pg`
- Image data lives on disk at `<DATA>images/<doc-hash>/` — not in Qdrant

## Common Failures

| Failure | Symptom | Fix |
|---------|---------|-----|
| Pipeline A — PDF read error | Chunk file empty or zero pages | Check PDF is not password-protected or corrupted |
| Pipeline D — Qdrant write fails | Embedding count mismatch | Verify Qdrant is running; check collection not locked |
| Pipeline E — ZK proof fails | Proof status stuck | Check `zkevm-prover` service; check memory (needs ~50GB) |
| Website shows no documents | Catalog empty | Check `GET /api/catalog`; verify Qdrant has data; check registry.json |

## API Server

- Binary: `<VENV>venv/bin/python3 <REPO>shared/api_server.py`
- Port: **8100** (port 8080 is taken by `llama-server`)
- Service: `zk-rag-api.service` (systemd)
- Health: `curl http://localhost:8100/health`
- Catalog endpoint: `GET /api/catalog` (sources from Qdrant, 10-min cache)
- Search endpoint: `GET /api/search?q=<query>`
- Context endpoint: `GET /api/context?doc_id=<id>&chunk_index=0&window=1000`

## Systemd Services

| Service | Port | Purpose |
|---------|------|---------|
| `zk-rag-api.service` | 8100 | FastAPI — search, catalog, context, ZK proof |
| `llama-server` (manual) | 8080 | llama.cpp embedding server (now in-process since 2026-05-12) |
| `qdrant.service` | 6333 | Vector database |
