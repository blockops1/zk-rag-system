# PRD: Website Refactor — Modular RAG + Provenance UI

## Context

The current `index.html` is a ~1640-line monolith. All logic — API calls, state management, HTML rendering, event handling — is in a single file as anonymous functions and global variables. This causes:

1. **Load more is broken** — `performSearch()` resets state on every call. Clicking "Load More" doesn't re-call `performSearch`, but the bug is the same root cause: state is entangled with rendering in a way that makes it impossible to add more docs without re-fetching the original query.
2. **`query-provable` conflates search + proof generation** — The website can't request chunks first and prove them later. Provenance navigation requires re-running a full vector search every time the user wants to see next/prev provenance.
3. **`_buildDocGroupHtml` and rendering logic are inline** — impossible to test in isolation, hard to modify without breaking something else.

The fix is a clean separation: search returns chunks, prove generates proofs on demand, and the UI state/renderer are modular components.

---

## Architecture

```
index.html
  └─ <script type="module" src="js/api.js">
  └─ <script type="module" src="js/state.js">
  └─ <script type="module" src="js/renderer.js">
  └─ <script type="module" src="js/app.js">
  └─ <script type="module" src="js/event-handlers.js">
  └─ <script>
       // ZK proof submenu + verification UI (uses api + state, kept minimal)
     </script>
```

Each module has a single responsibility:

| Module | Responsibility |
|--------|---------------|
| `api.js` | All HTTP calls to the API. Returns JSON. Zero DOM or state. |
| `state.js` | In-memory state: `_allResults`, `_loadedDocCount`, `_zkCache`, provenance mode. Provides pure getter/setter functions. |
| `renderer.js` | Builds HTML strings. Pure function: `state → HTML`. No `fetch`, no side effects. |
| `app.js` | Orchestrates: calls api → updates state → calls renderer. Wires events to action functions. |
| `event-handlers.js` | All click/keypress handlers. Dispatches to app actions. |

---

## Data Flow

```
User action
  └─ event-handler.js
       └─ app.js action function (e.g., handleSearch(query))
            ├─ api.js.fetchChunks(query, provenance)
            │    └─ POST /api/query  OR  POST /api/query-provable
            ├─ state.js.setResults(results, provenance)
            ├─ renderer.js.renderAll(state)
            │    └─ builds HTML from state
            └─ DOM update (app.js does the .innerHTML assignment)

User clicks "Load More"
  └─ event-handler.js
       └─ app.js.handleLoadMore()
            ├─ state.js.incrementLoadedDocCount(PAGE_SIZE)
            ├─ renderer.js.appendMoreDocs(state)
            │    └─ appends next doc group HTML to existing DOM
            └─ DOM: insertAdjacentHTML (no full rebuild)

User clicks "Next + Provenance"
  └─ event-handler.js
       └─ app.js.handleNavProvenance(docId, chunkId, collection, direction)
            ├─ api.js.fetchContextNav(docId, chunkIndex, collection, 0)
            ├─ api.js.fetchZKProof(docId, chunkId, collection)
            ├─ state.js.cacheZKProof(chunkId, proofData)
            ├─ renderer.js.buildPassageCard(chunk, zkProof)
            └─ DOM: replace card in-place

User clicks "Verify on Chain"
  └─ event-handler.js (inline in ZK submenu, delegates to api.js)
       └─ api.js.submitProof(cached)
       └─ api.js.pollStatus(jobId)
       └─ state.js.cacheVerificationResult(chunkId, result)
```

---

## State Shape (state.js)

```javascript
// _zkCache[chunkId] = { proof_hex, vk_hex, public_inputs_hex, public_inputs,
//                       evm_block_number, job_id, explorer_url, tx_hash, ... }
export const _zkCache = {};

export let _allResults = [];       // full array of chunk results from last query
export let _loadedDocCount = 0;   // how many doc groups currently shown
export let _lastSearchWasProvenance = false;
export let PAGE_SIZE = 3;         // docs per load-more page

export function setResults(results, provenance) { ... }
export function incrementLoadedDocCount(n) { ... }
export function cacheZKProof(chunkId, proofData) { ... }
export function cacheVerificationResult(chunkId, result) { ... }
export function getChunkById(chunkId) { ... }  // find in _allResults
```

