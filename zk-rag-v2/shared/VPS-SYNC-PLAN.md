# VPS Sync Plan — R730 → Production Server
**Status:** FINAL — ready for review

---

## Overview

Weekly workflow: ingest new documents on R730, test, then sync everything to VPS. VPS is a read-only production server — serves clients only, no pipelines ever run there.

**First sync is a full replacement.** Wipe existing VPS data, rebuild from R730 mirrors.

---

## What's Running on R730 (source of truth)

| Service | Port | Binary | Data |
|---------|------|--------|------|
| Qdrant | 6333 | `/usr/local/bin/qdrant` | `$DATA_DIR/qdrant/` |
| Embedding service | 8200 | `python3 $REPO_DIR/shared/embedding_service.py` | None |
| RAG API | 8100 | `python3 $REPO_DIR/shared/api_server.py` | Qdrant at 6333 |

**R730 runs all three as standalone processes (not systemd). The systemd unit files exist in `$REPO_DIR/shared/` but are not installed on R730.**

---

## What's Running on VPS (to be rebuilt)

Three services, identical to R730:

| Service | Port | Binary | Data |
|---------|------|--------|------|
| Qdrant | 6333 | `/usr/local/bin/qdrant` | `/data/rag/qdrant_data/` |
| Embedding service | 8200 | `python3 $ZK_RAG_HOME/rag/embedding_service.py` | None |
| RAG API | 8100 | `python3 $ZK_RAG_HOME/rag/api_server.py` | Qdrant at 6333 |

**nginx on VPS requires a clean config — do NOT use the default.** The VPS was initially deployed with `access_by_lua_block` rate limiting and IP-based auth. Replace it with the proven-clean R730 config pattern before any sync:

1. On R730: read `/etc/nginx/conf.d/military-manuals-local.conf`
2. Copy it to VPS `/tmp/militarymanuals.ai.conf` (no Lua auth blocks)
3. On VPS: back up old config, deploy new config, reload openresty:

```bash
# Back up old config
sudo cp /etc/nginx/conf.d/militarymanuals.ai.conf \
  /etc/nginx/conf.d/militarymanuals.ai.conf.bak-$(date +%Y%m%d%H%M%S)

# Deploy clean config
sudo cp /tmp/militarymanuals.ai.conf /etc/nginx/conf.d/militarymanuals.ai.conf

# Reload (NOPASSWD sudoers configured)
sudo systemctl reload openresty.service
```

The clean config: no auth, no rate limiting, simple `proxy_pass http://127.0.0.1:8100;`, website root at `/data/rag/docs/`.

---

## Directory Structure (VPS after sync)

```
/data/rag/
  docs/           ← website (html, js, css) — mirrors R730 website dir
  images/         ← document images — mirrors R730 $DATA_DIR/images/
  qdrant_data/   ← qdrant vector DB — mirrors R730 $DATA_DIR/qdrant/
  registry.json   ← document registry — mirrors R730 $DATA_DIR/registry.json

$ZK_RAG_HOME/rag/
  api_server.py          ← RAG API server (from R730 shared/)
  embedding_service.py   ← Embedding service (from R730 shared/)
  venv/                  ← Python venv for both services (from R730 $REPO_DIR/venv/)
```

---

## What Syncs (R730 → VPS)

| Source | Destination | Method |
|--------|-------------|--------|
| `$REPO_DIR/website/` | `/data/rag/docs/` | rsync |
| `$DATA_DIR/images/` | `/data/rag/images/` | rsync |
| `$DATA_DIR/qdrant/` | `/data/rag/qdrant_data/` | rsync |
| `$DATA_DIR/registry.json` | `/data/rag/registry.json` | rsync (or cp) |
| `$REPO_DIR/shared/api_server.py` | `$ZK_RAG_HOME/rag/api_server.py` | rsync |
| `$REPO_DIR/shared/embedding_service.py` | `$ZK_RAG_HOME/rag/embedding_service.py` | rsync |
| `$REPO_DIR/venv/` | `$ZK_RAG_HOME/rag/venv/` | rsync |

---

## What's Excluded (R730-only, never sync to VPS)

- All pipelines (B, C, D, E, F, G)
- `bm25_index.pkl`, `chunks/`, `embeddings/`, `extracted/`, `extracted-vision/`, `merkle_trees/`
- `zk_proofs/`, `proofs_run/`, `source_pdfs/`, `wiki/`, `storage/`
- `$REPO_DIR/shared/` venv symlink and other non-service files
- Any R730-specific logs, checkpoints, lock files

---

## Phase 1 — One-time VPS setup (first sync only)

**Execute on VPS before first sync.**

### 1.1 — Wipe existing data
```bash
# Delete all existing VPS data — cannot be undone
sudo rm -rf /data/rag/*
sudo rm -rf $ZK_RAG_HOME/rag/*
sudo mkdir -p /data/rag/docs /data/rag/images /data/rag/qdrant_data
sudo mkdir -p $ZK_RAG_HOME/rag
```

