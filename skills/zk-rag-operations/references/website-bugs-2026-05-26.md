# Website Bugs Fixed — 2026-05-26

## 1. Collection search always shows "army" regardless of selected collection

**Symptom:** Selecting Marines and searching shows "📁 Searching collection: army".

**Root cause:** `handleCollectionSearch` and `handleCollectionSearchProvenance` in `app2.js` set `_searchScope: "COLLECTION"` but never set `_activeCollection` in state. The `getSearchScopeLabel()` helper in `state.js` fell back to `"army"` when `_activeCollection` was null.

**Fix in `app2.js`:**
```javascript
// In handleCollectionSearch (~line 184) — add _activeCollection to setState:
setState({
    allResults: results,
    _activeDocId: null,
    _activeCollection: collection,   // ← ADD THIS
    _searchScope: "COLLECTION",
});

// In handleCollectionSearchProvenance (~line 232) — same fix:
setState({
    allResults: results,
    _activeDocId: null,
    _activeCollection: collection,   // ← ADD THIS
    _searchScope: "COLLECTION",
});

// In handleSearch (full corpus, ~line 151) — clear it:
setState({ _activeCollection: null });
```

**Fix in `state.js` `getSearchScopeLabel()`:**
```javascript
// COLLECTION case: remove "army" fallback
label: `Searching collection: ${_state._activeCollection || "—"}`,
// default (CORPUS) case: remove hardcoded "army"
return { icon: "🔎", label: "Searching corpus" };
```

---

## 2. Catalog link in wrong position

**Symptom:** "Browse document catalog →" appears after the results section instead of under the intro paragraph.

**Fix in `index.html`:**
- Remove from line ~523 (after `#resultsContainer`)
- Add immediately after the `</div>` closing the `.hero` div (around line 488)

```html
<div class="hero">
  <p>Search and retrieve information...</p>
</div>

<div class="catalog-link">
  <a href="/catalog.html">Browse document catalog →</a>
</div>
```

---

## 3. H1 heading not linked to main page

**Symptom:** Clicking the site title does not reset to homepage with no results.

**Fix in `index.html`:**
```html
<!-- Before: -->
<h1>Military Manuals — Military Doctrine RAG API</h1>

<!-- After: -->
<h1><a href="/">Military Manuals — Military Doctrine RAG API</a></h1>
```

**CSS additions:**
```css
h1 a { color: inherit; text-decoration: none; }
h1 a:hover { text-decoration: underline; }
```

---

## 4. `handleSearchProvenance` — collection param hardcoded to "army"

**Symptom:** Full-corpus provenance search always searched "army" instead of all collections.

**Location:** `app2.js` `handleSearchProvenance` — `collection: "army"` hardcoded.

**Fix:** Use `getState()._activeCollection || undefined` so it passes `undefined` for full-corpus (no collection filter) and the correct collection name when in collection-scoped mode.

---

## 5. ZK Proof UX — Provenance Search Auto-Submit (RESOLVED 2026-05-26)

**Decision: Option A + auto-submit.**

| Search type | `zk_proof` on results | Badge initial state | After click |
|---|---|---|---|
| Search (no provenance) | absent | `"🔗 Generate Proof"` | generates + auto-submits to Kurier |
| Search with Provenance | present | `"📤 Submitted…"` → `"✅ Verified"` | shows proof modal |

**Implementation — `autoSubmitProvenanceResults(results)` in `app2.js`:**
```javascript
async function autoSubmitProvenanceResults(results) {
  const submissions = results
    .filter((r) => r.zk_proof && r.chunk_id)
    .map(async (r) => {
      const chunkId = r.chunk_id || r.id;
      const cached = getZkCache(chunkId);
      if (cached?.kurier_job_id) return; // skip if already submitted

      const { job_id } = await submitProof({
        proof_hex: r.zk_proof.proof_hex,
        public_inputs_hex: r.zk_proof.public_inputs_hex,
        vk_hex: r.zk_proof.vk_hex,
      });
      setZkCache(chunkId, { ...cached, ...r.zk_proof, kurier_job_id: job_id });

      // Badge may not exist yet (renderResults hasn't run) — defer via microtask:
      queueMicrotask(() => {
        const badge = document.getElementById(`zk-status-${CSS.escape(chunkId)}`);
        if (badge) {
          badge.textContent = "📤 Submitted…";
          badge.style.color = "#64b5f6";
          badge.style.cursor = "wait";
        }
      });
      _autoPollKurier(chunkId, job_id); // background polling → "✅ Verified" / "❌"
    });
  await Promise.allSettled(submissions);
}
```

**Call sites:** `handleSearchProvenance` and `handleCollectionSearchProvenance` — both call `autoSubmitProvenanceResults(results)` **before** `renderResults()` so badges are pre-wired with `kurier_job_id` on first paint.

**Badge state machine:**
- `"🔗 Generate Proof"` — no proof in cache (regular search, click to generate)
- `"📤 Submitted…"` — `kurier_job_id` in cache, polling in flight
- `"✅ Verified"` — `kurier_status === "verified"` or `"finalized"`
- `"❌"` — polling failed or Kurier returned error
- `"🔗 Not verified"` — proof in cache but no `kurier_job_id` (manual submit path)

---

## 5a. `handleSearchProvenance` called wrong API function

