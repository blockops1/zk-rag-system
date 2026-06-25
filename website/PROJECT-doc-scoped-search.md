# PROJECT: Document-Scoped Search

**Feature:** From the catalog page, click a button to navigate to a document-specific view that shows chunk 0 on load and accepts a text filter to search within that document.

---

## Background Audit

### What already exists

| Component | Current state | Assessment |
|-----------|--------------|------------|
| `GET /api/context?doc_id=X&chunk_index=0&window=0` | Works; returns all chunks for a doc | ✅ Reusable |
| `GET /api/context?query=Y` | `query` param is silently ignored by the backend | ❌ Doesn't work |
| `api2.js` `searchDocument()` | Sends `doc_id`, `top_k`, `query` — endpoint expects `chunk_index`, `window` | ❌ Broken, never worked |
| `api2.js` `fetchContextNav()` | Correctly calls `GET /api/context?doc_id=X&chunk_index=N&window=0` | ✅ Already correct |
| `app2.js` `initUrlState()` | Only reads `?q=` param; no `doc_id` handling | ❌ Missing |
| `catalog.html` doc cards | Only Archive.org link; no search button | ❌ Missing button |
| `renderer.js` `buildPassageCard()` | Builds a passage card from a chunk object | ✅ Reusable |
| `renderer.js` `buildDocGroupHtml()` | Builds doc header + multiple passage cards | ✅ Reusable |
| `event-handlers.js` chunk nav | Handles prev/next on `.chunk-nav-btn` | ✅ Already wired |

### Existing pieces we can reuse

- **`fetchContextNav(docId, 0, collection, 0)`** → returns all chunks for a document (backend scrolls entire doc)
- **`buildDocGroupHtml(docId, passages)`** → renders a document header + passage cards
- **`buildPassageCard(chunk)`** → renders a single passage card for nav replacements
- **`seedZkCacheFromResults()`** → seeds ZK cache from search results
- **`groupByDocId()`, `computeLoadedDocCount()`** → state helpers
- **`setState({ allResults, loadedDocCount })`** → state management
- **`wireZKBadges()`** → ZK badge wiring after render
- **`handleNavPlain()`, `handleNavProvenance()`** → prev/next chunk navigation (already wired in event-handlers)

---

## Architecture

```
catalog.html
  └─ "Search within this document" button (new)
       └─ window.location.href = '/?doc_id=' + docId

index.html (search page)
  └─ initUrlState() detects ?doc_id=
       └─ handleDocSearch(docId) — no query, fetches chunk 0 + all chunks
            ├─ fetchContextNav(docId, 0, collection, 0)  → all chunks
            ├─ setState({ allResults: chunks })
            ├─ renderResults()  → doc header + passage cards
            └─ searchInput keypress → handleDocSearch(docId, queryText)
                 └─ client-side text filter on allResults
```

**Key design decision:** No new API endpoint needed. The backend's `window=0` with `chunk_index=0` already returns ALL chunks for a document (it scrolls the entire doc). The frontend fetches all chunks once and does client-side text filtering. This is simple and works well for documents up to ~500 chunks. For very large documents this could be revisited, but military doctrine docs are typically <200 chunks.

---

## Changes

### 1. `api2.js` — Fix `searchDocument()` or add `fetchAllChunksForDoc()`

The existing `searchDocument()` is broken. Add a new focused function:

```javascript
/**
 * GET /api/context?doc_id=...&chunk_index=0&collection=...&window=0
 * Returns ALL chunks for a document (backend scrolls entire collection).
 *
 * @param {string} docId
 * @param {string} collection
 * @returns {Promise<Array>} sorted array of all chunks for this doc
 */
export async function fetchAllChunksForDoc(docId, collection = "army") {
  const params = new URLSearchParams({
    doc_id: docId,
    chunk_index: "0",
    collection,
    window: "0",
  });
  const resp = await fetch(`${CONTEXT_API_BASE}?${params}`);
  if (!resp.ok) throw new Error(`fetchAllChunksForDoc failed: ${resp.statusText}`);
  const data = await resp.json();
  return data.results || [];
}
```

`searchDocument()` is left as-is (broken but unused in our new feature; can be fixed separately if needed later).

### 2. `app2.js` — Add `handleDocSearch()` and wire `doc_id` in URL state

**Add import:**
```javascript
import { fetchAllChunksForDoc } from "./api2.js";
```

**Expose on window (already has window exports, add):**
```javascript
window.handleDocSearch = handleDocSearch;
```

