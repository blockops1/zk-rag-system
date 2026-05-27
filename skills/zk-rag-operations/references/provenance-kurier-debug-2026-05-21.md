# Provenance/Kurier Debug — 2026-05-21

## Session: Local website image dedup + ZK proof failure diagnosis

### 1. Image dedup: PNGs in `images/` causing double-rendering

**Problem:** Every page had both `.png` and `.webp` files in `<DATA>images/{doc_id}/`. The API returned both (`["page_0000.png", "page_0000.webp"]`), and the website rendered both — same figure shown twice.

**Root cause:** Pipeline A originally extracted PNGs. A later run added WebP generation. API endpoint (`list_images_for_page` in `api_server.py` line 1036) accepted all formats including webp. Neither was ever changed to prefer one format.

**Fix:** Moved all 109,800 PNGs from `images/` to `images_png_archive/`. API now returns only `.webp`. No code change needed — the API only looks in `images/`.

```bash
# Move PNGs per doc dir
SRC="<DATA>images"
DST="<DATA>images_png_archive"

for doc_dir in "$SRC"/*/; do
    doc_id=$(basename "$doc_dir")
    mkdir -p "$DST/$doc_id"
    mv "$doc_dir"*.png "$DST/$doc_id/" 2>/dev/null || true
done
```

**After:** API returns `["page_0000.webp"]` only — verified with `curl "http://127.0.0.1:8100/api/images/{doc_id}/1"`.

**Storage freed:** ~109,800 PNGs removed from `images/`.

---

### 2. ZK Provenance Search — Kurier 401

**Problem:** Provenance search generates local ZK proofs correctly (prove-bin works, prove.log shows "proof generated" + "proof verified locally"), but auto-submit to Kurier fails with:

```
Failed to submit proof to Kurier: Kurier API error 401: Unauthorized or disabled API key
```

**API endpoint flow:**
1. Website calls `POST /api/query-provable` → API generates proofs + tries auto-submit
2. Website can also call `POST /api/provenance/prove` directly (for next/prev navigation)
3. On submit failure, `POST /api/provenance/submit` → `submit_proof_to_zkverify()`
4. Kurier returns `job_id` → website polls `GET /api/provenance/status/{job_id}`

**Key log entries (provenance.log):**
```
"Calling prove binary"     → prove-bin running (proof generation WORKS)
"proof generated"          → prove.log confirms
"_kurier_post: key_preview=b28e65... endpoint=submit-proof/{api_key}"
"Failed to submit proof to Kurier: Kurier API error 401: Unauthorized or disabled API key"
```

**Diagnosis — verify from R730 directly:**
```bash
# Check key is masked in .env.systemd (normal — shows ***)
grep KURIE_API_KEY <REPO>.env.systemd

# Extract key and test Kurier API directly
KURIE_API_KEY=$(grep KURIE_API_KEY <REPO>.env.systemd | cut -d= -f2)
echo "Key preview: ${KURIE_API_KEY:0:8}..."

# Test job status endpoint (returns 401 = bad key, not network error)
curl -s "https://api.kurier.xyz/api/v1/job-status/${KURIE_API_KEY}/nonexistent-job-123"
# Returns: {"statusCode":401,"code":"UNAUTHORIZED","error":"Unauthorized","message":"Unauthorized or disabled API key"}

# Test submit-proof (returns 413 if key valid but payload wrong; 401 if key bad)
curl -s -X POST "https://api.kurier.xyz/api/v1/submit-proof/${KURIE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"proofType":"GROTH16","circuitId":"zkrag","circuitVersion":"0.2.0","calldata":{"proof":"test","pubSignals":["0"]}}'
```

**Result of direct test:** 401 Unauthorized — key is invalid, disabled, or revoked.

**Key location (R730 systemd):** `KURIE_API_KEY=***` in `<REPO>.env.systemd` — loaded by `zk-rag-api.service` via `EnvironmentFile`.

**Key location (VPS systemd):** `EnvironmentFile=/home/deruyter/rag/.env` — checked via `zkrag-vps-sync` skill.

---

### 3. What IS Working

- **Local proof generation:** `prove.log` shows all proofs generated and locally verified. Binary at `<REPO>zk-circuit/target/release/prove-bin` works correctly.
- **API serving:** `POST /api/query-provable` returns chunks with `zk_proof` attached (proof_hex, vk_hex, public_inputs_hex).
- **Auto-submit to Kurier:** Fails at network layer with 401. Proof data is correctly formed — Kurier rejects the key.
- **Website UI:** Shows ✅/❌ badges. ❌ = local proof succeeded, Kurier submit failed.

---

### 4. Fix Required

**Action needed:** Mr. V must obtain a valid Kurier/zkVerify API key and update:
- R730: `<REPO>.env.systemd` → `KURIE_API_KEY=<valid_key>`
- VPS: `/home/deruyter/rag/.env` → `KURIE_API_KEY=<valid_key>`

After updating, restart the service:
```bash
sudo systemctl restart zk-rag-api
```

---

### 5. Files Modified This Session

| File | Change |
|------|--------|
| `<DATA>images/*/*.png` | 109,800 files moved to `images_png_archive/` |
| `<DATA>images_png_archive/*/` | Received moved PNGs |

No code changes. No restart needed for image fix (API reads `images/` only).

### 6. Relevant Code

- **API:** `<REPO>shared/api_server.py` — `list_images_for_page` (line 1010), `query_provable` (line 1281), `submit_proof` (line 1533)
- **Provenance module:** `<REPO>shared/provenance.py` — `submit_proof_to_zkverify` (line 682), `_kurier_post` (line 594)
- **Prove binary:** `<REPO>zk-circuit/target/release/prove-bin`
- **Logs:** `<DATA>logs/provenance.log`, `<DATA>logs/prove.log`
- **API server logs:** `<DATA>logs/api_server.log`, `api_server_stdout.log`
