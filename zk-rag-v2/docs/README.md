# Military Documents RAG System
**Block Operations — Document Intelligence Pipeline**

Four-pipeline document processing and semantic search system for military doctrine PDFs. PDFs go in, searchable and verifiable vector chunks come out.

---

## Architecture

```
PDF → A (fitz) → B (docling) → C (vision) → D (Qdrant)
       NO Qdrant writes until D
```

| Pipeline | Script | Output | Qdrant? |
|----------|--------|--------|---------|
| A | `batch_ingest_branch.py --pass 1` | Page JSONs (fitz) | No |
| B | `batch_ingest_branch.py --pass 2` | Page JSONs (docling OCR) | No |
| C | `batch_image_describe.py` | Page JSONs + SmolVLM2 vision descriptions | No |
| D | `pipeline_d.py` | Chunks → embeddings → Qdrant | **YES — only write point** |

---

## Repository Structure

```
$REPO_DIR/              ← git working copy
  scripts/                       ← Active pipeline scripts
    api_server.py               ← FastAPI query server (port 8100)
    batch_ingest_branch.py      ← Pipeline A + B
    batch_image_describe.py     ← Pipeline C (SmolVLM2)
    pipeline_d.py               ← Pipeline D (chunk + embed + Qdrant)
    pdf_processing.py           ← Shared module (A + B)
    run_pipeline_*.sh           ← Pipeline runners
  harvester/                    ← Internet Archive harvest scripts
  logs/                         ← Pipeline logs
  venv/                         ← Python environment
  venv-docling/                 ← Docling OCR venv

/data/rag/                      ← Data storage (gitignored)
  mil-docs-staging/
    new-unified-registry-v2.json  ← Document registry (983 docs)
    uploads/                    ← Source PDFs (by branch)
  ingested/                     ← Pipeline A + B output
  ingested-vision/              ← Pipeline C output
  chunks/                       ← Per-doc chunk JSONL
  embeddings/                   ← Per-doc embedding numpy
  qdrant_data/                  ← Qdrant vector database
  docs/
    admin.md                    ← Full operator guide
```

---

## Quick Start

```bash
cd $REPO_DIR
source venv/bin/activate

# Check status
ps aux | grep "batch_image\|batch_ingest" | grep -v grep
curl http://127.0.0.1:8100/health

# Start pipelines
nohup ./scripts/run_pipeline_a.sh > logs/pipeline_a_latest.log 2>&1 &
nohup ./scripts/run_pipeline_b.sh > logs/pipeline_b_latest.log 2>&1 &
nohup ./scripts/run_pipeline_c.sh > logs/pipeline_c_latest.log 2>&1 &
```

---

## API

- **Query server:** `http://127.0.0.1:8100/` (proxied via OpenResty on VPS)
- **Interactive docs:** `http://127.0.0.1:8100/docs`

```bash
# Search
curl -X POST http://127.0.0.1:8100/query \
  -H "Content-Type: application/json" \
  -d '{"query": "enemy prisoner of war handling", "top_k": 5}'

# List collections
curl http://127.0.0.1:8100/collections
```

---

## Service Management

```bash
# API server
sudo systemctl status rag-api
sudo systemctl restart rag-api

# Qdrant
sudo systemctl status qdrant
```

---

## Full Operator Guide

See [`docs/admin.md`](docs/admin.md) for:
- Complete pipeline details
- Status check commands
- Registry format
- Known issues
- Git workflow
