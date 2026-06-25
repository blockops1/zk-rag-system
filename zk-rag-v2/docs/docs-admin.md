# RAG System — Admin Guide
**Last updated:** 2026-04-02
**Git:** `./` (git@github.com:blockops1/document_rag_system.git)

This document tells you everything you need to know to operate the RAG system. Read this before doing anything with it.

---

## What the RAG System Is

A **four-pipeline document processing and semantic search system** for military doctrine PDFs. PDFs go in, searchable vector chunks come out.

- **API:** `http://127.0.0.1:8100/` (port 8100, FastAPI)
- **Qdrant vector DB:** Running as systemd service
- **VPS:** `ssh deruyter@blockoperations.com` (production)
- **Local server:** Dell R730, 56 cores, 377GB RAM, 7.3TB SSD at `/data/rag/`

---

## The Four Pipelines

```
PDF → A (fitz) → B (docling) → C (vision) → D (Qdrant)
       NO Qdrant writes until D
```

### Pipeline A — fitz text extraction
- **Script:** `batch_ingest_branch.py --pass 1`
- **What it does:** Extracts text from PDFs using fitz (PyMuPDF). Classifies each page as text or figure-only.
- **Output:** Per-page JSON in `/data/rag/ingested/{doc_id}/pages/`
- **Qdrant:** Writes nothing
- **Logic:**
  - High-density (avg_chars/page >= 300): pages written, status → `extracted`
  - Low-density (avg_chars/page < 300): added to `extraction_queue.json` for Pipeline B
- **Dedup:** SHA256 check against registry (status=extracted/ingested only), then title similarity against Qdrant

### Pipeline B — docling OCR
- **Script:** `batch_ingest_branch.py --pass 2` (daemon mode)
- **What it does:** Runs docling OCR on low-density PDFs that Pipeline A flagged
- **Output:** Same per-page JSON format, overwrites Pipeline A's output for that doc
- **Qdrant:** Writes nothing
- **Registry:** status → `extracted`
- **Runs as:** While-loop daemon (run_pipeline_b.sh)

### Pipeline C — SmolVLM2 vision
- **Script:** `batch_image_describe.py`
- **What it does:** Runs SmolVLM2-2.2B vision model on figure-only pages, generates text descriptions
- **Output:** Copies pages to `/data/rag/ingested-vision/{doc_id}/pages/` with `vision_description` field added
- **Qdrant:** Writes nothing
- **Design:** Copy-first — snapshots ingested/ at startup, only processes what existed then
- **Speed:** ~20-40 pages/hr per worker (CPU-only, slow)
- **Runs as:** Cron job (run_pipeline_c.sh) or manual

### Pipeline D — Qdrant index
- **Script:** `pipeline_d.py` (NOT YET TESTED)
- **What it does:** Reads from ingested-vision/ (or ingested/), chunks text, generates embeddings, upserts to Qdrant
- **Output:** Vectors in Qdrant collections
- **Qdrant:** THE ONLY WRITE POINT — all other pipelines write nothing to Qdrant
- **Dedup:** SHA256 exact match against Qdrant doc_ids + title similarity check
- **Registry:** status → `ingested`
- **Must run after:** B finishes processing extraction queue

---

## Directory Map

```
./              ← Git working copy. All scripts live here.
  scripts/
    api_server.py               ← FastAPI server (port 8100)
    batch_ingest_branch.py      ← Pipeline A + B
    batch_image_describe.py     ← Pipeline C
    pipeline_d.py               ← Pipeline D (UNTESTED)
    pdf_processing.py          ← Shared module for A + B
    run_pipeline_*.sh          ← Pipeline runners
    _log.py                     ← Logging utility
  harvester/                    ← IA harvest scripts
  logs/                         ← Pipeline logs
  venv/                         ← Python env
  venv-docling/                 ← Docling venv

/data/rag/
  mil-docs-staging/
    new-unified-registry-v2.json  ← Registry (983 docs)
    uploads/                      ← Source PDFs (741, by branch)
  ingested/                     ← A + B output (page JSONs)
  ingested-vision/              ← C output (page JSONs with vision_description)
  images/                       ← Extracted images
  chunks/                       ← Per-doc chunk JSONL
  embeddings/                   ← Per-doc embedding numpy
  pdfs/                        ← Short-term PDF storage
  qdrant_data/                  ← Qdrant vector DB
  docs/                        ← Static web files (catalog, index)
```

**Archive dirs (ignore):** `qdrant_data_wipe_backup_*`, `qdrant_data_presnap`, `qdrant_data_clean_*`, `archive_20260401/`, `scripts_v2/`, `scripts_v2_archived_*/`

---

## How to Check Things

### Are pipelines running?
```bash
ps aux | grep "batch_image\|batch_ingest\|pipeline_d" | grep -v grep
```

