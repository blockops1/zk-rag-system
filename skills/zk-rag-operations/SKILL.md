---
name: zk-rag-operations
description: Operational knowledge for ZK-RAG system — Qdrant management, Pipeline G/F execution, and website troubleshooting. Covers storage backends, systemd operation, website bugs, and common failure modes.
category: zk-rag
---

# ZK-RAG Operations

## POST-FIX AUDIT — After Every Website/API Bug Fix

After every website or API bug fix, do ALL three without waiting to be asked:

1. **Update skill files** — record the bug, root cause, fix, and verification in `references/website-troubleshooting.md`
2. **Push git** — commit and push to `origin/main`
3. **Run Playwright tests** — verify the fix works end-to-end in a browser; curl alone is not sufficient for frontend fixes

This is a hard rule, not optional. Mr. V should not have to ask for a follow-up test.

## Critical Facts (always verify before acting)

- **VPS service name:** `zk-rag-api.service` (VPS) / `rag-api-local.service` (R730). Check with `systemctl list-units --type=service --state=running`.
- **FastAPI `/health` route:** Registered at root `/health`, NOT `/api/health`. Nginx catchall sends `/health` → backend `/health` → 404.
- **Public repo:** See `references/public-repo-status.md`.

## Scope Discipline

**Do NOT make arbitrary operational changes to the VPS without explicit user direction.** Status checks are fine. Fixes require approval first. Scope ends where user authorization ends.

## Quick Reference

- **VPS health:** `curl https://<PUBLIC_HOST>/health`
- **VPS API:** `https://<PUBLIC_HOST>/api/`
- **Qdrant dashboard:** `http://localhost:6333/dashboard` (ssh tunnel required)
- **Dependency map:** `docs/dependency-map.md`
- **Pipeline/Qdrant ref:** `references/qdrant-pipeline-ref.md`
- **Website troubleshooting:** `references/website-troubleshooting.md` — doc-scoped search bugs, banner bugs, cache issues
- **VPS vs R730 nginx diff (rate limits, zone definitions):** `references/vps-r730-rate-limit-diff.md`
- **Systemd services:** `zk-rag-api.service` (port 8100) — embedding is now in-process (2026-05-12); `embedding-service.service` (port 8200) was stopped and disabled
- **Image extraction details:** `references/image-extraction.md` — format, inventory, fix for missing WebP dirs
- **Pipeline runners:** `run_pipeline_{a,b,c,d,e,f,g}.sh` in each pipeline directory
- **Venv for pipeline_c:** `<REPO>venv/bin/python3` (not `./venv/bin/python3` from within pipeline_c/ — no venv exists there)
- **Qdrant payload + bugs:** `references/qdrant-payload-structure.md`
- **Qdrant scroll patterns (incl. delete/wipe):** `references/qdrant-scroll-patterns.md`
- **Qdrant rebuild reference (2026-05-22):** `references/qdrant-rebuild-reference.md` — branch inference rules, title prefix rules, directory inventory, collection plan, Pipeline G key facts, common failures
- **Pre-flight checklist:** `references/zk-rag-preflight-checklist.md` — inventory script, missing images fix, branch inference rules, Pipeline G BRANCH_NORMALIZE_RULES bug (missing `air_force`/`joint`)

## Title Prefix Convention

Documents are stored with service-name prefixes on titles:
- Army docs: `US Army ` prefix
- Navy docs: `US Navy ` prefix
- Marine Corps: `US Marine Corps ` prefix
- Air Force: `US Air Force ` prefix
- Joint: `US Joint ` prefix

Apply these when inserting/updating documents via Pipeline G or registry edits.
- **Qdrant rebuild playbook:** `references/qdrant-rebuild-plan.md` — full step-by-step for rebuilding Qdrant from scratch, including branch normalization rules, archive commands, and known bugs
- **`normalize_branch()` in `pipeline_g.py`** — maps registry `branch` values to Qdrant collection names. **MUST include both `"air_force"` (underscore) and `"air force"` (space)** as separate keys, both mapping to `"air_force"`. **MUST include `"joint"`** mapping to `"joint"`. Missing entries silently fall through to `"other"`. Correct rules: `army→army`, `navy→navy`, `marines→marines`, `air_force→air_force`, `air force→air_force`, `joint→joint`, `coast guard→coast_guard`; everything else → `other`.
  - **Bug 2026-05-22:** missing `"air_force"` (underscore) caused 10 air_force docs to land in `other`; missing `"joint"` caused 13 joint docs to land in `other`. Both are now fixed.
- **Qdrant collections (6 total):** `army`, `navy`, `marines`, `air_force`, `joint`, `other`
- **Title review CSV:** `<DATA>title_review_all.csv` — 791 entries, cols: `doc_id, current_title, proposed_title, status, page1_preview`

## Website Troubleshooting

See `references/website-troubleshooting.md` — covers doc-scoped search bugs, banner CSS, and image display issues.

## Systemd Services

### zk-rag-api.service
Manages: `shared/api_server.py` on **port 8100** (NOT 8080 — that port is hijacked by `llama-server`).
```bash
sudo systemctl restart zk-rag-api
sudo systemctl status zk-rag-api
tail -f <DATA>logs/api_server.log
```
**Restart note:** Always run `openclaw doctor` first if the gateway is involved, then get real-time approval before restarting.
**Symptom:** Some documents render with images, others don't.
**Root cause:** Not a code bug — image data coverage is ~523/527 documents. `fetchImageList` handles missing images gracefully (`return []` on API 404). The Convoy Handbook (`1aa38a…`) has no image directory on disk.
**Verification:** `ls <DATA>images/<doc-hash>/`

### API port: 8100, not 8080
`llama-server` occupies 8080. `api_server.py` (FastAPI) runs on port **8100**. Nginx proxies `http://localhost/` → `127.0.0.1:8100`.
**Health:** `curl http://localhost:8100/health`

## Pipeline Dependency Chain

See `references/zk-rag-pipeline-reference.md` for full table, runner scripts, registry structure, Qdrant collections, and common failures.

## Website Troubleshooting

See `references/website-troubleshooting.md` — covers doc-scoped search bugs, banner CSS, and image display issues.

## Systemd Services

### zk-rag-api.service
Manages: `shared/api_server.py` on **port 8100** (NOT 8080 — that port is hijacked by `llama-server`).
```bash
sudo systemctl restart zk-rag-api
sudo systemctl status zk-rag-api
tail -f <DATA>logs/api_server.log
```
**Restart note:** Always run `openclaw doctor` first if the gateway is involved, then get real-time approval before restarting.
**Symptom:** Some documents render with images, others don't.
**Root cause:** Not a code bug — image data coverage is ~523/527 documents. `fetchImageList` handles missing images gracefully (`return []` on API 404). The Convoy Handbook (`1aa38a…`) has no image directory on disk.
**Verification:** `ls <DATA>images/<doc-hash>/`

### API port: 8100, not 8080
`llama-server` occupies 8080. `api_server.py` (FastAPI) runs on port **8100**. Nginx proxies `http://localhost/` → `127.0.0.1:8100`.
**Health:** `curl http://localhost:8100/health`

## Qdrant Storage
See `references/qdrant-pipeline-ref.md` for storage backends, collection info, and operations.

### Config Location
The Qdrant systemd unit specifies `--config-path`. The config file lives alongside the data directory:

Current production setup:
- Config: `<DATA>qdrant/config/config.yaml`
- Data: `<DATA>storage` (LMDB backend — actual storage dir, NOT `qdrant/` subdir)
- Systemd unit: `/etc/systemd/system/qdrant.service`

**Current point counts (2026-04-27):** army:90,250 | navy:57,094 | marines:11,296 | other:13,984 = **172,624 total** (Qwen/Qwen3-Embedding-0.6B, 1024-dim)

### Config broke on 2026-04-24 — Qdrant fell back to /tmp (LESSON LEARNED)

**What happened:** The systemd unit specified `--config-path <DATA>qdrant/config/config.yaml` but the directory didn't exist. Qdrant silently fell back to default storage at `/tmp/qdrant/` (volatile — wiped on reboot). All 16 docs marked "ingested" in registry had NO data in Qdrant.

**Symptoms:**
- `curl http://127.0.0.1:6333/collections` → `{"collections":[]}` (empty)
- `ls <DATA>qdrant/` → "No such file or directory"
- Process running with `--config-path <DATA>qdrant/config/config.yaml` (non-existent path)
- Qdrant falls back to `/tmp/qdrant/` when config is missing

**Fix:**
```bash
# 1. Create the config directory
sudo mkdir -p <DATA>qdrant/config
sudo chown -R <USER>:<USER> <DATA>qdrant

# 2. Write config
cat > <DATA>qdrant/config/config.yaml << 'EOF'
storage:
  storage_path: <DATA>qdrant
EOF

# 3. Restart (always via systemd)
sudo systemctl restart qdrant

# 4. Verify
curl http://127.0.0.1:6333/collections
ls -la <DATA>qdrant/  # should show storage/ database/ collections/ aliases/
```

**Key insight:** When Qdrant's config path doesn't exist, it does NOT fail — it silently uses defaults. Check `/tmp/qdrant/` if you suspect this. The process will bind to port 6333 normally but store data in `/tmp`.

### Archive /data/rag/ — stale staging directory

`/data/rag/` was the old staging/proving-ground directory (archived 2026-04-24 to `/data/archive/rag_OLD_20260424/`). It contained stale Qdrant config, old ingestion state files, and obsolete batch scripts. **Do not use this directory.** Production data lives in `<DATA>`.

### CRITICAL: KURIE_API_KEY — Mr. V manages this himself

**The Kurier/zkVerify API key lives in `.env.systemd`, NOT `.env`.**
- Path: `<REPO>.env.systemd`
- Variable: `KURIE_API_KEY`
- The API server reads it via `EnvironmentFile=` in the systemd unit — **do NOT modify this file**
- If Kurier submissions return `401 Unauthorized` in `provenance.log`, the key has expired or been revoked — Mr. V updates it
- After key update: restart the API server via systemd:
  ```bash
  sudo systemctl restart zk-rag-api
  ```
- Verify: `curl -s "https://api.kurier.xyz/api/v1/job-status/${KURIE_API_KEY}/test"` — "Invalid jobId format" (not 401) = key is valid

## Critical Rules & Secrets

- **ALWAYS use systemd** for Qdrant and the API server — never from the command line
- **NEVER modify `.env.systemd`** — it contains KURIE_API_KEY and is managed by Mr. V
- **DEPLOYER_KEY** requires `0x` prefix in env var value

## Detailed Reference Material

**Extended Qdrant troubleshooting, Pipeline G batch-mode eligibility, Kurier/zkVerify provenance debugging:**
See `references/zk-rag-common-issues.md`

## Pipeline G Batch Mode

Pipeline G `--batch` mode eligibility checks `emitted_testnet` OR `emitted_mainnet`. A doc that has a valid mainnet emission but a failed testnet emission IS eligible — the two networks are checked independently.

**Eligibility logic (pipeline_g.py `is_doc_eligible()`):**
```python
emitted_testnet = registry_entry.get("emitted_testnet", {})
emitted_mainnet = registry_entry.get("emitted_mainnet", {})
testnet_ok = emitted_testnet.get("status") == "emitted"
mainnet_ok = emitted_mainnet.get("status") == "emitted"
if not testnet_ok and not mainnet_ok:
    return {"eligible": False}  # genuinely not emitted anywhere
# Otherwise eligible — accepts either network
```

**If Qdrant is wiped but registry still has emission records:**
- Reset ALL docs with `status` set (not just `ingested`) back to `None`:
```python
import json
with open("<DATA>registry.json") as f:
    reg = json.load(f)
reset = [d for d in reg["documents"] if d.get("status")]
for d in reset:
    d["status"] = None
    json.dump(reg, f, indent=2)
print(f"Reset {len(reset)} docs")
```
- This handles docs with `status=ingested`, `status=failed`, `status=skipped`, etc.
- Do NOT re-run Pipeline F — emission records (`emitted_mainnet`) are still valid
- Re-run Pipeline G — it will re-ingest all eligible docs

**Dry run before any real run:**
```bash
cd <REPO>pipeline_g
python3 pipeline_g.py --batch --dry-run
# Check "Eligible docs: N" and verify N makes sense (604 total - genuinely non-emitted docs)
```

**Run for real:**
```bash
cd <REPO>pipeline_g
python3 pipeline_g.py --batch  # no --dry-run
```

## Common Issues

### Qdrant API returns 0 collections but meta.json shows collection definitions
**Symptom:** `curl http://127.0.0.1:6333/collections` returns `{"collections":[]}` but `<DATA>qdrant/meta.json` shows army, navy, marines, other with vector configs.

**Possible causes:**
1. **portalocker conflict** — a local-mode client held the lock during your API call; retry
2. **Wrong Qdrant process** — check `ps aux | grep qdrant` and `ss -tlnp | grep 6333` to confirm which PID owns the port; a different Qdrant process may be serving on 6333 while this storage belongs to another instance
3. **Storage backend mismatch** — data was written by a SQLite-configured Qdrant but current config uses LMDB (or vice versa)
4. **Collections defined but never upserted** — `meta.json` records collection *definitions* (vector size, distance), not actual points. If Pipeline G never ran successfully, collections exist as empty shells with 0 points

