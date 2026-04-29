# ZK-RAG v2 Consolidation Plan

**Date**: 2026-04-15
**Completed**: 2026-04-15
**Target**: `$REPO_DIR/`
**Status**: COMPLETE

---

## Guiding Principle

One project. One directory. All code, scripts, PRDs, and docs in one place. Original files MOVED to `$ZK_RAG_HOME/archive/zk-rag-v2-archive/` — nothing deleted.

---

## Directory Structure

```
$REPO_DIR/
│
├── pipeline_a/                        ← Pipeline A: ingest
│   ├── run_pipeline_a.sh
│   ├── ingest.sh
│   ├── ingest_pdf.py
│   ├── harvest.sh
│   ├── pdf_processing.py
│   ├── pdf_eval.py
│   └── prd/                          ← no PRD (skip)
│
├── pipeline_b/                        ← Pipeline B: docling loop
│   ├── run_pipeline_b.sh
│   ├── batch_parallel_retry.py
│   └── prd/                          ← no PRD (skip)
│
├── pipeline_c/                        ← Pipeline C: SmolVLM2 vision
│   ├── run_pipeline_c.sh
│   ├── describe_image_pages.py
│   ├── extract_images.py
│   ├── batch_image_describe.py
│   ├── reingest_vision.py
│   └── prd/                          ← no PRD (skip)
│
├── pipeline_d/                        ← Pipeline D: chunking
│   ├── run_pipeline_d.sh
│   ├── pipeline_d.py
│   ├── chunk_document.py
│   └── prd/
│       └── PRD-MIL-01-pipeline-d-chunking.md
│
├── pipeline_e/                        ← Pipeline E: Merkle tree build
│   ├── run_pipeline_e.sh             ← calls Rust binary build_merkle_trees
│   └── prd/
│       ├── PRD-MIL-02-pipeline-e-merkle-tree.md
│       └── PROJ-military-rag-verification.md
│
├── pipeline_f/                        ← Pipeline F: EVM emit
│   ├── AppendRoot.s.sol
│   ├── Deploy.s.sol
│   ├── emit_all.py
│   ├── foundry.toml
│   └── prd/
│       ├── PRD-MIL-03-pipeline-g-evm-emit.md
│       └── PROJ-military-rag-verification.md
│
├── pipeline_g/                        ← Pipeline G: Qdrant upsert
│   └── prd/
│       └── PRD-MIL-04-pipeline-g-qdrant-upsert.md
│   Note: script not yet built.
│
├── zk-circuit/                        ← ZK circuit Rust project
│   ├── src/
│   │   ├── circuits/merkle.rs
│   │   ├── circuits/zk_rag.rs
│   │   ├── circuits/mod.rs
│   │   ├── corpus.rs
│   │   ├── prove.rs
│   │   ├── verify.rs
│   │   ├── witness.rs
│   │   ├── lib.rs
│   │   └── bin/build_merkle_trees.rs
│   ├── tests/
│   │   ├── test_e2e_synthetic.rs
│   │   └── test_zk_rag_circuit.rs
│   ├── contracts/
│   │   └── MerkleRootRegistry.sol
│   ├── configs/
│   │   └── horizen_evm.json
│   ├── prd/
│   │   ├── PRD-zk-circuit-01-merkle-corpus.md
│   │   ├── PRD-zk-circuit-02-zk-rag-circuit.md
│   │   ├── PRD-zk-circuit-03-rag-embedding-integration.md
│   │   ├── PRD-zk-circuit-04-llm-generation-integration.md
│   │   ├── PRD-zk-circuit-06-kurier-verification.md
│   │   ├── PRD-zk-circuit-07-proof-aggregation.md
│   │   └── PRD-zk-circuit-08-e2e-integration-testing.md
│   ├── critique.md
│   ├── report.md
│   └── PROJ-zk-circuit.md
│
├── shared/                           ← Scripts used by multiple pipelines
│   ├── api_server.py
│   ├── build_new_registry.py
│   ├── catalog_generator.py
│   ├── verify_ingest.py
│   ├── test_query.py
│   ├── test_pending_check.py
│   ├── test_offline_ingest.py
│   ├── batch_ingest_branch.py
│   ├── deduplicate_uploads.py
│   ├── cleanup_filtered_pages.py
│   ├── rollback_on_failure.sh
│   ├── rag_healthcheck.sh
│   ├── push_to_vps.sh
│   ├── verify_push.sh
│   ├── daily_git_push.sh
│   ├── test_amcp.py
│   ├── _log.py
│   ├── tests_api/                   ← API test suite
│   │   ├── test_api_context.py
│   │   ├── test_api_images.py
│   │   └── test_verify_ingest.py
│   └── [cron runners:]
│       ├── run_geoip_refresh.sh
│       ├── run_image_extraction.sh
│       ├── run_key_reminder.sh
│       ├── run_push.sh
│       ├── run_session_cleanup.sh
│       ├── run_ssl_check.sh
│       ├── run_usage_report.sh
│       └── run_vps_health.sh
│
└── docs/                             ← Admin + project docs
    ├── admin.md
    ├── docs-admin.md
    ├── PROJ-rag-rebuild-v5.md
    ├── PROJ-rag-pipeline-fix.md
    └── README.md
```