### Is the API running?
```bash
curl http://127.0.0.1:8100/health
```

### Is Qdrant running?
```bash
sudo systemctl status qdrant
```

### How many docs in Qdrant?
```bash
curl http://127.0.0.1:8100/collections
```

### Pipeline C progress?
```bash
python3 -c "
import json, pathlib
iv = pathlib.Path('/data/rag/ingested-vision')
doc_dirs = [d for d in iv.iterdir() if d.is_dir()]
fig = total = 0
for d in doc_dirs:
    for pf in (d/'pages').glob('*.json'):
        try:
            page = json.loads(pf.read_text())
            if page.get('figure_only'): fig += 1
            total += 1
        except: pass
print(f'ingested-vision docs: {len(doc_dirs)}, figure pages: {fig}, total pages: {total}')
"
```

### Registry status distribution?
```bash
python3 -c "
import json
reg = json.load(open('/data/rag/mil-docs-staging/new-unified-registry-v2.json'))
from collections import Counter
counts = Counter(doc.get('status') for doc in reg['documents'])
print(dict(counts))
"
```

---

## How to Start Pipelines

### Pipeline A (200 doc batch)
```bash
cd ./
source venv/bin/activate
nohup ./scripts/run_pipeline_a.sh > logs/pipeline_a_latest.log 2>&1 &
```

### Pipeline B (daemon — processes extraction queue)
```bash
cd ./
source venv/bin/activate
nohup ./scripts/run_pipeline_b.sh > logs/pipeline_b_latest.log 2>&1 &
```

### Pipeline C (vision)
```bash
cd ./
source venv/bin/activate
nohup ./scripts/run_pipeline_c.sh > logs/pipeline_c_latest.log 2>&1 &
```

### Pipeline D (Qdrant index — NOT TESTED YET)
```bash
cd ./
source venv/bin/activate
nohup ./scripts/run_pipeline_d.sh > logs/pipeline_d_latest.log 2>&1 &
```

---

## API Reference

### Query (semantic search)
```bash
curl -X POST http://127.0.0.1:8100/query \
  -H "Content-Type: application/json" \
  -d '{"query": "enemy prisoner of war handling procedures", "top_k": 5}'
```

### List collections
```bash
curl http://127.0.0.1:8100/collections
```

### Get doc metadata
```bash
curl http://127.0.0.1:8100/api/doc/{doc_id}
```

### Extract a PDF (Pipeline A endpoint — no Qdrant write)
```bash
curl -X POST http://127.0.0.1:8100/extract \
  -H "Content-Type: application/json" \
  -d '{"pdf_path": "/data/rag/mil-docs-staging/uploads/army/fm-21-76.pdf", "doc_id": "fm-21-76", "skip_ocr": true}'
```

### Reindex a doc (forces docling OCR)
```bash
curl -X POST http://127.0.0.1:8100/reindex \
  -H "Content-Type: application/json" \
  -d '{"doc_id": "some-doc-id", "collection": "army", "force_ocr": true}'
```

---

## Registry

**File:** `/data/rag/mil-docs-staging/new-unified-registry-v2.json`

Format:
```json
{
  "documents": [
    {
      "doc_id": "army-atp3-04-13",
      "filename": "ATP3-04.13.pdf",
      "sha256": "abc123...",
      "status": "downloaded",   // downloaded | extracted | ingested
      "branch": "army",
      "category": "Army Tactical",
      "title": "Mission Command",
      "pub_year": 2019,
      "ia_id": "something"
    },
    ...
  ]
}
```

Status values:
- `downloaded`: File staged, never processed
- `extracted`: Pages written to ingested/ (A or B completed)
- `ingested`: Written to Qdrant (D completed)

---

## Known Issues

### Queue file mismatch (CRITICAL — not fixed)
`run_pipeline_b.sh` watches `/data/rag/batch_ingest_branch_needs_docling.json`
`batch_ingest_branch.py` writes to `/data/rag/extraction_queue.json`
These are different files — Pipeline B daemon never sees new queue entries.
**Workaround:** Run `batch_ingest_branch.py --pass 2` manually or fix the runner script.

### Pipeline C copy-first staleness
C snapshots `ingested/` at startup. If A/B add new docs while C runs, those pages are missed.
Fix: Rerun C after A/B finish.

### Pipeline D not tested
`pipeline_d.py` exists but has never been run end-to-end. Do not trust it until tested.

### VPS registry path mismatch
VPS `rag-api.service` starts with old registry path. Fix before re-enabling push cron.

---

## Git Workflow

All scripts are git-tracked in `./`:
```bash
cd ./
git status                    # check what changed
git add scripts/...           # stage specific files
git commit -m "message"       # commit
git push origin main          # push to GitHub
```

Large data dirs are gitignored (venv/, qdrant_data/, ingested/, etc.).