**New action function:**
```javascript
/**
 * Handle document-scoped search: fetch all chunks for a doc, then filter.
 * If query is empty, shows all chunks (chunk 0 first).
 * If query is provided, client-side filters chunks by text match.
 *
 * @param {string} docId
 * @param {string} query  — optional text filter
 */
export async function handleDocSearch(docId, query = "") {
  _searchBtn.disabled = true;
  _searchBtn.textContent = query ? "Searching…" : "Loading…";
  _resultsContainer.innerHTML = buildLoadingHtml(
    query ? `Searching within document…` : "Loading document…"
  );

  try {
    let chunks = await fetchAllChunksForDoc(docId, "army");

    if (!chunks || chunks.length === 0) {
      _resultsContainer.innerHTML = buildEmptyHtml("No chunks found for this document.");
      return;
    }

    // Client-side text filter if query provided
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      chunks = chunks.filter((c) => (c.text || "").toLowerCase().includes(q));
    }

    if (chunks.length === 0) {
      _resultsContainer.innerHTML = buildEmptyHtml(`No passages match "${query}".`);
      return;
    }

    // Mark the active doc_id so prev/next nav knows which doc to stay within
    setState({ allResults: chunks, _activeDocId: docId });
    setState({ loadedDocCount: 1 }); // single doc, show all its chunks
    renderResults();
  } catch (err) {
    _resultsContainer.innerHTML = buildErrorHtml(err.message);
  } finally {
    _searchBtn.disabled = false;
    _searchBtn.textContent = "Search";
  }
}
```

**Update `initUrlState()`** (existing function, modify):
```javascript
function initUrlState() {
  const params = new URLSearchParams(window.location.search);
  const docId = params.get("doc_id");
  const q = params.get("q");

  if (docId) {
    // Document-scoped view: load the doc and optionally filter
    handleDocSearch(docId, q || "");
    return;
  }

  if (q) {
    // General search: pre-fill input only
    if (_searchInput) _searchInput.value = q;
  }
}
```

**Update `handleSearch()` and `handleSearchProvenance()`**: Add `_activeDocId = null` reset on regular search to prevent doc-scoped nav leaking into general searches.

### 3. `event-handlers.js` — Wire search input to doc-scoped mode

Update the Enter key handler to detect when a `doc_id` is active:

```javascript
// In initSearchHandlers():
searchInput?.addEventListener("keypress", async (e) => {
  if (e.key === "Enter") {
    const docId = getState()._activeDocId;
    if (docId) {
      // In doc-scoped mode: search within this document
      window.handleDocSearch?.(docId, searchInput.value);
    } else {
      // General search
      const provBtn = document.getElementById("searchProvenanceBtn");
      const provActive = provBtn && (
        provBtn.classList.contains("active") ||
        provBtn.getAttribute("aria-pressed") === "true"
      );
      if (provActive) {
        handleSearchProvenance(searchInput.value);
      } else {
        handleSearch(searchInput.value);
      }
    }
  }
});
```

### 4. `catalog.html` — Add "Search within this document" button

In the doc card's `.doc-links` div (line 192), add a search button alongside the Archive.org link:

```javascript
const searchBtn = `<a href="/?doc_id=${escapeHtml(doc.doc_id)}" class="search-btn">🔍 Search within this document</a>`;

card.innerHTML = `
  <div class="doc-info">
    <div class="doc-title">${escapeHtml(doc.title)}</div>
    <div class="doc-meta">${metaParts.map(m => `<span>${m}</span>`).join('')}</div>
  </div>
  <div class="doc-links">
    ${iaLink}
    ${searchBtn}
  </div>
`;
```

**CSS already exists** for `.search-btn` (line 73 in catalog.html). No new CSS needed.

### 5. `state.js` — Add `_activeDocId` to state

```javascript
let _state = {
  allResults: [],
  loadedDocCount: 0,
  lastSearchWasProvenance: false,
  zkCache: {},
  searchState: "IDLE",
  _activeDocId: null,  // NEW: doc_id when in document-scoped search mode
};
```

---

## UX Clarity

Three targeted changes so the user always knows they're in a document-scoped view:

### 1. Search input placeholder
When `doc_id` is active, change the placeholder text to indicate document-scoped mode:

```javascript
// In initUrlState() or renderResults(), when _activeDocId is set:
if (_activeDocId) {
  _searchInput.placeholder = "Search within this document…";
} else {
  _searchInput.placeholder = "Search military doctrine…";
}
```

