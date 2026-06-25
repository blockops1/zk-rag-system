# VPS Sync Plan — R730 → Production Server
**Status:** CURRENT — reflects embedding-in-process architecture (no embedding-service)

---

## Overview

Weekly workflow: ingest new documents on R730, test, then sync everything to VPS. VPS is a read-only production server — serves clients only, no pipelines ever run there.

**First sync is a full replacement.** Wipe existing VPS data, rebuild from R730 mirrors.

---

## Architecture: R730 vs VPS

Both servers run the **same Python code** (`api_server.py`, `provenance.py`). Paths are driven by environment variables. The only differences are service configuration and data paths.

**Embedding:** In-process in `api_server.py` on both servers. No separate embedding service. Port 8200 is unused.

---

## Directory Structure (VPS after sync)

```
/data/rag/
  docs/               ← website (html, js, css)
  images/             ← document page images (WebP only)
  qdrant_data/       ← Qdrant vector DB (synced from R730 storage/)
  chunks/             ← per-doc chunk JSONs
  embeddings/         ← embedding data
  merkleTrees/       ← Merkle tree JSONs (for proof generation)
  zk_proofs/          ← existing proof JSONs
  zk-circuit/
    target/release/           ← prove-bin + Rust build artifacts
    circuit_depth5-12.bin     ← circuit binaries
  registry.json       ← document registry

/home/deruyter/rag/
  api_server.py       ← synced from R730 shared/ (same binary)
  provenance.py       ← synced from R730 shared/ (same binary)
  venv/              ← Python dependencies
  logs/              ← service log output
  .env               ← secrets (API keys, etc.)
```

---

## What Syncs (R730 → VPS)

| Source | Destination | Notes |
|--------|-------------|-------|
| `./website/` | `/data/rag/docs/` | |
| `./data/images/` | `/data/rag/images/` | WebP only |
| `./data/storage/` | `/data/rag/qdrant_data/` | **Source is `storage/`, NOT `qdrant/`** |
| `./data/registry.json` | `/data/rag/registry.json` | |
| `./data/chunks/` | `/data/rag/chunks/` | |
| `./data/embeddings/` | `/data/rag/embeddings/` | |
| `./data/merkleTrees/` | `/data/rag/merkleTrees/` | |
| `./data/zk_proofs/` | `/data/rag/zk_proofs/` | |
| `./zk-circuit/target/release/` | `/data/rag/zk-circuit/target/release/` | prove-bin + build artifacts |
| `./zk-circuit/circuit_depth*.bin` | `/data/rag/zk-circuit/` | |
| `./shared/*.py` | `/home/deruyter/rag/` | api_server.py, provenance.py, etc. |
| `./venv/` | `/home/deruyter/rag/venv/` | Python deps |

---

## What's Excluded (R730-only, never sync to VPS)

- All pipelines (B, C, D, E, F, G)
- `sourcePDF/`, `failed_pdfs/`, `extracted/`, `wiki/`, `bm25_index.pkl`
- `images_png_archive/` — cold PNG backup, not served
- `qdrant/` directory (empty — Qdrant data lives in `storage/`)
- R730-specific logs, checkpoints, lock files

---

## Sync Sequence (Full one-way: R730 → VPS, full downtime)

**Pre-condition:** VPS is in a scheduled maintenance window. ALL services will be stopped — openresty (website), qdrant, zk-rag-api/rag-api, and embedding-service.

### Phase 0: Stop all R730 services (clean state for sync)

```bash
# Stop R730 API server (systemd service)
sudo systemctl stop zk-rag-api && echo "R730 zk-rag-api stopped"

# Stop R730 Qdrant
sudo systemctl stop qdrant && echo "R730 qdrant stopped"
```

### Phase 1: Stop all VPS services, archive existing VPS files

```bash
# Stop all services (full downtime — website goes down too)
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'sudo systemctl stop zk-rag-api rag-api embedding-service qdrant openresty 2>/dev/null; \
   echo "All VPS services stopped"'

# Archive existing VPS service files before overwriting
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'sudo mkdir -p /home/deruyter/archive/vps-services-$(date +%Y%m%d); \
   sudo cp /etc/systemd/system/rag-api.service /home/deruyter/archive/vps-services-$(date +%Y%m%d)/ 2>/dev/null; \
   sudo cp /etc/systemd/system/embedding-service.service /home/deruyter/archive/vps-services-$(date +%Y%m%d)/ 2>/dev/null; \
   sudo cp /etc/systemd/system/qdrant.service /home/deruyter/archive/vps-services-$(date +%Y%m%d)/ 2>/dev/null; \
   echo "VPS service files archived"'

# Archive existing VPS Python scripts before overwriting
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'sudo mkdir -p /home/deruyter/archive/vps-scripts-$(date +%Y%m%d); \
   sudo cp /home/deruyter/rag/api_server.py /home/deruyter/archive/vps-scripts-$(date +%Y%m%d)/ 2>/dev/null; \
   sudo cp /home/deruyter/rag/provenance.py /home/deruyter/archive/vps-scripts-$(date +%Y%m%d)/ 2>/dev/null; \
   echo "VPS scripts archived"'

# Wipe VPS data dirs
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'sudo rm -rf /data/rag/images/* /data/rag/chunks/* /data/rag/embeddings/* \
                /data/rag/merkleTrees/* /data/rag/zk_proofs/* \
                /data/rag/zk-circuit/target/release/* \
                /data/rag/qdrant_data/storage /data/rag/qdrant_data/raft_state.json \
                /data/rag/zk-circuit/*.bin; \
   echo "VPS data dirs wiped"'
```
### Phase 2: Sync Qdrant storage to VPS

