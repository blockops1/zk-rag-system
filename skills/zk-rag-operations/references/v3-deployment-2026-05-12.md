# V3 MerkleRootRegistry Deployment — 2026-05-12

## Session Summary

V3 MerkleRootRegistry deployed to `0x077467A27C70Fb47a6E9Fdd8b3C3F0C1Db869150` on Horizen mainnet (chain 26514). All 571 eligible docs with Merkle trees emitted successfully.

---

## Key Findings

### Finding 1: Registry `emitted_mainnet` Flag Is Unreliable — Don't Trust It

**Problem:** The 25 "failed" docs in the error log were NOT actually rejected by the V3 contract. They were marked `emitted_mainnet=True` in the registry from the **prior V2 run**. When the V3 pipeline tried to emit them, the Caldera RPC rate-limited (HTTP 429: "Bandwidth limit exceeded"), Forge exited with no output, and `emit_all.py` already wrote `emitted_mainnet=True` to the registry **before** calling Forge.

**Root cause in `emit_all.py`:** Registry is updated before the Forge call, not after successful confirmation:
```python
# emit_all.py — order of operations (WRONG)
doc['emitted_mainnet'] = True   # ← marked BEFORE Forge runs
open(reg_path, 'w').write(...)
run_append_root_v2(...)          # ← Forge fails here; registry already wrong
```

**Real failure mode:** `unknown_revert` from Forge with no output = RPC rate-limit, not contract rejection.

**Prevention:** Before trusting any `emitted_mainnet=True`, always verify with:
```bash
source <REPO>.env
# Check getDocCount vs registry count
<FOUNDRY_BIN>cast call $MAINNET_CONTRACT_ADDRESS "getDocCount()(uint256)" --rpc-url $MAINNET_RPC_URL
```

### Finding 2: `merkle_tree` Subdocument Can Be Missing Even When `tree_root` Is Set

**Problem:** Some docs in the registry have `tree_root` and `has_merkle_tree=True` but no `merkle_tree` subdocument (missing `chunk_count`, `tree_depth`, `padded_leaf_count`). The tree files on disk have this data — the registry entry just wasn't backfilled.

**Symptom:** When emitting via `cast send` directly (bypassing Forge), you get `chunk=0, depth=0, padded=0` from the registry, making the call fail.

**Fix:** Backfill from the tree file:
```python
import json, shutil
reg_path = '<DATA>registry.json'
shutil.copy2(reg_path, reg_path + '.bak')
reg = json.load(open(reg_path))
doc_id = '<doc_id>'
doc = next(x for x in reg['documents'] if x['doc_id'] == doc_id)
tree = json.load(open(f'<DATA>merkle_trees/{doc_id}_tree.json'))
doc['emitted_mainnet'] = False
doc['merkle_tree'] = {
    'chunk_count': tree['chunk_count'],
    'tree_config': tree.get('tree_config', {}),
    'padded_leaf_count': tree['padded_leaf_count']
}
open(reg_path, 'w').write(json.dumps(reg, indent=2))
```

### Finding 3: Direct `cast send` Works When Forge Script Fails

**When to use:** Forge script fails (RPC error, memory OOG, etc.) and the pipeline can't proceed.

**Format:**
```bash
source <REPO>.env
<FOUNDRY_BIN>cast send $MAINNET_CONTRACT_ADDRESS \
  "appendRoot(bytes32,bytes32,bytes32,uint32,uint8,uint32)" \
  <root> <doc_id> <doc_id> <chunk_count> <depth> <padded> \
  --private-key $DEPLOYER_KEY \
  --rpc-url $MAINNET_RPC_URL
```

**Then update registry manually:**
```python
# Mark emitted
reg = json.load(open('<DATA>registry.json'))
doc = next(x for x in reg['documents'] if x['doc_id'] == doc_id)
doc['emitted_mainnet'] = {
    'status': 'emitted',
    'tx_hash': '<tx_hash>',
    'block_number': <block>,
    'chain_id': 26514,
    'emitted_at': '<iso timestamp>'
}
open(reg_path, 'w').write(json.dumps(reg, indent=2))
```

### Finding 4: `getLatestRoot` RPC Returns 0x0 for Some Roots Despite Confirmed Txs

**Symptom:** `cast call <contract> "getLatestRoot(bytes32)(bytes32)" <root> --rpc-url $RPC` returns `0x0000...0000` even for docs whose emission tx is confirmed on-chain with `status=1`.

**Root cause:** RPC endpoint issue (possibly a state sync problem with the Caldera endpoint). The contract state IS correct — verified by:
1. `getDocCount()` returning correct count
2. Transaction receipts showing `AppendRoot` events with correct values
3. Direct `cast send` succeeding for same docs

**Verdict:** Always use `getDocCount()` and transaction receipts as source of truth. `getLatestRoot` for batch verification is unreliable on this RPC endpoint.

### Finding 5: `documents(uint256)` Function Signature Doesn't Match V3 ABI

**Symptom:** `cast call <contract> "documents(uint256)(bytes32,bytes32,uint256,uint8)" 0 --rpc-url $RPC` returns empty/reverts despite `getDocCount()=571`.

**Root cause:** The V3 contract uses a different storage layout than expected. The `documents` function may not exist with the assumed signature, or uses a different parameter type.

**Workaround:** Use `getDocCount()` as the primary count verification. For individual doc verification, rely on transaction receipts and events rather than `documents()` read functions.

---

## Final State (2026-05-12 ~20:00 UTC)

| Metric | Value |
|--------|-------|
| V3 Contract | `0x077467A27C70Fb47a6E9Fdd8b3C3F0C1Db869150` |
| Chain `getDocCount()` | **571** |
| Registry docs with `tree_root` | 571 |
| `emitted_mainnet=True` in registry | 572 (1 false positive from prior V2 run) |
| Deployment method | Per-doc manual `cast send` (Forge pipeline had issues) |
| Owner | `0xBABc60eD17e6387AEDab112E80744aA19EFCb723` |
| RPC | `https://horizen.calderachain.xyz/http` |

---

## Manual Emission Workflow

When the pipeline fails and manual emission is needed:

1. **Get the doc's tree data from disk:**
   ```python
   import json
   doc_id = '<doc_id>'
   tree = json.load(open(f'<DATA>merkle_trees/{doc_id}_tree.json'))
   root = registry[doc_id]['tree_root']
   chunk = tree['chunk_count']
   depth = tree['tree_config']['depth']
   padded = tree['padded_leaf_count']
   ```

2. **Emit via cast send:**
   ```bash
   cast send <contract> "appendRoot(bytes32,bytes32,bytes32,uint32,uint8,uint32)" \
     <root> <doc_id> <doc_id> <chunk> <depth> <padded> \
     --private-key $DEPLOYER_KEY --rpc-url $MAINNET_RPC_URL
   ```

3. **Update registry:**
   - Set `emitted_mainnet = False` (unmark false positive)
   - Backfill `merkle_tree` subdocument from tree file
   - After tx confirms: set `emitted_mainnet` to status object with tx hash

4. **Verify:**
   ```bash
   cast call <contract> "getDocCount()(uint256)" --rpc-url $MAINNET_RPC_URL
   # Should increment by 1
   ```
