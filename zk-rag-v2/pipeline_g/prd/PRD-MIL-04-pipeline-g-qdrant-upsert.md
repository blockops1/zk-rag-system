# PRD-MIL-04: Pipeline G — Qdrant Upsert with Merkle Metadata + EVM Provenance Proof

**Status:** Draft
**Author:** Fred (data backbone)
**Date:** 2026-04-14
**Pipeline:** G (Qdrant Upsert)
**Depends On:** Pipeline F (EVM emit) — requires `emitted_testnet` or `emitted_mainnet` in registry
**Runs After:** Pipeline F has emitted Merkle root to EVM and written `emitted_testnet`/`emitted_mainnet` to the registry
**Git repo:** `$REPO_DIR/pipeline_g/pipeline_g.py`

---

## 1. Problem Statement

Pipeline D splits documents into chunks. Pipeline E computes Poseidon2 Merkle trees over those chunks. Pipeline F emits the Merkle root to the Horizen EVM blockchain — providing public, immutable proof of the document corpus's existence at a point in time.

Pipeline G is the final step: it reads chunk data (Pipeline D), Merkle tree metadata (Pipeline E), and the EVM transaction receipts (Pipeline F), then upserts everything to Qdrant. The Qdrant payload includes the on-chain tx hash as cryptographic provenance proof — enabling ZK proofs that reference the public blockchain record.

**Pipeline G is the sole write point to Qdrant.** No other pipeline touches Qdrant.

---

## 2. Goals

- Attach Merkle tree metadata (from Pipeline E) to each Qdrant point
- Attach EVM provenance proof (from Pipeline F) to each Qdrant point
- Upsert to Qdrant — one collection per branch
- Update registry to `status: "ingested"` after successful upsert
- Idempotent: skipping already-ingested docs is safe
- **Pipeline G is the ONLY pipeline that writes to Qdrant**

---

## 3. Input

### 3.1 From Pipeline D (chunking output)

```
$DATA_DIR/chunks/{doc_id}/chunks.jsonl
$DATA_DIR/chunks/{doc_id}/chunk_ids.json
```

**chunks.jsonl format:**
```json
{
  "chunk_id": "05f9cb1d...-0",
  "doc_id": "05f9cb1d...",
  "text": "...",
  "page": 2,
  "chapter": "1",
  "section": "1-2",
  "section_title": "Launcher Operation",
  "chunk_index": 0
}
```

**chunk_ids.json format:**
```json
["05f9cb1d...-0", "05f9cb1d...-1", ...]
```

### 3.2 From Pipeline E (Merkle tree output)

```
$DATA_DIR/merkle_trees/{doc_id}_tree.json
```

**Format:**
```json
{
  "doc_id": "05f9cb1d...",
  "merkle_root": ["0x...", "0x...", ...x16],
  "chunk_count": 1433,
  "paths": {
    "0": {
      "leaf_index": 0,
      "leaf_hash": "0xabcd...1234",
      "siblings": [
        {"index": 1, "hash": "0xffff...0000", "at_depth": 0},
        {"index": null, "hash": "...", "at_depth": 1}
      ]
    }
  }
}
```

Note: `merkle_root` is an array of 16 hex strings (bytes32 each), representing the MerkleCap. All 16 values are stored as-is.

### 3.3 From Pipeline F (EVM emit output)

Pipeline F writes emission records directly into the registry under `emitted_testnet` (chain 2651420) or `emitted_mainnet` (chain 26514). Pipeline G reads these fields from the registry — it does NOT read `emitted_roots.json`.

**Registry emission record format:**
```json
{
  "emitted_testnet": {
    "status": "emitted",
    "tx_hash": "0xabcd...1234",
    "chain_id": 2651420,
    "emitted_at": "2026-04-15T20:46:16.765688+00:00"
  }
}
```

`block_number` and `block_timestamp` are not currently captured by Pipeline F and are not yet available — those fields in Qdrant payload will be `null` until emit_all.py is enhanced to parse them from forge output.

### 3.4 From Registry

