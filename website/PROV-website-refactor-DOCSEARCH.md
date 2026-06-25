# PRD: Option D — Per-Document Search from Catalog

## Context

When a user clicks "Search this document" in `catalog.html`, they are sent to `index.html` with a URL like:
```
/?q=doc:abc123 "FM 3-0 Operations"
```

`index.html` reads the `q` param and pre-fills the search input box — but **never actually fires the search**. The query sits in the input, the URL is correct, and nothing happens until the user manually presses Enter or clicks Search. This defeats the entire purpose of the "Search this document" link.

The root cause is architectural: the `q` parameter was designed for user-typed queries, not for machine-generated document-scoped searches.

---

## Option D: First-Class Per-Document Search

**Core idea:** Introduce a dedicated `doc_id` URL parameter for document-scoped searches. The website detects it on load and automatically searches within that document — no user action required. Keep `q=` for general queries (with optional `doc:` prefix for scoped general queries).

### Changes

#### 1. `catalog.html` — Change `q=` to `doc_id=`

Current (line 180):
```javascript
const searchUrl = `/?q=doc:${encodeURIComponent(doc.doc_id)} "${escapeHtml(doc.title.replace(/"/g, ''))}"`;
```

New:
```javascript
const searchUrl = `/?doc_id=${encodeURIComponent(doc.doc_id)}`;
```

The title is now communicated via the document metadata already in `registry.json` (the catalog API already returns it), so embedding it in the URL is unnecessary.

> **Note:** `catalog.html` is marked "Do NOT change" in PRD-website-refactor.md, but that PRD predates this feature request. This change is targeted and backwards-compatible — old `?q=` links still work via the fallback handler in Option D.

#### 2. `app.js` — Detect `doc_id` param on init and auto-search

In `initApp()` (or a new `initUrlState()` function called on page load):

```javascript
function initUrlState() {
  const params = new URLSearchParams(window.location.search);
  const docId = params.get('doc_id');
  const q = params.get('q');

  if (docId) {
    // Priority: doc_id param → auto-search within that document
    handleSearchDocument(docId, q || '');
    return;
  }

  if (q) {
    // Legacy: q param → pre-fill input (existing behavior)
    const input = document.getElementById('searchInput');
    if (input) input.value = q;
    // Don't auto-fire — preserve existing UX for manual queries
  }
}
```

#### 3. `api.js` — New `searchDocument(docId, query, options)` function

```javascript
/**
 * GET /api/context?doc_id=...&collection=...&query=...
 * Fetches chunks for a specific document, optionally filtered by text query.
 * Returns sorted by relevance if query provided, sequential if not.
 */
export async function searchDocument(docId, query = '', options = {}) {
  const params = new URLSearchParams({ doc_id: docId, collection: options.collection || 'army' });
  if (query) params.set('query', query);
  const resp = await fetch(`${CONTEXT_API_BASE}?${params}`);
  if (!resp.ok) throw new Error('searchDocument failed: ' + resp.statusText);
  const data = await resp.json();
  return data.results || [];
}
```

**API note:** The existing `GET /api/context` endpoint already accepts `doc_id` + `query` and returns matching chunks. The `searchDocument` JS function maps to this endpoint directly.

#### 4. `app.js` — New `handleSearchDocument(docId, query)` action

```javascript
export async function handleSearchDocument(docId, query = '') {
  _searchBtn.disabled = true;
  _searchBtn.textContent = 'Searching…';
  _resultsContainer.innerHTML = buildLoadingHtml('Searching within document…');
  saveCurrentSearch(query, false);

  try {
    const chunks = await searchDocument(docId, query, { collection: 'army' });
    if (!chunks || chunks.length === 0) {
      _resultsContainer.innerHTML = buildEmptyHtml();
      return;
    }

    seedZkCacheFromResults(chunks);
    setState({ allResults: chunks });

    const docGroups = groupByDocId(chunks);
    const initialDocCount = computeLoadedDocCount(docGroups);
    setState({ loadedDocCount: initialDocCount });

    renderResults();
  } catch (err) {
    _resultsContainer.innerHTML = buildErrorHtml(err.message);
  } finally {
    _searchBtn.disabled = false;
    _searchBtn.textContent = 'Search';
  }
}
```

#### 5. Backwards compatibility: parse `q` param for `doc:` prefix

If someone arrives with `?q=doc:abc123 "some text"` (old format), parse and redirect:

```javascript
function initUrlState() {
  const params = new URLSearchParams(window.location.search);
  const docId = params.get('doc_id');
  const q = params.get('q');

  if (docId) {
    handleSearchDocument(docId, q || '');
    return;
  }

  if (q) {
    // Check for legacy doc: prefix
    const match = q.match(/^doc:([a-f0-9]+)\s+(.*)$/i);
    if (match) {
      handleSearchDocument(match[1], match[2]);
      return;
    }
    const input = document.getElementById('searchInput');
    if (input) input.value = q;
  }
}
```

---

## UX Summary

| Scenario | URL | Behavior |
|----------|-----|----------|
| General search | `/?q=urban operations` | Pre-fill input, wait for Enter |
| Per-doc search (new) | `/?doc_id=abc123` | Auto-search within that doc, show all chunks |
| Per-doc + text (new) | `/?doc_id=abc123&q=patrol` | Auto-search within doc filtered to "patrol" |
| Legacy catalog link | `/?q=doc:abc123 "Title"` | Parse doc: prefix, treat as doc_id search |

---

## File Changes

| File | Change |
|------|--------|
| `website/catalog.html` | Change `q=doc:...` link to `doc_id=` param |
| `website/js/api.js` | Add `searchDocument()` function |
| `website/js/app.js` | Add `initUrlState()`, `handleSearchDocument()` |
| `website/js/event-handlers.js` | No change |
| `website/js/state.js` | No change |
| `website/js/renderer.js` | No change |

**API changes:** None. The `GET /api/context` endpoint with `doc_id` + `query` params already exists and works.

---

## Sync (R730 → VPS)

After implementing on R730:
1. Sync website: `rsync -avz --delete -e "ssh -i ..." .//website/ ...:/data/rag/docs/`
2. No API sync needed (no backend change)
3. Verify: click "Search this document" in catalog, confirm auto-search

---

## Testing

1. **Catalog link click**: Navigate to catalog, click "Search this document" on any doc → search results appear automatically
2. **Empty query**: `/?doc_id=abc123` → shows all chunks in document (up to top_k default of 5)
3. **With query**: `/?doc_id=abc123&q=patrol` → shows only chunks matching "patrol"
4. **Backwards compat**: `/?q=doc:abc123 "patrol"` → still works (legacy parser)
5. **General query**: `/?q=urban operations` → pre-fills input (existing behavior unchanged)

---

## Dependencies

- No API changes required
- `catalog.html` change is targeted (one line)
- VPS sync via standard rsync (website only, no API code)
