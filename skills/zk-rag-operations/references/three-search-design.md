# Three-Search Design (2026-05-27)

## Main Page (`index2.html`)

Two separate search sections:

1. **Corpus search** — `POST /api/query` with `collection="*"`
   - Input: `#corpusSearchInput`
   - Button: `#corpusSearchBtn`
   - Scope label: `#corpusScopeLabel`

2. **Collection search** — `GET /api/collection/search?collection=army&q=...&top_k=10`
   - Input: `#collectionSelect` (dropdown: army, navy, marines, coast_guard, air_force, other)
   - Query input: `#collectionSearchInput`
   - Button: `#collectionSearchBtn`
   - Scope label: `#collectionScopeLabel`

## Document Search

- **Page**: `docsearch.html` (dedicated, not on main page)
- **Reached from**: catalog.html via "Search within this document" link
- **URL params**: `doc_id`, `collection`, `title`, `branch`
- **API**:
  - `GET /api/context?doc_id=...&collection=...&chunk_index=0&limit=N` — sequential N chunks (limit=0 uses window-based centering)
  - `POST /api/query?doc_id=...` — semantic search within a document
  - `POST /api/query-provable?doc_id=...` — semantic search with ZK proofs (provenance mode)
- **Back link**: "← Back to Search" → main page

### docsearch.html Search Modes

| Mode | Button | Banner | ZK Badges |
|------|--------|--------|-----------|
| Initial load (no query) | — | `📄 Searching within document` | None |
| Regular search | `#searchBtn` | `📄 Searching within document` | `🔗 Generate Proof` |
| Provenance search | `#searchProvenanceBtn` (gold) | `📄 Searching within document — with Provenance` | `📤 Submitted…` |

### Scope Banner Rendering
`renderResults()` in `app2.js` builds the scope banner as inline HTML inside `#resultsContainer`. There is no separate `#scopeBanner` div that gets updated via JavaScript — the banner is part of the rendered result HTML. The `#searchScopeLabel` element (updated via `_searchScopeEl` in `app2.js`) is for `index.html` Section 1 only and does not apply to docsearch.

### Event Handler Architecture for docsearch
`docsearch.html` uses TWO event registration sites for the provenance button:
1. **Inline `<script>` in `docsearch.html`** — registers `handleDocSearchProvenance` with `stopPropagation()` to prevent bubbling
2. **`event-handlers.js` `initSearchHandlers()`** — also wires `#searchProvenanceBtn` but only as fallback (calls `handleDocSearch`)

The inline registration wins due to `stopPropagation()`. When adding new docsearch handlers, always use `stopPropagation()` on the inline registration to prevent conflicts with the global `initSearchHandlers()`. State fields that must be set: `_activeDocId`, `_activeCollection`, `_searchScope="DOCUMENT"`, `lastSearchWasProvenance`.

## Catalog

- "Search within this document" links → `docsearch.html?doc_id=...&collection=...&title=...&branch=...`
- Previously used `/?doc_id=...` (wrong)

## New API Endpoint

- `GET /api/collection/search` at `shared/api_server.py` line ~1098
- Semantic vector search within a single Qdrant collection
- Uses `_encode_semaphore` + `_encode_texts_sync` for query embedding
- `scroll` across all docs in collection + cosine similarity scoring
- 10-minute cache
- Returns `{"results": [...], "total": N}`
