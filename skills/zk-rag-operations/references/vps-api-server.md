# VPS API Server Reference

## Service Name (VPS-specific)
**`zk-rag-api.service`** — NOT `rag-api-vps.service` or any other variant.

## Ports
- API server: **port 8100**
- Qdrant: port 6333 (gRPC), 6334 (REST)
- OpenResty/nginx: 80, 443

## Nginx Config
**Path:** `/etc/nginx/conf.d/<PUBLIC_HOST>.conf`

OpenResty on VPS (not plain nginx):
```bash
# Syntax check
sudo openresty -t

# Reload after config changes
sudo openresty -s reload
```

## Key VPS Endpoints (public)
| Endpoint | Status | Notes |
|---|---|---|
| `GET /health` | ✅ 200 | FastAPI registers at `/health`, not `/api/health` — needs explicit nginx proxy |
| `GET /api/docs` | ✅ 200 | Swagger UI |
| `GET /api/openapi.json` | ✅ 200 | OpenAPI spec |
| `GET /api/manifest` | ✅ 200 | API manifest |
| `GET /api/catalog` | ✅ 200 | Document catalog |
| `POST /api/query` | ✅ 200 | Semantic search |
| `POST /api/query-provable` | ✅ 200 | ZK search |
| `POST /api/provenance/prove` | ✅ 200 | ZK prove |
| `POST /api/provenance/submit` | ✅ 200 | Kurier submit |
| `GET /api/provenance/status/{job_id}` | ✅ 200 | Status poll |
| `GET /api/provenance/poll/{job_id}` | ✅ 200 | Browser poll |

## Common VPS Issues

### /health returns 404
**Root cause:** FastAPI registers `/health` at root, but nginx catch-all `location /api/` proxies `/health` → `http://127.0.0.1:8100/health` (404 — FastAPI only has `/health`).

**Fix:** Add explicit proxy rule in nginx config:
```nginx
location = /health {
    proxy_pass http://127.0.0.1:8100/health;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 30s;
}
```

### /api/docs returns 404 or CSP blocks CDN
If Swagger UI doesn't render, check:
1. Proxy rule should be `proxy_pass http://127.0.0.1:8100/docs` (strip `/api` prefix)
2. CSP header needs `https://cdn.jsdelivr.net` in `script-src` and `style-src`

### Service not found
```bash
# Check actual running service
systemctl list-units --type=service --state=running | grep -E 'rag|api|zk'

# Check ports
ss -tlnp | grep -E '8100|6333'
```
