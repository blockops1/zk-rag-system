# VPS vs R730 Nginx Rate Limit Comparison

**Purpose:** Diagnosing why the same code behaves differently on VPS (localhost:8100) vs R730 (<PUBLIC_HOST>).

## Rate Limit Zone Definitions

**File:** `/etc/nginx/conf.d/rag-rate-limit.conf`

| Zone | R730 (correct) | VPS (wrong) | Impact if wrong |
|------|---------------|-------------|-----------------|
| `rag_limit` | `rate=120r/m` | `rate=10r/m` | Provenance search exhausts 10/min instantly → 503 |
| `query_limit` | `rate=30r/m` | `rate=30r/m` | ✓ correct |
| `prove_limit` | `rate=3r/m` | `rate=60r/m` | Overly permissive; allows proof spam |

### VPS zone definitions (current — WRONG)
```
limit_req_zone $binary_remote_addr zone=rag_limit:10m rate=10r/m;
limit_req_zone $binary_remote_addr zone=query_limit:10m rate=30r/m;
limit_req_zone $binary_remote_addr zone=prove_limit:10m rate=60r/m;
```

### R730 zone definitions (authoritative)
```
limit_req_zone $binary_remote_addr zone=rag_limit:10m rate=120r/m;
limit_req_zone $binary_remote_addr zone=query_limit:10m rate=30r/m;
limit_req_zone $binary_remote_addr zone=prove_limit:10m rate=3r/m;
```

## `/api/images/` Location

| Server | Has limit_req on `/api/images/`? | Burst |
|--------|----------------------------------|-------|
| R730 | ✅ yes — `limit_req zone=rag_limit burst=30 nodelay` | 30 |
| VPS | ❌ **no** — no `limit_req` directive | N/A |

On R730, the `/api/images/` location at line ~136 has:
```nginx
location = /api/images {
    limit_req zone=rag_limit burst=30 nodelay;
    proxy_pass http://127.0.0.1:8100;
}
```

On VPS, the `location ~ ^/api/images/` block has **no `limit_req`** — requests pass through without rate limiting.

## Provenance Image Bug — Root Cause Chain

1. Provenance search renders ~4-10 result cards simultaneously
2. Each card fires 3-4 `/api/images/{docId}/{page}` requests at once
3. R730: `burst=30` handles ~30 concurrent image requests; excess 503
4. VPS: No rate limit on `/api/images/` → no 503 from rate limiting
5. **BUT:** If `/api/images/` did have a limit_req on VPS with `rate=10r/m`, it would 503 immediately since 10+ requests fire at once

**Key insight:** VPS images work after provenance search because there is NO rate limit on `/api/images/` on VPS. If you add one without increasing `rag_limit` to 120r/m, you'll reintroduce the 503 bug.

## Fix Sequence for VPS Rate Limits

If you need to add rate limits to VPS matching R730:

1. Fix zone definitions in `/etc/nginx/conf.d/rag-rate-limit.conf`:
   - `rag_limit`: `rate=10r/m` → `rate=120r/m`
   - `prove_limit`: `rate=60r/m` → `rate=3r/m`

2. Add `limit_req zone=rag_limit burst=30 nodelay;` to the `location ~ ^/api/images/` block in `/etc/nginx/conf.d/military-manuals-local.conf` (around line 113)

3. Reload: `sudo systemctl reload openresty`

4. Verify:
   ```bash
   # Fire 20 concurrent image requests — should all 200, not 503
   for i in $(seq 1 20); do
     curl -s -o /dev/null -w "%{http_code}\n" \
       "http://127.0.0.1/api/images/80dc29e5dac5bf19fdc5016debb97e6737f58ca79275055def33891d50bac151/1" &
   done; wait
   ```
   All should return `200` with `burst=30`.