```
$DATA_DIR/registry.json
```

**Fields used per document:**

| Field | Description |
|-------|-------------|
| `doc_id` | Document ID |
| `title` | Document title |
| `branch` | Branch (army, navy, marines, coastguard, joint, other) |
| `pub_year` | Publication year |
| `ia_identifier` | Internet Archive identifier |
| `merkle_root` | Single hex string — `tree_root` from registry (Pipeline E single-root mode) |
| `sha256` | PDF hash (used as pdf_hash for emit) |
| `emitted_testnet` | Dict with emit record; check `.status == "emitted"` |
| `emitted_mainnet` | Same structure, for mainnet emissions |

---

## 4. Output

### 4.1 Qdrant Points

One Qdrant collection per branch (e.g., `army`, `navy`, `airforce`, `other`).

**Point structure:**

| Field | Type | Description |
|-------|------|-------------|
| `vector` | float[4096] | BGE-small-en-v1.5 embedding of chunk text (shape: `(N, 4096)` per doc) |
| `id` | string | First 32 chars of `chunk_id` — deterministic, unique per chunk |

Note: `chunk_id` already contains the doc_id as a prefix (e.g. `0a21e7692759f40c...-0`). Taking the first 32 chars gives a deterministic, collision-free point ID without needing an explicit SHA256 hash.

**Payload fields:**

| Field | Source | Description |
|-------|--------|-------------|
| `doc_id` | registry | Document ID |
| `chunk_id` | Pipeline D | e.g. `05f9cb1d...-0` |
| `text` | Pipeline D | Chunk text (truncated to 200 chars in storage) |
| `page` | Pipeline D | Starting page number |
| `chapter` | Pipeline D | Chapter identifier |
| `section` | Pipeline D | Section identifier |
| `section_title` | Pipeline D | Section title |
| `branch` | registry | Document branch |
| `title` | registry | Document title |
| `pub_year` | registry | Publication year |
| `ia_identifier` | registry | Internet Archive identifier |
| `chunk_index` | Pipeline D | 0-based position in document |
| `merkle_leaf_hash` | Pipeline E | Poseidon2 hash of this chunk's text |
| `merkle_leaf_index` | Pipeline E | Leaf index in Merkle tree |
| `merkle_path` | Pipeline E | Array of sibling hashes `[{hash, at_depth}, ...]` |
| `merkle_root` | Pipeline E | MerkleCap array (16 hex strings, same for all chunks of this doc) |
| `merkle_tree_depth` | Pipeline E | Tree depth (from tree JSON, varies per doc — e.g. 5, 9, 13) |
| `evm_tx_hash` | Pipeline F | Hex tx hash of the on-chain emission (may be `null` for legacy emissions) |
| `evm_block_number` | Pipeline F | Block number where emission was confirmed (available for all 19 emitted docs) |
| `evm_block_timestamp` | Pipeline F | Block timestamp of emission — ISO8601 string (available for all 19 emitted docs) |
| `evm_chain_id` | Pipeline F | Chain ID of the EVM network (2651420 = testnet, 26514 = mainnet) |
| `evm_uploader` | Pipeline F | EOA address that submitted the root to the contract |
| `vision_description_used` | Pipeline D | Whether this chunk used SmolVLM2 vision description |

### 4.2 Registry Update

After successful Qdrant upsert, update the registry entry for this doc_id:
```json
{
  "doc_id": "05f9cb1d...",
  "status": "ingested",
  "chunk_count": <N>,
  "merkle_root": <merkle_root array>,
  "evm_tx_hash": "0x...",
  "evm_block_number": <N>,
  "evm_block_timestamp": "2026-04-15T21:30:45Z",
  "evm_uploader": "0xbabc60ed...",
  "evm_chain_id": 2651420,
  "ingested_at": "2026-04-15T22:00:00Z"
}
```

---

## 5. Design Decisions

### 5.1 Embeddings Already Exist

Pipeline D already produces embeddings. Pipeline G reads them from disk — it does NOT re-embed chunk text. This is critical for performance and consistency.

