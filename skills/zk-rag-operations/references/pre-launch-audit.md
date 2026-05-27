# Pre-Launch Audit Checklist — ZK-RAG Public Sites

Run before sharing a site publicly. Covers security, performance, and usability.

## Security
- [ ] SSL cert expiry > 30 days away
  ```bash
  ssh -i ~/.ssh/id_ed25519 deruyter@<PUBLIC_HOST> \
    "echo | openssl s_client -connect 127.0.0.1:443 2>/dev/null | openssl x509 -noout -dates"
  ```
- [ ] Sensitive paths return 404: `.env`, `.git/config`, `config.json`
- [ ] Admin endpoints not bypassable: `GET /api/admin/documents` returns auth error
- [ ] Qdrant dashboard (port 6333) not reachable publicly
- [ ] fail2ban running with sshd jail
- [ ] Security headers present on HTTPS: HSTS, CSP, X-Frame-Options, X-Content-Type-Options
  ```bash
  curl -sI "https://<PUBLIC_HOST>/" | grep -iE 'strict-transport|content-security|x-frame'
  ```
- [ ] `robots.txt` exists at root (add if missing — `nginx conf.d` as `location = /robots.txt`)
- [ ] CSP allows any CDN resources used by the app (Swagger UI uses cdn.jsdelivr.net — see Swagger UI Gotcha below)

## Performance
- [ ] Key endpoints < 500ms via public HTTPS
  ```bash
  time curl -s -o /dev/null "https://<PUBLIC_HOST>/api/catalog"
  time curl -s -o /dev/null "https://<PUBLIC_HOST>/api/collections"
  time curl -s -o /dev/null "https://<PUBLIC_HOST>/"
  ```
- [ ] Slow endpoint triage: if public is slow but local is fast, the problem is nginx or the network path — not the API server

**Known acceptable:** `/api/catalog` first call ~13s local (cache miss rebuilding internal cache from Qdrant), subsequent ~30ms. This is expected behavior.

## Usability
- [ ] Swagger UI loads at `/api/docs` — see gotcha below
- [ ] All documented API paths return correct HTTP status codes
- [ ] `/health` exposed publicly if needed for monitoring

## Swagger UI Gotcha (VPS)
FastAPI serves Swagger at `/docs`, not `/api/docs`. When proxying through nginx:

**Problem 1 — prefix mismatch:**
```
location = /api/docs { proxy_pass http://127.0.0.1:8100/docs; }  # strips /api — CORRECT
location = /api/docs { proxy_pass http://127.0.0.1:8100/api/docs; }  # WRONG — 404
```

**Problem 2 — CSP blocking CDN:**
Swagger UI loads `https://cdn.jsdelivr.net` JS/CSS. If CSP only has `default-src 'self'`, resources are blocked.

Fix in nginx CSP header:
```
# Before (broken):
more_set_headers "Content-Security-Policy: ... script-src 'self' 'unsafe-inline' ...";
# After (fixed):
more_set_headers "Content-Security-Policy: ... script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; ...";
```

Then `sudo systemctl reload openresty`.

**Verification:**
```bash
curl -sI "https://<PUBLIC_HOST>/api/docs" | grep 'content-security-policy'
# Must contain: https://cdn.jsdelivr.net in both script-src and style-src
```