### 2. Document-scoped banner above results
When `doc_id` is active, inject a banner above the results container showing the document title and a "back" link:

```javascript
// In renderResults(), before building results HTML:
function buildDocScopedBanner(docId, docTitle) {
  return `
    <div class="doc-scoped-banner">
      <span>🔍 Viewing: <strong>${escapeHtml(docTitle)}</strong></span>
      <a href="/">← Back to Search</a>
    </div>
  `;
}
```

CSS (add to index.html's `<style>`):
```css
.doc-scoped-banner {
  background: #16213e;
  border: 1px solid #2a4a8a;
  border-radius: 6px;
  padding: 10px 16px;
  margin-bottom: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.9em;
}
.doc-scoped-banner a { color: #64b5f6; text-decoration: none; }
.doc-scoped-banner a:hover { text-decoration: underline; }
```

The banner is inserted into `_resultsContainer.innerHTML` before the passage cards by `handleDocSearch()`.

### 3. Passage count label change
In doc-scoped mode, the doc group header in `buildDocGroupHtml()` changes from "N relevant passages" to "Viewing all N passages in this document". This is passed via a state flag or by detecting that all chunks are from the same doc (which is always true in doc-scoped mode).

```javascript
// In buildDocGroupHtml(), when rendered in doc-scoped mode:
const label = isDocScoped
  ? `Viewing all ${passages.length} passage${passages.length !== 1 ? "s" : ""} in this document`
  : `${passages.length} relevant passage${passages.length !== 1 ? "s" : ""}`;
```

Pass `isDocScoped` as an extra parameter or detect it from `groupByDocId(allResults).size === 1`.

---

## UX Flows

| Action | Result |
|--------|--------|
| Click "🔍 Search within this document" on a doc card | Navigate to `/?doc_id=xxx` → chunk 0 shown immediately in passage card |
| Type in search box + Enter (in doc mode) | Filter passage cards by text match, still within same doc |
| Click "← Prev Chunk" / "Next Chunk →" | Navigate through chunks of this doc (already works via `_handleNav`) |
| Click "← Back to Search" | Navigate to `/?` (empty search, fresh state) |
| Load `/?q=urban+operations` | Normal general search (unchanged) |

---

## File Changes

| File | Change |
|------|--------|
| `website/js/api2.js` | Add `fetchAllChunksForDoc()` function |
| `website/js/app2.js` | Add `handleDocSearch()`, update `initUrlState()`, expose on window |
| `website/js/event-handlers.js` | Wire Enter key to doc-scoped mode when `_activeDocId` is set |
| `website/js/state.js` | Add `_activeDocId: null` to initial state |
| `website/js/renderer.js` | Add `isDocScoped` flag to `buildDocGroupHtml()` for label change |
| `website/catalog.html` | Add "Search within this document" button to doc cards |
| `website/index.html` | Add `.doc-scoped-banner` CSS; wire placeholder text change |
| `api_server.py` | **No changes** — backend already returns all chunks with `window=0` |

---

## Sync (R730 → VPS)

After implementing on R730:
1. Sync website: `rsync -avz --delete -e "ssh -i ./.ssh/id_ed25519" .//website/ deruyter@militarymanuals.ai:.//website/`
2. No API sync needed (no backend changes)
3. Restart API if api_server.py was touched: `sudo systemctl restart zk-rag-api`

---

## Testing Checklist

- [ ] Catalog page: each doc card has "🔍 Search within this document" button
- [ ] Click button → navigate to `/?doc_id=xxx` → passage card appears immediately (no search button press needed)
- [ ] Document header shows correct title from registry/Qdrant
- [ ] **Doc-scoped banner appears** above results with document title and "← Back to Search" link
- [ ] **Search input placeholder reads** "Search within this document…" in doc-scoped mode
- [ ] **Passage count label** reads "Viewing all N passages in this document" instead of "N relevant passages"
- [ ] Prev/Next chunk navigation works within the document
- [ ] Type a term in search box + Enter → passage cards filter to matching text
- [ ] Clear search box + Enter → all chunks for the document shown
- [ ] Click "← Back to Search" or "← Back to Catalog" → land on empty search page, no doc-scoped state leaks
- [ ] **Placeholder reverts** to "Search military doctrine…" when back on general search
- [ ] `/?q=urban operations` (general search) still works as before with no banner and correct placeholder
- [ ] ZK badges / verify-on-chain still work in doc-scoped mode