---

## API Layer (api.js)

All functions return `Promise<json>`. Auth header is injected automatically.

```javascript
// Search — returns plain chunks or provenance-annotated chunks
export async function searchChunks(query, { top_k=10, collection='army', provenance=false })
  → POST /api/query-provable  (if provenance=true)
  → POST /api/query           (if provenance=false)

// Fetch adjacent chunk(s) for prev/next navigation (no proof)
export async function fetchContextNav(docId, chunkIndex, collection, windowSize=0)
  → GET /api/context?doc_id=...&chunk_index=...&collection=...&window=...

// Generate ZK proof for a specific chunk
export async function fetchZKProof(docId, chunkId, collection)
  → POST /api/provenance/prove  { doc_id, chunk_id, collection }

// Submit proof to Kurier for on-chain verification
export async function submitProof({ proof_hex, public_inputs_hex, vk_hex })
  → POST /api/provenance/submit

// Poll verification status
export async function getProofStatus(jobId)
  → GET /api/provenance/status/{job_id}
```

---

## Rendering (renderer.js)

Pure functions — no side effects, no fetch, no DOM access.

```javascript
// Build full results HTML: doc groups + load-more button
export function buildResultsHtml(state)
  → string (HTML)

// Build one doc group's HTML (header + passage cards)
export function buildDocGroupHtml(docId, passages, allResults)
  → string (HTML)

// Build a single passage card HTML
export function buildPassageCard(chunk, zkProof, isProvenanceSearch)
  → string (HTML)

// Append next doc group HTML (for load-more, no full rebuild)
export function buildLoadMoreHtml(remainingDocCount)
  → string (HTML)

// Wire a newly inserted passage card's ZK button and image loaders
// (DOM side-effect function — called after insertAdjacentHTML)
export function wireNewCard(cardElement, chunk) { ... }
```

---

## App Actions (app.js)

Orchestration functions. Each is called by an event handler.

```javascript
export async function handleSearch(query, provenance) { ... }
export async function handleLoadMore() { ... }
export async function handleNavProvenance(docId, chunkId, chunkIndex, collection, direction) { ... }
export async function handleNavPlain(docId, chunkId, chunkIndex, collection, direction) { ... }
export async function handleVerifyOnChain(chunkId, menuElement) { ... }
```

---

## Bug Fixes Embedded in Refactor

### Load More Bug (current symptom: reloads first 3 docs)

**Root cause:** `performSearch()` is a monolithic function that resets `_loadedDocCount = 0` and `_allResults = results` every time it runs. The load-more button's click handler calls `performSearch()` again in some code paths, which wipes state.

**Fix:** `handleLoadMore()` only calls `state.incrementLoadedDocCount(PAGE_SIZE)` and `renderer.appendMoreDocs()`. It does NOT call `searchChunks()` again. The `_allResults` array is preserved.

### Prev/Next Navigation (current: falls back to non-provenance)

**Root cause:** `fetchZKProof()` was calling a non-existent endpoint. That endpoint now exists (`POST /api/provenance/prove`).

**Fix:** No change needed to the nav flow — it already calls `fetchZKProof` which now maps to the working endpoint.

### Load More Rewrites Entire Results HTML

**Root cause:** After loading more docs, the code calls `resultsContainer.innerHTML = html` which destroys all event listeners on existing DOM nodes (ZK buttons, etc.).

**Fix:** `renderer.appendMoreDocs()` uses `insertAdjacentHTML` to append new doc groups without touching existing DOM. `wireNewCard()` is called on each new card to attach ZK button listeners. Existing listeners on already-rendered cards are untouched.

---

## Load More: Correct Step-by-Step

