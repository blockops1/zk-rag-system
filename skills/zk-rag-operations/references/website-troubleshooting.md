# Website Troubleshooting Reference

## Known Bugs and Fixes

### Images returning 503 after provenance search (R730 / production only)
**Symptoms:** Regular doc search works; provenance search results show 0 images and 503 errors for `/api/images/{docId}/{pageNum}`. VPS (localhost:8100) never has this issue.

**Root cause:** Two compounding factors:
1. `fetchImageList` in `api2.js` had no retry logic — a 503 returned immediately
2. nginx `limit_req zone=rag_limit burst=10 nodelay` on `/api/images/` — 10+ simultaneous requests (one per provenance result, ~3 images each) exhaust the burst instantly; excess get 503

**Fix (both required):**

**JS side — add 503 retry to `fetchImageList` in `api2.js`:**
```javascript
async function fetchImageList(docId, pageNum, retries = 3) {
    for (let attempt = 0; attempt <= retries; attempt++) {
        const response = await fetch(url);
        if (response.status === 503 && attempt < retries) {
            await new Promise(r => setTimeout(r, 100 * (attempt + 1)));
            continue;
        }
        return response;
    }
}
```
Only retry on 503 — other HTTP errors should return immediately.

**nginx side — increase burst on R730** (file: `/etc/nginx/conf.d/<PUBLIC_HOST>.conf`):
Change `burst=10` → `burst=30` in all three `rag_limit` locations, or add a dedicated location before the catch-all:
```nginx
location = /api/images {
    limit_req zone=rag_limit burst=30 nodelay;
    proxy_pass http://127.0.0.1:8100;
}
```
`burst=30` handles 10 provenance results × ~3 images each = ~30 concurrent requests without 503s.

**⚠️ Always reload after nginx config changes:**
```bash
ssh -i ~/.ssh/id_ed25519 deruyter@<PUBLIC_HOST> "sudo systemctl reload openresty"
```
**Never use `nginx -s reload` or `openresty -s reload`** — OpenResty is controlled via systemd. Only `systemctl reload openresty` works correctly.
The `sudo` requires either the user's password or pre-approved sudo access. Confirm before running.

**Verification:**
```bash
# 1. Check nginx config is syntactically valid
ssh -i ~/.ssh/id_ed25519 deruyter@<PUBLIC_HOST> "sudo nginx -t"

# 2. Reload (requires sudo password or pre-approved sudo)
ssh -i ~/.ssh/id_ed25519 deruyter@<PUBLIC_HOST> "sudo nginx -s reload"

# 3. Run Playwright test — provenance search should yield images with 0 503s
node -e "
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on('response', r => { if (r.status() === 503 && r.url().includes('/api/images')) errors.push(r.url()); });
  await page.goto('https://<PUBLIC_HOST>');
  await page.fill('#searchInput', 'army tactics');
  await page.click('#searchProvenanceBtn');
  await page.waitForTimeout(3000);
  console.log('503 errors:', errors.length, errors.slice(0,3));
  await browser.close();
})();
"
```

**Why VPS had this issue too (2026-05-27):** VPS had no `limit_req` on `/api/images/` initially, but it also had the wrong rate limit zone rates and no burst directive. After the fix, VPS now matches R730: `limit_req zone=rag_limit burst=30 nodelay` with `rag_limit` at 120r/m. VPS and R730 are now consistent.

### Browser shows stale JS after editing app2.js or api2.js
The browser caches JS files. Hard refresh with Ctrl+Shift+R (Win/Linux) or Cmd+Shift+R (Mac), or disable cache in DevTools Network tab. Symptoms: `handleCollectionSearchProvenance is not defined` or event listeners not firing. Playwright headless does not have this issue — it always fetches fresh.

### Provenance search shows "Search Results" instead of "Search with Provenance Results"
`getSearchScopeLabel()` in `state.js` appends `" — with Provenance"` to the banner when `_state.lastSearchWasProvenance === true`. Each handler in `app2.js` must explicitly set this flag in its `setState()` call — it is never auto-reset:
- `handleSearch` / `handleCollectionSearch` → `setState({ lastSearchWasProvenance: false })`
- `handleSearchProvenance` / `handleCollectionSearchProvenance` → after `autoSubmitProvenanceResults()`, `setState({ lastSearchWasProvenance: true })`

