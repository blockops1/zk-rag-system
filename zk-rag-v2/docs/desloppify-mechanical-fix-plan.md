# Desloppify Mechanical Fix Plan — ZK-RAG V2 api_server.py

## Score Impact
- Current objective score: 79.8/100
- Fixing all mechanical issues in api_server.py won't dramatically move the score (mechanical = 25% of overall, subjective = 75%)
- But these are genuine code quality improvements

---

## Issue Summary (api_server.py only)

| Category | Count | Severity | Complexity |
|---|---|---|---|
| broad_except | 20 | medium | straightforward |
| monster_function | 2 (>150 LOC) | high | complex |
| high_cyclomatic_complexity | 5 (>12 branches) | medium | complex |
| silent_except | 4 | medium | straightforward |
| swallowed_error | 1 | medium | straightforward |
| loose_type_annotation | 12 | low | straightforward |
| raise_without_from | 8 | low | straightforward |
| global_keyword_usage | 1 | low | straightforward |
| function_level_import | 6 | low | straightforward |
| runtime_init_import | 1 | low | straightforward |
| magic_numbers | 2 | low | straightforward |
| duplicate_constant | 1 | low | straightforward |
| hardcoded_url | 1 | medium | DONE |
| dict_keys issues | 3 | medium | complex |

**Total: ~67 issues** (many are quick fixes, some are complex refactors)

---

## Straightforward Fixes (can batch)

### 1. broad_except (20 instances across 10 lines)
**What:** Replace `except Exception` with specific exceptions.

Pattern found:
- `except Exception` around Qdrant client calls → should be `except (qdrant_client.exceptions.ResponseHandlingException, ...)`
- `except Exception` around httpx calls → should be `except httpx.HTTPError`
- `except Exception` in API layer → should be `except (ValueError, RuntimeError)` depending on context

**Files:** shared/api_server.py lines 221, 279, 295, 368, 444, 489, 603, 641, 711, 794
**Effort:** ~15 min
**Risk:** Low (catching the right exceptions is safer than too-broad)

### 2. silent_except / swallowed_error (5 instances)
**What:** Add logging to `except: pass` and `except` blocks that don't log or re-raise.

**Files:** shared/api_server.py
**Effort:** ~5 min
**Risk:** Low

### 3. loose_type_annotation (12 instances)
**What:** Replace `list`, `dict`, `str` with `List[]`, `Dict[]`, `str` (or use `from __future__ import annotations`).

**Effort:** ~5 min
**Risk:** Very low

### 4. raise_without_from (8 instances)
**What:** Add `from e` to `raise ...` statements in except blocks.

**Effort:** ~5 min
**Risk:** Very low

### 5. function_level_import (6 instances)
**What:** Move imports to module level (only if not causing circular import — verify first).

**Effort:** ~10 min
**Risk:** Medium (may cause circular import issues)

### 6. magic_numbers (2 instances)
**What:** Replace hardcoded numbers > 1000 with named constants.

**Effort:** ~5 min
**Risk:** Low

### 7. global_keyword_usage (1 instance — line 744)
**What:** `global query_stats` — could refactor to pass state explicitly, or accept as-is (it's a counter update in an API endpoint).

**Effort:** ~5 min
**Risk:** Low

---

## Complex Fixes (need careful refactoring)

### 8. Monster Functions — `query()` (line 726) and `query_provable()` (line 914)
**What:** Each is >150 LOC with high cyclomatic complexity.

`query()` (line 726): Main vector search endpoint
- Handles: auth checking, cache lookup, embedding service call, Qdrant search, cache population, stats update, response building
- ~200+ LOC

`query_provable()` (line 914): Vector search + parallel ZK proof generation
- Handles: everything query() does plus parallel proof generation with semaphore capping
- ~200+ LOC

**Suggested approach:**
1. Extract embedding service call → `_get_query_embedding(query, model)`
2. Extract Qdrant search → `_execute_search(collection, vector, top_k, filters)`
3. Extract cache key building → `_make_cache_key(...)` (already exists but used inline)
4. Extract response building → `_build_query_response(...)`
5. For `query_provable()`: extract proof generation → `_generate_proofs_parallel(chunks, parallelism)`

**Effort:** ~2-3 hours
**Risk:** High (core API endpoints — any mistake breaks production traffic)

### 9. dict_keys Issues (3 instances)
**What:**
- Line 563: `chunk_data["chunk_id"]` read but never written
- Schema drift at line 846: `"proof_path"` key inconsistency

**Effort:** ~20 min to investigate and fix
**Risk:** Medium

### 10. High Cyclomatic Complexity (5 functions)
**What:** Functions with >12 decision points: lines 195, 380, 498, 726, 914

Note: 726 and 914 are the monster functions (same refactor as above).
The other 3 (lines 195, 380, 498) likely need moderate refactoring.

**Effort:** ~1-2 hours total for the 3 smaller ones
**Risk:** Medium

---

## Recommended Execution Order

### Batch 1: Quick wins (30 min)
1. Fix `silent_except` / `swallowed_error` (5 instances)
2. Fix `raise_without_from` (8 instances)
3. Fix `loose_type_annotation` (12 instances — add `from __future__ import annotations`)

### Batch 2: Moderate risk (45 min)
4. Fix `broad_except` (20 instances — needs careful exception mapping)
5. Fix `magic_numbers` (2 instances)
6. Fix `dict_keys` (3 instances — investigate first)

### Batch 3: Complex refactors (3-5 hours)
7. Refactor `query()` monster function
8. Refactor `query_provable()` monster function
9. Fix remaining high cyclomatic complexity (lines 195, 380, 498)
10. Address `function_level_import` (verify if circular import risk)

---

## Test Coverage
Before starting refactors: ensure tests exist and pass
```
cd $REPO_DIR && python -m pytest tests/test_api_server.py -v
```

After each monster function refactor: run tests again.

---

## Git Strategy
- Commit after each batch
- Tag each commit with the batch name
- Keep refactors in separate commits from quick fixes for easier rollback