```
1. User clicks "Load More"
2. handleLoadMore():
   a. docGroups = buildGroupsFromResults(_allResults)
   b. _loadedDocCount += PAGE_SIZE
   c. newGroups = docGroups.slice(lastShown, _loadedDocCount)
   d. html = newGroups.map(g → buildDocGroupHtml(...)).join('')
   e. DOM: resultsContainer.insertAdjacentHTML('beforeend', html)
   f. wireNewCard() on each new .passage-card
3. Update "X more docs" counter — replace the load-more button's text
4. If _loadedDocCount >= docGroups.size → remove load-more button
```

---

## Testing Strategy

### JS Unit Tests (vitest or jest)
- `renderer.buildDocGroupHtml` — verify correct HTML for 1 doc, 2 docs, 0 passages
- `renderer.buildPassageCard` — verify zkProof button appears when passed, absent when null
- `state.setResults` / `state.incrementLoadedDocCount` — verify counter bounds
- `state.cacheZKProof` / `state.getChunkById` — verify retrieval
- `api.searchChunks` response parsing — mock fetch, verify correct endpoint called per provenance flag

### API Tests (existing pytest suite)
- No changes to API endpoints required
- Existing tests cover: `/api/query`, `/api/query-provable`, `/api/context`, `/api/provenance/prove`, `/api/provenance/submit`, `/api/provenance/status`

### Integration Tests
- `test_website_load_more` (new): open the page, search, load more, verify correct doc groups shown, prev/next nav works, ZK proof loads
- `test_website_provenance_nav` (new): search with provenance, click next+provenance, verify proof attaches

---

## Constraints

- **Do NOT change API endpoints** — `POST /api/query`, `POST /api/query-provable`, `GET /api/context`, `POST /api/provenance/prove`, `POST /api/provenance/submit`, `GET /api/provenance/status/{job_id}` all stay as-is
- **Do NOT change `catalog.html`** — separate page, out of scope
- **Do NOT change API response shapes** — website must adapt to existing JSON format
- **Auth header** — injected into all API calls via `apiKey` global (existing pattern)
- **Load More state** — `_loadedDocCount` and `_allResults` must survive across renders (state module, not global vars)
- **ZK proof cache** — `_zkCache` must survive across load-more appends
- **Provenance search** — `query-provable` path stays; website splits search from proving only for prev/next nav

---

## File Inventory

| File | Status | Notes |
|------|--------|-------|
| `website/index.html` | Modified | Remove all inline JS functions. Import 5 module scripts. Keep ZK submenu HTML/CSS as inline. |
| `website/js/api.js` | Created | All `fetch()` calls. Zero DOM. Zero global state. |
| `website/js/state.js` | Created | `_allResults`, `_loadedDocCount`, `_zkCache`, `PAGE_SIZE`, getter/setters. |
| `website/js/renderer.js` | Created | Pure HTML builder functions. |
| `website/js/app.js` | Created | Orchestration actions: handleSearch, handleLoadMore, handleNavProvenance, etc. |
| `website/js/event-handlers.js` | Created | All event listener registrations. Delegates to app actions. |
| `website/js/api.test.js` | Created | Unit tests for api.js |
| `website/js/state.test.js` | Created | Unit tests for state.js |
| `website/js/renderer.test.js` | Created | Unit tests for renderer.js |
| `tests/test_website_integration.py` | Created | Playwright tests for load-more and provenance nav |

---

## Acceptance Criteria

1. `python -m pytest tests/test_api_server.py` — all 25 tests pass (no API changes)
2. `python -m pytest tests/test_api_integration.py` — all 22 tests pass (no API changes)
3. `vitest run website/js/` — all JS unit tests pass
4. Searching and clicking "Load More" appends the next doc group without destroying existing ZK buttons or re-fetching results
5. Clicking "← Prev + Provenance" or "Next + Provenance →" shows a ZK proof button on the new card
6. "🔗 ZK Proof" button opens submenu, "Download" saves `zk_proof_{doc_id}_{leaf}.json`, "Verify on Chain" submits to Kurier and shows verified/failed state
7. After load-more, prev/next navigation works within the newly loaded docs
8. All existing API endpoints respond with the same JSON shape as before the refactor
