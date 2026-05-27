# Qdrant Storage Location Discovery

## Problem
Qdrant service runs under systemd (`qdrant.service`), but the config file it claims to use does not exist:
```
/etc/systemd/system/qdrant.service:
ExecStart=/usr/local/bin/qdrant --config-path <DATA>qdrant/config/config.yaml
<DATA>qdrant/config/config.yaml  # DOES NOT EXIST
```

Qdrant runs on defaults when config is missing.

## How to Find Actual Storage Location

```bash
# Find the qdrant process PID
ps aux | grep qdrant | grep -v grep
# e.g., PID=2821633

# Find open file handles for REG (regular file) paths
lsof -p <PID> | grep REG | grep -E "vector_storage|payload_storage|storage" | head -10
```

Result: storage lives at `<DATA>storage/` (collections, raft_state, aliases).

## Actual Directory Layout
```
<DATA>storage/
├── aliases/
├── collections/
│   ├── army/0/segments/<segment-uuid>/
│   │   ├── vector_storage/vectors/chunk_*.mmap
│   │   └── payload_storage/page_*.dat
│   └── navy/0/segments/<segment-uuid>/
├── raft_state.json
└── api-server-transfer/
```

## Stop/Start the Service
```bash
sudo systemctl stop qdrant   # wait ~10s (TimeoutStopSec=10)
sudo systemctl start qdrant
```

## Archive Storage Before Reset
```bash
sudo systemctl stop qdrant
mv <DATA>storage <DATA>archive/storage_YYYYMMDD
sudo systemctl start qdrant
# Qdrant auto-creates fresh storage on startup
```

## Qdrant ↔ Registry Sync Issue

Qdrant and the registry can get out of sync. Registry says `status: ingested` but Qdrant is empty (e.g., after a reset). Pipeline G detects this via `check_eligibility()` which calls the Qdrant API to count existing points.

**Dry-run to reveal true state:**
```bash
# This says "already ingested, skipped" — misleading when Qdrant is empty
python3 pipeline_g/pipeline_g.py --batch --dry-run

# This reveals what WOULD be ingested — correct when Qdrant is empty
python3 pipeline_g/pipeline_g.py --batch --reingest --dry-run
```

**`--reingest` flag:** Forces re-ingestion of docs already marked `ingested` in the registry. Required when:
- Qdrant was reset and needs full repopulation
- Payload schema changed and existing Qdrant records need to be overwritten

**Always do a dry run first:**
```bash
python3 pipeline_g/pipeline_g.py --batch --reingest --dry-run
```

**Full reset + repopulate workflow (2026-05-18 verified):**
```bash
# 1. Stop Qdrant
sudo systemctl stop qdrant

# 2. Archive existing storage
mv <DATA>storage <DATA>archive/storage_YYYYMMDD

# 3. Start fresh
sudo systemctl start qdrant

# 4. Verify Qdrant is empty
curl http://localhost:6333/collections | python3 -m json.tool
# → {"result": {"collections": []}, "status": "ok"}

# 5. Repopulate from registry (527 docs)
cd <HOME>/zk-rag-v2
source .env
python3 pipeline_g/pipeline_g.py --batch --reingest
```
