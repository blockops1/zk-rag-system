# RAG Pipeline Fix Plan
**Date:** 2026-03-31
**Last Updated:** 2026-03-31 (post-session)
**Status:** Complete — all phases done
**Goal:** Fix dedup gate, fix registry update loop, consolidate scripts, add logging rotation

---

## Progress — What's Been Done

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | Fix dedup gate + add SHA256-based dedup | **Complete** |
| Phase 2 | Registry update after ingest | **Complete** |
| Phase 3 | Consolidate scripts (archive old versions) | **Complete** |
| Phase 4 | Update PROJ doc | **Complete** |
| Phase 5 | Deterministic point IDs (eliminate delete-before-upsert) | **Complete** |
| Phase 6 | Logging rotation (logrotate + cron) | **Complete** |
| Phase 7 | Consolidated two-log design with script prefixes | **Complete** |

---

## Current State

### What's Running
- **RAG API:** Healthy at `http://127.0.0.1:8100` — OpenResty on VPS proxies `/api/` → this server
- **Qdrant:** 391,384 vectors across 4 collections (`army`, `marines`, `coastguard`, `other`)
- **PDFs on disk:** 983 (all tracked in `new-unified-registry-v2.json`)
- **Cron:** `run_pipeline_a.sh` (Pass 1, daily ~3 AM) + `run_pipeline_b.sh` (Pass 2, daily ~5 AM)

### Registry Files

| File | Count | Key | Notes |
|------|-------|-----|-------|
| `new-unified-registry-v2.json` | 983 | doc_id | Primary registry — all PDFs tracked |
| `registry.json` | archived | SHA256 | Old dedup registry → `scripts/archive/20260331/` |
| `unified-registry.json` | archived | list | Old pre-v2 registry → `scripts/archive/20260331/` |

---

## Phase 7: Consolidated Logging — Complete

**Two-log design with script prefixes:**

- `pipeline_main.log` — core pipeline scripts (`batch_ingest_branch.py`, `ingest_pdf.py`)
- `pipeline_harvest.log` — harvesting/enrichment scripts (`check_duplicate.py`, `enrich_*.py`, `run_harvest.py`)
- Format: `[2026-03-31 14:08:52] [__MAIN__    ] Dedup registry: 1 ingested entries`

**Key file:** `$REPO_DIR/scripts/_log.py`
- `get_logger(name, log_group="main"|"harvest")` — returns a `_LogWrapper` supporting both `log.info()` and `log("msg", level=...)` styles
- Script name (`__name__`) used as prefix in brackets
- Stdout output unchanged — scripts still print to console

**Updated call sites (6 scripts):**
- `batch_ingest_branch.py` → `log_group="main"`
- `ingest_pdf.py` → `log_group="main"`
- `run_harvest.py` → `log_group="harvest"`
- `check_duplicate.py` → `log_group="harvest"`
- `enrich_manual_docs.py` → `log_group="harvest"`
- `enrich_registry.py` → `log_group="harvest"`
- `scan_duplicates.py` → `log_group="harvest"`

**Logrotate:** `$REPO_DIR/logrotate.conf` — daily rotation, 14-day retention, covers all pipeline logs including `pipeline_main.log` and `pipeline_harvest.log`. Cron runs at 2 AM daily.

**Note:** `api_server.py` uses `print()` directly (FastAPI/uvicorn service) — excluded from consolidated logging. Its stdout goes to systemd journal.

---

## Phase 6: Logging Rotation — Complete

**Problem:** 74 log files across the pipeline with no rotation — unbounded disk growth.

**What was done:**
1. Created user-space logrotate config: `$REPO_DIR/logrotate.conf`
   - Covers ALL `*.log` files in `$REPO_DIR/logs/` and `/data/rag/`
   - Daily rotation, 14-day retention, compress enabled, delaycompress
   - User-space state file: `$REPO_DIR/logrotate.state`
2. Created cron script: `$REPO_DIR/logrotate_cron.sh`
3. Added to crontab: `0 2 * * * $REPO_DIR/logrotate_cron.sh` (daily 2 AM)
4. Forced test rotation — 74 log files rotated with `-YYYYMMDD` date suffixes

**Files created:**
- `$REPO_DIR/logrotate.conf` — logrotate config
- `$REPO_DIR/logrotate.state` — user-space state file
- `$REPO_DIR/logrotate_cron.sh` — cron runner script

---

## Phase 3: Consolidate Scripts — Complete

### Archive Contents (`scripts/archive/20260331/`)
```
batch_docling.py
batch_ingest.py
dedup_collection.py
ingest_batch.py
registry.json               (old SHA256-keyed registry)
unified-registry.json       (old pre-v2 registry)
workspace_copies/           (14 stale workspace scripts from openclaw workspace)
```

### What's Kept

```
scripts/
  api_server.py              — FastAPI RAG server (production)
  batch_ingest_branch.py     — Primary orchestrator (Pass 1 + Pass 2)
  ingest_pdf.py              — PDF text extraction
  chunk_document.py          — Chunking
  embed_chunks.py            — Embedding
  build_index.py            — Qdrant + BM25 build
  catalog_generator.py       — HTML catalog
  _log.py                   — Consolidated logging utility

harvester/
  check_duplicate.py        — SHA256 + title dedup
  enrich_manual_docs.py      — Manual enrichment
  enrich_registry.py        — Registry enrichment
  run_harvest.py            — Harvest orchestrator
  scan_duplicates.py        — Duplicate scanner
```