**To check actual point counts (server must be running — use network mode):**
```python
from qdrant_client import QdrantClient
client = QdrantClient(url='http://127.0.0.1:6333')  # network mode while server runs
for name in ['army', 'navy', 'marines', 'other']:
    try:
        info = client.get_collection(name)
        print(f'{name}: {info.points_count} points')
    except Exception as e:
        print(f'{name}: error — {e}')
```

### Qdrant Delete — Correct API Usage

**⚠️ SAFETY: Always confirm with user before deleting**
Multiple versions of the same document can exist (e.g., FM 5-34 has 1976, 1999, and 1962 versions — three different `doc_id` values). When asked to delete a document:
1. First query Qdrant to identify ALL matching doc_ids (by title or other metadata)
2. Report back: "Found X versions: [list of titles + doc_id prefixes]. Delete which ones?"
3. Only delete after explicit confirmation specifying WHICH doc_ids

Deleting without confirmation risks removing all versions when user only wanted one.

**Delete all chunks for a specific doc_id (all collections):**
```python
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny

client = QdrantClient(url='http://127.0.0.1:6333')

doc_id = 'd1f5100a456129871e5f8dadcc02ccc3a461c834d3c8c4c870070b9189ddc000'
for col in ['army', 'navy', 'marines', 'other']:
    result = client.delete(
        collection_name=col,
        points_selector=Filter(
            must=[FieldCondition(key='doc_id', match=MatchAny(any=[doc_id]))]
        )
    )
    print(f'{col}: {result}')
```

**Delete all chunks for a specific doc_id (single collection):**
```python
client.delete(
    collection_name='army',
    points_selector=Filter(
        must=[FieldCondition(key='doc_id', match=MatchAny(any=[target_doc_id]))]
    )
)
```

**Find all versions of a document across collections (by title keyword):**
```python
from qdrant_client import QdrantClient

client = QdrantClient(url='http://127.0.0.1:6333')

keyword = 'FM 5-34'  # or 'Engineer Field Data'
for col in ['army', 'navy', 'marines', 'other']:
    all_points = []
    offset = None
    while True:
        result = client.scroll(collection_name=col, limit=100, with_payload=True, offset=offset)
        points = result[0]
        if not points:
            break
        all_points.extend(points)
        if len(points) < 100:
            break
        offset = result[1]  # next_page_offset

    found = []
    for r in all_points:
        t = (r.payload.get('title') or '').lower()
        if keyword.lower() in t:
            found.append((r.id, r.payload.get('doc_id'), r.payload.get('title')))
    print(f'{col}: {found}')
```

**Note:** `scroll_offset=` is NOT valid kwarg — use `offset=`. The next-page token is `result[1]`, not a named field.

**Delete points by ID (single point):**
```python
# Correct — points_selector, NOT points
client.delete(collection_name='army', points_selector=[point_id])

# Wrong — this will raise TypeError
client.delete(collection_name='army', points=[point_id])
```

### Qdrant API returns 0 collections but data files exist
Two possible causes:
1. **Wrong Qdrant process** — check `ss -tlnp | grep 6333` to confirm which PID owns the port
2. **Storage backend mismatch** — SQLite files were written by a SQLite-configured Qdrant, but current config uses LMDB (or vice versa). The API can't read data from the wrong backend.

Fix: identify which backend wrote the data, configure matching backend, OR wipe and rebuild.

### Pipeline G reports success but API shows nothing
- Check Qdrant process ownership: `ps aux | grep qdrant` and `ss -tlnp | grep 6333`
- Confirm config path matches the running process
- Verify storage backend in config matches what wrote the data
- **Remember:** Pipeline G must use `QdrantClient(url='http://127.0.0.1:6333')` (network mode) when the server is running — local-path mode will throw portalocker errors

### Silent ingest failure: registry says "ingested" but Qdrant is empty
**Root cause:** `QdrantClient(path=...)` local mode throws `portalocker.AlreadyLocked` when the server is running. If the exception is caught or occurs after the registry save step, the registry gets marked `ingested` while Qdrant stays empty.

**Diagnosis:** Compare registry ingested count vs actual Qdrant point counts (use network mode to query):
```python
from qdrant_client import QdrantClient
client = QdrantClient(url='http://127.0.0.1:6333')
for coll in ['army', 'navy', 'marines', 'other']:
    try:
        info = client.get_collection(coll)
        print(f'{coll}: {info.points_count} pts')
    except Exception:
        pass
```

**Recovery:** Reset `status=ingested` docs back to `emitted_testnet`, re-run Pipeline G.

### pipeline_g reads wrong registry field: `merkle_root` vs `tree_root`
**Symptom:** Pipeline G queries return empty `merkle_root` values despite `tree_root` being populated in registry.
**Root cause:** Registry stores `tree_root`, not `merkle_root`. Pipeline G was reading `registry_entry.get("merkle_root", "")`.
**Fix:** Use `registry_entry.get("tree_root", "")` instead.

## Pipeline E — Merkle Tree Builder

**Source:** `<REPO>zk-circuit/pipeline_e/src/main.rs`
**Build:** `cargo build -p pipeline_e` from `zk-circuit/`
**Run (batch):** `cargo run -p pipeline_e -- --batch [--chunks-dir <DATA>chunks] [--out-dir <DATA>merkle_trees]`
> ⚠️ **Caution:** `--batch` processes ALL eligible documents with no tree — can mean hours on large datasets (692 eligible docs observed). Prefer single-doc mode for targeted runs:
> ```bash
> cargo run -p pipeline_e -- --doc-id <doc_id> --chunks-dir <DATA>chunks --out-dir <DATA>merkle_trees
> ```
> Pipeline E has an **existing-file guard** — it skips with `SKIPPED (already exists)` if the tree file is already present. No `--force` needed just to avoid overwrite; use `--force` only when intentionally rebuilding.

### Chunk file structure (important: not flat files)

Chunks live in **subdirectories per doc_id**, NOT flat files:
```
<DATA>chunks/
  {doc_id}/
    chunks.jsonl    ← one JSON per line, one per chunk
    chunk_ids.json  ← array of chunk_id strings
    metadata.json   ← doc metadata
  {doc_id}/
    ...
```

**Do NOT look for `{doc_id}_chunks.json`** — that suffix doesn't exist. The chunk dir name IS the doc_id.

### Filtering chunked docs with non-empty chunks

Before running Pipeline E, filter out docs with empty `chunks.jsonl`:
```python
import os

chunks_dir = "<DATA>chunks"
valid = []
for d in os.listdir(chunks_dir):
    chunk_file = os.path.join(chunks_dir, d, "chunks.jsonl")
    if os.path.exists(chunk_file):
        with open(chunk_file) as f:
            lines = [l for l in f if l.strip()]
        if lines:
            valid.append(d)
print(f"Chunked docs with content: {len(valid)}")
```

### What Pipeline E does

Reads `chunks.jsonl` from `<DATA>chunks/{doc_id}/`, hashes each chunk text using Poseidon (Goldilocks field, NFKC normalization), builds a binary Merkle tree with a **single root**, and writes `{doc_id}_tree.json` to `<DATA>merkle_trees/`.

**Output tree JSON contains:**
- `merkle_root` — single Poseidon root (hex string, e.g. `0x99271cc523478dd4c89ef08cbdc9cfb427d46814fd08f4caf72625122c680af0`)
- `tree_config.depth` — tree depth
- `chunk_count` — number of real text chunks
- `padded_leaf_count` — total padded leaves
- `doc_id_leaf_index` — always 0 (doc_id is leaf[0])
- `leaf_hashes` — all padded leaf hashes
- `paths` — per-chunk Merkle proof (siblings + leaf index), keyed by chunk index string

**Pipeline E does NOT update the registry.** It only writes tree JSON to disk. Use `update_registry.py` (`<REPO>pipeline_e/update_registry.py`) to write tree fields to `registry.json`.

**No existing-file guard:** Pipeline E will overwrite existing tree JSONs without `--force`. It has no check for pre-existing output files — re-running always writes fresh. However, `--force` is the correct flag to use when intentionally rebuilding.

**Empirically verified deterministic:** Re-running Pipeline E on the same document produces an **identical merkle_root** (confirmed with `--force` on `00c8a75d...` — root `0x99271cc523478dd4c89ef08cbdc9cfb427d46814fd08f4caf72625122c680af0` matched exactly before and after).

### Registry structure (IMPORTANT)

Registry is stored at `<DATA>registry.json`. **It is NOT inside the git project repo** (`<REPO>.git`). The directory `<DATA>` has its **own git repo** initialized on 2026-04-23 for checkpointing.

**Registry JSON structure:** `documents` is a **list**, NOT a dict keyed by doc_id. Code that does `reg['documents'].values()` will fail.
```json
{
  "documents": [  // <-- list, NOT keyed by doc_id
    {
      "doc_id": "sha256_hex_64_chars",
      "sha256": "...",      // same as doc_id in current pipeline
      "tree_root": "0x...", // set by update_registry.py after Pipeline E
      ...
    }
  ]
}
```

**Key access patterns:**
```python
# Load
with open("<DATA>registry.json") as f:
    reg = json.load(f)
docs = reg["documents"]          # list
doc_by_id = {d["doc_id"]: d for d in docs}  # dict for fast lookup

# Find by doc_id
for doc in reg["documents"]:
    if doc["doc_id"] == target_id:
        idx = reg["documents"].index(doc)
        break
```

### CRITICAL: Parallel writes corrupt the registry

**NEVER run pipeline_e or update_registry.py with multiple parallel workers on the same registry.json.** The `update_registry.py` script writes a temp file then renames it — with concurrent writers, renames fail with `FileNotFoundError` and the registry becomes corrupted (JSON parse error at line 31547).

**Safe pattern:** Read all trees in parallel, collect results in memory, write registry ONCE at the end:
```python
from concurrent.futures import ThreadPoolExecutor, as_completed

results = {}
with ThreadPoolExecutor(max_workers=4) as ex:
    futures = {ex.submit(build_tree, doc_id): doc_id for doc_id in all_doc_ids}
    for fut in as_completed(futures):
        results[fut.result()["doc_id"]] = fut.result()

# Apply all updates to registry in memory, then write ONCE
for doc in reg["documents"]:
    r = results.get(doc["doc_id"])
    if r and r["ok"]:
        doc["tree_root"] = r["tree"]["merkle_root"]
        # ... other fields

with open(REGISTRY_PATH, "w") as f:
    json.dump(reg, f, indent=2)
```

### Git checkpointing before registry changes

`<DATA>` has its own git repo. Always commit before modifying the registry:

```bash
cd /data/military-documents
git add registry.json
git commit -m "Checkpoint: description of change"
```

If something goes wrong:
```bash
cd /data/military-documents
git log --oneline           # find the good commit
git checkout <commit> -- registry.json  # restore
```

### The 604 production document set

Only 604 of 742 registry docs have both chunks AND embeddings. The other 138 must be removed before production deployment:

| Set | Count | Notes |
|-----|-------|-------|
| Registry docs total | 742 | |
| With chunk dirs | 613 | |
| With embeddings.npy | 604 | |
| **Chunk + Embeddings (∧)** | **604** | **Production target** |
| Chunks only, no embeddings | 9 | Remove |
| Neither | 129 | Remove |

The 604 docs are the intersection: `chunk_doc_ids ∧ emb_doc_ids`. These have chunks, embeddings, and tree JSONs — ready for Pipelines E, F, G.

### Pipeline E shell script — canonical runner

`<REPO>pipeline_e/run_pipeline_e.sh` is the canonical runner for Pipeline E. It:
1. Sources `.env` to get `DEPLOYER_KEY`
**Pipeline E binary path (updated 2026-05-01):** `zk-circuit/target/release/pipeline_e` — release is significantly faster for plonky2 code. Never use the debug binary for production runs.

**PRD corrected (2026-05-01):** `PRD-MIL-02-pipeline-e-merkle-tree.md` originally described BN254 + Poseidon2 — wrong field and hasher. Corrected to Goldilocks field + PoseidonHash. See `references/pipeline-e-*.md` for details.
3. Calls `update_registry.py` to write `tree_root` into `registry.json`

**Usage:**
```bash
# Single doc
./run_pipeline_e.sh --doc-id <doc_id>

# Batch — all chunked docs without trees
./run_pipeline_e.sh --batch
```

**Registry + disk sync invariant:** The registry and `<DATA>merkle_trees/` must agree.
If Pipeline E was run previously without registry update (leaving orphan tree files on disk for docs
NOT marked `has_merkle_tree=True` in registry), archive the orphans before re-running:

