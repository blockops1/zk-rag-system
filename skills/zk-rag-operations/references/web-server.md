# Web Server Reference

## Service Names (CRITICAL — names have changed)

| Service | Type | Unit file | Port | Notes |
|---|---|---|---|---|
| **Qdrant** | system (sudo) | `qdrant.service` | 6333 | Vector DB |
| **OpenResty** | system (sudo) | `openresty.service` | 80 | Web server |
| **API Server** | user (systemctl --user) | `rag-api-local.service` | 8100 | Started by user service, not the system unit |

**Common mistake:** Using `zk-rag-api.service` (the old/disabled system unit) instead of `rag-api-local.service` (the active user unit).

## Starting / Restarting

```bash
# Qdrant (system service — sudo required)
sudo systemctl start qdrant.service
sudo systemctl status qdrant.service

# OpenResty (system service — sudo required)
sudo systemctl reload openresty  # hot-reload config (preferred over restart)
sudo systemctl restart openresty  # full restart — use only if reload fails

# API Server (user service — no sudo)
systemctl --user restart rag-api-local.service
systemctl --user status rag-api-local.service
```

## OpenResty (not plain nginx)

**Binary:** `/usr/local/openresty/nginx/sbin/nginx`
```
ps aux | grep nginx
# → nginx: master process /usr/local/openresty/nginx/sbin/nginx
```

**Config paths:**
- Main conf: `/usr/local/openresty/nginx/conf/nginx.conf`
- **Site configs: `/etc/nginx/conf.d/`** (NOT `/usr/local/openresty/nginx/conf/conf.d/` — that directory does not exist on the VPS)
- OpenResty's `nginx.conf` contains `include /etc/nginx/conf.d/*.conf`, so all site configs live under `/etc/nginx/conf.d/`
- **NOT** `/etc/nginx/nginx/` or `/etc/nginx/sites-enabled/` — those are for plain nginx, not OpenResty
- `nginx -t` (as root) validates the config but may fail on log perm issues (non-fatal)
- **CRITICAL site-config conflict (2026-05-26):** `/etc/nginx/sites-enabled/` was loading a duplicate `military-manuals-local` config alongside the correct one in `conf.d/`. Both competed for port 80 and caused the rate limit fix on `/api/images/` to not apply. The duplicate was moved to `/tmp/`. If rate limits or routing seem wrong, verify `nginx -T` shows only one config for the domain.

**VPS vs R730 service names (different on each host):**
| Service | R730 | VPS |
|---|---|---|
| API server | `rag-api-local.service` (user) | `zk-rag-api.service` (system) |
| Web server | OpenResty | OpenResty |
| Vector DB | qdrant.service | qdrant.service |

**VPS nginx items remaining (known 2026-05-27):**
1. `/health` — not publicly exposed. Only works at `localhost:8100/health`. Add `location = /health { proxy_pass http://127.0.0.1:8100/health; }` if public monitoring is needed.

**VPS rate limit configuration (2026-05-27):**
VPS (`/etc/nginx/conf.d/rag-rate-limit.conf`) had three misconfigurations vs R730:

| Zone | R730 | VPS (was) | VPS (fixed) |
|---|---|---|---|
| `rag_limit` rate | 120r/m | 10r/m | 120r/m |
| `prove_limit` rate | 3r/m | 60r/m | 12r/m |
| `/api/images/` burst | burst=30 | none | burst=30 |

The `/api/images/` location (`location ~ ^/api/images/`) had no `limit_req` directive — provenance search fires 10+ simultaneous image requests and exceeded the burst limit. Fix: `limit_req zone=rag_limit burst=30 nodelay;` added to that location block in `military-manuals-local.conf`.

**VPS backup (2026-05-27):**
VPS files backed up to `/data/rag/vps-backup/` on R730:
- `website/` — static website (what nginx serves, 204MB)
- `api_server/` — API Python files (api_server.py, provenance.py, etc.)
- `nginx/` — VPS nginx configs
- `systemd/` — VPS systemd unit

**Known permission issue:** `<REPO>website/` on R730 is root-owned — `deruyter` cannot read or rsync these files. This blocks backup of the zk-rag-v2 dev source. If full backup is needed, either `sudo chown -R deruyter:deruyter <REPO>` on R730, or use a different transfer method. The production website content is already covered by the `website/` backup (nginx-served static files).

**Website root:** `<REPO>website/` — served at `/`
- `registry.json` (315 docs) at `/registry.json`
- `llms.txt` at `/llms.txt`
- `catalog.html` at `/catalog.html`

**API proxy:** `/api/` and `/images/` → `http://127.0.0.1:8100` (OpenResty proxies to api_server on port 8100)

**Test locally:**
```bash
curl http://127.0.0.1:80/          # website HTML
curl http://127.0.0.1:80/registry.json  # should return JSON
curl http://127.0.0.1:80/api/manifest  # should proxy to API server
```

**Public Swagger UI (2026-05-27):** `https://<PUBLIC_HOST>/api/docs`
- FastAPI serves Swagger at `/docs`, NOT `/api/docs` — two fixes applied:
  1. Prefix fix: nginx `location = /api/docs` proxies to `http://127.0.0.1:8100/docs` (strips `/api`)
  2. CSP fix: added `https://cdn.jsdelivr.net` to `script-src` and `style-src` in the CSP header (`<PUBLIC_HOST>.conf`)
- `/api/openapi.json` similarly fixed: proxies to `8100/openapi.json`
- See `references/pre-launch-audit.md` for full Swagger CDN CSP gotcha details