### Legacy Fallback Removed
- `load_legacy_registry()` function removed from `batch_ingest_branch.py`
- `LEGACY_REG` constant removed
- `legacy_lookup` variable removed
- `get_metadata()` simplified — v2 registry only
- Verified: 6 filenames only in legacy registry — all already in v2 registry

---

## Phase 2: Registry Update After Ingest — Complete

**What was fixed:**
- `write_v2_registry_update()` in `api_server.py` writes back to `new-unified-registry-v2.json` on successful ingest
- Both `/ingest` and `/reindex` endpoints call it with appropriate fields
- Case-insensitive doc_id lookup fixed (v2 registry has mixed-case, pipeline normalizes to lowercase)

**Fields written on ingest:**
- `status: "ingested"`
- `chunk_count: <N>`
- `page_count: <N>`
- `ingested_at: <ISO timestamp>`

**Fields written on reindex:**
- Same as above, plus `ocr_used: true`

---

## Phase 1: Dedup Gate + SHA256 — Complete

**Two-layer dedup gate in `batch_ingest_branch.py`:**

**Layer 1 — SHA256 exact match (checked first):**
- Computes SHA256 of candidate PDF at ingest time
- Looks up in `sha256_lookup` dict built from v2 registry (751 of 983 entries have SHA256)
- If found → same file, different name → skip immediately

**Layer 2 — Title + pub_year similarity (checked second):**
- Jaccard word-set similarity on normalized titles
- Threshold: 0.75 — if score >= threshold AND pub_year matches → likely duplicate

**Verified working:** `army/adp-1-the-army-05e982bb.pdf` correctly matched `other/army_adp_1.pdf` via SHA256 and was skipped.

---

## Phase 5: Deterministic Point IDs — Complete

### Problem
`index_to_qdrant()` in `api_server.py` uses `uuid.uuid4()` for every point ID. Qdrant's upsert is upsert-by-ID: since UUID is always new, it always inserts rather than updates. The `/reindex` endpoint works around this with a `delete()` before upsert — but repeated deletes cause Qdrant instability.

### Solution
Replace `uuid.uuid4()` with a SHA256-based point ID derived from `(doc_id):(chunk_id):(first_200_chars_of_text)`. Same chunk always produces the same ID → Qdrant performs a true upsert (update existing) → no delete needed.

### Changes Made

**File:** `$REPO_DIR/scripts/api_server.py`

**1. `index_to_qdrant()` — replaced UUID with deterministic ID:**
```python
# OLD (line ~502):
points.append(PointStruct(
    id=str(uuid.uuid4()),
    vector=emb.tolist(),
    payload=payload
))

# NEW:
chunk_text = payload.get('text', '')[:200]
point_id = hashlib.sha256(
    f"{doc_metadata.get('doc_id', '')}:{chunk_id}:{chunk_text}".encode()
).hexdigest()[:32]
points.append(PointStruct(
    id=point_id,
    vector=emb.tolist(),
    payload=payload
))
```

**2. `/reindex` endpoint — delete block removed:**
The `delete()` call before upsert was removed. The per-doc reindex lock file mechanism is retained (prevents concurrent reindex for the same doc).

**3. `scripts_v2/embed_and_index.py`** — was already removed (directory empty except `__pycache__`).

**API restarted** with `sudo systemctl restart rag-api.service` — confirmed healthy at `http://127.0.0.1:8100`.

### Cross-File Impact

| File | Impact |
|------|--------|
| `api_server.py` | Primary change — `index_to_qdrant()` + `/reindex` delete removed |
| `batch_ingest_branch.py` | None — only calls `/ingest` |
| `scripts_v2/embed_and_index.py` | Already removed |
| Qdrant data | No migration — existing UUID-based points remain; new ingest/reindex uses deterministic IDs |

### Testing After Phase 5
1. Ingest a test doc via Pipeline A
2. Reindex same doc via Pipeline B — verify vectors are replaced, not duplicated
3. Query Qdrant — confirm point count for test doc is correct (not doubled)
4. Confirm `/reindex` no longer prints "Cleared existing vectors"

---

## Rollback Plan

- Archived scripts in `$REPO_DIR/scripts/archive/20260331/` — restore with `mv`
- `api_server.py` has `.bak` and `.bak-20260323-1825` backups already
- `batch_ingest_branch.py` — single file; canonical version is in `scripts/archive/20260331/` workspace copy if needed
- Registry writes are additive-only (only adds fields, never removes)

---

## What This Does NOT Change

- `batch_ingest_branch.py` pass structure (Pass 1 / Pass 2) — unchanged
- Done log location: `/data/rag/batch_ingest_branch_done.json`
- Needs-docling log: `/data/rag/batch_ingest_branch_needs_docling.json`
- Failed retry log: `/data/rag/ingest_failed_retry.log`
- Telegram notification format
- Cron jobs or their schedules
- Harvester scripts (separate system)
- `scripts_v2/ingest_pdf.py` and `scripts_v2/chunk_document.py` — unchanged, pending future review
