# Pipeline F Batch Mode Bugs — 2026-05-12

## Bugs Found During V3 Deployment

### Bug A: `--nonce` flag doesn't exist → fixed with `ETH_NONCE` env var

**File:** `pipeline_f/emit_all.py` line ~689

**Symptom:** Every `--forge-batch` call fails with `Error: unexpected argument '--nonce' found`

**Root cause:** `forge script` has no `--nonce` flag. The script was passing `--nonce <n>` as a CLI argument.

**Fix:**
```python
# WRONG
cmd += ["--nonce", str(nonce)]

# CORRECT — set in env dict
env["ETH_NONCE"] = str(nonce)
```

`forge script` picks up `ETH_NONCE` from the process environment automatically.

---

### Bug B: `_countDocs()` marker wrong (12 bytes vs actual 14 bytes)

**File:** `pipeline_f/script/CommitBatchV2.s.sol`

**Symptom:** Every batch fails with `Error: documents array not found` even after adding `_countDocs()`.

**Root cause:** The `_countDocs()` function was copied from `AppendRootV2.s.sol` which uses a 12-byte marker:
```
"documents":[   (12 bytes)
```
But the actual registry JSON format is:
```
"documents": [\n  [   (14 bytes: space after :, newline after [)
```

**Fix:** Use the correct 14-byte marker:
```solidity
// Correct marker matches actual registry format: "documents": [\n  [
uint256 markerLen = 14;
if (b[i] == '"' && b[i+1] == "d" && b[i+2] == "o" && b[i+3] == "c" &&
    b[i+4] == "u" && b[i+5] == "m" && b[i+6] == "e" && b[i+7] == "n" &&
    b[i+8] == "t" && b[i+9] == "s" && b[i+10] == '"' && b[i+11] == ":" &&
    b[i+12] == " " && b[i+13] == "[") {
```

**Prevention:** When copying `_countDocs()` from one Forge script to another, always verify the actual registry JSON format with:
```bash
head -3 <DATA>registry.json
```
The marker in the function MUST match the actual format.

---

### Bug C: Missing tree files cause revert (first doc at index 0 has no tree)

**File:** `pipeline_f/script/CommitBatchV2.s.sol`

**Symptom:** After fixing bugs A and B, batch still fails with:
```
vm.readFile: failed to open file ".../215e98c0..._tree.json": No such file or directory
```

**Root cause:** `CommitBatchV2.s.sol` processes docs by registry array index, including docs without tree files (registry index 0 = doc `215e98c0...` has `has_merkle_tree=False`). The script blindly calls `vm.readFile()` on every index.

`AppendRootV2.s.sol` works because the Python side (`emit_all.py`) pre-filters to only docs that have tree files. `CommitBatchV2.s.sol` has no such filter.

**Fix:** Use try/catch on `vm.readFile()` to skip docs without tree files:
```solidity
// First pass: count docs that have tree files
uint256 actualCount = 0;
for (uint256 i = batchOffset; i < end; i++) {
    string memory docIdHex = ...;
    string memory treePath = ...;
    try vm.readFile(treePath) returns (string memory) {
        actualCount++;
    } catch {
        // No tree file — skip
    }
}
if (actualCount == 0) return;

// Second pass: fill arrays only for docs with trees
// ... (use outIdx counter, not i - batchOffset)
```

**Prevention:** Any Forge script that processes registry array indices must handle the case where a doc has no tree file. Always verify tree file existence before including in the batch.

---

### Bug D: MemoryOOG — batch mode fundamentally broken for >~5 docs

**File:** `pipeline_f/script/CommitBatchV2.s.sol`

**Symptom:** 2-doc batch succeeds. 14-doc batch fails with `MemoryOOG`. Per-doc mode succeeds.

**Root cause:** `vm.readFile()` + `vm.parseJson*()` inside Forge's in-EVM simulation is extremely memory-inefficient. Each tree file read allocates Solidity string memory that isn't freed between reads. With ~13 tree files (~1KB each), EVM memory exhausts.

**Threshold:** ~2-5 docs works. ~13+ docs OOMs.

**Verdict:** `--forge-batch` with `CommitBatchV2.s.sol` is NOT viable for production. Use per-doc mode.

**Current working approach:** Per-doc mode (`--batch` without `--forge-batch`) with `SUB_BATCH_SIZE=15`. 571 docs = 571 Forge invocations. Slow but reliable.

**Batch mode viability by size:**
| Docs in batch | Tree files read | Result |
|---|---|---|
| 1 | 1 | ✅ works |
| 2 | 2 | ✅ works |
| ~5 | ~5 | ✅ borderline |
| ~14 | ~13 | ❌ MemoryOOG |

---

## Working Configuration (2026-05-12)

**V3 Contract:** `0x077467A27C70Fb47a6E9Fdd8b3C3F0C1Db869150` (Horizen mainnet, chain 26514)

**Deployer key:** Set in `.env` as `DEPLOYER_KEY=0x...` (with `0x` prefix)

**Contract verification:**
```bash
<FOUNDRY_BIN>cast call 0x077467A27C70Fb47a6E9Fdd8b3C3F0C1Db869150 "owner()(address)" --rpc-url https://horizen.calderachain.xyz/http
# Should return: 0xBABc60eD17e6387AEDab112E80744aA19EFCb723

<FOUNDRY_BIN>cast call 0x077467A27C70Fb47a6E9Fdd8b3C3F0C1Db869150 "getDocCount()(uint256)" --rpc-url https://horizen.calderachain.xyz/http
# Should return: 0 (fresh V3)
```

**Run per-doc mode (reliable):**
```bash
cd <REPO>pipeline_f
source <REPO>.env && python3 emit_all.py --batch --limit 15  # test 15 docs
source <REPO>.env && python3 emit_all.py --batch            # full 571 docs
```

**Verify on-chain after run:**
```bash
<FOUNDRY_BIN>cast call 0x077467A27C70Fb47a6E9Fdd8b3C3F0C1Db869150 "getDocCount()(uint256)" --rpc-url https://horizen.calderachain.xyz/http
```

**Registry state:** 721 total docs. 571 with `has_merkle_tree=True` and tree files. 0 emitted on V3.
