---
name: linux-admin
description: "Administer Linux servers via SSH. Use when: checking server health, service status (systemctl), disk/memory/CPU usage, installing or upgrading packages (apt), inspecting logs (journalctl), managing firewall (ufw), checking fail2ban jails, SSH into a server, or any OS-level operation on crestview2 or other Linux hosts. Trigger even if the user says 'check the server', 'what's the load', 'install X on the server', 'look at the logs', or 'is the service running'. For nginx config, SSL certs, and certbot → use web-server skill instead."
---

# Linux Admin

Reusable OS-level commands for administering Linux servers — service management, resource monitoring, package operations, log inspection, and security baseline checks.

## Anti-Loop Guardrails
- Max 6 tool steps per task.
- Same call repeats twice → stop: "Loop detected — stopping."
- Max 2 retries → ask for direction.
- Simple query → answer in 1 step.

## Execution Guardrails
- Limit to 3 uses per task.
- No new info → stop and summarize.
- After 2+ calls → condense to 1 bullet.

## WHY This Skill Exists
- **Problem:** Manually remembering and typing specific Linux commands for routine checks is repetitive and error-prone.
- **User Outcome:** Quickly and reliably check the health and status of any Linux server.
- **Value:** Provides a consistent, token-efficient toolkit for common admin tasks, reducing the need for manual lookups or complex reasoning.

## Use Cases (this skill)
- "Check the status of the nginx service on crestview2"
- "What's the current load and memory usage?"
- "Update the package list on the server"
- "Is fail2ban running? Which IPs are banned?"
- "Check disk space" / "Check memory" / "Show logs for X"
- "Install fail2ban / apt-get install anything"

**Scope boundary:** OS-level operations → this skill. nginx config, SSL certs, certbot, site configs → `web-server` skill.

## Connection Workflow
1.  **Identify Target:** User specifies a hostname (e.g., "deruyter").
2.  **Consult Inventory:** Read `references/servers.md` to find the `Address` and `Username` for the given hostname.
3.  **Establish Connection:** Use `ssh <username>@<address>` to connect to the server.
4.  **Execute Command:** Run the requested admin command (e.g., `systemctl status nginx`).

## Quick Start
The following commands are the most common entry points for this skill.

| Task | Command (run on target server) |
| --- | --- |
| Check Service Status (system) | `sudo systemctl status <service_name>` |
| Check Service Status (user) | `systemctl --user status <service_name>` |
| Check Server Load | `uptime` |
| Check Disk Space | `df -h` |
| Check Memory Usage | `free -h` |

## Core Commands

### Service Management (`systemctl`)

| Action | Command |
| --- | --- |
| **Check Status** | `sudo systemctl status <service_name>` |
| **Check Status (user session)** | `systemctl --user status <service_name>` |
| **Start Service** | `sudo systemctl start <service_name>` |
| **Start User Service** | `systemctl --user start <service_name>` |
| **Stop Service** | `sudo systemctl stop <service_name>` |
| **Stop User Service** | `systemctl --user stop <service_name>` |
| **Restart Service** | `sudo systemctl restart <service_name>` |
| **Restart User Service** | `systemctl --user restart <service_name>` |
| **Enable on Boot** | `sudo systemctl enable <service_name>` |
| **Enable on Boot (user)** | `systemctl --user enable <service_name>` |
| **Reload Config** | `sudo systemctl daemon-reload` |
| **Reload User Config** | `systemctl --user daemon-reload` |
| **View Logs** | `journalctl --user -u <service_name> -n 50 --no-pager` |

#### Diagnosing "address already in use" failures

When a service fails to start with `Errno 98: address already in use`:

1. Find what's holding the port: `ss -tuln | grep <port>` or `lsof -i :<port>`
2. Check if a manual background process is running: `ps aux | grep <script_name>`
3. If a manual nohup/background process occupies the port — kill it first, then start the service
4. If the service is in `activating (auto-restart)` loop — check `journalctl --user -u <service>` for the actual error