---

## Archived Originals

Original files moved to: `$ZK_RAG_HOME/archive/zk-rag-v2-archive/`

```
zk-rag-v2-archive/
├── original_scripts/             ← all scripts from $REPO_DIR/scripts/
├── original_rag/                ← admin docs from $REPO_DIR/
├── original_zk_rag_project/      ← original ZK-RAG git repo (before rename to zk-circuit)
├── original_archives/            ← historical scripts from $REPO_DIR/scripts/archive/
├── 20260331/                    ← timestamped historical archive
├── 20260401-restructuring/      ← timestamped historical archive
├── dedup_collection.py
├── mil-docs-pipelines/           ← Ralph's pipeline docs (wrong-location copy, moved here)
├── circuits                     ← broken partial copy (from failed cp, moved to archive)
└── bin                          ← broken partial copy (from failed cp, moved to archive)
```

---

## What's Excluded

### From ZK circuit (not source code, not portable)
- `tests/fork/`, `tests/test/` — Solidity tests
- `cache/`, `out/`, `target/`, `lib/`, `.slithervenv/` — build artifacts and git submodules

### Data (server-specific, not portable)
- `/data/rag/merkle_trees/`, `/data/rag/chunks/`, `/data/rag/qdrant_data/`, `/data/rag/race/`

### Ralph's workspace (development only — leave in place)
- `.ralph.lock`, `progress.txt`, `prd.json` — Ralph's agent state
- Ralph's `zk-rag-proofs/src/` — plonky2 exploration, not canonical

---

## Ralph's Active Work — Status

1. **emit_all.py** — COMPLETED and already in `pipeline_f/emit_all.py`
2. **chunk_document.py** — consolidated version is in `pipeline_d/chunk_document.py`. Ralph's workspace version may have improvements — compare and update if better.
3. **Pipeline PRDs** (MIL-01, 04) — already in `pipeline_d/prd/` and `pipeline_g/prd/`

---

## Git

Repository: `git@github.com:youruser1/document-rag-with-zk.git`
Branch: `main`
Status: Pushed and tracking origin.

---

## Production Notes

- **Production directory**: `$REPO_DIR/` — all code, PRDs, docs, tests git-tracked
- **Logs directory**: `/data/logs/` — separate from production
- **Rust binary name**: Cargo.toml `name = "zk-rag"` (unchanged — binary still builds as `zk-rag`)
- **Circuit IDs**: onchain IDs like `zk-rag-v1` remain unchanged

---

## This Plan's Location

`$REPO_DIR/PROJ-zk-rag-v2-consolidation.md`
