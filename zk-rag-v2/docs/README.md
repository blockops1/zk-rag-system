# ZK-RAG System — Docs

## Quick Reference

**Full documentation:** See the top-level [`README.md`](README.md) in this repository.

**Pipeline overview:**

| Stage | Script | Purpose |
|-------|--------|---------|
| A | `pipeline_a.py` | Extract text from source PDFs |
| D | `pipeline_d.py` | Chunk, embed, and upsert to Qdrant |
| E | `pipeline_e.py` | Generate ZK proofs for document chunks |
| F | `pipeline_f.py` | Verify ZK proofs and register on-chain |
| J | `pipeline_j_cleanup.py` | Remove documents from the active corpus |

**Data directory (`data/`):**

```
data/
  registry.json          ← Document registry
  sourcePDF/             ← Source PDFs
  chunks/                ← Per-document chunk JSONL
  embeddings/           ← Per-document embeddings
  merkleTrees/           ← Merkle tree roots per document
  zk_proofs/             ← Generated ZK proofs
  images/                ← Extracted images
  extracted/            ← Intermediate extracted content
  logs/                  ← Pipeline logs
  archive/               ← Removed documents
```

**API server:** `shared/api_server.py` — runs on port 8100 by default.
