# Qdrant Rebuild Reference

**Created:** 2026-05-22
**Updated:** 2026-05-22 (post-rebuild)
**Trigger:** Cross-collection misclassification + title prefix standardization

## Final State (after successful rebuild)

- **Registry:** 525 docs at `<DATA>registry.json`
- **Branch distribution:** army=344, navy=116, marines=36, joint=18, air_force=1, other=10
- **Qdrant points:** army=115,087 · navy=53,684 · marines=10,576 · air_force=709 · joint=5,419 · other=2,922 · **total=188,397**
- **Images:** all 525 docs have WebP image directories (4 missing docs re-rendered from PDF)
- **Merkle orphans:** 59 archived to `merkle_trees_orphan_20250522/` (not deleted)

## Registry Structure

**Path:** `<DATA>registry.json`

```json
{
  "documents": [ ... ],   // list of doc dicts, NOT dict keyed by doc_id
  "updated_at": "...",
  "total_documents": 586  // HEADER VALUE — may not match actual list length
}
```

- Access: `reg["documents"]` — iterate the list
- `total_documents` in header is unreliable (showed 586, actual was 527)
- Each doc entry has keys: `doc_id`, `branch`, `title`, `status`, `staged_path`, `chunk_count`, `page_count`, `doc_type`, `topic`, `pub_year`, `ia_identifier`, `emitted_testnet`, `emitted_mainnet`

## Branch Inference Rules

Used when reassigning documents to correct collections:

| Title prefix | → branch |
|---|---|
| `MCRP*`, `MCWP*`, `NAVMC*` | `marines` |
| `NWP*` | `navy` |
| `JP *`, `JP-*` | `joint` |
| `FM *`, `TC *`, `TM *`, `STP *`, `AR *`, `AMCP*`, `FMFRP*`, `IT*`, `P-*`, `T-*` | `army` |
| (existing `air_force` docs) | `air_force` |
| anything else | `other` |

## Title Prefix Rules

After branch assignment, prepend the service name:

| branch | title prefix |
|---|---|
| `army` | `US Army ` |
| `navy` | `US Navy ` |
| `marines` | `US Marine Corps ` |
| `air_force` | `US Air Force ` |
| `joint` | `US Joint ` |
| `other` | `US Joint ` |

## Directory Inventory (post-rebuild)

| Component | Count | Path |
|---|---|---|
| Registry docs | 525 | `<DATA>registry.json` |
| Chunks dirs | 525 | `<DATA>chunks/` |
| Embeddings dirs | 525 | `<DATA>embeddings/` |
| Merkle tree files | 527 | `<DATA>merkle_trees/` |
| Merkle orphans | 59 archived | `merkle_trees_orphan_20250522/` |
| Images dirs | 525 (all WebP) | `<DATA>images/` |

**Qdrant archives** (storage moved, not deleted):
```
qdrant_archive_20260522/                   ← first rebuild (wrong branch dist)
qdrant_archive_20260522_第二次/            ← second rebuild (wrong branch dist)
qdrant_archive_20260522_第三次/           ← third rebuild (wrong branch dist)
qdrant_archive_20260522_第四次/           ← fourth rebuild (this run, correct)
```

## Qdrant Collections (post-rebuild)

| Collection | Points | Registry docs |
|---|---|---|
| `army` | 115,087 | 344 |
| `navy` | 53,684 | 116 |
| `marines` | 10,576 | 36 |
| `joint` | 5,419 | 18 |
| `air_force` | 709 | 1 |
| `other` | 2,922 | 10 |

**Note:** `air_force` and `joint` were EMPTY in all rebuilds until the registry `branch` values were corrected AND `normalize_branch()` rules were fixed in pipeline_g.py.

## Known Bugs Found During Rebuild

### Bug: `_COLLECTION_DESCRIPTIONS` silently skips collections

**Symptom:** Catalog API (`/api/catalog`) returns fewer docs than expected, and `air_force`/`joint` collections don't appear.

**Root cause:** `api_server.py` line 479 defines `_COLLECTION_DESCRIPTIONS` with only 4 entries (`army`, `navy`, `marines`, `other`). The catalog endpoint iterates `_COLLECTION_DESCRIPTIONS.keys()` to build the response — if `air_force` or `joint` are missing, they're silently skipped with no error or empty collection in the response.

**Fix:** Add missing collections to `_COLLECTION_DESCRIPTIONS`, then `sudo systemctl restart zk-rag-api`.

**Verification:** `curl --max-time 120 http://127.0.0.1:8100/api/catalog` should return all 6 collections with correct doc counts.

### Bug: `website/registry.json` stale copy is irrelevant

