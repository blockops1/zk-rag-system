# Pipeline F — emit_all.py Bug Compendium

## Batch mode silently skips registry save on exception (2026-04-24)

**Problem:** `run_batch_forge()` in `emit_all.py` can succeed on-chain but leave the registry with stale `emitted_testnet` records. This happens when:

1. `forge script --broadcast` succeeds (tx confirmed on-chain)
2. Broadcast receipt JSON is written to `broadcast/CommitBatchV2.s.sol/<chain_id>/run-*.json`
3. Exception occurs during receipt parsing (e.g., `except Exception: pass` silently swallows errors at line ~609)
4. `save_registry()` is never called → in-memory `registry_data` updates are lost

**Symptom:** Some docs in a batch show `emitted_testnet` in registry, others in the same batch have EMPTY `emitted_testnet` despite being on-chain. The contract has ALL docs, but registry is incomplete.

**If this bug occurred:** Some docs have `emitted_mainnet` SET but the corresponding on-chain root may belong to a different doc. Diagnosis:
```python
import json
with open('<DATA>registry.json') as f:
    reg = json.load(f)

# For docs with emitted_mainnet SET, verify their on-chain root matches their tree file
# Use getLatestRoot(bytes32) for each, compare to {doc_id}_tree.json merkle_root
```

**Prevention:** Always verify registry array index matches tree file doc_id before emit. The emit ordering must be identical between Python batch selector and Forge script array access.

## Array index mismatch between Python and Forge (2026-04-21)

**Problem:** Batch selection used tree file ordering, but Forge used registry array ordering. These don't match if any docs were re-ingested (different doc_id, same title).

**Fix:** Batch selection now iterates `registry['documents']` in array order. Forge receives `BATCH_OFFSET` and `sub_reg_offset` as registry array indices, not tree file indices.
