# Quickstart

Two situations are covered here:

- **[Restarting an existing setup](#a-after-a-reboot)** — the PC rebooted and you just
  want everything back. 1 minute.
- **[Setting up from scratch](#b-from-a-fresh-clone)** — fresh clone on a new machine.
  About 45 minutes, most of it waiting on downloads.

---

## A. After a reboot

**Only one thing doesn't auto-start: WSL.** Everything else recovers by itself once WSL
is running —

| Layer | Restarts by itself? |
|---|---|
| Docker daemon | Yes — systemd-enabled |
| The containers (Radarr, Sonarr, Jellyfin, …) | Yes — `restart: unless-stopped` |
| Tailscale (remote access) | Yes — systemd-managed, node key persists, no re-login |
| GPU passthrough into Jellyfin | Yes — restored with the container |
| **WSL2 itself** | **No** — Windows does not start it at boot |

Everything (including Tailscale) runs *inside* WSL, so until WSL starts **nothing** is
up. The tell-tale symptom: the machine shows **offline** in your phone's Tailscale app.

### Bring it back — two steps

**1. Start WSL** — open Windows Terminal / Ubuntu, or from PowerShell:

```powershell
wsl.exe -d Ubuntu -u root /bin/true
```

Give it ~30-60s; Docker, the containers and Tailscale come up on their own.

**2. Verify (and fix anything that didn't come up).** Easiest is the Claude Code skill
**`start-media-stack`** — in a Claude session run `/start-media-stack`, or just ask to
"start the media server". It brings the stack up and health-checks every layer
(containers, Tailscale, GPU, all web UIs) in one pass.

Without Claude, do the same by hand:

```bash
cd ~/self-host-film
docker compose up -d        # start anything missing (idempotent)
docker compose ps           # long-running services should show Up
tailscale status            # this node online?
```

### Make even step 1 automatic

Register a scheduled task **once**, in PowerShell, so WSL starts at Windows logon:

```powershell
schtasks /create /tn "Start WSL" /tr "wsl.exe -d Ubuntu -u root /bin/true" /sc onlogon /rl highest /f
```

After this a reboot needs nothing from you — WSL boots at logon and the whole stack
comes up with it. Caveat: it fires at **logon**, not power-on, so someone still has to
sign into Windows.

> The `start-media-stack` skill lives in this repo at
> `.claude/skills/start-media-stack/SKILL.md`. On a fresh machine, copy it to
> `~/.claude/skills/` to make it invocable:
> ```bash
> mkdir -p ~/.claude/skills && cp -r .claude/skills/start-media-stack ~/.claude/skills/
> ```

### Where to go

| | |
|---|---|
| Watch | http://localhost:8096 |
| Watch remotely | `http://admin-pc-1.tail9dbb76.ts.net:8096` |
| Add a film | http://localhost:7878 |
| Logins | [`CREDENTIALS.md`](CREDENTIALS.md) |

---

## B. From a fresh clone

### 0. Prerequisites

- Docker + Docker Compose
- Your user in the `docker` group: `sudo usermod -aG docker $USER`, then log out and
  back in (or prefix commands with `sg docker -c "..."` for this session)
- A drive with room for media — films run 2–20 GB each

### 1. Configure paths

```
cp .env.example .env
```

Edit `.env`:

| Variable | Meaning |
|---|---|
| `DATA_ROOT` | Where media lives (e.g. `/mnt/f/film-data`) |
| `PUID` / `PGID` | Your user/group — check with `id -u` and `id -g` |
| `TZ` | e.g. `Asia/Bangkok` |

Create the folders — all under **one root**, or hardlinking breaks and every import
becomes a slow full copy:

```
mkdir -p "$DATA_ROOT"/{media/{movies,tv},torrents/{incomplete,movies,tv}}
```

### 2. Start everything

```
docker compose up -d
docker compose ps
```

| Service | URL | Purpose |
|---|---|---|
| Jellyfin | http://localhost:8096 | Watch |
| Radarr | http://localhost:7878 | Movies |
| Sonarr | http://localhost:8989 | TV |
| Prowlarr | http://localhost:9696 | Indexers |
| Bazarr | http://localhost:6767 | Subtitles |
| qBittorrent | http://localhost:8080 | Downloads |
| FlareSolverr | http://localhost:8191 | Cloudflare solver (no UI) |
| Recyclarr | — | Quality profiles (CLI only) |

### 3. Configure, in this order

Order matters — each step depends on the previous one. Full detail for each is in the
[README](../README.md#setup); this is the sequence and the gotchas.

**1 · qBittorrent** — http://localhost:8080
Get the temporary password: `docker logs qbittorrent | grep -A2 "temporary password"`.
Set a permanent one under **Options → WebUI** (the temp one regenerates on every
restart). Set **Default Save Path** `/data/torrents` and incomplete path
`/data/torrents/incomplete`.

**2 · Prowlarr** — http://localhost:9696
Add indexers. If one fails with a Cloudflare or TLS reset error, add FlareSolverr
under **Settings → Indexer Proxies** (`http://flaresolverr:8191`) and attach it to
that indexer.

**3 · Prowlarr → Radarr/Sonarr** — **Settings → Apps**
Use container names (`http://radarr:7878`), never `localhost`. API keys:
```
grep -o '<ApiKey>[^<]*</ApiKey>' appdata/{radarr,sonarr}/config.xml
```

**4 · Radarr / Sonarr** — root folders `/data/media/movies` and `/data/media/tv`;
download client host `qbittorrent`, port `8080`.

**5 · Quality profiles** — pulled from TRaSH Guides, not configured by hand:
```
docker compose run --rm recyclarr config create --template hd-bluray-web --template web-1080p
# edit appdata/recyclarr/configs/*.yml -> set base_url + api_key
docker compose run --rm recyclarr sync
```
Then **select the new profile** (`HD Bluray + WEB` / `WEB-1080p`) when adding media —
the built-in profiles have no custom-format scoring and will grab junk.

**6 · Bazarr** — http://localhost:6767
Languages → create a profile → **enable it as the Default Language Profile for both
Movies and Series** (skip this and Bazarr silently ignores everything). Add providers.
Connect Radarr/Sonarr with container names + API keys, then **restart Bazarr** —
connection settings are only read at startup.
For dual-language subtitles, deploy the merge script and set the post-processing
hook — see the [Bazarr guide](user-guide/bazarr.md#dual-language-subtitles-post-processing):
```
mkdir -p appdata/bazarr/scripts && cp scripts/merge-subs.py appdata/bazarr/scripts/
```

**7 · Jellyfin** — http://localhost:8096
Create your admin account, add libraries `/data/media/movies` (Movies) and
`/data/media/tv` (TV Shows). Then **profile icon → Settings → Playback**: set a
subtitle language and change **Subtitle mode** to **`Always Play`**, or films play
with no subtitles even when they have them.

### 4. Test it end to end

Add one well-known film in Radarr with the `HD Bluray + WEB` profile and watch it
flow: **Radarr Activity → Queue** → qBittorrent downloading → Radarr History shows
*imported* → it appears in Jellyfin.

Confirm hardlinking is working — `links=2` means the file is in both the library and
the seeding folder while using disk space once:

```
find "$DATA_ROOT/media" -name '*.mkv' -printf '%n links  %p\n'
```

If it says `1`, downloads and media are on different filesystems and every import is
being copied instead.

### 5. Optional — remote access

Watch from outside the house via Tailscale, with nothing exposed to the public
internet: [REMOTE-ACCESS.md](REMOTE-ACCESS.md).

---

## When something looks broken

| Symptom | Likely cause |
|---|---|
| Everything times out after a reboot | WSL is not running — [see above](#a-after-a-reboot) |
| Indexer: DNS/SSL error | IPv6 with no route — `sysctls` disable it; already set in compose |
| Indexer: Cloudflare / connection reset | Route that indexer through FlareSolverr |
| Download stuck at 0%, `metaDL`, 0 seeds | Dead torrent — remove with **Blocklist and Search** |
| Finished downloading but not in Jellyfin | Radarr import failed, or Jellyfin has not rescanned |
| No subtitles despite tracks existing | Jellyfin **Subtitle mode** is `Default` — set `Always Play` |

Fuller explanations: [README troubleshooting](../README.md#troubleshooting) ·
[user guide](user-guide/) · [architecture](technical/ARCHITECTURE.md)