If the banner text is wrong, check that the provenance handler is setting the flag correctly in `app2.js`. Full details in `references/website-bugs-2026-05-26.md` §5c.

## Debugging Provenance Search (Empty Results)

**Symptoms:** `/api/query-provable` returns `{"chunks":[],"proofs":{},"total":0}` but `/api/query` returns results. Vector search works — proofs silently fail.

**Step 1 — Check api_server.log:**
```bash
ssh deruyter@<PUBLIC_HOST> "tail -50 <DATA>logs/api_server.log | grep query-provable"
```
Look for `ZK proof failed for [chunk_id]` — the error reveals the root cause. Common patterns:
- `[Errno 2] No such file or directory: '/data/rag/zk-circuit/target/release/prove-bin'` → binary path mismatch
- Proof generation exceptions → binary crash or missing inputs

**Step 2 — Binary path mismatch (most common):**

Systemd services load `.env` at startup, not dynamically. If `ZK_PROVE_BINARY` in `.env` was updated but the service wasn't restarted, the running process has a stale path.

Check what the running process expects vs. what exists:
```bash
ssh deruyter@<PUBLIC_HOST> "grep ZK_PROVE /home/deruyter/rag/.env"
ssh deruyter@<PUBLIC_HOST> "ls -la /data/rag/zk-circuit/prove-bin"
ssh deruyter@<PUBLIC_HOST> "ls -la /data/rag/zk-circuit/target/release/"
```

**⚠️ CRITICAL PITFALL — Inline `Environment=` overrides `EnvironmentFile=`:**
Systemd `Environment=` directives in the `[Service]` block take precedence over `EnvironmentFile=`. If both the unit file AND the .env file set the same variable (e.g., `ZK_PROVE_BINARY`), the inline value wins and the .env update has no effect until the unit file is also updated.

Always check BOTH to avoid chasing stale .env changes:
```bash
# What the running service actually has (merged view — authoritative)
ssh deruyter@<PUBLIC_HOST> "systemctl show zk-rag-api.service --property=Environment"

# What the unit file itself declares (may differ from .env)
ssh deruyter@<PUBLIC_HOST> "systemctl cat zk-rag-api.service | grep Environment"
```

If `systemctl show` shows a different path than `.env` contains, the unit file has an inline override. Fix: patch the unit file's inline `Environment="ZK_PROVE_BINARY=..."` to the correct path, then `daemon-reload && restart`.

**No-restart fix (symlink):**
```bash
ssh deruyter@<PUBLIC_HOST> "mkdir -p /data/rag/zk-circuit/target/release && ln -sf /data/rag/zk-circuit/prove-bin /data/rag/zk-circuit/target/release/prove-bin"
```
Symlink at the expected (wrong) path → actual binary. Transparent to the running process; proofs work immediately.

**Permanent fix (restart, after correcting unit file):**
```bash
ssh deruyter@<PUBLIC_HOST> "sudo systemctl daemon-reload && sudo systemctl restart zk-rag-api.service"
```
Do during low-traffic window. After restart: `POST /api/query-provable` should return chunks with `proof_hex` populated.

**Step 3 — Verify:**
```bash
curl -s -X POST http://localhost:8100/api/query-provable \
  -H "Content-Type: application/json" \
  -d '{"query":"tank","top_k":3,"collection":"army"}' | jq '.total, .proofs'
```
`total > 0` and `proof_hex` values present = working.

**Step 4 — Public endpoint test:**
```bash
curl -s -X POST https://<PUBLIC_HOST>/api/query-provable \
  -H "Content-Type: application/json" \
  -d '{"query":"tank","top_k":3,"collection":"army"}' | jq '.total'
```