```python
import os, json, shutil

trees_dir = "<DATA>merkle_trees"
archive_dir = "/data/archive/merkle_trees_orphan"
os.makedirs(archive_dir, exist_ok=True)

with open("<DATA>registry.json") as f:
    reg = json.load(f)
registry_tree_docs = {d["doc_id"] for d in reg["documents"] if d.get("has_merkle_tree")}

for f in os.listdir(trees_dir):
    doc_id = f.replace("_tree.json", "")
    if doc_id not in registry_tree_docs:
        shutil.move(os.path.join(trees_dir, f), os.path.join(archive_dir, f))

print(f"Archived orphans. Kept {len(registry_tree_docs)} valid trees.")
```

This was confirmed on 2026-04-21: 198 tree files on disk, only 10 matched registry — 188 orphans moved to archive.

### Registry mass-reset: tree fields (2026-04-21)

When the registry shows `has_merkle_tree=true` but no tree JSONs exist on disk (common after data migration or storage wipe):

```python
import json

with open("<DATA>registry.json") as f:
    reg = json.load(f)

reset = []
for doc in reg["documents"]:
    if doc.get("has_merkle_tree") is True:
        doc["has_merkle_tree"] = False
        for field in ["tree_root", "tree_depth", "chunk_count", "padded_leaf_count",
                      "doc_id_leaf_index", "pipeline_e_status", "processed_at"]:
            doc.pop(field, None)
        reset.append(doc["doc_id"])

with open("<DATA>registry.json", "w") as f:
    json.dump(reg, f, indent=2)

print(f"Reset {len(reset)} docs")
```

The 5 archived trees were moved to `/data/archive/merkle_trees/` before reset.

### Registry fields Pipeline E SHOULD write (TODO — not yet implemented)

Pipeline E should update the registry after writing a tree. Fields to populate for each `doc_id`:

| Registry field | Source |
|---|---|
| `tree_root` | `tree.merkle_root` from tree JSON |
| `tree_depth` | `tree.tree_config.depth` |
| `chunk_count` | `tree.chunk_count` |
| `padded_leaf_count` | `tree.padded_leaf_count` |
| `doc_id_leaf_index` | `tree.doc_id_leaf_index` (always 0) |
| `has_merkle_tree` | `true` |
| `pipeline_e_status` | `"complete"` |
| `processed_at` | RFC3339 timestamp |

**Critical:** Registry stores `tree_root`, NOT `merkle_root`. Any code reading this field must use `tree_root`.

**Pipeline A → Pipeline C → D1 → D2 → E → F → G — Data Flow**

```
source_pdfs/{branch}/     (input PDFs)
        ↓ Pipeline A
extracted/{doc_id}/pages/  (page JSONs)
images/{doc_id}/page_XXXX.png  (full-page PNG renders)
        ↓ Pipeline C (SmolVLM2 — figure_only pages only)
extracted/{doc_id}/pages/  (vision_description added to original page JSONs)
        ↓ Pipeline D1 (chunking)
chunks/{doc_id}/chunks.jsonl              → <DATA>chunks/
chunks/{doc_id}/chunk_ids.json            → <DATA>chunks/  (array of chunk_ids)
        ↓ Pipeline D2 (embeddings)
embeddings/{doc_id}/embeddings.npy         → <DATA>embeddings/
        ↓ Pipeline E
merkle_trees/{doc_id}_tree.json           → <DATA>merkle_trees/
        ↓ Pipeline F
(on-chain MerkleRootRegistry emission)
        ↓ Pipeline G
Qdrant (army/navy/marines/other collections)
```

**NOTE:** `extracted-vision/` is NOT part of the pipeline. Pipeline C writes vision descriptions directly back to `extracted/{doc_id}/pages/*.json`. The `extracted-vision/` directory is always empty (2026-05-01).

## Pipeline D1 — Chunking (2026-05-01 rewrite)

**Location:** `<REPO>pipeline_d1/chunk_document.py`

**Algorithm:** Paragraph-aware recursive character splitter (no LlamaIndex dependency).
1. Split on `\n\n` (paragraph boundaries)
2. Then on `\n` (line boundaries)
3. Then on character limit

**⚠️ D1 IS FAST — A IS THE SLOW STEP.** D1 reads Pipeline A's JSON output and splits text into chunks. This takes seconds per document. If D1 appears slow, check Pipeline A (PDF parsing, OCR) — that is the actual bottleneck.

**chunk_id:** SHA256 hex of normalized chunk text (content-addressable).

**Chunk size:** 512 chars. **Overlap:** 10% (51 chars) — adjacent chunks share 51 chars for context continuity.

**Vision handling:** `[VISUAL: description]` prepended inline to page text before chunking. Figure-only pages become part of adjacent text chunks — no standalone visual chunks. If a page has `figure_only=true` and `ocr_chars==0`, it is skipped entirely.

**Output structure per doc:**
```
<DATA>chunks/{doc_id}/
  chunks.jsonl     ← one JSON per line
  chunk_ids.json   ← array of chunk_id strings (for D2 reference)
```

**chunk record fields:**
```json
{
  "chunk_id": "sha256_hex_64_chars",
  "doc_id": "parent_doc_sha256",
  "chunk_index": 0,
  "text": "chunk content...",
  "page_nums": [1, 2],
  "char_count": 512,
  "vision_used": false
}
```

**`vision_used`:** `true` if any source page was `figure_only=true` with a vision description. Propagates from page-level `visual_refs` field.

**PRD note (2026-04-30):** The PRD originally said "keep LlamaIndex chunking." That was WRONG — LlamaIndex not in venv, `mlx_embeddings` Mac-only. D1 uses simple paragraph-aware recursive character split instead. Chunking quality is equivalent for military doctrine text.

**Shell wrapper:** `pipeline_d1/run_pipeline_d1.sh` — processes all docs or single doc-id, lock file, structured logging.

**Type hint note:** Use `Optional[mp.Queue]` from `typing` instead of `mp.Queue | None` — the venv's `multiprocess` package shadows `multiprocessing`, making `mp.Queue` a bound method, not a class.

## Pipeline D2 — Embedding via NomicEmbedText-v1.5 (2026-05-01)

**Location:** `<REPO>pipeline_d2/embed_chunks.py`

**Model:** `nomic-ai/nomic-embed-text-v1.5` via FastEmbed.
- 768 dimensions (NOT 384 as some docs state)
- **Prefix required:** `passage: ` prepended to every chunk text (model requirement — without it vectors are semantically wrong)
- CPU-friendly, no GPU needed
- ~0.52GB model size

**Model cache:** `_get_model()` singleton at module level — model is downloaded once and cached globally. Re-calling `embed_chunks()` does NOT re-download.

**Algorithm:**
1. Read `chunks.jsonl` for doc
2. Prepend `passage: ` to each chunk's text
3. Batch embed via FastEmbed `TextEmbedding.embed()` (batch size: 256)
4. Save `embeddings.npy` (float32, shape `(num_chunks, 768)`) + `meta.json` (`{chunk_count, dim}`)

**Output structure per doc:**
```
<DATA>embeddings/{doc_id}/
  embeddings.npy   ← numpy float32 array
  meta.json        ← {"chunk_count": N, "dim": 768}
```

**Chunk→embedding alignment:** `chunk_ids.json` (D1 output) and `embeddings.npy` are positionally aligned — chunk at index i in `chunk_ids.json` corresponds to row i in `embeddings.npy`.

**Shell wrapper:** `pipeline_d2/run_pipeline_d2.sh` — flock lock, structured JSON logging.

**PRD vs reality:**
- PRD said `NomicEmbedText-v1.5` produces 1024 dims — WRONG, it's 768 dims
- PRD said model name is `NomicEmbedText-v1.5` or `nomic-ai/NomicEmbedText-v1.5` — WRONG, FastEmbed's HF-style identifier is `nomic-ai/nomic-embed-text-v1.5` (lowercase `nomic-embed-text`, not `NomicEmbedText`)
- Verify with: `TextEmbedding.list_supported_models()` filtered for `nomic`
- **Prefix required:** `passage: ` MUST be prepended to every chunk text. Without it, Nomic produces semantically wrong vectors (confirmed: `norm` differs significantly with vs without prefix). Query prefix is `search: `.
- Verify prefix works with:
  ```python
  r1 = list(model.embed(['test']))           # no prefix — high norm
  r2 = list(model.embed(['passage: test'])) # with prefix — different vector
  ```

**Pitfalls discovered (2026-05-01):**
- Model re-downloads on every `TextEmbedding()` instantiation — cache globally with a module-level singleton. `embed_chunks.py` uses `_get_model()` singleton. Always use the cache; instantiating `TextEmbedding()` directly on every call wastes time re-loading from disk.
- The `tee -a` pattern in `run_pipeline_d2.sh` mixes Python `print()` output (plain text) with shell `jq`-formatted JSON log lines in the same `.jsonl` file. This makes the log file non-parseable as JSONL. Python scripts should write their own structured JSON lines (e.g., via `_jlog()` helper) rather than mixing with shell wrapper output.
- **Shell `$EXIT` capture after pipe-tee:** When a shell script does `cmd 2>&1 | tee -a "$LOG"`, `$?` captures `tee`'s exit code (0 or 1), NOT `cmd`'s. Use `${PIPESTATUS[0]}` instead. The correct pattern:
  ```bash
  cmd 2>&1 | tee -a "$STRUCTURED_LOG"
  EXIT=${PIPESTATUS[0]}
  ```
