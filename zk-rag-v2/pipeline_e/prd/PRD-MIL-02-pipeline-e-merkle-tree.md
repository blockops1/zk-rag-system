# PRD-MIL-02: Pipeline E — Merkle Tree Build with Poseidon2

**Status:** Draft
**Author:** Fred (data backbone)
**Date:** 2026-04-02
**Pipeline:** E (Merkle Tree Build)
**Depends On:** PRD-MIL-01 (Pipeline D chunking)
**Runs After:** Pipeline D is fully working, entire document corpus is chunked, Qdrant is serving queries
**Git repo:** `$REPO_DIR/scripts/build_merkle_trees.py`

---

## 1. Problem Statement

After Pipeline D produces `chunks.jsonl` files, each document needs a Poseidon-based Merkle tree built over the chunk hashes. This tree provides cryptographic commitment to the document's chunk set — enabling ZK proof of membership at query time.

Pipeline E is a **pure computation step**: it reads chunk files, computes Poseidon hashes and tree, writes a JSON manifest. It does NOT touch Qdrant or any external service.

---

## 2. Goals

- Build a Poseidon Merkle tree for each document's chunk set
- Store all tree data (root, paths per leaf) in a JSON file on disk
- Produce deterministic, reproducible trees (same input = same output)
- Make tree data available to Pipeline F (Qdrant upsert) and Pipeline G (EVM emit)
- **Does NOT run until the full corpus is chunked and Qdrant is confirmed working**

---

## 3. Input

```
/data/rag/chunks/{doc_id}/chunks.jsonl
```

**chunks.jsonl format (from Pipeline D):**
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

---

## 4. Output

```
/data/rag/merkle_trees/{doc_id}_tree.json
```

**Format:**
```json
{
  "doc_id": "05f9cb1d...",
  "merkle_root": "0x1234...abcd",
  "poseidon_params": {
    "variant": "poseidon2",
    "field": "bn254",
    "rate": 8,
    "rounds": 22,
    "sbox": "x^5"
  },
  "tree_config": {
    "arity": 2,
    "depth": 13,
    "max_leaves": 8192,
    "padding": "zero"
  },
  "chunk_count": 1433,
  "padded_leaf_count": 8192,
  "leaf_hashes": [
    "0xabcd...1234",
    "0x9876...4321",
    ...
  ],
  "tree_nodes": {
    "depth_0": [...],   // leaf hashes (8192 entries, zero-padded)
    "depth_1": [...],   // 4096 parent hashes
    "depth_2": [...],   // 2048
    ...
    "depth_12": [...]   // 1 root hash (duplicated as single-element array)
  },
  "paths": {
    "0": {
      "leaf_index": 0,
      "leaf_hash": "0xabcd...1234",
      "siblings": [
        {"index": 1, "hash": "0xffff...0000", "at_depth": 0},
        {"index": null, "hash": "...", "at_depth": 1},
        ...
      ]
    },
    "1432": {
      "leaf_index": 1432,
      "leaf_hash": "...",
      "siblings": [...]
    }
  },
  "computed_at": "2026-04-02T12:00:00Z"
}
```

---

## 5. Design Decisions

### 5.1 Poseidon2 Parameters

Using `poseidon2` Python implementation (not the original Poseidon). Parameters chosen to be compatible with Plonky2's BN254 field:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Field | BN254 (GF(p) where p = 21888242871839275222246405745257275088548364400416034343698204186575808495617) | Matches Plonky2's native field |
| S-box | x^5 | Standard Plonky2 Poseidon gate |
| Rate | 8 | Standard for Merkle-tree-friendly Poseidon |
| Rounds | 22 (8+8+6 full rounds split) | Matches Plonky2 Poseidon reference |
| Capacity | 1 element | Standard |

**Library:** `poseidon2` PyPI package or direct reference implementation. Must be verified to match the Plonky2 circuit's Poseidon2 implementation.

**⚠️ CRITICAL:** The Python Poseidon2 implementation used here MUST produce identical output to the Plonky2 circuit's Poseidon2 gate. Before Pipeline E is used in production, a cross-validation test is required: hash a known string in Python, verify it matches the Plonky2 reference output.

### 5.2 Arity: Binary (2)

Arity 2 (binary Merkle tree) chosen for simplicity. Grok recommends arity 4 or 8 for lower depth, but:
- Binary is the simplest to implement and verify
- Depth 13 is fully manageable (8,192 leaves max)
- Changing arity later is possible if performance demands
- Decision deferred to future PRD if needed

### 5.3 Tree Depth

```
max_chunks_observed: 4,342 (from real data)
max_leaves: 8192 (2^13, binary)
tree_depth: 13 (ceil(log2(8192)))
```

All documents zero-padded to `max_leaves = 8192`. Leaves beyond `chunk_count` are set to `0`.

### 5.4 Leaf Hash Computation

```
For each chunk in chunks.jsonl (in order, 0 to chunk_count-1):
    canonical_text = normalize(chunk.text)   // strip trailing whitespace, normalize unicode
    leaf_hash = Poseidon2(canonical_text.encode('utf-8'))
```

**Normalization rules:**
- Strip leading/trailing whitespace from chunk text
- Normalize unicode using NFKC ( unicodedata.normalize("NFKC", text) )
- Encode as UTF-8 before hashing
- Empty chunks (should not exist after Pipeline D filtering): hash of empty string

