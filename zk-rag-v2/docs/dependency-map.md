# ZK-RAG Dependency Map

## Live Files (actively used)

| File | How it's used |
|------|---------------|
| `shared/api_server.py` | systemd service (`rag-api-local.service`), port 8100; handles embedding in-process (fastembed + NomicEmbedText-v1.5, 768d) |
| `shared/provenance.py` | imported by api_server |
| `shared/_log.py` | imported by pipeline scripts |
| `shared/batch_ingest_branch.py` | `pipeline_a/run_pipeline_a.sh` |
| `pipeline_c/batch_image_describe.py` | `pipeline_c/run_pipeline_c.sh` |
| `pipeline_d/pipeline_d.py` | `pipeline_d/run_pipeline_d.sh` |
| `pipeline_d/chunk_document.py` | imported by pipeline_d.py |
| `pipeline_d/embed_docs_cpu.py` | Jill NFS call |
| `pipeline_e/update_registry.py` | `pipeline_e/run_pipeline_e.sh` |
| `pipeline_f/emit_all.py` | `pipeline_f/run_pipeline_f.sh` |
| `pipeline_f/sync_block_metadata_to_registry.py` | pipeline_f flow |
| `pipeline_f/sync_merkle_cap_to_registry.py` | pipeline_f flow |
| `pipeline_f/backfill_block_numbers.py` | pipeline_f flow |
| `pipeline_f/emit_remaining.py` | pipeline_f flow |
| `pipeline_f/run_remaining.py` | pipeline_f flow |
| `pipeline_f/retro_lookup_tx.py` | pipeline_f flow |
| `pipeline_g/pipeline_g.py` | Jill calls it |
| `pipeline_a/pdf_processing.py` | imported by chunk_document.py |
| `zk-circuit/` | used for ZK proof testing |

## Archived Files

All archived to `./data/archive/2026-04-25-orphans/` (not in git).

### Pipeline A
- `pipeline_a/ingest_pdf.py` — superseded by `batch_ingest_branch.py`
- `pipeline_a/pdf_eval.py` — orphaned eval script

### Pipeline B
- `pipeline_b/batch_parallel_retry.py` — pipeline_b is dead

### Pipeline C (all superseded by `batch_image_describe.py`)
- `pipeline_c/describe_image_pages.py`
- `pipeline_c/extract_images.py`
- `pipeline_c/reingest_vision.py`

### Pipeline D (standalone ops tools, not called by pipeline)
- `pipeline_d/chunk_docs.py` — superseded by `chunk_document.py`
- `pipeline_d/chunk_with_vision.py` — superseded by `chunk_document.py`
- `pipeline_d/sync_registry_embeddings.py` — ops tool, can re-enable if needed
- `pipeline_d/sync_registry_emission.py` — ops tool, can re-enable if needed
- `pipeline_d/sync_registry_from_chunks.py` — ops tool, can re-enable if needed
- `pipeline_d/sync_registry_merkleTrees.py` — ops tool, can re-enable if needed

### Shared
- `shared/bm25.py` — BM25 fully disabled, removed from api_server and pipeline_g
- `shared/embedding.py` — superseded by `embedding_service.py`
- `shared/phase_l.py` — one-off experiment
- `shared/build_new_registry.py` — one-off experiment
- `shared/deduplicate_uploads.py` — one-off experiment
- `shared/cleanup_filtered_pages.py` — one-off experiment
- `shared/catalog_generator.py` — one-off experiment

### Root level
- `update_all_trees.py` — orphaned
- `run_pipeline_e.py` — orphaned wrapper, `run_pipeline_e.sh` is the live entrypoint

### Archived package
- `shared/archive_api_server_package/` — consolidated away; canonical is `shared/api_server.py`

## Pipeline Runners (shell scripts)

| Script | Status |
|--------|--------|
| `pipeline_a/run_pipeline_a.sh` | LIVE — calls `batch_ingest_branch.py` |
| `pipeline_c/run_pipeline_c.sh` | LIVE — calls `batch_image_describe.py` |
| `pipeline_d/run_pipeline_d.sh` | LIVE — calls `pipeline_d.py` |
| `pipeline_e/run_pipeline_e.sh` | LIVE |
| `pipeline_f/run_pipeline_f.sh` | LIVE — calls `emit_all.py` |
| `pipeline_g/run_pipeline_g.sh` | LIVE — calls `pipeline_g.py` |
| `pipeline_b/run_pipeline_b.sh` | DEAD — pipeline_b unused |

## Services (systemd)

| Service | Unit file | Port |
|---------|-----------|------|
| RAG API server | `rag-api-local.service` | 8100 |
| Qdrant | `qdrant.service` | 6333 |

**Note:** The standalone `embedding-service.service` (port 8200) was stopped and disabled 2026-05-12. Embedding is now handled in-process by `api_server.py` via fastembed + NomicEmbedText-v1.5 (768d).

## API Extensions (docsearch provenance — added 2026-05-26)

The following API features support doc-scoped provenance search:

- `GET /api/context?doc_id=…&chunk_index=…&limit=N` — returns N consecutive chunks; enables multi-chunk initial display in docsearch.html
- `POST /api/query-provable` — accepts optional `doc_id` field to scope provenance search to a single document
- `GET /api/catalog?collection=…` — returns document manifest with titles, branches, and page counts

## Key Paths

- Registry: `./data/registry.json`
- Merkle trees: `./data/merkleTrees/`
- Chunks: `./data/chunks/`
- Embeddings: `./data/embeddings/`
- Source PDFs: `./data/sourcePDF/`
- Archive: `./data/archive/`
- Qdrant: `./data/qdrant/`