**Symptom:** Confusion about whether the website needs an updated registry copy after rebuild.

**Finding:** The API (`api_server.py`) reads from `<DATA>registry.json` — the server's authoritative copy. The `website/registry.json` file is only a staging artifact for `push_to_vps.sh` — it gets copied to `/data/rag/docs/registry.json` on the VPS during push. The website JavaScript fetches catalog data from API endpoints, not from a static JSON file. **Phase 9 (syncing website registry copy) is unnecessary.**

## Common Failures

Full plan: `<REPO>docs/QDRANT-REBUILD-PLAN.md`

Phases: Stop services → Archive Qdrant → Archive orphan merkle files → Update registry (branch + title) → Create collections → Pipeline G → Verify → Restart API

## Pipeline G Key Facts

- **Entry point:** `cd <REPO>pipeline_g && python3 pipeline_g.py --batch`
- **Venv:** Use `<VENV>venv` (has `httpx` + `qdrant_client`), NOT `pipeline_a/venv` (missing `httpx`)
- **Reingest flag:** `--reingest` forces re-upload to all collections
- **Branch normalization:** `normalize_branch()` in `pipeline_g.py` lines 108-114 maps registry `branch` values to Qdrant collection names
- **CRITICAL normalize_branch rules:** `("air_force","air_force")` and `("joint","joint")` must both be present — without them, those branches fall through to `"other"`
- **Payload field:** `branch` written to Qdrant payload at line 277
- **Cache invalidation:** calls `_invalidate_query_cache_for_collection()` after each batch
- **EVM emit fields:** `build_evm_payload()` reads `emitted_testnet` or `emitted_mainnet` from registry entry
- **Merkle siblings:** `build_merkle_payload()` reads from `<DATA>merkle_trees/{doc_id}_tree.json`
- **Qdrant vector dimension:** 768 (from `rag/venv` embedding model)

## Why air_force and joint Were Empty (Root Cause)

Two separate bugs had to be fixed together:

**Bug 1 — Wrong registry branch values:**
Registry had stale branch assignments from an earlier inference run (army=311, navy=131, marines=43, joint=3, air_force=0, other=40). The inference script had saved incorrectly or used wrong patterns.

**Fix:** Re-ran comprehensive branch inference on all 525 docs with expanded ruleset:
- `JP*`, `NTTP*` → `joint`
- `MCRP*`, `MCWP*`, `NAVMC*` → `marines`
- `NWP*`, `NAVEDTRA*`, `NAVORD*`, `NSTM*` → `navy`
- `FM*`, `TC*`, `TM*`, `STP*`, `AR*`, `AMCP*`, `FMFRP*`, `IT*`, `P-*`, `T-*` → `army`
- `AF*`, `AFTTP*`, `AFJMAN*`, `Air Force*` → `air_force`

Result: army=344, navy=116, marines=36, joint=18, air_force=1, other=10.

**Bug 2 — `normalize_branch()` missing rules:**
`BRANCH_NORMALIZE_RULES` in pipeline_g.py had:
```python
("air force", "air_force"),  # matches "air force" (space) but registry uses "air_force" (underscore)
# "joint" had no mapping at all → fell through to "other"
```

**Fix:** Added explicit rules:
```python
("air_force", "air_force"),
("joint", "joint"),
```

Both bugs had to be fixed together. Even with corrected registry values, missing normalize rules would route joint/air_force docs to "other". Even with correct rules, wrong registry values meant no joint/air_force docs existed.

## Common Failures

- **Missing merkle tree:** Pipeline G skips or fails the doc — check `merkle_trees/` has the file
- **Missing emit record:** `build_evm_payload()` returns `None` for all EVM fields — doc still indexed but without on-chain provenance
- **Wrong collection:** if `normalize_branch()` returns `"other"` but doc was meant for `joint` or `air_force`, it goes to wrong collection — fix BOTH registry `branch` field AND `normalize_branch()` rules before rerunning
- **`httpx` import error:** Pipeline G fails with `ModuleNotFoundError: No module named 'httpx'` when using wrong venv — use `<VENV>venv`
- **Catalog API returns wrong count:** If `_COLLECTION_DESCRIPTIONS` is missing entries, the catalog silently skips those collections. Also: the catalog endpoint caches results per-collection for `_CATALOG_DOCS_CACHE_TTL_SECONDS` — after a rebuild, the cache holds stale counts until it expires. If testing immediately after restart, either wait for cache to refresh or restart the service to clear cache.
- **Stale website registry copy:** `website/registry.json` had 315 docs (old copy) — API reads from `<DATA>registry.json` which is correct; website static copy is for fallback only; Phase 9 sync is unnecessary