### 5.5 Zero-Padding

Documents with fewer than 8192 chunks are zero-padded to exactly 8192 leaves. This:
- Makes the tree depth fixed (depth 13)
- Simplifies the ZK circuit (fixed depth = fixed circuit)
- Does not leak actual chunk count (padded hashes are distinguishable from real ones only by knowing the padding)

Padding leaf hash: `Poseidon2(b"")` (empty byte string — precompute once)

### 5.6 Path Storage Format

For each leaf index `i` that has a real chunk (i < chunk_count):
- `leaf_hash`: the Poseidon2 hash of the chunk text
- `siblings`: array of sibling hashes at each level
  - `at_depth 0`: sibling at leaf level (index 0 if i is odd, index 1 if i is even)
  - `at_depth 1`: sibling at next level up
  - ...continues to `at_depth 12` (root level)

Sibling hashes for padded leaves use the padding hash (empty string Poseidon2).

### 5.7 Tree Node Storage

Storing ALL intermediate nodes (`tree_nodes`) — not just paths per leaf — because:
- Enables verification of any path without recomputation
- Useful for debugging
- Storage cost is manageable: 8192 leaves + 4096 + 2048 + ... + 1 ≈ 16,383 hashes per doc
- At 32 bytes per hash (BN254 field element): ~500KB per doc worst case
- For 1000 docs: ~500MB total

**Alternative (deferred):** Store only root + per-leaf paths (paths dict). Full node storage is acceptable for now.

---

## 6. CLI Interface

```bash
python build_merkle_trees.py \
    --doc-id <doc_id> \
    [--out-dir /data/rag/merkle_trees] \
    [--max-depth 13] \
    [--dry-run]
```

**Arguments:**
| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--doc-id` | Yes | — | Document ID |
| `--out-dir` | No | /data/rag/merkle_trees | Output directory |
| `--max-depth` | No | 13 | Tree depth |
| `--dry-run` | No | false | Show what would be computed without writing |

**Or batch mode:**
```bash
python build_merkle_trees.py --batch --input-dir /data/rag/chunks --out-dir /data/rag/merkle_trees
```

---

## 7. Error Handling

| Error | Behavior |
|-------|----------|
| `chunks.jsonl` not found | Exit with error |
| Zero chunks | Write tree with `chunk_count: 0`, `merkle_root: padding_hash`, emit warning |
| Leaf hash computation fails | Log error per leaf, skip that leaf (do not fail entire doc) |
| Output directory not writable | Exit with error |

---

## 8. Cross-Validation Test (Required Before Production Use)

```python
# MUST pass before Pipeline E is trusted:
import hashlib
from poseidon2 import poseidon2

# Known test vector
test_input = b"hello world"
expected_leaf_hash = "0x..."  # Pre-computed using Plonky2 reference

computed = poseidon2(test_input, rate=8, rounds=22, sbox=5)
assert computed == expected_leaf_hash, f"Mismatch: {computed} != {expected_leaf_hash}"
```

This test vector must be agreed upon and hardcoded. If Poseidon2 implementation changes or Plonky2 circuit changes, this test catches the mismatch.

---

## 9. Testing

### Unit Tests
1. **Known test vector**: Poseidon2 of `"hello world"` matches pre-computed Plonky2 reference
2. **Binary tree construction**: 4 leaves → 2 parents → 1 root; verify root matches manual computation
3. **Zero-padding**: 3-chunk doc → padded to 8 leaves; verify padding hashes are correct
4. **Path correctness**: For leaf i, verify the path recomputes to the stored root
5. **Determinism**: Same input file → same merkle_root on two runs

### Integration Tests
1. Run Pipeline D on a test doc → chunks.jsonl
2. Run Pipeline E on same doc → merkle_tree.json
3. Verify merkle_root matches recomputation from chunks.jsonl
4. Verify every stored path recomputes to the stored root

---

## 10. Blocking Issues (Must Resolve Before Proceeding)

1. **Poseidon2 library selection**: Must identify and validate a Python Poseidon2 implementation that matches Plonky2's BN254 Poseidon gate output. Options: `poseidon2` PyPI, `circomlib` compatible implementation, or custom reference implementation.
2. **Test vector agreement**: Need a known test vector (input → expected Poseidon2 output) agreed upon with the Plonky2 circuit team (or derived from the circuit's reference).
3. **Max depth confirmation**: Confirm `max_leaves = 8192` (depth 13) is sufficient for all observed documents (current max: 4,342 chunks).

---

## 11. Open Questions

| Question | Decision Needed | Recommendation |
|----------|----------------|----------------|
| Library for Poseidon2? | PyPI `poseidon2` or reference impl | Start with reference implementation (fewer dependencies). Swap if PyPI version matches Plonky2 reference. |
| Per-leaf path storage vs. full node storage? | Now vs. later | Store full nodes (per section 5.7). Switch to path-only if storage becomes problematic. |
| Compress `tree_nodes` JSON? | Yes/no | No — keep human-readable JSON for debugging. Compress if storage becomes an issue. |
| Re-run Pipeline E when chunking changes? | Policy needed | Yes — if Pipeline D re-runs and changes chunks, Pipeline E must re-run before Pipeline F/G |
