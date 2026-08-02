---
name: start-media-stack
description: Start and health-check the self-hosted film stack (Radarr, Sonarr, Prowlarr, Bazarr, qBittorrent, FlareSolverr, Jellyfin) plus Tailscale and GPU transcoding, after a PC/WSL restart. Use when the user says the media server / Jellyfin / *arr apps are down, asks to "start the stack / media server / film system", reports the Tailscale peer is offline, or wants a post-reboot health check.
---

# Start the self-hosted film stack

Brings the Docker media stack up and verifies every layer after a reboot. The stack
lives at `~/nas-lab` (compose file there). Full background is in that repo's
`docs/` (QUICKSTART, TRANSCODING, REMOTE-ACCESS).

## Context: what auto-starts and what doesn't

On a Windows reboot, once **WSL is running** everything should come back on its own:
`docker` and `tailscaled` are systemd-enabled, and all containers use
`restart: unless-stopped`. The one thing Windows does NOT auto-start is **WSL itself**.
Since Claude Code runs inside WSL, if you can run this skill at all, WSL is already up —
so this skill's job is to **confirm/complete the startup and health-check it**, and fix
anything that didn't come up.

The stack has 8 services: 7 long-running (**prowlarr, flaresolverr, radarr, sonarr,
bazarr, qbittorrent, jellyfin**) plus **recyclarr**, which is run-to-completion
(`restart: "no"`) and is *expected to be exited/not running* — do not flag it as down.

## Step 0 — pick the docker command form

Depending on the session's group membership, either `docker …` works directly or it
needs `sg docker -c "…"`. Detect once and use that form for every command below:

```bash
if docker ps >/dev/null 2>&1; then DKR="docker"; else DKR='sg docker -c'; fi
# usage: if DKR is "docker": run `docker compose ps`
#        if DKR is 'sg docker -c': run `sg docker -c "docker compose ps"`
```

If neither works, the docker daemon is likely down — try `sudo systemctl start docker`
(needs the user's password; ask them to run it) then retry.

## Step 1 — bring the stack up

```bash
cd ~/nas-lab
docker compose up -d          # or: sg docker -c "docker compose up -d"
```

Idempotent — already-running containers are left alone, missing ones start, and any
service added to the compose file later is picked up automatically.

This also starts **recyclarr**, which runs a one-off quality-profile sync and exits.
That's harmless (idempotent) — so recyclarr showing as *exited* afterwards in Step 2 is
expected, not a failure.

## Step 2 — verify the 7 long-running containers are Up

Wait ~15s, then:

```bash
docker compose ps --format '{{.Name}}  {{.Status}}'
```

Confirm **prowlarr, flaresolverr, radarr, sonarr, bazarr, qbittorrent, jellyfin** all
show `Up`. (recyclarr absent/exited is normal.) If any is missing or restarting, check
its logs: `docker compose logs --tail 40 <name>`.

## Step 3 — verify Tailscale, and recover the node if it's down

Expected identity: `admin-pc-1` / `admin-pc-1.tail9dbb76.ts.net` / `100.69.57.57`.
`tailscaled` is systemd-managed and the node key persists, so no browser login is
needed — a down node just needs the daemon started and/or `tailscale up`.

`tailscale status` reads without sudo (use it to detect), but **starting the daemon and
`tailscale up` need sudo**. Try them non-interactively (`sudo -n`); if sudo needs a
password, print the exact command for the user to run rather than hanging.

```bash
# 1. Daemon up?
if ! systemctl is-active --quiet tailscaled; then
  echo "tailscaled is down — starting it"
  sudo -n systemctl start tailscaled 2>/dev/null \
    || echo "  ACTION NEEDED (run yourself): sudo systemctl start tailscaled"
  sleep 3
fi

# 2. Node connected? (Logged out / stopped -> bring it up)
if tailscale status 2>&1 | grep -qiE 'logged out|stopped|Tailscale is stopped'; then
  echo "node is offline — bringing it up"
  sudo -n tailscale up 2>/dev/null \
    || echo "  ACTION NEEDED (run yourself): sudo tailscale up"
  sleep 3
fi

# 3. Report
tailscale status
tailscale ip -4
```

Interpreting `tailscale status`:
- normal peer list = online, done.
- **"Logged out." / "Tailscale is stopped."** = daemon is up but the node isn't
  connected → `sudo tailscale up` (reconnects instantly, no browser, key persists).
- command errors / no daemon = `tailscaled` isn't running → `sudo systemctl start tailscaled`.

If `tailscale up` ever asks for a login URL (only if the node key expired — rare), tell
the user to open the printed `https://login.tailscale.com/...` link and sign in with
the **same account** (`hiep622032001@…`).

## Step 4 — verify GPU transcoding (Jellyfin + RTX 4080 Super)

```bash
docker exec jellyfin nvidia-smi --query-gpu=name --format=csv,noheader
```

Should print `NVIDIA GeForce RTX 4080 SUPER`. If it errors, the GPU didn't pass into the
container — the docker daemon may have restarted without the nvidia runtime; recreate
Jellyfin: `docker compose up -d --force-recreate jellyfin`. Deeper detail in the repo's
`docs/technical/TRANSCODING.md`.

## Step 5 — health-check the web UIs

Each should answer (302 = redirect to login, normal for the *arr apps; 200 for
qBittorrent/Bazarr/Jellyfin):

```bash
for p in 9696:Prowlarr 7878:Radarr 8989:Sonarr 6767:Bazarr 8080:qBittorrent 8191:FlareSolverr 8096:Jellyfin; do
  code=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "http://localhost:${p%%:*}/")
  echo "  ${p##*:} (${p%%:*}) -> HTTP $code"
done
```

If something is down, check Beszel (http://localhost:8091) for host-level causes
and `docker compose logs -f <name>` for the container's own output — see
`docs/technical/MONITORING.md`.

## Step 6 — report

Give the user a short summary:

- Which containers are Up (and any that failed, with the log hint)
- Tailscale: online + the address to reach Jellyfin remotely
  (`http://admin-pc-1.tail9dbb76.ts.net:8096`)
- GPU: available to Jellyfin or not
- Local Jellyfin URL: `http://localhost:8096`
- Disk headroom (`df -h /mnt/f /`) — flag it if the media drive is above 95%,
  since a full drive breaks downloads and imports before it breaks a health check

Keep it to the essentials — a green "all up" line plus anything that needed attention.

## If nothing responds at all

The likely cause is that **WSL wasn't running** and something restarted it without the
services (rare, since they're systemd/compose-managed). Re-run Step 1. To make WSL
itself start automatically at Windows logon so this is never needed, the user can run
this once in **PowerShell** (documented in the repo's `docs/QUICKSTART.md`):

```powershell
schtasks /create /tn "Start WSL" /tr "wsl.exe -d Ubuntu -u root /bin/true" /sc onlogon /rl highest /f
```