### 1.2 — Install Qdrant binary
Qdrant binary already exists at `/usr/local/bin/qdrant` on R730. Copy it to VPS:
```bash
scp /usr/local/bin/qdrant deruyter@militarymanuals.ai:/usr/local/bin/qdrant
```

### 1.3 — Install Qdrant systemd unit
```bash
scp /etc/systemd/system/qdrant.service deruyter@militarymanuals.ai:/tmp/qdrant.service.vps
# On VPS: edit path to /data/rag/qdrant_data/, install
sudo mv /tmp/qdrant.service.vps /etc/systemd/system/qdrant.service
sudo systemctl daemon-reload
sudo systemctl enable qdrant
sudo systemctl start qdrant
```

**VPS qdrant.service changes from R730 version:**
```diff
- ExecStart=/usr/local/bin/qdrant --config-path $DATA_DIR/qdrant/config/config.yaml
+ ExecStart=/usr/local/bin/qdrant --config-path /data/rag/qdrant_data/config/config.yaml
- WorkingDirectory=$DATA_DIR
+ WorkingDirectory=/data/rag
```

### 1.4 — Install embedding service systemd unit
```bash
scp $REPO_DIR/shared/embedding-service.service deruyter@militarymanuals.ai:/tmp/embedding-service.service.vps
# On VPS: edit paths, install
sudo mv /tmp/embedding-service.service.vps /etc/systemd/system/embedding-service.service
sudo systemctl daemon-reload
sudo systemctl enable embedding-service
sudo systemctl start embedding-service
```

**VPS embedding-service.service changes:**
```diff
- User=youruser
- WorkingDirectory=$REPO_DIR/shared
- ExecStart=$REPO_DIR/venv/bin/python3 $REPO_DIR/shared/embedding_service.py
- StandardOutput=append:$DATA_DIR/logs/embedding_service_stdout.log
- StandardError=append:$DATA_DIR/logs/embedding_service_stderr.log
+ User=deruyter
+ WorkingDirectory=$ZK_RAG_HOME/rag
+ ExecStart=$ZK_RAG_HOME/rag/venv/bin/python3 $ZK_RAG_HOME/rag/embedding_service.py
+ StandardOutput=append:/var/log/embedding-service.log
+ StandardError=append:/var/log/embedding-service.log
```

### 1.5 — Install RAG API systemd unit
```bash
scp $REPO_DIR/shared/zk-rag-api.service deruyter@militarymanuals.ai:/tmp/rag-api.service.vps
# On VPS: edit paths, install
sudo mv /tmp/rag-api.service.vps /etc/systemd/system/rag-api.service
sudo systemctl daemon-reload
sudo systemctl enable rag-api
sudo systemctl start rag-api
```

**VPS rag-api.service changes:**
```diff
- User=youruser
- WorkingDirectory=$REPO_DIR/shared
- ExecStart=$REPO_DIR/venv/bin/python3 $REPO_DIR/shared/api_server.py
- StandardOutput=append:$DATA_DIR/logs/api_server_stdout.log
- StandardError=append:$DATA_DIR/logs/api_server_stderr.log
+ User=deruyter
+ WorkingDirectory=$ZK_RAG_HOME/rag
+ ExecStart=$ZK_RAG_HOME/rag/venv/bin/python3 $ZK_RAG_HOME/rag/api_server.py
+ StandardOutput=append:/var/log/rag-api.log
+ StandardError=append:/var/log/rag-api.log
```

### 1.6 — Verify services
```bash
curl http://127.0.0.1:6333/collections   # Qdrant
curl http://127.0.0.1:8200/health       # Embedding service
curl http://127.0.0.1:8100/health      # RAG API
```

---

## Phase 2 — Static website (live, no downtime)

Rsync to staging, swap, no service restart needed.

```bash
# On R730:
rsync -az --delete \
  -e "ssh -o StrictHostKeyChecking=accept-new" \
  $REPO_DIR/website/ \
  deruyter@militarymanuals.ai:/data/rag/docs_staging/

# On VPS (atomic swap):
sudo mv /data/rag/docs /data/rag/docs_old
sudo mv /data/rag/docs_staging /data/rag/docs
sudo rm -rf /data/rag/docs_old
```

**Verify:** `curl https://militarymanuals.ai/`

---

## Phase 3 — Images (live, no downtime)

Rsync to staging, swap, no service restart needed.

```bash
# On R730:
rsync -az --delete \
  -e "ssh -o StrictHostKeyChecking=accept-new" \
  $DATA_DIR/images/ \
  deruyter@militarymanuals.ai:/data/rag/images_staging/

# On VPS (atomic swap):
sudo mv /data/rag/images /data/rag/images_old
sudo mv /data/rag/images_staging /data/rag/images
sudo rm -rf /data/rag/images_old
```

**Verify:** `curl https://militarymanuals.ai/images/` (any image path)

