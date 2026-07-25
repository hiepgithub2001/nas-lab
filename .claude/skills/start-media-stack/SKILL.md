---
name: start-media-stack
description: Start and health-check the self-hosted film stack (Radarr, Sonarr, Prowlarr, Bazarr, qBittorrent, FlareSolverr, Jellyfin) plus Tailscale and GPU transcoding, after a PC/WSL restart. Use when the user says the media server / Jellyfin / *arr apps are down, asks to "start the stack / media server / film system", reports the Tailscale peer is offline, or wants a post-reboot health check.
---

# Start the self-hosted film stack

Brings the Docker media stack up and verifies every layer after a reboot. The stack
lives at `~/self-host-film` (compose file there). Full background is in that repo's
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

Start only the 7 long-running services — **not** recyclarr (starting recyclarr here
would trigger an unnecessary quality-profile sync every boot; it's meant to run on
demand via `docker compose run --rm recyclarr sync`):

```bash
cd ~/self-host-film
docker compose up -d prowlarr flaresolverr radarr sonarr bazarr qbittorrent jellyfin
# or: sg docker -c "docker compose up -d prowlarr flaresolverr radarr sonarr bazarr qbittorrent jellyfin"
```

This is idempotent — already-running containers are left alone, missing ones start.

## Step 2 — verify the 7 long-running containers are Up

Wait ~15s, then:

```bash
docker compose ps --format '{{.Name}}  {{.Status}}'
```

Confirm **prowlarr, flaresolverr, radarr, sonarr, bazarr, qbittorrent, jellyfin** all
show `Up`. (recyclarr absent/exited is normal.) If any is missing or restarting, check
its logs: `docker compose logs --tail 40 <name>`.

## Step 3 — verify Tailscale (remote access)

```bash
tailscale status
tailscale ip -4
```

This node should be online. Expected identity: `admin-pc-1` /
`admin-pc-1.tail9dbb76.ts.net` / `100.69.57.57`. `tailscaled` is systemd-managed and
needs no login (the node key persists). If it is not running:
`sudo systemctl start tailscaled` (needs the user's password).

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

## Step 6 — report

Give the user a short summary:

- Which containers are Up (and any that failed, with the log hint)
- Tailscale: online + the address to reach Jellyfin remotely
  (`http://admin-pc-1.tail9dbb76.ts.net:8096`)
- GPU: available to Jellyfin or not
- Local Jellyfin URL: `http://localhost:8096`

Keep it to the essentials — a green "all up" line plus anything that needed attention.

## If nothing responds at all

The likely cause is that **WSL wasn't running** and something restarted it without the
services (rare, since they're systemd/compose-managed). Re-run Step 1. To make WSL
itself start automatically at Windows logon so this is never needed, the user can run
this once in **PowerShell** (documented in the repo's `docs/QUICKSTART.md`):

```powershell
schtasks /create /tn "Start WSL" /tr "wsl.exe -d Ubuntu -u root /bin/true" /sc onlogon /rl highest /f
```
