# Failed Subjective Review Run — 2026-05-23

## Run ID
`20260523_123131`

## Command Run
```bash
cd <HOME>/desloppify
python -m desloppify review --run-batches --runner opencode --parallel \
  --path <HOME>/zk-rag-v2 --batch-timeout-seconds 600
```

## Result
**Complete failure — all batches crashed with ContextOverflowError.**

## Error Pattern (all batches)
```
{"type":"error","timestamp":..., "error":{"name":"ContextOverflowError",
  "data":{"message":"request (4151-4313 tokens) exceeds the available context size (4096 tokens)",
          "type":"exceed_context_size_error"}}}
```

## Batch Status
| Batch | Dimension | Elapsed | Result |
|---|---|---|---|
| 1 | cross_module_architecture | 250s | ContextOverflowError |
| 2 | error_consistency | 226s | ContextOverflowError |
| 3 | initialization_coupling | 222s | ContextOverflowError |
| 4 | ? | 55s | code=1 (crashed) |
| 5 | ? | 61s | code=1 (crashed) |
| 6 | ? | ~50s | ContextOverflowError |
| 7 | ? | ~30s | ContextOverflowError |

**Results directory was empty** — zero output files produced.

## Root Cause
The blind packet (`holistic_packet_20260523_123131.json`) + system prompt + dimension rubric = ~4,150–4,313 tokens sent to a model with a 4,096-token context window. No headroom for actual code reading.

## Logs
- Run log: `<DESLOP>.desloppify/subagents/runs/20260523_123131/run.log`
- Batch logs: `<DESLOP>.desloppify/subagents/runs/20260523_123131/logs/batch-{1..7}.log`

## Current Desloppify Status (2026-05-25)
- **Mechanical:** 98.4% ✅
- **Subjective:** 0.0% (20 dimensions unassessed — blocked by context overflow)
- **Overall:** 24.6/100
- **Queue:** 1 item (subjective review)

## Options to Unblock Subjective Review
1. **Manual review** — human reads code, records scores via `desloppify review --manual-override`
2. **Slim the blind packet** — reduce `holistic_context` size sent per batch
3. **Find a larger-context model** — if opencode supports model override, use 8K+ context model