**Symptom:** "Search with Provenance" showed badges as `"🔗 Generate Proof"` — the provenance proofs were never being returned from the server.

**Root cause:** `handleSearchProvenance` called `searchChunks(query, { collection, top_k })` (the regular search API) instead of `searchChunksProvenance(query, { collection, top_k })`. The `searchChunksProvenance` function uses `POST /api/query-provable` which returns results with `zk_proof` attached; `searchChunks` uses `POST /api/query` which does not.

**Fix:** Replace `searchChunks` with `searchChunksProvenance` in `handleSearchProvenance`.

---

## 5b. Provenance button not re-enabled on error

**Symptom:** If "Search with Provenance" failed (network error, API error), the provenance button stayed disabled and kept the loading text forever.

**Root cause:** The `finally` block only reset `_searchBtn` (the regular "Search" button). The provenance button (`#searchProvenanceBtn`) is a separate element and was never touched.

**Fix in `handleSearchProvenance` `finally` block:**
```javascript
} finally {
    _searchBtn.disabled = false;
    _searchBtn.textContent = "Search";
    const provBtn = document.getElementById("searchProvenanceBtn");
    if (provBtn) { provBtn.disabled = false; provBtn.textContent = "Search with Provenance"; }
}
```

---

## 5c. `lastSearchWasProvenance` state flag controls banner suffix (RESOLVED 2026-05-26)

**What it does:** `getSearchScopeLabel()` in `state.js` appends `" — with Provenance"` to the banner label when `_state.lastSearchWasProvenance === true`.

**Every search handler must explicitly set this flag** — it is never auto-reset:
- Regular search handlers (`handleSearch`, `handleCorpusSearch`, `handleCollectionSearch`): set `lastSearchWasProvenance: false` in their `setState()` call.
- Provenance handlers (`handleSearchProvenance`, `handleCollectionSearchProvenance`): set `lastSearchWasProvenance: true` after `autoSubmitProvenanceResults()` completes.

---

## 6. Kurier submission rate limit too low (FIXED 2026-05-26)

**Symptom:** "Search with Provenance" returned 503 errors for many results — badges stayed `"🔗 Generate Proof"` and console showed `[autoSubmitProvenanceResults] submit failed for chunk <id>: 503 Service Temporarily Unavailable`.

**Root cause:** `prove_limit` zone in nginx was `rate=3r/m` with `burst=3`. A provenance search with ~10 results fires 10 parallel POSTs to `/api/provenance/submit`, instantly exceeding burst.

**Fix applied:**
```nginx
# /etc/nginx/conf.d/rag-rate-limit.conf
limit_req_zone $binary_remote_addr zone=prove_limit:10m rate=60r/m;  # was 3r/m

# /etc/nginx/conf.d/military-manuals-local.conf
limit_req zone=prove_limit burst=20 nodelay;  # was burst=3
```
Reload after changing: `sudo systemctl reload openresty`

---

## 7. `rag-api-local.service` missing EnvironmentFile (FIXED 2026-05-26)

**Symptom:** `POST /api/provenance/submit` returned `{"detail":"Kurier submission failed: Kurier API error 0: KURIE_API_KEY not set in environment"}` — Kurier submission silently failed for all results.

**Root cause:** The systemd service unit did not load `EnvironmentFile=<REPO>.env.systemd`, so `KURIE_API_KEY` was never set in the API server process.

**Fix applied to `<HOME>/.config/systemd/user/rag-api-local.service`:**
```ini
[Service]
EnvironmentFile=<REPO>.env.systemd
ExecStart=...
```

**To verify the key is loaded:**
```bash
PID=$(systemctl --user show rag-api-local.service -p MainPID --value)
grep -a "KURIE_API_KEY" /proc/$PID/environ | cut -d= -f2 | sed 's/./*/g'
# Expected: ******************************** (masked but non-empty = key is present)
```

**To restart after fixing:**
```bash
systemctl --user daemon-reload
systemctl --user restart rag-api-local.service
```

## 8. `joint` collection causes 400 on all docsearch operations (FIXED 2026-05-26)

**Symptom:** Loading a joint document in `docsearch.html` shows "Error: fetchAllChunksForDoc failed: Bad Request". The same document's context API call and provenance search both return 400.

**Root cause:** `joint` is a real Qdrant collection (confirmed by `curl http://localhost:6333/collections`), but it was absent from `KNOWN_COLLECTIONS` in `api_server.py`. Both `get_context` and `query_provable` validate strictly against this list.

**Also:** `get_context` was hard-rejecting unknown collections with a 400 instead of attempting cross-collection doc_id lookup. `query_provable` lacked any cross-collection resolution path when given a non-standard collection name.

**Fix applied (api_server.py, commit `a46e50f`):**
1. Added `"joint"` to `KNOWN_COLLECTIONS`
2. `get_context`: replaced hard 400 rejection of unknown collections with cross-collection doc_id resolution (iterates known collections to find which one holds the doc)
3. `query_provable`: same cross-collection resolution pattern added before the strict collection validation

**Debugging note:** When investigating 400 errors on `collection=joint`, first verify Qdrant directly:
```bash
curl http://localhost:6333/collections | python3 -c "import json,sys; [print(c['name']) for c in json.load(sys.stdin)['result']['collections']]"
```