Embeddings are stored as a single file per document:
```
$DATA_DIR/embeddings/{doc_id}/
  embeddings.npy   # shape: (N, 4096) — N chunks × 4096 dimensions, float32
```

`N` matches `chunk_count` from the registry. The file contains all chunk embeddings for the document packed in a single numpy array. Load with `numpy.load()`.

If the `embeddings.npy` file does not exist for a doc, Pipeline G skips that doc and logs a warning.

### 5.2 Qdrant Collection Per Branch

Each branch gets its own Qdrant collection:

| Registry `branch` value | Qdrant collection name |
|------------------------|----------------------|
| `"army"` | `army` |
| `"navy"` | `navy` |
| `"marines"` | `marines` |
| `"coast guard"` | `coast_guard` |
| `"air force"` | `air_force` |
| Any other value | `other` |

Branch name normalization: lowercase, replace spaces with underscores, strip extra whitespace. This produces valid Qdrant collection names (no spaces, no special chars).

### 5.3 Dedup: Skip Already-Ingested Docs

Before processing any doc, Pipeline G checks whether it already has points in Qdrant:

```python
existing = client.scroll(
    collection_name=branch,
    scroll_filter={"must": [{"key": "doc_id", "match": {"value": doc_id}}]},
    limit=1,
    with_payload=False,
)
if existing.points:
    return {"status": "already_indexed", "doc_id": doc_id}
```

If already in Qdrant, Pipeline G skips silently (idempotent).

### 5.4 Source Selection (Chunk Text)

If both `ingested/{doc_id}` and `ingested-vision/{doc_id}` exist:
- Prefer `ingested-vision` if it has figure pages with `vision_description`
- Fall back to `ingested` otherwise
- Same logic as Pipeline D

### 5.5 Merkle Metadata Attachment

Pipeline G reads `{doc_id}_tree.json` from Pipeline E output and attaches per-chunk Merkle metadata:
- The `paths` dict in the tree JSON uses integer string keys (`"1"` through `"N"`) — NOT zero-based indices
- **Leaf index offset**: The document hash (doc_id) occupies leaf index 0 in the Merkle tree. The first chunk is at leaf index 1. Therefore: `paths[str(chunk_index + 1)]` maps chunk at `chunk_index` to its path entry
- Each path entry contains: `chunk_id`, `leaf_index`, `leaf_hash`, `siblings`
- `siblings` is an array of `{hash, at_depth}` objects (note: `at_depth`, NOT `index`)
- `merkle_tree_depth`: read from the registry field `tree_depth` (string, e.g. `"7"`) — NOT from the tree JSON

### 5.6 EVM Provenance Attachment

Pipeline G reads `emitted_testnet` or `emitted_mainnet` from the registry (written by Pipeline F) and attaches per-document EVM provenance:
- Look up `doc[emitted_testnet]` in registry — if `.status == "emitted"`, use it
- Fall back to `doc[emitted_mainnet]` if testnet not present
- If neither has `status == "emitted"`: skip doc (Pipeline F has not successfully emitted it yet)
- For the 19 existing emitted docs: `tx_hash` is `null` (not captured at emission time), but `block_number`, `block_timestamp`, and `uploader` are populated from the contract via sync_block_metadata_to_registry.py
- `evm_tx_hash`, `evm_chain_id`, `emitted_at` attached to every chunk of that doc
- `evm_block_number`, `evm_block_timestamp`, `uploader` — now available for all emitted docs

### 5.7 Text Truncation in Payload

Qdrant payload stores a truncated preview of chunk text:
- Store first 200 characters of chunk text in `text` field
- Full text is recoverable from `chunks.jsonl` on disk

---

## 6. CLI Interface

```bash
python pipeline_g.py \
    [--doc-id <doc_id>] \
    [--dry-run]
```

