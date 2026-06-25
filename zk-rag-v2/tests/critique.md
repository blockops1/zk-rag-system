# Critique: US-013 - Write integration tests for API server
**Reviewed:** 2025-01-10T00:00:00Z
**Files changed:** 1
**Overall verdict:** ✅ Clean

---

## ✅ What's Good
- **Proper mocking strategy**: Tests correctly mock `api_server.api_server.client` to avoid requiring live Qdrant, meeting the PRD requirement
- **All required tests present**: All 9 specified tests are implemented (test_health, test_collections_list, test_query_vector_only, test_query_hybrid, test_query_star_collection, test_query_top_k, test_doc_metadata, test_context_window, test_openapi_spec)
- **Correct doc_id usage**: Uses the specified doc_id `0a21e7692759f40c37bdc33b0a2d1f38aa6de9a35efc6f83eac8d4b7f8a3ae1a` from the PRD
- **Good test isolation**: Each test properly patches dependencies and doesn't rely on shared state
- **Graceful dependency handling**: Tests skip gracefully if FastAPI or numpy are not available
- **Proper import structure**: Uses `from api_server.api_server import app` as specified in PRD
- **Comprehensive assertions**: Tests verify status codes, response structure, and key fields

---

## ⚠️ Improvements (low risk)

### Extra tests beyond PRD scope
**File:** test_api_server.py
**Issue:** The PRD specified exactly 9 tests, but the file contains 18 tests total. While additional tests (test_collections_single, test_collections_not_found, test_query_invalid_collection, test_query_top_k_validation, test_doc_not_found, test_context_invalid_collection, test_context_negative_chunk_index, test_manifest, test_query_stats, test_query_stats_updates) provide better coverage, they weren't requested and may indicate scope creep.
**Suggestion:** Consider whether these extra tests are necessary for the current sprint or should be moved to a follow-up story.
**Priority:** low

### Path inconsistency in docstring
**File:** test_api_server.py
**Issue:** The docstring says `cd /app/projects/zk-rag-v2` but the PRD acceptance criteria says `cd /app/zk-rag-v2`. This is a minor documentation inconsistency.
**Suggestion:** Align the docstring with the actual project path structure.
**Priority:** low

### MockPoint class could be simplified
**File:** test_api_server.py
**Issue:** The MockPoint class is defined but could be replaced with a simpler dict or namedtuple in most cases, reducing boilerplate.
**Suggestion:** Consider using `{"payload": {...}, "score": 0.9}` directly where the MockPoint structure isn't needed.
**Priority:** low

---

## 🔴 Must Rework (high risk or clearly wrong)

None identified. The implementation meets all PRD requirements and acceptance criteria.

---

## 🗑️ Deletion Candidates

None. All tests serve a purpose, even if some weren't explicitly requested.

---

## Rework Stories Suggested

None required.