# Production Sync Plan — R730 → VPS (Weekly)

## Architecture

**R730 (dev/test):** everything running here. Source of truth.
**VPS (prod):** read-only mirror. No pipelines, no embedding service, no dev code.

## What Runs on VPS (prod)
| Component | Detail |
|-----------|--------|
| Qdrant | Standalone service, port 6333, data at `/data/rag/qdrant_data/` |
| RAG API | `rag-api.service` on port 8100, embedding via `http://127.0.0.1:8200` |
| OpenResty | Port 80/443, serves `/data/rag/docs/` as static files |
| Static files | Website HTML/JS/CSS/registry at `/data/rag/docs/` |

## What Runs on R730 (NOT synced to VPS)
- Pipeline B/C/D/E/F/G (ingestion pipelines)
- Embedding service (port 8200) — R730-specific
- Local Qdrant (port 6333) — dev data only
- `bm25_index.pkl`, `chunks/`, `embeddings/`, `extracted/`, `merkleTrees/`, `zk_proofs/`
- All archive/, wiki/, sourcePDF/

## Sync Phases

### Phase 1 — One-time VPS setup (first run only)
1. Install Qdrant standalone on VPS
2. Create systemd service for Qdrant
3. Create `/data/rag/` directories
4. Configure `rag-api.service` to match R730

### Phase 2 — Static files sync (website)
**Source:** `./website/` (HTML, JS, CSS, registry.json)
**Dest:** `/data/rag/docs/`

Rsync is safe to run live — OpenResty serves these files statically with caching.

### Phase 3 — Qdrant data sync
**Source:** `./data/qdrant/` (collection `army`)
**Dest:** `/data/rag/qdrant_data/`

**Steps:**
1. Stop `rag-api.service` on VPS (Qdrant stays UP — serves via network)
2. Rsync Qdrant data dir to staging dir
3. Swap staging → live
4. Restart `rag-api.service`
5. Health check

**Downtime:** ~2-3 min (Qdrant data is ~1.3GB on VPS, likely smaller on R730)

### Phase 4 — API server + venv sync
**Source:** `./shared/` (api_server.py + venv)
**Dest:** `/home/deruyter/rag/`

**Steps:**
1. Stop `rag-api.service`
2. Rsync venv + api_server.py to staging
3. Swap staging → live
4. Restart `rag-api.service`

### Phase 5 — Rollback
If health check fails: swap back `_old` dirs, restart services.

## Script: `sync_to_vps.sh`
Located at `./shared/sync_to_vps.sh`

Usage:
```bash
./shared/sync_to_vps.sh          # full sync
./shared/sync_to_vps.sh --phase 2  # static files only
./shared/sync_to_vps.sh --phase 3  # qdrant only
./shared/sync_to_vps.sh --dry-run  # show what would happen
./shared/sync_to_vps.sh --abort    # rollback to pre-sync state
```

## Weekly Workflow
1. Run full ingest/pipeline on R730 until satisfied
2. `./sync_to_vps.sh --dry-run` — verify what will be synced
3. `./sync_to_vps.sh` — execute full sync
4. Verify: `curl https://militarymanuals.ai/api/manifest`
5. Done