**Arguments:**

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--doc-id` | No | all | Process single doc, or all eligible if omitted |
| `--dry-run` | No | false | Show what would be done without writing to Qdrant |

**No `--batch` flag needed:** Pipeline G processes all eligible docs on each run. Use `--doc-id` to process a single doc.

---

## 7. Eligibility Criteria

A document is eligible for Pipeline G if ALL of the following are true:

1. Registry `status` is NOT `"ingested"`
2. `chunks.jsonl` exists at `$DATA_DIR/chunks/{doc_id}/chunks.jsonl`
3. Embedding files exist at `$DATA_DIR/embeddings/{doc_id}/`
4. `{doc_id}_tree.json` exists at `$DATA_DIR/merkle_trees/{doc_id}_tree.json`
5. Registry has `emitted_testnet.status == "emitted"` OR `emitted_mainnet.status == "emitted"`

Documents missing any of the above are skipped and logged.

---

## 8. Error Handling

| Error | Behavior |
|-------|----------|
| `chunks.jsonl` not found | Skip, log error, continue |
| Embedding files not found | Skip, log error, continue |
| `{doc_id}_tree.json` not found | Skip, log error, continue |
| Doc not emitted on testnet or mainnet | Skip, log warning, continue |
| Qdrant unavailable | Retry 3x with backoff; alert on failure |
| Zero chunks | Skip, log warning, continue |
| Already ingested | Skip silently, log at debug level |

---

## 9. Testing

### Unit Tests

1. **Dedup check**: Doc already in Qdrant → returns `already_indexed`, no Qdrant write
2. **EVM provenance attached**: For a doc with known registry emission record, verify all Qdrant points have `evm_tx_hash`, `evm_block_number`, `evm_block_timestamp`
3. **Merkle metadata attached**: Verify Qdrant point payload contains `merkle_root`, `merkle_leaf_hash`, `merkle_path`
4. **Missing tree JSON**: When `merkle_trees/{doc_id}_tree.json` is absent, verify doc is skipped
5. **Missing EVM emit**: When registry has no emitted_testnet or emitted_mainnet with status="emitted", verify doc is skipped
6. **Chunk count alignment**: Verify `chunk_ids.json` length matches number of Qdrant points upserted

### Integration Test

1. Pick a doc with known Pipeline D, E, and F outputs
2. Run Pipeline G on it
3. Query Qdrant for that doc's chunks
4. Verify:
   - Point count matches chunk_ids.json length
   - Each point's `merkle_root` matches the tree JSON
   - Each point's `evm_tx_hash` matches the registry emission record
   - Each point's `chunk_id` matches the chunks.jsonl

### Regression Test

1. Run Pipeline G on a doc already in Qdrant
2. Verify: no new points created, no error raised

---

## 10. Pipeline Execution Order

```
For each doc_id in registry where status != "ingested":
    if Pipeline D output exists
    AND Pipeline E output exists
    AND Pipeline F output exists (registry has emitted_testnet or emitted_mainnet, status="emitted"):
        run Pipeline G
```

Pipeline G is the final write step. The full sequence is:

```
Pipeline D (chunking) → Pipeline E (Merkle tree) → Pipeline F (EVM emit) → Pipeline G (Qdrant upsert)
```

---

## 11. Blocking Issues (Must Resolve Before Proceeding)

1. **Pipeline F must be working first**: Pipeline G reads `emitted_testnet`/`emitted_mainnet` from the registry — if Pipeline F has not emitted the Merkle root for a doc and written the record to the registry, Pipeline G cannot process that doc
2. **Qdrant accessible**: Must confirm Qdrant is running on `localhost:6333`
3. **Embeddings exist**: Pipeline D must have produced embeddings for the doc

---

## 12. Open Questions

| Question | Decision Needed | Recommendation |
|----------|----------------|-----------------|
| Collection naming | Per-branch or single collection? | Per-branch (current plan) — change if Qdrant performance degrades |
| Batch size for Qdrant upsert | How many points per upsert call? | 100 (balance between RPC size and retry granularity) |
| Run G on a schedule or event-driven? | Cron vs. trigger | Cron after F completes nightly |
| What if EVM emit failed for a doc? | Skip or retry? | Skip — manual intervention needed to determine cause |