---

## Phase 4 — Qdrant database (downtime ~2-3 min)

### 4.1 — Stop RAG API (Qdrant stays up as standalone service)
```bash
# On VPS:
sudo systemctl stop rag-api
```

### 4.2 — Rsync Qdrant data to staging
```bash
# On R730:
rsync -az --delete \
  -e "ssh -o StrictHostKeyChecking=accept-new" \
  $DATA_DIR/qdrant/ \
  deruyter@militarymanuals.ai:/data/rag/qdrant_data_staging/
```

### 4.3 — Atomic swap
```bash
# On VPS:
sudo mv /data/rag/qdrant_data /data/rag/qdrant_data_old
sudo mv /data/rag/qdrant_data_staging /data/rag/qdrant_data
sudo rm -rf /data/rag/qdrant_data_old
```

### 4.4 — Restart services
```bash
# On VPS:
sudo systemctl start rag-api
```

### 4.5 — Health check
```bash
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:6333/collections
```

---

## Phase 5 — API server + venv (downtime ~1 min)

### 5.1 — Stop services
```bash
# On VPS:
sudo systemctl stop rag-api embedding-service
```

### 5.2 — Rsync to staging
```bash
# On R730:
rsync -az --delete \
  -e "ssh -o StrictHostKeyChecking=accept-new" \
  $REPO_DIR/shared/api_server.py \
  $REPO_DIR/shared/embedding_service.py \
  deruyter@militarymanuals.ai:$ZK_RAG_HOME/rag_staging/

rsync -az --delete \
  -e "ssh -o StrictHostKeyChecking=accept-new" \
  $REPO_DIR/venv/ \
  deruyter@militarymanuals.ai:$ZK_RAG_HOME/rag_staging/venv/
```

### 5.3 — Atomic swap
```bash
# On VPS:
sudo mv $ZK_RAG_HOME/rag $ZK_RAG_HOME/rag_old
sudo mv $ZK_RAG_HOME/rag_staging $ZK_RAG_HOME/rag
sudo rm -rf $ZK_RAG_HOME/rag_old
```

### 5.4 — Start services
```bash
# On VPS:
sudo systemctl start embedding-service
sudo systemctl start rag-api
```

### 5.5 — Health check
```bash
curl http://127.0.0.1:8200/health
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:6333/collections
```

---

## Rollback

If any phase fails after cutover, swap the `_old` directory back:

```bash
# Website: docs
sudo mv /data/rag/docs /data/rag/docs_broken
sudo mv /data/rag/docs_old /data/rag/docs

# Images: images
sudo mv /data/rag/images /data/rag/images_broken
sudo mv /data/rag/images_old /data/rag/images

# Qdrant: qdrant_data
sudo systemctl stop rag-api
sudo mv /data/rag/qdrant_data /data/rag/qdrant_data_broken
sudo mv /data/rag/qdrant_data_old /data/rag/qdrant_data
sudo systemctl start rag-api

# API + venv: rag
sudo systemctl stop rag-api embedding-service
sudo mv $ZK_RAG_HOME/rag $ZK_RAG_HOME/rag_broken
sudo mv $ZK_RAG_HOME/rag_old $ZK_RAG_HOME/rag
sudo systemctl start embedding-service
sudo systemctl start rag-api
```

---

## Script Interface

```bash
./sync_to_vps.sh              # run phases 2-5 in order
./sync_to_vps.sh --dry-run    # show what would sync, no changes
./sync_to_vps.sh --phase 2    # static website only
./sync_to_vps.sh --phase 3    # images only
./sync_to_vps.sh --phase 4    # qdrant only
./sync_to_vps.sh --phase 5    # api + venv only
./sync_to_vps.sh --rollback   # revert to pre-sync state
```

**Phase 1 (one-time VPS setup) is manual — run commands from this doc directly.**

---

## Key Tweaks for VPS (vs R730)

| Item | R730 | VPS |
|------|------|-----|
| User | `youruser` | `deruyter` |
| Data root | `$DATA_DIR/` | `/data/rag/` |
| Qdrant data | `$DATA_DIR/qdrant/` | `/data/rag/qdrant_data/` |
| Images | `$DATA_DIR/images/` | `/data/rag/images/` |
| Website | `$REPO_DIR/website/` | `/data/rag/docs/` |
| API root | `$REPO_DIR/shared/` | `$ZK_RAG_HOME/rag/` |
| Python venv | `$REPO_DIR/venv/` | `$ZK_RAG_HOME/rag/venv/` |
| Qdrant config path | `$DATA_DIR/qdrant/config/config.yaml` | `/data/rag/qdrant_data/config/config.yaml` |
| Collection | `army` | `army` (same) |
| Embedding model | `Qwen/Qwen3-Embedding-0.6B` | `Qwen/Qwen3-Embedding-0.6B` (same) |

Everything else (ports, collection name, embedding model, nginx config) stays identical.