**Never try to start a systemd service while a manual process holds its port.** Always kill the manual process first.

Example on deruyter: `rag-api-local.service` failed with "address already in use" — manual `api_server.py` nohup process (PID 2582418) was on port 8100. Killed it, then `systemctl --user start rag-api-local.service` succeeded.

#### Qdrant is a system service, not a user service

Qdrant (`qdrant.service`) runs as a **system** service — use `sudo systemctl`, not `systemctl --user`.
On deruyter, checking `--user` units finds nothing for qdrant. Always use:
- `sudo systemctl status qdrant.service` (not `systemctl --user status qdrant`)
- `sudo systemctl start qdrant.service`

#### Systemctl says "active" but HTTP health check returns 000

**Symptom:** `systemctl is-active <service>` returns `active`, but a curl health check on its port returns `000` (connection failure or non-HTTP response). A monitoring script may report the service as failing despite systemctl showing it as running.

**Pattern seen on VPS (`vps-monitor.sh`):** Service is `active`, but the script checks `/manifest` on port 8104 which returns a non-200 response (or curl gets a non-HTTP protocol response), while `/health` works fine.

**Diagnosis steps:**
1. `systemctl is-active <service>` — confirms systemd view
2. `curl -sf --max-time 5 http://127.0.0.1:<port>/health` — try the correct health endpoint
3. `curl -sf --max-time 5 http://127.0.0.1:<port>/manifest` — check what the monitoring script actually hits
4. `journalctl -u <service> --since "1 hour ago"` — check service logs for crashes or restart events

**Root cause:** The monitoring script checks a different endpoint than the standard `/health`. A `000` from curl means curl got a connection but couldn't parse an HTTP response — usually means the endpoint doesn't exist, returns empty, or the service is partially started. systemctl showing `active` means the process is alive, not that the health endpoint is responding correctly.

### Resource Monitoring

| Resource | Command |
| --- | --- |
| **Load Average / Uptime** | `uptime` |
| **CPU / Process List** | `htop` (or `top -bn1`) |
| **Disk Usage** | `df -h` |
| **Memory Usage** | `free -h` |
| **Network Connections** | `ss -tuln` |
| **Port Conflict Check** | `ss -tuln | grep <port>` or `lsof -i :<port>` |

### Package Management (`apt` for Debian/Ubuntu)

| Action | Command |
| --- | --- |
| **Update Package List** | `sudo apt update` |
| **Upgrade Packages** | `sudo apt upgrade -y` |
| **Install Package** | `sudo apt install <package_name> -y` |
| **Remove Package** | `sudo apt remove <package_name> -y` |

### Log Inspection

| Log Type | Command |
| --- | --- |
| **Systemd Service Logs (system)** | `sudo journalctl -u <service_name> -n 50 --no-pager` |
| **Systemd Service Logs (user)** | `journalctl --user -u <service_name> -n 50 --no-pager` |
| **Kernel / System Log** | `sudo journalctl -k -n 100 --no-pager` |

## Security
- All commands requiring elevated privileges use `sudo` via pre-configured `NOPASSWD` in `/etc/sudoers.d/`.
- Read-only by default. Destructive actions require explicit user confirmation.
- If a `sudo` command fails with "password required" — it's not in the NOPASSWD list. **Stop and ask Mr. V to add it manually.** Do not attempt workarounds.

## Standard Linux Security Stack
Mr. V's typical security baseline for all Linux servers:

| Component | Purpose | Check Command |
| :--- | :--- | :--- |
| **SSHD hardening** | `PermitRootLogin no`, `PasswordAuthentication no` | `grep -E '(PermitRootLogin\|PasswordAuth)' /etc/ssh/sshd_config` |
| **UFW** | Firewall — allow only needed ports | `sudo ufw status verbose` |
| **fail2ban** | Brute-force protection | `sudo systemctl status fail2ban` |
| **rkhunter** | Rootkit detection + file integrity | `sudo rkhunter --check --sk` |

## References
- `references/servers.md` — server inventory, security profiles, NOPASSWD notes