```bash
# Qdrant storage (~1.3GB, 6 collections)
# Source is storage/, NOT qdrant/
rsync -avz --delete \
  -e "ssh -i ./.ssh/id_ed25519" \
  ./data/storage/ \
  deruyter@militarymanuals.ai:/data/rag/qdrant_data/
```

### Phase 3: Sync website (live, no downtime)

```bash
rsync -avz --delete \
  -e "ssh -i ./.ssh/id_ed25519" \
  ./website/ \
  deruyter@militarymanuals.ai:/data/rag/docs/
```

### Phase 4: Sync images (live, no downtime)

```bash
rsync -avz --delete \
  -e "ssh -i ./.ssh/id_ed25519" \
  ./data/images/ \
  deruyter@militarymanuals.ai:/data/rag/images/
```

### Phase 5: Sync chunks, embeddings, merkleTrees, zk_proofs (live)

```bash
rsync -avz --delete \
  -e "ssh -i ./.ssh/id_ed25519" \
  ./data/chunks/ \
  deruyter@militarymanuals.ai:/data/rag/chunks/

rsync -avz --delete \
  -e "ssh -i ./.ssh/id_ed25519" \
  ./data/embeddings/ \
  deruyter@militarymanuals.ai:/data/rag/embeddings/

rsync -avz --delete \
  -e "ssh -i ./.ssh/id_ed25519" \
  ./data/merkleTrees/ \
  deruyter@militarymanuals.ai:/data/rag/merkleTrees/

rsync -avz --delete \
  -e "ssh -i ./.ssh/id_ed25519" \
  ./data/zk_proofs/ \
  deruyter@militarymanuals.ai:/data/rag/zk_proofs/
```

### Phase 6: Sync ZK-circuit (prove-bin + circuit binaries)

```bash
rsync -avz --delete \
  -e "ssh -i ./.ssh/id_ed25519" \
  ./zk-circuit/target/release/ \
  deruyter@militarymanuals.ai:/data/rag/zk-circuit/target/release/

rsync -avz --delete \
  -e "ssh -i ./.ssh/id_ed25519" \
  ./zk-circuit/circuit_depth*.bin \
  deruyter@militarymanuals.ai:/data/rag/zk-circuit/
```

### Phase 7: Sync API code (all *.py from shared/)

```bash
# Archive existing VPS Python scripts before overwriting
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'sudo mkdir -p /home/deruyter/archive/vps-scripts-$(date +%Y%m%d); \
   sudo cp /home/deruyter/rag/api_server.py /home/deruyter/archive/vps-scripts-$(date +%Y%m%d)/ 2>/dev/null; \
   sudo cp /home/deruyter/rag/provenance.py /home/deruyter/archive/vps-scripts-$(date +%Y%m%d)/ 2>/dev/null; \
   echo "VPS scripts archived"'

rsync -avz --delete \
  -e "ssh -i ./.ssh/id_ed25519" \
  ./shared/*.py \
  deruyter@militarymanuals.ai:/home/deruyter/rag/
```

### Phase 8: Sync registry.json

```bash
cat ./data/registry.json | \
  ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'cat > /data/rag/registry.json'
```

### Phase 9: Fix Qdrant config path

Qdrant embeds the storage path in its config. After syncing, fix the path:

```bash
cat > /tmp/qdrant_config.yaml << 'EOF'
storage:
  storage_path: /data/rag/qdrant_data
EOF

rsync -avz -e "ssh -i ./.ssh/id_ed25519" \
  /tmp/qdrant_config.yaml \
  deruyter@militarymanuals.ai:/tmp/qdrant_config.yaml

ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'sudo mkdir -p /data/rag/qdrant_data/config && \
   sudo cp /tmp/qdrant_config.yaml /data/rag/qdrant_data/config/config.yaml'
```

### Phase 10: Sync Python venv

```bash
rsync -avz --delete \
  -e "ssh -i ./.ssh/id_ed25519" \
  ./venv/ \
  deruyter@militarymanuals.ai:/home/deruyter/rag/venv/
```

### Phase 11: Install zk-rag-api.service

