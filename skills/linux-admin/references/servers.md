# Server Inventory

| Hostname | Address | Username | Purpose | Key Services | OpenClaw Path |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `deruyter` | `<SERVER_IP>` | `<USER>` | Local LLM & OpenClaw Server | `ollama`, `llama-minimax`, `openclaw-gateway` | `<HOME>/.npm-global/bin/openclaw` |
| `crestview-web` | `crestview2.crestvieworchards.com` | `jack` | Crestview Web Server | `nginx`, `fail2ban`, `rkhunter`, `ufw` | `(n/a)` |
| `api.blockoperations.com` | `66.228.55.68` | `deruyter` | RAG API VPS | `qdrant`, `openresty`, `rag-api` | — |

---

## api.blockoperations.com — RAG API VPS (verified 2026-03-26)

**Arch:** Lightweight read-only replica. All processing lives on DeRuyter. VPS receives nightly Qdrant sync and serves queries. No cron on VPS.

| Component | Status | Details |
| :--- | :--- | :--- |
| **SSH** | Key-auth | Ed25519 key `~/.ssh/id_ed25519` on DeRuyter → user `deruyter` on VPS |
| **OpenResty** | Active | nginx-style config; serves HTTPS on 443, proxies to rag-api on 8104 |
| **rag-api** | Active (PID 21530) | Systemd service; venv at `/home/deruyter/rag/venv`; API at `127.0.0.1:8104` |
| **Qdrant** | Embedded | Runs inside rag-api Python process (not standalone); data at `/data/rag/qdrant_data` |
| **SSL cert** | Valid | Let's Encrypt; expires **2026-06-14** |
| **Disk** | 10% used | 269GB free of 315GB |
| **RAM** | 8.3GB avail | 15GB total; rag-api capped at 12G via MemoryMax |

### Service Management (VPS)

| Action | Command |
| :--- | :--- |
| Restart rag-api | `sudo systemctl restart rag-api` |
| Check rag-api status | `sudo systemctl status rag-api` |
| Restart openresty | `sudo systemctl restart openresty` |
| Full status check | `ps aux \| grep api_server \| grep -v grep` |

### Log Locations

| Log | Path |
| :--- | :--- |
| OpenResty access | `/var/log/nginx/access.log` |
| OpenResty error | `/var/log/nginx/error.log` |
| rag-api | `/home/deruyter/rag/logs/api_server.log` |

### Key Paths

| Purpose | Path |
| :--- | :--- |
| RAG venv | `/home/deruyter/rag/venv` |
| Qdrant data | `/data/rag/qdrant_data` |
| Registry | `/data/rag/registry.json` |
| Nginx config | `/etc/nginx/conf.d/rag-api.conf` |
| rag-api systemd unit | `/etc/systemd/system/rag-api.service` |

### Health Check Commands

```bash
# Is the API responding?
curl -s https://<PUBLIC_HOST>/collections | python3 -m json.tool | head -5

# Is the process alive?
ps aux | grep api_server | grep -v grep

# OpenResty healthy?
systemctl status openresty --no-pager | head -5

# Qdrant data present?
ls /data/rag/qdrant_data/collection/
```

### SSL Renewal

Certbot handles Let's Encrypt renewal automatically. Check renewal status:
```bash
sudo certbot certificates
sudo systemctl status certbot.timer
```
If cert expires before Jun 14 — debug certbot, do not manually regenerate.

---

## Crestview Web Server — Security Profile (verified 2026-02-23)

| Component | Status | Notes |
| :--- | :--- | :--- |
| **SSHD** | Hardened | `PermitRootLogin no`, `PasswordAuthentication no` (key-auth only) |
| **Fail2ban** | Active | Running since 2026-02-17; default jail.conf (no jail.local) |
| **UFW** | Installed | ⚠️ NOPASSWD sudoers has wrong path (`/usr/bin/ufw`) — actual: `/usr/sbin/ufw`; needs fix |
| **rkhunter** | Installed | ⚠️ Baseline pending — needs `/usr/bin/rkhunter` added to NOPASSWD sudoers |
| **SSL cert** | Valid | Expires 2027-02-19 |
| **Open ports** | 22, 80, 443 | Port 25 (SMTP) — postfix stopped & disabled 2026-02-23; will use Gmail when email needed |

## NOPASSWD Sudoers — Crestview Web Server
Path: `/etc/sudoers.d/` — user `jack`
Allowed (no password): `apt`, `apt-get`, `systemctl`, `ufw` ⚠️(wrong path), `nginx`, `tee`, `cat`, `cp`, `mv`, `rm`, `mkdir`, `chmod`, `chown`, `nano`, `vim`, `less`, `head`, `tail`, `ps`, `curl`, `wget`, `ls`, `grep`, `find`, `tar`, `git`, `ln`
**Pending additions:** `/usr/sbin/ufw` (fix path), `/usr/bin/rkhunter`