**Pre-launch `robots.txt` (add before sharing publicly):** add to `/etc/nginx/conf.d/<PUBLIC_HOST>.conf`:
```nginx
location = /robots.txt {
    add_header Content-Type text/plain always;
    return 200 "User-agent: *\nAllow: /\nDisallow: /api/\n";
}
```
Full checklist: `references/pre-launch-audit.md`

## API Server (port 8100)

**R730 process:** `<VENV>venv/bin/python3 <REPO>shared/api_server.py`
- Started by systemd user service `rag-api-local.service` (NOT `zk-rag-api.service`)

**VPS process:** `/home/deruyter/rag/venv/bin/python3 /home/deruyter/rag/api_server.py`
- Started by systemd system service `zk-rag-api.service`
- Embedding: in-process via `nomic-ai/nomic-embed-text-v1.5` (fastembed, no port 8200)
- Routes: `/health`, `/api/collections`, `/api/catalog`, `/api/query`, `/api/query-provable`, `/api/context`, `/api/images/{doc_id}/{page_num}`, `/api/provenance/{submit,prove,status/{job_id},poll/{job_id}}`

**Health check:** `curl http://127.0.0.1:8100/health`

**Health check:** `curl http://127.0.0.1:8100/health`

**Key manifest routes:**
```
/api/manifest          → API description
/api/collections       → collection list
/api/catalog           → grouped doc catalog (titles from Qdrant payloads)
/api/doc/{doc_id}      → single doc metadata
/api/query             → vector search
/api/query-provable    → vector search + ZK proofs
/api/context           → chunk context (nav)
/api/images/{doc_id}/{page} → page PNG list
/api/provenance/*      → ZK proof submit/poll/status
```

## Known Crash Bugs / Fixed Issues

### `list_collections` ValueError: max_workers must be greater than 0
**Symptom:** `GET /api/collections` returns 500; logs show `ValueError: max_workers must be greater than 0` in `list_collections`.
**Root cause:** `ThreadPoolExecutor(max_workers=min(len(target_collections), 4))` — when `target_collections` is empty, `min(0, 4) = 0` is passed as `max_workers`.
**Fix:** Guard with `max(1, ...)`:
```python
# api_server.py line ~433
with ThreadPoolExecutor(max_workers=max(1, min(len(target_collections), 4))) as executor:
```
**Then restart:** `systemctl --user restart rag-api-local.service` (R730) or `sudo systemctl restart zk-rag-api.service` (VPS)

### Qdrant down causes catalog to return empty or timeout
**Symptom:** `/api/catalog` returns `[]` for all collections, or times out.
**Root cause:** Qdrant not running — API has no data to serve.
**Fix:** `sudo systemctl start qdrant.service`

### Rogue API server process (wrong Python, patched code not loading)
**Symptom:** Code changes to `api_server.py` have no effect. Service appears active but responds with old behavior. Adding a DEBUG print to source and restarting doesn't produce output.
**Root cause:** A previous invocation of `python3 <REPO>shared/api_server.py` is running as a rogue process outside systemd, under a different Python interpreter (e.g. system Python 3.12 instead of the venv's python). Systemd's restart only TERM-kills the child it spawned — if that process already died and was replaced, systemd's PID is stale.
**Diagnosis:**
```bash
ps aux | grep api_server | grep -v grep
# WRONG (rogue): python3.12 <REPO>shared/api_server.py
# CORRECT (R730): <VENV>venv/bin/python3 ...
# CORRECT (VPS): /home/deruyter/rag/venv/bin/python3 ...
```
**Fix:**
```bash
kill <PID>  # kill the rogue process
find /home/.../shared -name "*.pyc" -delete  # clear bytecode
systemctl --user start rag-api-local.service  # R730
# or
sudo systemctl start zk-rag-api.service  # VPS
```
**Prevention:** After any restart, check `ps aux` to confirm the correct PID and Python path.

### `joint` collection returns 400 on all API calls
**Symptom:** `GET /api/context?collection=joint` or `POST /api/query-provable` (collection=joint) returns `{"detail": "Invalid collection. Must be one of: army, navy, marines, coast_guard, air_force, other"}`.
**Root cause:** `joint` is a real Qdrant collection but was missing from `KNOWN_COLLECTIONS` in `api_server.py`. Both endpoints validate strictly.
**Fix:** Add `"joint"` to `KNOWN_COLLECTIONS`: `["army", "navy", "marines", "coast_guard", "air_force", "joint", "other"]`.
**Cross-collection doc_id resolution:** When a non-standard catalog collection is requested and a `doc_id` is provided, `get_context` now looks up which actual Qdrant collection holds that doc_id rather than hard-rejecting. Apply the same pattern to `query_provable` when needed.

## PRD Refactor Backlog

**Location:** `<REPO>website/prd.json`

8 stories — modular JS refactor (WEBSITE-001 through WEBSITE-008):
- WEBSITE-001: `js/api.js` — ES module with all API fetch calls
- WEBSITE-002: `js/state.js` — pure in-memory state module
- WEBSITE-003: `js/renderer.js` — pure HTML builder functions
- WEBSITE-004: `js/app.js` — orchestration actions
- WEBSITE-005: `js/event-handlers.js` — event listener registration
- WEBSITE-006: `index.html` refactor to import modules
- WEBSITE-007: Vitest unit tests for api/state/renderer
- WEBSITE-008: Playwright integration tests

All 8 currently `passes: false`, `attempts: 0`.