```bash
# Archive existing service file before overwriting
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'sudo cp /etc/systemd/system/zk-rag-api.service /home/deruyter/archive/vps-services-$(date +%Y%m%d)/zk-rag-api.service 2>/dev/null; \
   echo "Existing zk-rag-api.service archived"'

scp -i ./.ssh/id_ed25519 \
  ./shared/vps-rag-api.service \
  deruyter@militarymanuals.ai:/tmp/vps-rag-api.service

ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'sudo cp /tmp/vps-rag-api.service /etc/systemd/system/zk-rag-api.service && \
   sudo systemctl daemon-reload'
```

### Phase 12: Install qdrant.service

```bash
# Archive existing service file before overwriting
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'sudo cp /etc/systemd/system/qdrant.service /home/deruyter/archive/vps-services-$(date +%Y%m%d)/qdrant.service 2>/dev/null; \
   echo "Existing qdrant.service archived"'

scp -i ./.ssh/id_ed25519 \
  ./shared/vps-qdrant.service \
  deruyter@militarymanuals.ai:/tmp/vps-qdrant.service

ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'sudo cp /tmp/vps-qdrant.service /etc/systemd/system/qdrant.service && \
   sudo systemctl daemon-reload'
```

### Phase 12.5: Mr. V updates VPS .env file

Mr. V edits `/home/deruyter/rag/.env` on the VPS to add required secrets. At minimum:
- `KURIE_API_KEY=<value>`
- `ADMIN_API_KEY=<value>`
- `ZK_PROOF_PARALLELISM=4`
- `PAID_DOWNLOAD_RECEIVING_ADDRESS=<value>` (x402 not used on VPS but harmless if present)

```bash
# Connect to VPS and edit the .env file
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai 'nano /home/deruyter/rag/.env'
```

### Phase 14: Start VPS services

```bash
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'sudo mkdir -p /home/deruyter/..../data/logs && \
   sudo systemctl start qdrant && sleep 2 && sudo systemctl start zk-rag-api'
```

### Phase 15: Verify

```bash
# VPS Qdrant collections
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'curl -s http://127.0.0.1:6333/collections | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f\"{c[\"name\"]}: {c.get(\"points_count\",0)} pts\") for c in d[\"result\"][\"collections\"]]"'

# VPS API health
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  'curl -s http://127.0.0.1:8100/health'

# Query test
ssh -i ./.ssh/id_ed25519 deruyter@militarymanuals.ai \
  "curl -s -X POST http://127.0.0.1:8100/api/query \
     -H 'Content-Type: application/json' \
     -d '{\"query\":\"infantry tactics\",\"collection\":\"army\",\"top_k\":1}'"

# Public URL test
curl -s https://militarymanuals.ai/api/query \
  -X POST -H "Content-Type: application/json" \
  -d '{"query":"infantry tactics","collection":"army","top_k":1}'
```

### Phase 16: Restart R730 services

```bash
# Restart R730 Qdrant
sudo systemctl start qdrant && echo "R730 qdrant started"

# Restart R730 API server (systemd service)
sudo systemctl start zk-rag-api && echo "R730 zk-rag-api started"

# Verify R730 is back up
sleep 3
curl -s http://127.0.0.1:8100/health && echo ""
curl -s http://127.0.0.1:6333/collections | python3 -c "import sys,json; d=json.load(sys.stdin); print('R730 Qdrant:', len(d['result']['collections']), 'collections')"
```

---

## What to do if verification fails

If any phase fails after cutover, swap the `_old` directory back:

```bash
# Website
sudo mv /data/rag/docs /data/rag/docs_broken
sudo mv /data/rag/docs_old /data/rag/docs

# Images
sudo mv /data/rag/images /data/rag/images_broken
sudo mv /data/rag/images_old /data/rag/images

# Qdrant
sudo systemctl stop zk-rag-api
sudo mv /data/rag/qdrant_data /data/rag/qdrant_data_broken
sudo mv /data/rag/qdrant_data_old /data/rag/qdrant_data
sudo systemctl start zk-rag-api

# API + venv
sudo systemctl stop zk-rag-api
sudo mv /home/deruyter/rag /home/deruyter/rag_broken
sudo mv /home/deruyter/rag_old /home/deruyter/rag
sudo systemctl start zk-rag-api
```

---

## Key Path Differences: R730 → VPS

| Item | R730 | VPS |
|------|------|-----|
| User | `blockops` | `deruyter` |
| Data root | `./data/` | `/data/rag/` |
| Qdrant storage | `./data/storage/` | `/data/rag/qdrant_data/` |
| API code | `./shared/` | `/home/deruyter/rag/` |
| Python venv | `./venv/` | `/home/deruyter/rag/venv/` |
| Website root | `./website/` | `/data/rag/docs/` |
| prove-bin | `.../zk-circuit/target/release/prove-bin` | same relative path |
| Images | `./data/images/` | `/data/rag/images/` |
| Image format | **WebP only** | same after sync |
| API port | 8100 | 8100 |
| Embedding | In-process | In-process (same) |

All other paths (chunks, embeddings, merkleTrees, zk_proofs, registry) are under `/data/rag/` on VPS, mirroring their R730 structure under `./data/`.