## MAX_CONCURRENT Tuning
The API server limits concurrent ZK proof encodes via `_encode_semaphore = Semaphore(4)` in `shared/api_server.py`. Provenance searches that hit this limit return HTTP 503. Increase `MAX_CONCURRENT` in api_server.py and restart the service if provenance searches are routinely failing under load.

## Doc-Scoped Search (docsearch.html) — Implementation Notes

All gaps documented here were resolved 2026-05-26 (commits `13522cf`, `a46e50f`, and subsequent patches). These notes capture what was built for future reference.

**What was built:**
1. `GET /api/context?limit=N` — new limit param returns N consecutive chunks (bypasses window centering)
2. `POST /api/query-provable` accepts optional `doc_id` — scopes provenance search to single document
3. `fetchAllChunksForDoc(..., limit)` in `api2.js` passes limit to backend
4. `searchDocProvenance(docId, collection, query, topK)` in `api2.js` — provenance search for single doc
5. `handleDocSearch(docId, collection, query)` — collection now passed; shows 5 initial chunks when query empty
6. `handleDocSearchProvenance(docId, collection, query)` — new; calls `searchDocProvenance`, auto-submits to Kurier
7. `event-handlers.js` updated to pass `_activeCollection` from state to `handleDocSearch`
8. `docsearch.html` — gold provenance button, correct collection passing, 5 initial chunks on load, full window bindings, site header, catalog link

**Final fixes applied 2026-05-26 (Rolf manual test "this looks great!"):**
- `window.fetchImageList` bound in `docsearch.html` — images now load
- `await autoSubmitProvenanceResults(chunks)` — ZK badges update from "Submitted..." to "Verified"
- `handleDocSearchProvenance` wired to provenance button click — actually triggers provenance search
- `.provenance-btn` CSS `#f0a500` (bright gold) — button matches main page buttons
- `<h1>` site header + catalog link added to `docsearch.html` header

**Key implementation pattern — dual event registration:**
`docsearch.html` inline `<script>` registers handlers with `stopPropagation()` to override `initSearchHandlers()` defaults. The inline registration fires first and stops propagation, so global handlers never fire. Pattern:
```javascript
provBtn?.addEventListener('click', (e) => {
  e.stopPropagation(); // prevents event-handlers.js from also firing
  handleDocSearchProvenance(docId, collection, query);
});
```

**State fields required for docsearch:**
```javascript
setState({
  _searchScope: "DOCUMENT",
  _activeDocId: docId,
  _activeCollection: collection,  // needed for event-handlers to pass correct collection
  lastSearchWasProvenance: false, // or true for provenance mode
});
```

**Catalog Playwright note:**
The catalog page (`catalog.html`) renders entirely via JavaScript — no `<table>` element exists. Playwright tests that wait for `table` will time out. Get doc IDs directly from `GET /api/catalog?collection=marines` for test setup.

## Navigation Completeness Rule
When adding new search modes, both `init()` event wiring AND `renderer.js` result-display functions must be updated together. A common mistake is adding the handler in app2.js but forgetting to update the rendering path that clears and repopulates the results container.

**State synchronization is part of navigation completeness.** A handler that updates `_searchScope` but not `_activeCollection` causes incorrect scope labels (e.g., showing "Searching collection: army" instead of the actual collection). Always check that `setState()` calls in new handlers include all relevant state fields used by `getSearchScopeLabel()` in `state.js`.

## Provenance Button Missing from Main Page
Check `index.html` for `searchInput`/`searchBtn`/`searchProvenanceBtn` in Search Section 1. A prior refactor renamed these to `corpusSearchInput`/`corpusSearchBtn` but `app2.js` event wiring still expects the original IDs. Fix: restore those IDs in the HTML, or update app2.js to use the new names.

## Section 2 (Collection) Provenance Button Has No Amber Color
CSS rule for `#collectionSearchProvenanceBtn` is missing from `index.html` styles. Add after the `#searchProvenanceBtn` rule:
```css
#collectionSearchProvenanceBtn { background: #f0a500; color: #1a1a2e; }
#collectionSearchProvenanceBtn:hover { background: #d4940a; }
```
