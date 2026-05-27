# ZK-RAG Common Issues & Operational Detail

## Qdrant API returns 0 collections but meta.json shows collection definitions
**Symptom:** `curl http://127.0.0.1:6333/collections` returns `{"collections":[]}` but `<DATA>qdrant/meta.json` shows army, navy, marines, other with vector configs.

**Possible causes:**
1. **portalocker conflict** — a local-mode client held the lock during your API call; retry
2. **Wrong Qdrant process** — check `ps aux | grep qdrant` and `ss -tlnp | grep 6333` to confirm which PID owns the port; a different Qdrant process may be serving on 6333 while this storage belongs to another instance
3. **Storage backend mismatch** — data was written by a SQLite-configured Qdrant but current config uses LMDB (or vice versa)
4. **Collections defined but never upserted** — `meta.json` records collection *definitions* (vector size, distance), not actual points. If Pipeline G never ran successfully, collections exist as empty shells with 0 points

**To check actual point counts (server must be running — use network mode):**
```python
from qdrant_client import QdrantClient
client = QdrantClient(url='http://127.0.0.1:6333')  # network mode while server runs
for coll in ['army', 'navy', 'marines', 'other']:
    info = client.get_collection(coll)
    print(f"{coll}: {info.points_count}")
```

### Website — Document-Scoped Search (2026-05-21)

**Feature:** Catalog "Search within this document" button → `/?doc_id=<doc_id>` → search page shows first chunk, search box filters within that doc.

**Implementation — no API changes needed.** `GET /api/context?chunk_index=0&window=0` returns the full document. Frontend fetches all chunks once, then filters client-side.

**Files changed:**
- `api2.js` — `fetchAllChunksForDoc(docId, collection)` calls the context endpoint with `window=0`
- `app2.js` — `handleDocSearch(docId, query)` + `initUrlState()` detects `?doc_id=` on load
- `state.js` — `_activeDocId` gates search box routing
- `renderer.js` — `buildDocGroupHtml(docId, passages, isDocScoped)` changes label/banner when doc-scoped
- `event-handlers.js` — Enter key + buttons check `_activeDocId` and route to `handleDocSearch`
- `catalog.html` — "🔍 Search within this document" button on each doc card
- `index.html` — `.doc-scoped-banner` CSS

**Bugs found and fixed during implementation:**
1. `searchDocument()` in api2.js was permanently broken — replaced with `fetchAllChunksForDoc()`
2. Search button fired `handleSearch` (general) even when `_activeDocId` was set — fixed routing
3. ZK badge initial text was "Not verified" — changed to "Generate Proof"
4. `fetchZKProof` silently swallowed HTTP errors — badge stuck at "⏳ Generating…" — fixed
5. "⏳ Generating…" never updated after success with no `kurier_job_id` — now shows "✅ Proof ready"

**ZK badge state machine:** See "ZK Badge State Machine" section above.

## Pipeline G Batch Mode

Pipeline G `--batch` mode eligibility checks `emitted_testnet` OR `emitted_mainnet`. A doc that has a valid mainnet emission but a failed testnet emission IS eligible — the two networks are checked independently.

**Eligibility logic (pipeline_g.py `is_doc_eligible()`):**
```python
emitted_testnet = registry_entry.get("emitted_testnet", {})
emitted_mainnet = registry_entry.get("emitted_mainnet", {})
testnet_ok = emitted_testnet.get("status") == "emitted"
mainnet_ok = emitted_mainnet.get("status") == "emitted"
if not testnet_ok and not mainnet_ok:
    return {"eligible": False}  # genuinely not emitted anywhere
# Otherwise eligible — accepts either network
```

**If Qdrant is wiped but registry still has emission records:**
- Reset ALL docs with `status` set (not just `ingested`) back to `None`:
```python
import json
with open("<DATA>registry.json") as f:
    reg = json.load(f)
reset = [d for d in reg["documents"] if d.get("status")]
for d in reset:
    d["status"] = None
    json.dump(reg, f, indent=2)
print(f"Reset {len(reset)} docs")
```
- This handles docs with `status=ingested`, `status=failed`, `status=skipped`, etc.
- Do NOT re-run Pipeline F — emission records (`emitted_mainnet`) are still valid
- Re-run Pipeline G — it will re-ingest all eligible docs

**Dry run before any real run:**
```bash
cd <REPO>pipeline_g
python3 pipeline_g.py --batch --dry-run
# Check "Eligible docs: N" and verify N makes sense (604 total - genuinely non-emitted docs)
```

**Run for real:**
```bash
cd <REPO>pipeline_g
python3 pipeline_g.py --batch  # no --dry-run
```

## ZK Badge State Machine (updated 2026-05-21)

The `.zk-status-badge` spans on passage cards progress through states on click:

| State | Label | Color | Next |
|-------|-------|-------|------|
| Initial | `🔗 Generate Proof` | default | click → start |
| Generating | `⏳ Generating…` | blue | await fetchZKProof |
| Submitted | `📤 Submitted…` | blue | auto-poll Kurier |
| Verified | `✅ Verified` | green | click → modal |
| Failed | `❌ Failed: <status>` | red | click → modal |
| Proof ready | `✅ Proof ready` | green | click → modal |
| Gen failed | `⚠️ Generation failed` | amber | retry on click |

**Badge is looked up by `chunkId`** via `document.getElementById('zk-status-' + chunkId)`.

**Bug fixed 2026-05-21:** `fetchZKProof` silently returned `null` on HTTP error — badge got stuck at "⏳ Generating…". Now throws on non-200, caller shows "⚠️ Generation failed". Success with `kurier_job_id` → auto-poll. Success without → "✅ Proof ready" (was "⚠️ No verification job").

## Kurier/zkVerify Provenance Troubleshooting

**Provenance search flow:**
1. `POST /api/query-provable` → queries Qdrant, generates ZK proofs in parallel
2. Each proof auto-submits to Kurier via `POST /api/provenance/submit`
3. Kurier returns `job_id` → website polls `GET /api/provenance/status/{job_id}`
4. Badge shows ✅ (verified) or ❌ (failed/rejected)

**Kurier API key location:** `<REPO>.env.systemd` — `KURIE_API_KEY`

**Key validation (from R730 shell):**
```bash
KURIE_API_KEY=$(grep KURIE_API_KEY <REPO>.env.systemd | cut -d= -f2)
curl -s "https://api.kurier.xyz/api/v1/job-status/${KURIE_API_KEY}/test-job-123"
# "Invalid jobId format" = key is valid (auth succeeded)
# "401 Unauthorized" = key is invalid/disabled
```

**Error signatures in `provenance.log`:**
- `Kurier API error 401: Unauthorized or disabled API key` → key expired or revoked
- `Kurier API error 400: Bad Request` → proof data malformed (rare, binary issue)
- Proof generated but Kurier submit fails → key issue (not a circuit issue)

**Restart after key update:**
```bash
sudo systemctl restart zk-rag-api
# Verify
curl -s http://127.0.0.1:8100/api/manifest
```

**`prove.log` (the binary output) shows circuit health — if proofs are generating and verifying locally (status=ok), the circuit is fine regardless of Kurier status.**
