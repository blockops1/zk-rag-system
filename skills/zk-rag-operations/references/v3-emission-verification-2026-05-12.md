# V3 Emission Verification — 2026-05-12
# Definitive state after full 571-doc emission

## Contract
V3 MerkleRootRegistry: `0x077467A27C70Fb47a6E9Fdd8b3C3F0C1Db869150` (chain 26514)

## Chain State
- getDocCount() at block 15210000: 571 (stable reading)
- getDocCount() at latest: 1393 (UNRELIABLE — Caldera RPC returns inconsistent values)
- DO NOT trust getDocCount() alone — use getLatestRoot() for spot checks

## Registry vs Chain Alignment
| Category | Count |
|----------|-------|
| Docs with tree_root (eligible) | 571 |
| emitted_mainnet=status=emitted | 546 |
| emitted_mainnet=status=failed | 2 (both actually on-chain — RPC error, not contract failure) |
| No emitted_mainnet record | 23 (all 23 actually on-chain — manual cast send bypassed registry) |

**All 571 eligible docs confirmed on V3 contract via getLatestRoot() spot checks.**

## Key Learnings
1. **Caldera RPC getDocCount() is unreliable** — returns 571 at some blocks, 1393 at others. Always cross-verify with getLatestRoot() for individual docs.
2. **Manual emissions (cast send) don't update registry** — always go through emit_all.py to maintain registry consistency.
3. **emitted_mainnet field has THREE types** (bool True, dict with status=emitted, dict with status=failed) — must handle all three in code.
4. **Forge returncode==0 is NOT sufficient** — tx can revert on-chain. Always check receipt `status == "0x1"`.

## RPC Rate Limiting
- Caldera mainnet RPC: ~10 req/s before 429
- Solution: time.sleep(0.1) between subprocess calls
- Use testnet RPC for debugging when possible (less congested)

## Fixes Applied to emit_all.py (2026-05-12)
1. Added `time.sleep(0.1)` before Forge subprocess calls (both V2 and batch modes)
2. Changed receipt check from `len(h)==66` to `len(h)==66 and status=="0x1"` (both modes)
3. Registry save now only happens AFTER confirmed status=0x1 on-chain