- **Multiline shell Python commands must be self-contained:** If a multiline Python command in a shell script uses `|| echo "?"` at the end, the entire command (including the closing delimiter) must be on the same logical line, or the `||` will bind to the wrong part of the command. Safe pattern:
  ```bash
  # WRONG — syntax error at `exit' token if shell parses the newlines wrong
  TOTAL=$(python3 -c "
      ...multi-line code...
  " 2>/dev/null || echo "?")
  log "INFO" "Done (exit $EXIT)"

  # CORRECT — `||` must be outside the same subshell that python3 runs in
  TOTAL=$(python3 -c "
      ...multi-line code...
  " 2>/dev/null) || TOTAL="?"
  ```
- **Desloppify workspace scan times out:** `desloppify scan --path <HOME>/zk-rag-v2` times out at 60s on the full workspace. Check the state file directly instead:
  ```bash
  python3 -c "
  import json
  with open('<DESLOP>.desloppify/state-javascript.json') as f:
      d = json.load(f)
  issues = [i for i in d.get('issues', []) if 'pipeline_x' in str(i.get('file', ''))]
  print(f'pipeline_x issues: {len(issues)}')
  if issues: print(issues[:3])
  "
  ```

## Lint + Desloppify Workflow (mandatory before commit)

Every pipeline script follows this sequence before committing:

```bash
# 1. ruff — fix auto-fixable issues
ruff check pipeline_x/ --fix

# 2. ruff — full check (fail on errors)
ruff check pipeline_x/

# 3. desloppify — workspace-wide scan times out; check state file directly
python3 -c "
import json
with open('<DESLOP>.desloppify/state-javascript.json') as f:
    data = json.load(f)
issues = [i for i in data.get('issues', [])
          if 'pipeline_d2' in str(i.get('file', ''))]
print(f'pipeline_d2 issues: {len(issues)}')
"

# 4. If desloppify shows issues in your file:
desloppify fix pipeline_x/script.py
```

**Always run ruff before desloppify.** Desloppify doesn't fix ruff errors — it catches different issue classes (security heuristics, antipatterns, complexity). Both must pass clean.

**Unicode whitespace:** `write_file` tool can inject non-breaking spaces (`\u00a0`) causing `TypeError` on import. Always run `ruff check <file.py> --fix` after writing Python files to strip invisible Unicode.

**Known cross-pipeline path inconsistencies:**

| Pipeline | File | Wrong Path | Correct Path |
|----------|------|-----------|-------------|
| D | `pipeline_d.py` lines 34-35 | `CHUNKS_DIR = /data/rag/chunks` | `<DATA>chunks` |
| D | `pipeline_d.py` line 35 | `EMBEDDINGS_DIR = /data/rag/embeddings` | `<DATA>embeddings` |
| B | `batch_ingest_branch.py` line 65 | `EXTRACTION_QUEUE = /data/rag/extraction_queue.json` | `<DATA>extraction_queue.json` |

**Pipeline A → Pipeline C image filename contract (CRITICAL):**

Pipeline A and Pipeline C have an implicit filename contract. When Pipeline A's output format changes, Pipeline C breaks silently because it does file-discovery by globbing for specific filename patterns.

| Field | Pipeline A Output | Pipeline C Expects |
|-------|------------------|-------------------|
| Image filename | `page_{page_num:04d}.png` (full-page render) | `page_{page_num:04d}.png` |
| Image manifest | `images/{doc_id}/manifest.json` — `"filename": "page_XXXX.png"` | Looks up `"filename"` in manifest for dimensions |
| Page JSON `figure_only` | `true` = has visual refs OR blank | Filter: only process `figure_only=true` |
| Page JSON `ocr_chars` | Character count of cleaned text | Used for blank-page detection (`ocr_chars == 0`) |

**When fixing Pipeline C after a Pipeline A format change:**
1. Check `build_work_queue()` — the image path glob pattern must match Pipeline A's actual output filename
2. Check the manifest filename format — if Pipeline A changes `filename` field, dimension lookups in Pipeline C will fail silently (no error, just skips aspect filtering)
3. Check `figure_only` filter — if Pipeline A changes how `figure_only` is computed, Pipeline C may process wrong pages

**Pipeline C policy (2026-05-01): SmolVLM2 on figure_only=true pages only.**

Pipeline C processes ONLY `figure_only=true` pages — text pages and blank pages are skipped. Write-back goes to the original `extracted/{doc_id}/pages/*.json` files (not `extracted-vision/`).

**Shell wrapper `$SCRIPT_DIR` — `bash -n` passes but runtime crashes (2026-05-01):** `run_pipeline_c.sh` had `bash -n` pass cleanly, yet crashed at runtime with `SCRIPT_DIR: unbound variable`. The shell's `set -u` only catches undefined variables when they are *referenced* — if the script never reaches the reference (e.g., early exit due to other causes), the bug silently passes both syntax-check and test-run. **Always define `SCRIPT_DIR` at the top of any shell wrapper that uses it, before any other variable references:**
```bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
```
**This bug was committed** in `8b61cc3` — the `SCRIPT_DIR` definition was absent from the committed script. The fix (`5c308a3`) was applied on top. When auditing a script for this bug, do NOT trust `bash -n` alone — grep for `SCRIPT_DIR` usage and trace back to its definition.

**Shell wrapper `tee -a` mixes plain text with JSON lines (2026-05-01):** Using `cmd 2>&1 | tee -a "$STRUCTURED_LOG"` in a shell wrapper causes Python's `print()` output (plain text) to mix with the shell's `jq`-formatted JSON log lines in the same `.jsonl` file. The resulting file is not parseable as JSONL. **Fix:** Python scripts should write their own structured JSON lines via an `_jlog()` helper. The shell wrapper should only write its own `jq`-formatted lines, and Python's stdout/stderr should go to the wrapper's stdout (not the JSON log file). Alternatively, redirect Python stdout to a separate file and only `tee` the wrapper's own structured lines to the JSONL file.

**Shell script `--output-dir` flag is invalid (2026-05-01):** `run_pipeline_c.sh` passed `--output-dir extracted-vision` to `batch_image_describe.py`, but that script does NOT accept `--output-dir` — it hardcodes `EXTRACTED_BASE = <DATA>extracted`. The script silently failed on every invocation (unknown arg → immediate exit). The `extracted-vision/` directory is always empty. Always verify the Python script accepts the flags you pass before passing them.

**`multiprocess` package shadows `multiprocessing` in zk-rag venv (2026-05-01):** The venv at `<REPO>venv` has `multiprocess-0.70.19` installed. This causes `import multiprocessing as mp; type(mp.Queue)` to resolve to a **bound method** (`BaseContext.Queue`) rather than a class. Union syntax `mp.Queue | None` fails with `TypeError: unsupported operand type(s) for |: 'method' and 'NoneType'`. **Fix:** Use `Optional[mp.Queue]` from `typing` instead. Check with:
```python
import multiprocessing as mp
assert isinstance(mp.Queue, type), "mp.Queue is not a class — multiprocess is shadowing multiprocessing"
```

**Unicode whitespace corrupts scripts written via `write_file` (2026-05-01):** The `write_file` tool can inject non-breaking spaces (`\u00a0`) and other invisible Unicode characters when writing Python files. This causes `TypeError` on import even when the visible code looks correct. **Fix:** Run `ruff check <file.py> --fix` — it strips invisible Unicode. Then verify with a Python import test. **Prevention:** Read the file after writing and check hex if possible; always test-import after any `write_file` of a Python script.

**Work queue count (confirmed 2026-05-01):** 571 docs, 117,222 total pages. `figure_only=true`: 37,268. Blank (`ocr_chars=0`): 2,141. **Net pages for Pipeline C: 35,083.** Test command:
```bash
cd <HOME>/zk-rag-v2 && ./venv/bin/python3 -c "
import sys; sys.path.insert(0, 'pipeline_c')
from batch_image_describe import build_work_queue
items = build_work_queue(resume=True, limit=5)
print(f'Queue works: {len(items)} items')
"
```

**Structured logging (2026-05-01):** Both `run_pipeline_c.sh` and `batch_image_describe.py` emit JSON lines to `<DATA>logs/`. Format:
```
{"ts":"2026-05-01T05:50:00.123Z","level":"INFO","msg":"Pipeline C start — SmolVLM2 vision description"}
{"ts":"2026-05-01T05:50:01.456Z","level":"INFO","msg":"[100/35127 (0%)] 1200 pages/hr — err=2"}
{"ts":"2026-05-01T05:50:02.789Z","level":"PAGE","msg":"100/35127 00c8a75d p5 success=True chars=142"}
```

Shell script: `log "LEVEL" "message"` — uses `jq -n` to emit structured lines.
Python script: `_jlog(lf, level, msg)` — uses `json.dumps()` with `separators=(",",":")`.

**Filters applied in `build_work_queue()`:**
- `figure_only=true` (required)
- `ocr_chars != 0` (skip blank pages — nothing to describe)
- Image file must exist at `page_XXXX.png` (not `page_XXXX_img_00.png`)
- Aspect ratio and minimum dimension filters still apply

Skip reason codes: `blank_page`, `no_image_file`, `image_too_small`, `aspect_ratio_excluded`.

**Pipeline C status (2026-05-01):** Running in background. 35,056 `figure_only=true` pages to process. Log: `<DATA>logs/pipeline_c_<timestamp>.jsonl`. Lock: `<DATA>.lock.pipeline_c`. 5 parallel workers. Empirical: ~90 pages/hr per worker. ETA ~37 hrs for full corpus at this rate.

**Pipeline D2 runtime (2026-05-01, observed):** 157 docs / ~125k chunks / 768d embedding — ~2 hours sequential on 28 threads. CPU 2448% (28 threads pinned). Memory ~49GB RSS at peak. All 157 embeddings.npy files validated.

**Pipeline C:** 5 parallel `llama-mtmd-cli` workers spawned by `batch_image_describe.py`. Each worker is I/O bound waiting on vision inference. Observed ~90 pages/hr per worker. Total ~450 pages/hr across 5 workers. ETA ~37 hrs for 35,056 pages.

**Pipeline A ruff fixes (2026-05-01):** `batch_ingest_branch.py` — removed unused `logging` and `time` imports; removed unused `branch` variable. `pdf_processing.py` and `pipeline_a.py` — renamed ambiguous `l` to `line` in list comprehensions. All fixed with `ruff check --fix` + manual patch. ruff passed clean.

Data on disk is in the correct `<DATA>` paths — the Python constants in Pipeline D are stale/wrong but apparently overridden or bypassed in actual execution. When fixing or testing Pipeline D, verify the actual output dirs match the constants.

**Shell runners** (`run_pipeline_a.sh`, `run_pipeline_b.sh`, `run_pipeline_c.sh`, `run_pipeline_d.sh`) reference:
- Lock files: `/data/rag/.lock.pipeline_*` — should be `<DATA>.lock.pipeline_*`
- Log dirs: `<VENV>logs` — should be `<DATA>logs`
- `$SCRIPT_DIR`: `<VENV>scripts` — does not exist (scripts live in `shared/` or per-pipeline dirs)

Shell runners are currently excluded from scaffold (not copied to public output) but if re-enabled, fix these paths first.

- **Pipeline E (tree):** Builds Merkle trees from chunks, writes tree JSON. Does NOT update registry.
- **Pipeline F (emit):** Emits `tree_root` on-chain via V2 contract, sets `emitted_mainnet` field. Registry `status` unchanged.
- **Pipeline G (ingest):** Reads docs with `emitted_testnet` OR `emitted_mainnet` set, upserts to Qdrant, sets `status="ingested"`.
- **BM25:** Built automatically after Pipeline G if not already indexed.

**Production state (2026-04-27):** All 604 docs emitted to mainnet V2 (`0x462fc86...`). ~594 docs ingested to Qdrant (~172,624 total points). 5 genuinely non-emitted (no testnet, no mainnet record — need Pipeline F first). Large docs (>1,700 chunks) fail Qdrant upsert — these remain status=extracted.

## Pipeline F — emit_all.py Operations

> **⚠️ V3 Contract Required First:** All 571 docs have new Merkle roots from the 2026-05 chunking rewrite. V2 contract has the OLD roots live on-chain. Submitting new roots to V2 would overwrite `latestRoot[docId]` and break ZK proof provenance for the old emission records. **Deploy a fresh V3 contract before running Pipeline F.** See `references/v2-contract-review-2026-05-09.md` for full analysis.

### CRITICAL: Source .env before running

`emit_all.py` reads `DEPLOYER_KEY` from `os.environ` at runtime — it does NOT load `.env` automatically. If the key is not in the current environment, every doc will fail with `error_type: missing_key` written to the error log.

**Correct invocation:**
```bash
cd <REPO>pipeline_f
bash -c "source <REPO>.env && python3 emit_all.py [args]"
```

**Wrong — key not in environment:**
```bash
python3 emit_all.py [args]           # DEPLOYER_KEY not set → all docs fail
./emit_all.py [args]                 # Same problem
source <REPO>.env && python3 emit_all.py [args]  # Won't work — subshell
```

### Log locations

- **Debug log:** `/data/logs/emit_all_debug_YYYYMMDD.log` — full env vars (DEPLOYER_KEY redacted), forge command, raw stdout/stderr. Written on every invocation (not gated behind a flag).
- **Error log:** `/data/logs/emit_all_errors_YYYYMMDD.log` — JSON lines for every failure. Check here when a doc fails — error type and message are more readable than raw forge output.
- **Broadcast receipts:** `<REPO>pipeline_f/broadcast/<script>/<chain_id>/run-*.json` — Foundry's own receipt files. Use these to recover `tx_hash` and `blockNumber` if the script failed to parse them.

**When diagnosing a failure:**
1. `grep doc_id /data/logs/emit_all_errors_YYYYMMDD.log` — get the error type and message
2. `grep doc_id /data/logs/emit_all_debug_YYYYMMDD.log` — get the full forge trace

### Single-doc emit (test pattern)

```bash
cd <REPO>pipeline_f
bash -c "source <REPO>.env && python3 emit_all.py --doc-id <doc_id>"
```

Verify on-chain:
```bash
# Get on-chain doc count
<FOUNDRY_BIN>cast call 0x83166A340c0A61bc836BD6383aD4acB23a3E3176 "getDocCount()(uint256)" --rpc-url https://horizen-testnet.rpc.caldera.xyz

# Verify specific doc at index N
<FOUNDRY_BIN>cast call 0x83166A340c0A61bc836BD6383aD4acB23a3E3176 "getDocIds(uint256,uint256)(bytes32[])" <N> 1 --rpc-url https://horizen-testnet.rpc.caldera.xyz

# Verify Merkle root matches tree file
<FOUNDRY_BIN>cast call 0x83166A340c0A61bc836BD6383aD4acB23a3E3176 "getLatestRoot(bytes32)(bytes32)" <doc_id> --rpc-url https://horizen-testnet.rpc.caldera.xyz
```

### Batch emit (200 docs/tx) — BROKEN (use per-doc mode instead)

> ⚠️ **2026-05-12 Update:** `--forge-batch` mode with `CommitBatchV2.s.sol` is broken by design. It hits MemoryOOG after ~5-13 tree file reads inside the EVM. Use per-doc mode.

```bash
# WORKING — per-doc mode (reliable, ~5s/doc)
cd <REPO>pipeline_f
bash -c "source <REPO>.env && python3 emit_all.py --batch --limit 15"  # test 15
bash -c "source <REPO>.env && python3 emit_all.py --batch"           # full run
```

See `references/pipeline-f-batch-bugs-2026-05-12.md` for full bug analysis of the batch mode failures.

## Pipeline F — Registry Corruption Recovery (2026-04-24)

### Diagnosing Registry Corruption

**Symptom:** `JSONDecodeError: Extra data: line X column Y (char YYYY)` on `<DATA>registry.json`.

**Diagnosis pattern:**
```python
import json
with open('<DATA>registry.json') as f:
    content = f.read()

# Find the last valid JSON position
valid_pos = None
for i in range(len(content)-1, 0, -1):
    try:
        json.loads(content[:i])
        valid_pos = i
        break
    except:
        pass

if valid_pos:
    data = json.loads(content[:valid_pos])
    print(f"Valid JSON up to char {valid_pos}: {len(data['documents'])} docs recovered")
    print(f"Garbage appended: {len(content) - valid_pos} chars")
    print(f"Last valid doc: idx={len(data['documents'])-1} doc_id={data['documents'][-1]['doc_id']}")
```

**Common corruption pattern:** Two processes write `json.dump()` simultaneously. Process A writes valid JSON, Process B's write is interleaved, producing `Extra data` at the point where B's output was appended. The result is valid JSON (A's) + garbage (partial B's write).

**Recovery by truncation:** If the corruption is appending garbage at the END (not overwriting middle sections), truncating at the last valid position recovers all docs. The corrupted file usually contains all 604 docs in the valid prefix.

### Rebuilding `emitted_mainnet` from On-Chain State

When `emitted_mainnet` records are stale (wrong doc_ids or missing entries):

**Step 1 — Query all on-chain doc IDs:**
```bash
# Returns bytes32[] as hex strings: ["0x980e09de...", "0x479978fe...", ...]
<FOUNDRY_BIN>cast call \
  0x462fc86E28c07798BD4656451611FE4E0A6D7760 \
  "getDocIds(uint256,uint256)(bytes32[])" 0 260 \
  --rpc-url https://horizen.calderachain.xyz/http
```

**Step 2 — Verify on-chain count matches:**
```bash
<FOUNDRY_BIN>cast call \
  0x462fc86E28c07798BD4656451611FE4E0A6D7760 \
  "getDocCount()(uint256)" --rpc-url https://horizen.calderachain.xyz/http
```

**Step 3 — For each on-chain doc, get Merkle root and verify against tree file:**
```bash
# getLatestRoot(bytes32)(bytes32) — returns the committed Merkle root
<FOUNDRY_BIN>cast call \
  0x462fc86E28c07798BD4656451611FE4E0A6D7760 \
  "getLatestRoot(bytes32)(bytes32)" 0x980e09dece3ed6794381a81eeb56d8eecc139804a56b947c17c0a6bc307518fb \
  --rpc-url https://horizen.calderachain.xyz/http
```
Then compare against tree file `merkle_root` at `<DATA>merkle_trees/{doc_id_no_prefix}_tree.json`.

**Step 4 — Backfill registry:**
```python
import json, os

with open('<DATA>registry.json') as f:
    reg = json.load(f)

# On-chain doc IDs (hex strings with 0x prefix)
onchain_ids = [...]  # from getDocIds() output

doc_id_to_idx = {d['doc_id']: i for i, d in enumerate(reg['documents'])}

for doc_id_hex in onchain_ids:
    doc_id = doc_id_hex[2:]  # strip 0x prefix — registry uses no-prefix hex
    if doc_id not in doc_id_to_idx:
        continue
    idx = doc_id_to_idx[doc_id]
    # Verify tree file exists and matches on-chain root
    tree_path = f"<DATA>merkle_trees/{doc_id}_tree.json"
    if not os.path.exists(tree_path):
        continue
    with open(tree_path) as f:
        tree = json.load(f)
    # Backfill — tx_hash unknown for batch-emitted docs, leave empty
    reg['documents'][idx]['emitted_mainnet'] = {
        "block_number": 0,
        "tx_hash": "",
        "timestamp": ""
    }

with open('<DATA>registry.json', 'w') as f:
    json.dump(reg, f)
```

**Step 5 — Clear stale entries:** Any doc with `emitted_mainnet` SET but doc_id NOT in on-chain set is stale — delete it.

**Key format fact:** Registry stores `doc_id` as 64-char hex WITHOUT `0x` prefix (matches tree filenames). `getDocIds()` returns `bytes32[]` with `0x` prefix. Strip `0x` for registry lookups.

## Pipeline F — Pre-Run Validation Checklist

Before running Pipeline F on a batch of documents, verify these alignment points to ensure on-chain data will match the circuit's expectations:

**Data types (Solidity on-chain):**
| Field | Source | Solidity type |
|---|---|---|
| `doc_id` | Registry `doc_id` = `sha256` of PDF | `bytes32` (64 hex chars) |
| `merkle_root` | Tree JSON `merkle_root` | `bytes32` (66 hex chars with 0x) |
| `pdf_hash` | Registry `doc_id` (old pipeline = sha256) | `bytes32` |
| `chunk_count` | Tree JSON `chunk_count` | `uint32` |

**Key alignments (confirmed 2026-04-21):**
- Registry `doc_id` = `sha256` = 64-char hex = correct `bytes32` ✅
- Tree JSON `merkle_root` = 66-char hex with `0x` = correct `bytes32` ✅
- Registry `pdf_hash` = equals `doc_id` (old pipeline) = `bytes32` ✅
- Tree JSON `chunk_count` = integer = correct `uint32` ✅

**Registry emission check** — `is_already_emitted()` skips docs where `registry[doc_id].emitted_testnet` is set:
```
for doc in registry["documents"]:
    if doc.get("emitted_testnet"):
        # skips — already on-chain
```
Confirm expected doc IDs have `emitted_testnet` records before running. If all docs show `emitted_testnet` but Qdrant is empty, reset `status` (not `emitted_testnet`) per the reset script above.



## Pipeline F — Critical Bug Fixes (2026-04-21)

### Bug 0: `CommitBatchV2.s.sol` missing `_countDocs()` and broadcast (2026-05-12)

**File:** `pipeline_f/script/CommitBatchV2.s.sol`

**Problem 1:** The script used `vm.parseJsonUint(regJson, ".total_documents")` to count documents, but the registry has no `total_documents` top-level key — only a `documents` array. This causes `vm.parseJsonUint` to revert, making every batch Forge call fail immediately.

**Problem 2:** The `vm.startBroadcast()` / `vm.stopBroadcast()` + `batchAppendRoots()` call was missing entirely (overwritten during a prior edit).

**Problem 3 — Registry JSON marker mismatch:** The `_countDocs()` function from `AppendRootV2.s.sol` uses a 12-byte marker `"documents":[` (no space). The actual registry format is `"documents": [\n[` (space after `:`, newline after `[`) — a 14-byte sequence. Without matching the actual marker, `_countDocs()` returns 0 and every batch fails with "documents array not found".

**Fix:** Replace `vm.parseJsonUint(regJson, ".total_documents")` with `_countDocs(regJson)` using the correct 14-byte marker. Restore the broadcast calls.

**Prevention:** Any Forge script that reads `registry.json` for doc counts must use `_countDocs()` with the correct marker. Inspect the actual registry JSON header before writing a scanner.

### Bug 0b: `CommitBatchV2.s.sol` MemoryOOG — batch mode is fundamentally broken (2026-05-12)

**Root cause:** `CommitBatchV2.s.sol` reads all tree JSON files **inside the EVM** via `vm.readFile()` + `vm.parseJson*()`. Each call allocates Solidity string memory that is not freed between iterations. With ~13+ tree files (each ~1KB JSON), EVM memory exhausts with `MemoryOOG`.

**Symptoms:**
- 2-doc batch: succeeds ✅
- 14-doc batch: fails with `MemoryOOG` ❌
- Per-doc mode (`--batch` without `--forge-batch`): succeeds reliably ✅

**Why it happens:** Forge's `forge script` runs the Solidity script in a local EVM simulation (not a real node). The memory allocator in that simulation is constrained. `vm.readFile()` + JSON parsing is extremely memory-inefficient in that context.

**Verdict:** `--forge-batch` mode with `CommitBatchV2.s.sol` is not viable for production use. The per-doc mode (`--batch` only) is the reliable path.

**Mitigation options (not implemented):**
1. Python reads all tree files → passes raw bytes32 arrays to Forge via env vars or stdin (major rework)
2. Reduce sub-batch size in `CommitBatchV2.s.sol` to 2-3 docs per tx (eliminates speed benefit)
3. Use a Solidity-only approach where the contract does all JSON parsing off-chain via pre-encoded data

**Current working approach:** Per-doc mode with `SUB_BATCH_SIZE=15` (max docs per Forge invocation). 571 docs ≈ 571 Forge calls = slow but reliable.

### Bug 1: `MERKLE_ROOT` 0x prefix stripped incorrectly
**File:** `pipeline_f/emit_all.py` line 240
**Problem:** `merkle_root.lstrip("0x")` stripped the `0x` prefix before passing to `forge script`. But `vm.envBytes32("MERKLE_ROOT")` in Forge stdlib **requires** the `0x` prefix to parse hex as bytes32. Without it, the value is misinterpreted.
**Fix:** Pass `merkle_root` unchanged. Tree JSON already has `0x` prefix; Forge handles it correctly.

### Bug 2: `--dry-run` is not a valid `forge script` flag
**Problem:** `forge script` does not have a `--dry-run` flag. Passing it causes: `Error: unexpected argument '--dry-run' found`. This was causing 100% failure on every doc.
**Fix:** Remove `--dry-run`. For simulation without broadcast, omit `--broadcast` only. Forge always simulates when not broadcasting.

### Bug 3: `--private-key` required even for dry-run
**Problem:** Omitting `--private-key` in dry-run mode caused Forge to use a default sender address that is not authorized on the `MerkleRootRegistryV2` contract (`onlyAuthorized` modifier requires `msg.sender == owner() || allowlist.contains(sender)`). The contract reverted with `MerkleRootRegistryV2: not authorized`.
**Fix:** Always pass `--private-key` (needed for correct `msg.sender` derivation in simulation). Only omit `--broadcast` for dry-run. `DEPLOYER_KEY` must be set in env for all runs.

### Bug 4: Debug log was conditional on `--debug` flag
**Problem:** Debug log was only written when `--debug` was passed. Failures/Timeouts/Exceptions were logged to error log but had no forge trace, making diagnosis impossible.
**Fix:** Debug log is now written on every invocation (not gated behind a flag). The `--debug` argument was removed from argparse and all call chains. Error log alone is insufficient for debugging Forge failures.

### Bug 5: `isRootEmitted` returning true silently succeeds without registry update
**Problem:** If a Merkle root is already on-chain (from a prior run or contract state), `appendRoot` succeeds but the script's `require(emitted, ...)` check at the end of the Foundry script would revert. However, if the contract's `appendRoot` returns success without revert (e.g., different contract state), the script could succeed while the registry update is skipped.
**Pre-flight check:** Before running Pipeline F on new docs, verify the contract doesn't already have those roots. The contract returns `isRootEmitted: true` for already-emitted roots. Run `forge script` dry-run and check the `APPEND ROOT SUMMARY` section in stdout for `isRootEmitted:` status.

### Retro-lookup: Recovering unknown tx_hash from RPC (2026-04-22)

**Problem:** Pipeline F sometimes records `tx_hash = "unknown"` in the registry even though the block number and timestamp ARE captured. This happens when the broadcast receipt file read fails (lines 377-391 of `emit_all.py` silently catch exceptions). However, `block_number` and `block_timestamp` are still captured from the contract event, so the on-chain emission is valid — only the transaction hash was lost.

**Solution:** Use the RPC endpoint + uploader address to retroactively find the tx hash.

```python
import requests, json

RPC = "https://horizen-testnet.rpc.caldera.xyz"
UPLOADER = "0xbabc60ed17e6387aedab112e80744aa19efcb723"
headers = {"Content-Type": "application/json"}

def find_tx_by_block_and_uploader(block_number: int) -> str | None:
    """Query eth_getBlockByNumber, find the tx FROM the uploader address."""
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBlockByNumber",
        "params": [hex(block_number), True],
        "id": 1
    }
    resp = requests.post(RPC, json=payload, headers=headers, timeout=30)
    txs = resp.json()["result"]["transactions"]
    for tx in txs:
        if tx["from"].lower() == UPLOADER.lower():
            return tx["hash"]
    return None

# Use with registry update:
# 1. Load registry
# 2. For each doc where emitted_testnet.tx_hash == "unknown":
#     real_tx = find_tx_by_block_and_uploader(doc["emitted_testnet"]["block_number"])
#     doc["emitted_testnet"]["tx_hash"] = real_tx
# 3. Save registry
# 4. Update Qdrant payloads for all chunks of that doc
```

**Also update Qdrant:** After fixing the registry, update Qdrant payloads for all chunks of the affected docs:
```python
from qdrant_client import QdrantClient
client = QdrantClient(url="http://127.0.0.1:6333")
- **emit_all.py bug compendium:** `references/pipeline-f-bugs.md`
onchain_ids = []
for i in range(count):
    data = "0x" + "0" * 56 + format(i, "04x")  # getDocIds slot approach
    # Use getDocByIndex if available, or iterate getDocument()
    ...

# Load registry
with open("<DATA>registry.json") as f:
    reg = json.load(f)

missing = []
for i, doc in enumerate(reg["documents"][:count]):
    rec = doc.get("emitted_testnet", {})
    if not rec or rec.get("status") != "emitted":
        missing.append((i, doc["doc_id"]))
print(f"Missing emission records: {len(missing)}")
for i, did in missing:
    print(f"  reg_idx={i} doc_id={did}")
```

**Recovery:** Read the batch broadcast receipt, then backfill manually:
```python
import json, requests, os
from datetime import datetime, timezone

BROADCAST_FILE = "<REPO>pipeline_f/broadcast/CommitBatchV2.s.sol/2651420/run-latest.json"
CHAIN_ID = 2651420
EXPLORER = "https://horizen-testnet.explorer.caldera.xyz"

with open(BROADCAST_FILE) as f:
    d = json.load(f)

receipt = d["receipts"][0]
tx_hash = receipt["transactionHash"]
block_number = int(receipt["blockNumber"], 16)

# Get block timestamp
 RPC = "https://horizen-testnet.rpc.caldera.xyz"
block_ts = int(requests.post(RPC, json={
    "jsonrpc": "2.0", "method": "eth_getBlockByNumber",
    "params": [hex(block_number), False], "id": 1
}).json()["result"]["timestamp"], 16)

# Doc IDs in this batch (decode from broadcast arguments)
doc_ids_in_batch = [
    "fadd109245d456ccc23feee5cd29f290420bd2433f3ad8e9d829a00ef43ccd8a",
    "86dc1fef8c63f442f976f8adfd5f46ad47e0ec573a4ef801af1df21d3b80293d",
    "f595ca62a4de1cff868b4445fa97e65248a1a2f5cca1dff03a50a29a906923c8",
    "4c6037bf02843f4e271b16de7692e1e4875509effb1108e7b0c5862d81cd4a12",
    "8d71927e7245e1daed452dfc41abd42a1a97f68a471e9a60909ac80e3766dbf4",
]

# Load and update registry
with open("<DATA>registry.json") as f:
    reg = json.load(f)

doc_id_index = {doc["doc_id"]: i for i, doc in enumerate(reg["documents"])}

for doc_id in doc_ids_in_batch:
    idx = doc_id_index[doc_id]
    reg["documents"][idx]["emitted_testnet"] = {
        "status": "emitted",
        "tx_hash": tx_hash,
        "block_number": block_number,
        "chain_id": CHAIN_ID,
        "explorer_url": f"{EXPLORER}/tx/{tx_hash}",
        "emitted_at": datetime.fromtimestamp(block_ts, tz=timezone.utc).isoformat(),
    }

with open("<DATA>registry.json", "w") as f:
    json.dump(reg, f, indent=2)

print(f"Backfilled {len(doc_ids_in_batch)} docs")
```

**Prevention:** The `except Exception: pass` silently swallowing receipt-parse errors is the root cause. The batch mode should:
- Fail explicitly if `tx_hash` is empty after parsing
- Call `save_registry()` BEFORE parsing receipts (or use a try/finally pattern)
- Write a recovery script that can reconcile on-chain state with registry state

### tx_hash extraction from broadcast receipt (FIXED 2026-04-21)

**Problem (2026-04-21):** `emit_all.py` reported `tx_hash = "unknown"` for every emitted doc even when the broadcast succeeded on-chain. The script tried to parse `Transaction hash:` from forge stdout, but `forge script --broadcast` does not print the hash to stdout — it writes receipts to a JSON file.

**Root cause:** `forge script --broadcast` writes tx receipts to `broadcast/AppendRootV2.s.sol/<chain_id>/run-<timestamp>.json`. The script was looking in the wrong place.

**Fix (applied 2026-04-21):** `run_append_root_v2()` now reads the latest `run-*.json` from the broadcast directory and extracts `receipt.transactionHash` directly:

```python
broadcast_dir = Path(__file__).parent / "broadcast" / SCRIPT_V2_PATH.name / str(CHAIN_ID)
broadcast_files = sorted(broadcast_dir.glob("run-*.json"), key=os.path.getmtime)
if broadcast_files:
    with open(broadcast_files[-1]) as bf:
        data = json.load(bf)
    for receipt in data.get("receipts", []):
        h = receipt.get("transactionHash", "")
        if h.startswith("0x") and len(h) == 66:
            tx_hash = h
            break
```

**Manual extraction (still useful for old runs where tx_hash was set to unknown):**
```python
import json, glob

broadcast_dir = "<REPO>pipeline_f/broadcast/AppendRootV2.s.sol/2651420"
files = sorted(glob.glob(f"{broadcast_dir}/run-*.json"))

target_ids = {"doc_id1", "doc_id2", ...}  # lower-case

for fpath in files:
    with open(fpath) as f:
        d = json.load(f)
    for tx in d.get("receipts", []):
        for log in tx.get("logs", []):
            topics = log.get("topics", [])
            if len(topics) >= 2:
                doc_id = topics[1].replace("0x", "").lower()
                if doc_id in {t.lower() for t in target_ids}:
                    print(f"{doc_id[:16]}... tx={tx.get('transactionHash','')}")
```

## plonky2 MerkleTree — `cap_height` Semantics

**Critical fact (learned 2026-04-20):** In plonky2 `MerkleTree::new(leaves, cap_height)`:
- `cap_height` is an **exponent** — the cap has `2^cap_height` entries
- `cap_height = 0` → **exactly 1 cap entry** (the single root hash) ✅ what you want for single-root design
- `cap_height = 4` → 16 cap entries (2^4 = 16)
- `cap_height = 8` → 256 cap entries (2^8 = 256)
- `cap_height = tree_depth` → 2^tree_depth cap entries (e.g., 256 for depth=8) — NOT a single root

**Common mistake:** Setting `cap_height = tree_depth` hoping for 1 entry. You get 2^tree_depth entries instead.

**How to get a single root:** Use `cap_height = 0`.

Source: `plonky2/src/hash/merkle_tree.rs` lines 152-175.

## Phase 2: ZK Proof of Provenance

### Phase D — Limb Validation

**Purpose:** Verify that real Qdrant chunk texts and LLM input texts fit within circuit limits before running full E2E.

**Circuit limits (from `zk-circuit/src/circuits/zk_rag.rs`):**
```
ZK_MAX_CHUNK_LIMBS = 512      # max 512 * 7 = 3584 bytes per chunk
ZK_MAX_LLM_INPUT_LIMBS = 1024  # max 1024 * 7 = 7168 bytes for LLM input
ZK_MAX_LLM_OUTPUT_LIMBS = 512  # max 512 * 7 = 3584 bytes for LLM output
```

**Encoding:** UTF-8 bytes → 7-byte chunks → one Goldilocks field element per chunk (56 bits, safe for modulus).

**Script:** `zk-circuit/scripts/phase_d_limb_validation.py`

```bash
python3 <REPO>zk-circuit/scripts/phase_d_limb_validation.py
```

**How it works:**
1. Samples chunks from Qdrant across all collections (payload field: `text`)
2. Applies `bytes_to_field_limbs(text)` = `ceil(len(text.encode()) / 7)` to get limb count
3. Compares against 512 (chunk) and 1024 (LLM input)
4. Constructs worst-case LLM input from 5 largest chunks + system prompt + user query
5. Saves results to `zk-circuit/scripts/phase_d_results.json`

**Results (2026-04-19):** All 200 chunk samples passed. Worst case: 31 limbs (214 bytes) vs 512 limit. LLM input worst-case: 221 limbs (1,547 bytes) vs 1,024 limit. Constants are very conservative for actual data.

**If Phase D fails:** Increase the constants in `zk-circuit/src/circuits/zk_rag.rs`, then `cargo build --release` in `zk-circuit/`.

### Phase E — Full E2E ZK Proof

Full end-to-end proof generation using real Qdrant data. Pending.

### Circuit Design: Public Inputs (current as of 2026-04-22)

The plonky2 Merkle proof circuit has **4 public inputs** (updated from original 3):

| Public Input | Description |
|---|---|
| `merkle_root` | Poseidon hash — document's committed root |
| `document_hash` | Poseidon(doc_id_bytes) = leaf[0] of Merkle tree (NOT SHA-256) |
| `ingestion_timestamp` | Unix timestamp when root was published on EVM |
| `ingestion_block` | Block number when root was published |

**Private witnesses:** `leaf_hash` (Poseidon of chunk text), `siblings[]` (Merkle proof path), `index_bits[]` (leaf index).

**The ZK proof statement:**
> "This exact chunk belongs to the committed Poseidon Merkle tree whose root was published on-chain for this specific document at this timestamp."

### test-from-chunks Binary

**Purpose:** End-to-end integration test that loads real document chunks, rebuilds the Merkle tree identically to Pipeline E, generates a ZK proof for a specific chunk, and verifies it.

**Location:** `<REPO>zk-circuit/test-from-chunks/src/main.rs`

```bash
cd <REPO>zk-circuit
cargo build -p test-from-chunks

# Run on document with existing Pipeline E tree
cargo run -p test-from-chunks -- --doc-id <doc_id>

# Specific chunk index
cargo run -p test-from-chunks -- --doc-id <doc_id> --chunk-index 5

# Picks random doc/chunk if none specified
cargo run -p test-from-chunks
```

**Key behaviors:**
- Hashing imported from `zk_circuit::merkle_tree` — bit-for-bit identical to Pipeline E
- `chunk_hash` is public input — verifiable outside circuit
- Compares rebuilt tree root against Pipeline E's saved tree at `<DATA>merkle_trees/{doc_id}_tree.json`
- Proof saved to `/tmp/zk_proof_{doc_id}_{chunk_index}.json`

## Diagnosing `status: extracted` (Pipeline G Skipped Docs)

When a doc has `status: extracted` instead of `ingested`, Pipeline G skipped it. There are **four distinct failure modes**:

### Failure Mode 1: emit record missing `status` field

`is_doc_eligible()` requires `emitted_mainnet.get("status") == "emitted"`. If the emit record has `tx_hash` but no `status` field, the doc is rejected.

**Diagnosis:**
```python
import json
registry = json.load(open("<DATA>registry.json"))
for doc in registry["documents"]:
    if doc.get("status") == "extracted":
        em = doc.get("emitted_mainnet", {})
        has_status = "status" in em and em["status"] == "emitted"
        has_tx = bool(em.get("tx_hash"))
        print(f"{doc['doc_id'][:30]:32}  status_ok={has_status}  has_tx={has_tx}")
```

**Known cases:** `applied-ee-v1` and 4 other extracted docs have `tx_hash` but no `status` field.

**Fix:** Backfill the `status` field in the emit record:
```python
for doc in registry["documents"]:
    em = doc.get("emitted_mainnet", {})
    if em.get("tx_hash") and "status" not in em:
        em["status"] = "emitted"
```

### Failure Mode 2: fake/placeholder Merkle tree

Some docs have tree files with `root: null`, `nodes: []`, and a `tree_root` that is actually ASCII text encoded as hex (e.g. `0x31762d65652d00...` decodes to `"1v-ee-\x00deilppa"`).

**Diagnosis — check tree file validity:**
```python
import json, os

trees_dir = "<DATA>merkle_trees"
for doc_id in ["applied-ee-v1"]:  # add others as needed
    path = f"{trees_dir}/{doc_id}_tree.json"
    if not os.path.exists(path):
        print(f"{doc_id}: NO TREE FILE")
        continue
    tree = json.load(open(path))
    root = tree.get("root") or tree.get("merkle_root")
    depth = tree.get("depth") or tree.get("tree_config", {}).get("depth")
    nodes = tree.get("nodes", [])
    print(f"{doc_id}: root={bool(root)}  depth={depth}  nodes={len(nodes)}")
```

**Known case:** `applied-ee-v1` — tree has `root: null`, `nodes: []`, chunk_count=2 for a 454-page doc. Never had a real Pipeline E run.

**Fix:** Re-run Pipeline E on the source PDF, then re-emit to mainnet.

### Failure Mode 3: doc too large for Qdrant upsert (HTTP 400)

Large docs (1,800+ chunks) consistently fail with `HTTP 400 Bad Request` during Pipeline G upsert, even after Qdrant restart. The collection goes `yellow` during background indexing and may block new writes.

**Diagnosis:**
```python
# Check which docs are NOT in Qdrant despite status=ingested
from qdrant_client import QdrantClient
client = QdrantClient(url="http://127.0.0.1:6333")

missing = []
for coll in ["army", "navy", "marines", "other"]:
    result = client.scroll(collection_name=coll, limit=10000, with_payload=True)
    in_qdrant = {p.payload.get("doc_id") for p in result[0]}
    # compare against registry ingested docs in this branch
```

**Known cases (2026-04-24):**
| doc_id (short) | chunks | title |
|---|---|---|
| `c22025ab...753c03` | 1,798 | Google-digitized library book |
| `231e0047...27ca1` | 2,005 | Soviet Union: A Country Study |
| `c1fff394...90e09` | 2,832 | U.S. Navy Diving Manual SS521-AG-PRO-010 |
| `b88febc5...1f85` | 2,779 | U.S. Navy Diving Manual SS521-AG-PRO-010 (different hash) |

**Note:** `b88febc5...` is `branch: navy`, not army — the failure is per-doc, not per-collection.

**Current decision (2026-04-24):** Skip these 4 docs. They remain `status: extracted`, Merkle roots are on-chain, provenance is still provable via ZK circuit even without Qdrant storage. This is an acceptable limitation.

**Possible fixes if needed later:**
- Delete and recreate the collection, then re-ingest all docs in small batches
- Increase Qdrant `max_payload_size` in config
- Process large docs with a chunk-size ceiling (ingest in sub-batches per doc)

### Failure Mode 4: no emit record at all

Doc has neither `emitted_testnet` nor `emitted_mainnet`. Needs Pipeline F first.

**Diagnosis:**
```python
for doc in registry["documents"]:
    if doc.get("status") == "extracted":
        has_testnet = bool(doc.get("emitted_testnet"))
        has_mainnet = bool(doc.get("emitted_mainnet"))
        if not has_testnet and not has_mainnet:
            print(f"{doc['doc_id'][:30]} — no emit record, needs Pipeline F")
```

### Quick Checklist for Any `status: extracted` Doc

1. `emitted_mainnet.status == "emitted"`? → if no, fix #1 above
2. Tree file has `root` and `nodes`? → if no, fix #2 above
3. Chunk count reasonable for page count? → if no (e.g. 2 chunks for 454 pages), fix #2
4. Doc is one of the 4 known large failures? → if yes, skip (fix #3)
5. Has any emit record? → if no, run Pipeline F

### Catalog API — `/api/catalog`

**Endpoint:** `GET /api/catalog` on the RAG API server (port 8100)

**Purpose:** Returns all documents grouped by collection, filtered to only those actually indexed in Qdrant. Used by `catalog.html` to display the document browser.

**Behavior:**
1. Reads `<DATA>registry.json`
2. For each collection (army/navy/marines/other), scrolls Qdrant to build the set of ingested `doc_id`s (cached 10 min)
3. Returns only docs whose `doc_id` appears in the corresponding Qdrant collection

**Response shape:**
```json
[
  {
    "name": "army",
    "description": "U.S. Army doctrine and field manuals",
    "document_count": 332,
    "documents": [
      {
        "doc_id": "76d424f9ff701064586a5d8f746d03264c04badcecce509324e4724347ebd6a9",
        "title": "Air Operations",
        "branch": "army",
        "category": "Operations",
        "pub_year": 2010,
        "page_count": 200,
        "ia_identifier": "ark:/13960/..."
      }
    ]
  }
]
```

**Current counts (2026-04-25):** army:332, navy:165, marines:48, other:49 — total 594 docs in Qdrant out of 604 with embeddings.

### Qdrant Scroll — Get All doc_ids from a Collection

To enumerate all `doc_id` values stored in a Qdrant collection (needed for catalog filtering, cross-checks, etc.):

```python
import requests

def get_all_doc_ids(collection: str, qdrant_url: str = "http://127.0.0.1:6333") -> set[str]:
    """Scroll all points from a Qdrant collection, extract unique doc_ids."""
    doc_ids: set[str] = set()
    offset = None
    while True:
        payload = {"limit": 1000, "with_payload": ["doc_id"]}
        if offset:
            payload["offset"] = offset
        resp = requests.post(
            f"{qdrant_url}/collections/{collection}/points/scroll",
            json=payload
        )
        d = resp.json()
        for point in d["result"]["points"]:
            did = point["payload"].get("doc_id")
            if did:
                doc_ids.add(did)
        offset = d["result"].get("next_page_offset")
        if not offset:
            break
    return doc_ids
```

**Note:** `scroll_offset=` (keyword arg) is NOT valid in the Qdrant Python client — use `offset=` positional or as a kwarg directly. The next-page token is `result[1]`, not a named field.

### Website Catalog — catalog.html

**Location:** `<REPO>website/catalog.html`

Loads from `GET /api/catalog` on page init. Collection tabs (Army/Navy/Marines/Other) filter the document list. Each document card shows title, category, year, and page count.

The catalog link is at `/catalog.html`. The main search page at `/index.html` links to it.

## Pipeline E — Merkle Tree Builder (see also: zk-rag-pipeline-e for standalone ops)

Pipeline E reads document chunks from `<DATA>chunks/{doc_id}/`, builds a Poseidon Merkle tree, and writes the tree JSON to `<DATA>merkle_trees/{doc_id}_tree.json`. The `run_pipeline_e.sh` wrapper then updates the registry with the new `tree_root`.

### Chunk Directory Structure
Chunks are in subdirectories per doc_id: `<DATA>chunks/{doc_id}/chunks.jsonl`
**Do NOT** look for `{doc_id}_chunks.json` — that suffix doesn't exist.

### References

- [Pipeline F batch bugs (2026-05-12)](references/pipeline-f-batch-bugs-2026-05-12.md) — `_countDocs` marker length, `--nonce`→`ETH_NONCE`, MemoryOOG threshold, tree-skip pattern
- [V3 emission verification notes (2026-05-12)](references/v3-emission-verification-2026-05-12.md) — 571-doc emission state, Caldera RPC unreliability, registry type safety, emit_all.py critical fixes
- [V3 MerkleRootRegistry deployment (2026-05-12)](references/v3-deployment-2026-05-12.md) — `emitted_mainnet` unreliability, `merkle_tree` subdocument backfill, direct `cast send` workflow, `getLatestRoot` RPC issue, manual emission procedure

## Pre-Filter: Zero-Chunk Docs
See `references/zk-rag-pipeline-reference.md` for the zero-chunk filter snippet.

### Running Pipeline E
```bash
# Single doc
cd <HOME>/zk-rag-v2 && bash pipeline_e/run_pipeline_e.sh --doc-id <doc_id>

# Batch mode (all chunked docs without trees — long-running)
cd <HOME>/zk-rag-v2 && bash pipeline_e/run_pipeline_e.sh --batch
```

The script: (1) runs the `pipeline_e` binary, (2) calls `update_registry.py <doc_id>` to update `registry.json`.

### Key Paths
| Purpose | Path |
|---------|------|
| Pipeline E binary | `<REPO>zk-circuit/target/release/pipeline_e` |
| Wrapper script | `<REPO>pipeline_e/run_pipeline_e.sh` |
| Registry updater | `<REPO>pipeline_e/update_registry.py` |
| Chunks | `<DATA>chunks/{doc_id}/chunks.jsonl` |
| Output trees | `<DATA>merkle_trees/{doc_id}_tree.json` |

> **Note:** The PRD (`PRD-MIL-02-pipeline-e-merkle-tree.md`) originally described BN254 + Poseidon2 — this was wrong. The implementation uses **Goldilocks field** (p = 2⁶⁴ − 2³² + 1) and plonky2's **PoseidonHash**. The PRD was corrected on 2026-05-01 to match the implementation. The circuit and pipeline share `zk-circuit/circuit/src/merkle_tree.rs` — identical by construction.

### Critical: Parallel Writes Corrupt Registry
**Never** run pipeline_e or update_registry.py with multiple parallel workers on the same `registry.json`. Use ThreadPoolExecutor → collect results in memory → write ONCE.

### Orphan Tree Files
If `<DATA>merkle_trees/` contains trees for docs NOT in the registry, move to archive:
```python
import os, json, shutil
trees_dir = "<DATA>merkle_trees"
archive_dir = "/data/archive/merkle_trees_orphan"
os.makedirs(archive_dir, exist_ok=True)
with open("<DATA>registry.json") as f:
    reg = json.load(f)
registry_tree_docs = {d["doc_id"] for d in reg["documents"] if d.get("has_merkle_tree")}
for f in os.listdir(trees_dir):
    doc_id = f.replace("_tree.json", "")
    if doc_id not in registry_tree_docs:
        shutil.move(os.path.join(trees_dir, f), os.path.join(archive_dir, f))
```

### Tree File Structure (what Pipeline E outputs)
The output `{doc_id}_tree.json` contains:
- `merkle_root` — single Poseidon root (hex string with `0x`)
- `tree_config.depth` — tree depth
- `chunk_count` — number of real text chunks
- `padded_leaf_count` — total padded leaves
- `doc_id_leaf_index` — always 0 (doc_id is leaf[0])
- `leaf_hashes` — all padded leaf hashes
- `paths` — per-chunk Merkle proof, keyed by chunk index string

**Pipeline E does NOT update the registry** — only writes tree JSON. Use `update_registry.py` to write `tree_root` into `registry.json`.

---

## Registry Backup Before Destructive Operations

**Always back up the registry before any delete, archive, or mass-update operation.**

```bash
cp <DATA>registry.json <DATA>registry.json.bak.$(date +%Y%m%d-%H%M%S)
```

The registry lives at `<DATA>registry.json` (NOT in the git project). It has its own git repo at `<DATA>.git` — also commit before destructive changes:
```bash
cd /data/military-documents && git add registry.json && git commit -m "checkpoint: description"
```

---

## Related ZK-RAG Operation Skills (absorbed siblings)

These narrower skills have been consolidated into this umbrella. Their content is preserved here or in references/:

| Former skill | Content absorbed as |
|---|---|
| `zk-rag-pipeline-e` | Pipeline E section above |
| `zk-rag-pipeline-g` | Pipeline G section above |
| `zk-rag-pipeline-f` | Pipeline F section above |
| `zk-rag-qdrant-upsert` | Qdrant upsert in Pipeline G section |
| `zk-rag-registry-sync` | Registry sync scripts in Pipeline F section |
| `zk-rag-registry-backfill` | Registry backfill in Pipeline F section |
| `zk-rag-registry-root-debug` | Registry root debugging in Pipeline F section |
| `zk-rag-registry-title-fix` | Title fix in Qdrant section |
| `zk-rag-registry-url-enrichment` | URL enrichment in Qdrant section |
| `zk-rag-pre-flight-check` | Pre-flight check patterns in Pipeline F section |
| `zk-rag-provenance-api` | Provenance API in Phase 2 section |
| `zk-rag-provenance-auto-submit` | Auto-submit in Phase 2 / Kurier API section |
| `zk-rag-2026-04-24-facts` | Scattered across this document (dates/states as of that date) |
| `zkrag-vps-sync` | VPS sync patterns in Pipeline F section |
| `zk-circuit-prove-bin` | prove-bin section near end of document |
| `zk-rag-api-server-admin` | API server ops in Pipeline G / Qdrant section |
| `zk-rag-v2-api-server-admin` | API server ops in Pipeline G / Qdrant section |

For standalone pipeline operations (single-doc runs, batch debugging), also see:
- `zk-rag-pipeline-e` — dedicated Pipeline E standalone guide
- `zk-rag-plonky2-circuit-debugging` — plonky2 circuit debugging skills (version conflict, wire conflict, serialization, field mismatch, RandomAccessGate bug)

---

## ZK-RAG Data Asset Audit — Registry vs Disk vs Qdrant

Before any deletion or cleanup, run a cross-check to understand what's actually on disk:

```python
import json, os
from qdrant_client import QdrantClient

# Load registry
with open('<DATA>registry.json') as f:
    reg = json.load(f)

registry_ids = {d['doc_id'] for d in reg['documents']}

# 1. Disk assets (images, chunks, embeddings, trees)
image_dirs   = set(os.listdir('<DATA>images'))
chunk_dirs   = set(os.listdir('<DATA>chunks'))
embed_dirs   = set(os.listdir('<DATA>embeddings'))
tree_files   = set(f.replace('_tree.json','') for f in os.listdir('<DATA>merkle_trees'))

# 2. Qdrant (server must be running)
client = QdrantClient(url='http://127.0.0.1:6333')
qdrant_ids = set()
for coll in ['army', 'navy', 'marines', 'other']:
    try:
        result = client.scroll(collection_name=coll, limit=10000, with_payload=['doc_id'])
        qdrant_ids.update(p.payload['doc_id'] for p in result[0] if p.payload.get('doc_id'))
    except: pass

# 3. Find gaps
missing_images  = registry_ids - image_dirs
missing_chunks  = registry_ids - chunk_dirs
missing_embeds  = registry_ids - embed_dirs
missing_trees   = registry_ids - tree_files
missing_qdrant  = registry_ids - qdrant_ids
orphaned_images = image_dirs   - registry_ids

print(f"Registry docs:    {len(registry_ids)}")
print(f"Has images:      {len(registry_ids & image_dirs)} | missing: {len(missing_images)}")
print(f"Has chunks:      {len(registry_ids & chunk_dirs)} | missing: {len(missing_chunks)}")
print(f"Has embeddings:  {len(registry_ids & embed_dirs)} | missing: {len(missing_embeds)}")
print(f"Has trees:       {len(registry_ids & tree_files)} | missing: {len(missing_trees)}")
print(f"In Qdrant:       {len(registry_ids & qdrant_ids)} | missing: {len(missing_qdrant)}")
print(f"Orphaned images: {len(orphaned_images)}")

if missing_images:
    print("\nDocs missing images:")
    for did in sorted(missing_images):
        doc = next((d for d in reg['documents'] if d['doc_id']==did), None)
        print(f"  {did}  [{doc.get('status','?')}]  {doc.get('title','NO TITLE')[:60]}")
if orphaned_images:
    print("\nOrphaned image dirs (no registry entry):")
    for did in sorted(orphaned_images):
        print(f"  {did}")
```

**Typical findings after deletions:**
- 8 deleted docs → 10 orphaned image dirs (deleted docs left image dirs behind)
- `status: extracted` docs → usually missing Qdrant entries (Pipeline G skipped them)
- `status: ingested` but no images → Pipeline B/C extracted text but didn't produce images (normal for text-only PDFs)

**Safe cleanup of orphaned image dirs** (after confirming they belong to deleted docs):
```bash
# Preview first
for did in <orphaned_ids>; do
    echo "Would remove: <DATA>images/$did"
done
# Then delete only after user confirmation
rm -r <DATA>images/<orphaned_id>
```

## Standard Logging Directory

**emit_all.py logs to:** `<DATA>logs/emit_all_debug_YYYYMMDD.log` and `<DATA>logs/emit_all_errors_YYYYMMDD.log`

When adding logging to a script, use JSON lines format:
```python
from pathlib import Path
import sys, json
from datetime import datetime, timezone

LOG_DIR = Path("<DATA>logs")

def log(level, msg, **fields):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "script": "script_name",
        "message": msg,
        **fields,
    }
    line = json.dumps(entry)
    print(f"[{level.upper()}] {msg}", file=sys.stderr)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / "script_name_<timestamp>.log", "a") as f:
        f.write(line + "\n")
```

Update `provenance.py` (2026-04-21): Added `LOG_DIR` constant and file logging. The shared `log_info`/`log_error` functions now write JSON lines to `<DATA>logs/provenance_<timestamp>.log`.

## Kurier API — Proven Working Format (2026-04-21)

### Critical Facts Discovered Through Trial and Error

**Base URL (MUST be correct):**
- **Testnet API base:** `https://api-testnet.kurier.xyz/api/v1` — NOT `https://testnet.kurier.xyz/api/v1`
  - `testnet.kurier.xyz` is the web portal — returns HTML 404 for API calls
  - `api-testnet.kurier.xyz` is the actual API server
  - The default in `provenance.py` was wrong — patched 2026-04-21 to `api-testnet.kurier.xyz`
- **Mainnet API base:** `https://api.kurier.xyz/api/v1`
- **API docs (Swagger):** `https://api-testnet.kurier.xyz/docs`

**`vkRegistered` must be `false`:**
Sending `vkRegistered: true` causes **HTTP 500** on mainnet. The working format is:
```json
{
  "proofData": {
    "proof": "0x...",
    "publicSignals": "0x...",
    "vk": "0x..."           ← bare hex string, NOT an object
  },
  "proofType": "plonky2",
  "proofOptions": {"hashFunction": "poseidon"},
  "vkRegistered": false,     ← must be explicitly false
  "submissionMode": "attestation"
}
```
The VK is auto-registered on first submit when `vkRegistered: false`. No separate registration step needed.

**`vk` field format:** bare hex string (`"0x040000000000..."`) — NOT `{"config": "Poseidon", "bytes": "0x..."}`.

**Terminal status is `Finalized`:** The plonky2 proof job reaches `"status": "Finalized"` as terminal state — not `"completed"` or `"verified"`. **Always use lowercase** when checking against terminal statuses (API returns capitalized but `state.lower()` normalizes). Add to terminal statuses set:
```python
TERMINAL_STATUSES = {"completed", "successful", "done", "verified", "finalized", "failed", "rejected", "invalid"}
```
**Bug (2026-04-22):** `"finalized"` was missing from this set — scripts would reach Finalized but loop forever. Fix: add `"finalized"` to the set.

**Poll interval ~10s, job completes in 30-50s** on mainnet.

### phase_l.py — Bulk ZK Proof Generation + Kurier Submission

**Location:** `<REPO>shared/phase_l.py`

Bulk script that processes all 20 docs end-to-end: Merkle tree metadata → Rust prove binary → Kurier submit → poll → save result JSON.

```bash
# All 20 docs
cd <REPO>shared
KURIE_API_KEY="..." <VENV>venv/bin/python3 phase_l.py

# Single doc
KURIE_API_KEY="..." python3 phase_l.py --doc <doc_id>

# Testnet (doesn't work — Kurier testnet returns 404 for submit-proof)
python3 phase_l.py --testnet
```

**Output:** Result JSONs at `<DATA>zk_proofs/<chunk_id>_proven.json`
**Logs:** `<DATA>logs/phase_l_<timestamp>.log`

**Proven working flow (2026-04-21):**
1. `get_chunk_metadata(chunk_id)` — loads from `{doc_id}_tree.json`
2. `generate_proof(metadata)` — calls Rust `prove-bin`, returns `{proof_hex, public_inputs_hex, vk_hex, public_inputs}`
3. Kurier submit with `vkRegistered: false` → returns `job_id`
4. Poll `job-status/{api_key}/{job_id}` until status in `TERMINAL_STATUSES`
5. Save to `_proven.json` with `kurier_final_status`, `zkverify_explorer_url`, `job_id`

### prove-chunks.py — Production ZK Proof Script

**Location:** `<REPO>zk-circuit/prove-chunks.py`

**Purpose:** Generate a ZK proof for a specific chunk of a specific document using pre-computed Merkle tree data from Pipeline E.

```bash
python3 <REPO>zk-circuit/prove-chunks.py <doc_id> <chunk_index> [--output <path>]
```

**How it works:**
1. Loads `merkle_tree/{doc_id}_tree.json` — contains pre-computed `leaf_hash` (Poseidon of chunk text), `siblings`, and `merkle_root` from Pipeline E
2. Loads `chunks/{doc_id}/chunk_ids.json` + `chunks.jsonl` to verify chunk exists
3. Calls `prove-bin` (Rust binary) with `CIRCUIT_DIR=<REPO>zk-circuit` — loads pre-built `circuit_depth{N}.bin` from disk (no recompilation)
4. Outputs zkVerify-compatible proof JSON to `<ZK_PROOFS_DIR>/<doc_id>_<chunk_index>.json`

**CRITICAL: Chunk indices are NOT sequential**
- The tree JSON only contains paths for specific chunk indices — not all chunks
- Available indices are stored as string keys in `tree_data["paths"]`
- Trying an index like `0` when it's not in the paths dict will fail with `No path found for chunk_index`
- Always check `tree["paths"].keys()` first to find valid indices
- The script uses `path_info["siblings"]` directly from Pipeline E — do NOT recompute in Python

**Outputs:** Proof JSON files (~248KB each, constant size regardless of chunk content)

**Log:** JSON lines to `<DATA>zk_proofs/prove-chunks.log` + stderr with ISO timestamps

**Example:**
```bash
# Find a valid chunk index for a doc
python3 -c "import json; t=json.load(open('<DATA>merkle_trees/<doc_id>_tree.json')); print(sorted(int(k) for k in t['paths'].keys()))"

# Generate proof
python3 <REPO>zk-circuit/prove-chunks.py <doc_id> <valid_chunk_index>
```

## prove-bin: stdin JSON Interface

**Critical discovery (2026-04-22):** The `prove-bin` Rust binary does NOT accept file paths as arguments. It accepts **JSON via stdin**.

**Current stdin input format (4 public inputs, flat JSON — no chunks array):**
```json
{
  "leaf_hash": "0x...",           // Poseidon(chunk_text), hex string
  "document_hash": "0x...",       // Poseidon(doc_id_bytes) = poseidon_doc_id_hash
  "merkle_root": "0x...",         // Poseidon root from Pipeline E
  "ingestion_timestamp": 1713000000,
  "ingestion_block": 12345678,
  "leaf_index": 42,               // integer — index of chunk in Merkle tree
  "siblings": ["0x...", ...]      // depth entries, one per level
}
```

**`depth` is NOT needed** — prove-bin derives it from the number of siblings entries.

**test-from-qdrant.py** (`zk-circuit/test-from-qdrant.py`) is the canonical script:
1. Queries Qdrant (network mode: `url='http://127.0.0.1:6333'`) for chunk payload
2. Extracts `leaf_hash`, `document_hash`, `merkle_root`, `leaf_index`, `siblings[]` from payload
3. Pipes JSON to `prove-bin` via stdin
4. Pipes proof output to `verify-zk-proof`
5. Prints timing and public inputs

```bash
# Random chunk from random collection
python3 <REPO>zk-circuit/test-from-qdrant.py

# Specific collection
python3 <REPO>zk-circuit/test-from-qdrant.py --collection army

# Specific chunk by ID
python3 <REPO>zk-circuit/test-from-qdrant.py --chunk-id "..."

# List available collections
python3 <REPO>zk-circuit/test-from-qdrant.py --list
```

**Results (depth 10, release binary):** prove ~34ms, verify ~6ms.

**Key Qdrant payload fields for ZK proof:**

| Field | Description |
|---|---|
| `merkle_leaf_hash` | Poseidon hash of chunk text (from Pipeline E) |
| `poseidon_doc_id_hash` | Poseidon(doc_id_bytes) = leaf[0] of Merkle tree |
| `merkle_root` | Poseidon root of entire Merkle tree |
| `merkle_leaf_index` | Integer position of chunk in tree |
| `merkle_siblings` | Array of sibling hashes for Merkle proof |

---

## Related ZK-RAG Quality & DevOps Skills (absorbed siblings)

| Absorbed Skill | Content | Why Absorbed |
|---|---|---|
| `zk-rag-document-quality` | Document quality assessment, low-quality doc removal from Qdrant | Same ZK-RAG pipeline/operations domain |
| `zk-rag-desloppify-config` | Desloppify scan config, YAML fixes, running scans, auto-fix | Same ZK-RAG quality/devops domain |

---

## Session 2026-05-01 Afternoon — D1/D2/E Review

### D1 is fast, A is the slow step
Mr. V clarified: D1 just reads Pipeline A's JSON and splits text into chunks — trivially fast. The ~5 hours was Pipeline A (PDF parsing, OCR, text extraction). D1 takes seconds per doc. The "5-hour D1" was a misattribution.

### D2 completed (2026-05-01 ~14:45)
- 157 docs / 124,961 chunks / 768d — all embedded, exit 0
- Runtime: ~3 hours on 28 threads, 49GB RSS peak
- Binary path fix (debug → release) committed as `f617f6e`

### Pipeline E review — PRD corrections (2026-05-01)
PRD `PRD-MIL-02-pipeline-e-merkle-tree.md` had multiple wrong specifications:

| Field | PRD Said | Actual (Goldilocks/PoseidonHash) |
|---|---|---|
| Field | BN254 | Goldilocks (p = 2^64 − 2^32 + 1) |
| Hasher | Poseidon2 | PoseidonHash (plonky2 native) |
| Paths | tree_nodes dict | paths only (no intermediate nodes stored) |
| Tree depth | Fixed 13 | Variable per-doc (next power of 2) |
| Input path | /data/rag/chunks/ | <DATA>chunks/ |
| Output path | /data/rag/merkle_trees/ | <DATA>merkle_trees/ |

All corrected in PRD and committed (`f617f6e`).

**PRD also marked resolved:**
- Library selection (uses plonky2 Rust, not Python poseidon2)
- Cross-validation (same merkle_tree.rs = identical by construction)
- Max depth confirmation (variable depth, pads to power of 2)

**Still open:** Variable tree depth — circuit must handle per-doc depth or cap at max. Needs circuit decision before Pipeline F/G.

### Pipeline C status (2026-05-01 ~22:20)
- 23,068 / 35,056 pages (65.8%)
- Rate: ~1,500 pages/hr across 5 workers
- ETA: ~8 hours (~6:15am next day)
- 100% success rate, 5 workers still running

### D1 partial run on 157 docs — NOT on remaining 414
Pipeline D1 was run on only 157 docs (those with existing chunks). The remaining 414 extracted docs are NOT yet chunked because Pipeline C (vision descriptions) has not completed for those docs. D1 reads from both Pipeline A's text JSON and Pipeline C's vision descriptions — partial runs are risky without C being done.

**E can run on the 157 done docs now** — D2 is complete for those, E is unblocked.
- **Tier A** (use): ≥3 pages, figures, substantial text — good for Qdrant + circuit proof
- **Tier B** (use with care): 1–3 pages, text-heavy
- **Tier C** (drop): single-page PDFs, pure images without text, administrative docs

### Removing Low-Quality Docs
```bash
# Remove Tier C docs from Qdrant
cd <HOME>/zk-rag-v2
python3 -c "
import json, httpx, asyncio

async def remove_low_quality():
    with open('data/low_quality_docs.json') as f:
        docs = json.load(f)
    async with httpx.AsyncClient() as client:
        for doc_id in docs['low_quality']:
            r = await client.delete(f'http://localhost:6333/collections/military-documents/points/{doc_id}')
            print(f'Deleted {doc_id}: {r.status_code}')

asyncio.run(remove_low_quality())
"
```

### Desloppify Scan Config
```yaml
# .desloppify.yaml
version: 1
rules:
  - id: PDF-TEXT-001
    severity: error
    pattern: "few characters"
```
Run: `desloppify scan /data/military-documents`

---

## Locations

**ZK circuit (main):** `<REPO>zk-circuit/`
- Binary: `test-from-chunks` (`cargo run -p test-from-chunks`)
- Merkle tree utils: `circuit/src/merkle_tree.rs` — `hash_leaf_text()` and `hash_doc_id()` (NFKC normalization)
- Circuit targets: `circuit/src/lib.rs` — `build_merkle_proof_circuit_targets()`

- **Qdrant service:** `qdrant.service` (systemd). Storage at `<DATA>storage/` — lsof discovery required because config-referenced path `<DATA>qdrant/config/config.yaml` does not exist; Qdrant runs on defaults. Stop/start: `sudo systemctl stop|start qdrant`. Reset: `mv <DATA>storage <DATA>archive/storage_YYYYMMDD && sudo systemctl start qdrant` (auto-creates fresh). Full procedure: `references/qdrant-storage-location.md`.
- BM25 index: `<DATA>bm25_index.pkl`
- Pipeline logs: `<DATA>logs/`
- Merkle trees: `<DATA>merkle_trees/`
- Phase D results: `<REPO>zk-circuit/scripts/phase_d_results.json`
