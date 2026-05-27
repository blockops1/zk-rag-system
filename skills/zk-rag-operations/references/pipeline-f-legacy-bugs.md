# Pipeline F — Legacy Bug Reference (2026-04/05)

Collected historical bugs from `emit_all.py` and Forge scripts. Current working state is in the main SKILL.md.

---

## `emit_all.py` Order-of-Operations Bug (2026-04-22)

**File:** `pipeline_f/emit_all.py`

**Problem:** Registry marked `emitted_mainnet=True` BEFORE Forge call, not after.

```python
# WRONG order — registry updated before Forge confirms
doc['emitted_mainnet'] = True
open(reg_path, 'w').write(...)
run_append_root_v2(...)  # Forge fails here; registry already wrong
```

**Fix:** Move registry write to AFTER successful Forge call + tx confirmation.

---

## `unknown_revert` with No Output = RPC Rate-Limit (2026-04-22)

**Symptom:** Forge exits with `unknown_revert` and no output.

**Root cause:** Caldera RPC HTTP 429 "Bandwidth limit exceeded" — NOT a contract rejection.

**Prevention:** Always check `getDocCount()` on-chain as source of truth, not registry flag.

---

## Registry `emitted_mainnet` Flag Is Unreliable — Don't Trust It

**Problem:** Docs marked `emitted_mainnet=True` from prior V2 run are not actually emitted on V3.

**Verification:**
```bash
source <REPO>.env
<FOUNDRY_BIN>cast call $MAINNET_CONTRACT_ADDRESS "getDocCount()(uint256)" --rpc-url $MAINNET_RPC_URL
```

---

## `--private-key` Required Even for Dry-Run (2026-04-24)

**Problem:** Omitting `--private-key` in dry-run caused Forge to use default sender — not authorized on `MerkleRootRegistryV2`.

**Fix:** Always pass `--private-key`. Only omit `--broadcast` for dry-run.

---

## Batch Mode Silently Skips Registry Save on Exception (2026-04-24)

**File:** `pipeline_f/emit_all.py`, `run_batch_forge()`

**Symptom:** Forge batch succeeds on-chain but registry has stale records.

**Root cause:** Exception after Forge broadcast but before registry write — registry never updated.

**Fix:** Wrap entire emit + registry cycle in atomic transaction, or verify `getDocCount()` after every batch.

---

## `_countDocs()` Marker Wrong — 12 bytes vs Actual 14 bytes (2026-05-12)

**File:** `pipeline_f/script/CommitBatchV2.s.sol`

**Symptom:** Every batch fails with `Error: documents array not found` even after adding `_countDocs()`.

**Root cause:** Copied from `AppendRootV2.s.sol` which uses 12-byte marker:
```
\"documents\":[   (12 bytes)
```
But actual registry format is:
```
\"documents\": [\n  [   (14 bytes: space after :, newline after [)
```

**Fix:** Use 14-byte marker matching actual registry JSON format.

---

## Missing Tree Files Cause Revert — First Doc at Index 0 Has No Tree (2026-05-12)

**File:** `pipeline_f/script/CommitBatchV2.s.sol`

**Symptom:**
```
vm.readFile: failed to open file \".../215e98c0..._tree.json\": No such file or directory
```

**Root cause:** `CommitBatchV2.s.sol` processes docs by registry array index, including docs without tree files. `AppendRootV2.s.sol` works because Python side pre-filters.

**Fix:** try/catch on `vm.readFile()` to skip docs without tree files.

---

## `getLatestRoot` RPC Returns 0x0 for Some Roots Despite Confirmed Txs (2026-05-12)

**Symptom:** `cast call <contract> "getLatestRoot(bytes32)(bytes32)" <root> --rpc-url $RPC` returns `0x0000...0000` even for confirmed txs.

**Root cause:** RPC endpoint state sync issue. Contract state IS correct.

**Workaround:** Use `getDocCount()` and tx receipts as source of truth.
