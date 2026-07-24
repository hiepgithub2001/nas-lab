# self-host-film

Self-hosted media automation stack on bare metal + Docker (WSL2), based on
[TRaSH Guides](https://trash-guides.info/).

**Docs:** [User Guide](docs/USER-GUIDE.md) — adding films/shows and watching them ·
[Remote Access](docs/REMOTE-ACCESS.md) — watching away from home via Tailscale ·
[Architecture](docs/ARCHITECTURE.md) — how it works internally

## How it fits together

1. **Prowlarr** searches indexers/trackers for releases and knows where to find them.
2. **Radarr** (movies) / **Sonarr** (TV) decide what to grab — track your wishlist,
   pick the best release per your quality profile, and send it to the download client.
3. **qBittorrent** downloads the actual files via BitTorrent into `/data/torrents`.
4. Radarr/Sonarr detect the finished download and **hardlink** it into
   `/data/media/movies` or `/data/media/tv` (renamed) — no duplicate disk usage,
   qBittorrent keeps seeding the original file.
5. **Bazarr** notices the new file, fetches matching subtitles, and writes `.srt`
   files alongside it.
6. **Jellyfin** serves the organized `/data/media` library for playback.

## Folder structure

Everything lives under one root so hardlinks work across containers:

```
/mnt/f/film-data          → mounted as /data in each container
├── media/                → the organized library (Jellyfin reads this)
│   ├── movies/           → Radarr root folder
│   └── tv/               → Sonarr root folder
└── torrents/             → qBittorrent's completed save path
    ├── incomplete/       → in-progress downloads (temp path)
    ├── movies/           → intended save path for Radarr's category
    └── tv/               → intended save path for Sonarr's category
```

> **Current deviation:** qBittorrent's `radarr` and `tv-sonarr` categories have no
> per-category save path set, so completed downloads land directly in
> `/data/torrents/` rather than the `movies/`/`tv/` subfolders. Hardlinking and
> importing still work (same filesystem either way), but the split TRaSH recommends
> isn't in effect. To align: in qBittorrent **Options → Downloads → Categories**,
> set the `radarr` category's save path to `/data/torrents/movies` and `tv-sonarr`
> to `/data/torrents/tv`.

## Prerequisites

- Docker + Docker Compose installed
- Your user in the `docker` group (`sudo usermod -aG docker $USER`, then re-login)

## Setup

### 1. Configure environment

Copy `.env.example` to `.env` and adjust `DATA_ROOT` / `TZ` / `PUID` / `PGID` if needed
(defaults assume `PUID=1000`, `PGID=1000`, `TZ=Asia/Bangkok`, data at `/mnt/f/film-data`).

### 2. Start the stack

```
docker compose up -d
```

This brings up:

| Service      | URL                     | Purpose                                          |
|--------------|-------------------------|----------------------------------------------------|
| Prowlarr     | http://localhost:9696   | Indexer manager                                    |
| Radarr       | http://localhost:7878   | Movie automation                                   |
| Sonarr       | http://localhost:8989   | TV automation                                       |
| Bazarr       | http://localhost:6767   | Subtitle automation for Radarr/Sonarr libraries      |
| qBittorrent  | http://localhost:8080   | Download client                                     |
| FlareSolverr | http://localhost:8191   | Cloudflare-challenge solver proxy for indexers      |
| Jellyfin     | http://localhost:8096   | Media playback                                      |
| Recyclarr    | (no UI — CLI only)      | Syncs TRaSH quality profiles/custom formats on demand |

### 3. Configure qBittorrent

1. Open http://localhost:8080.
2. Get the temporary admin password from the logs:
   ```
   docker logs qbittorrent | grep -A2 "temporary password"
   ```
3. Log in with username `admin` and the temporary password.
4. Go to the gear/wrench icon → **Options → WebUI**, set a permanent username/password
   under **Authentication**, then **Save**. (The temp password resets on every
   container restart until you do this.)
5. Go to **Options → Downloads**:
   - **Default Save Path:** `/data/torrents`
   - Enable **"Keep incomplete torrents in"** → `/data/torrents/incomplete`
6. **Save**.
7. Recommended — improve peer discovery on weakly-seeded public releases. Under
   **Options → BitTorrent**, enable **"Automatically add these trackers to new
   downloads"** and paste a curated public-tracker list:
   ```
   curl -s https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt
   ```
   This only affects torrents added *after* the change, and does nothing for
   private trackers (which reject foreign announce URLs).

### 4. Configure Prowlarr (indexers)

1. Open http://localhost:9696.
2. (Optional) **Settings → General** → set up authentication.
3. **Indexers → Add Indexer** → add your trackers (public, e.g. 1337x/YTS, or private
   tracker accounts). Test + Save each one.
4. Some indexers (e.g. 1337x) sit behind Cloudflare's bot protection and reset the
   connection for non-browser clients. This stack already runs **FlareSolverr**
   (a headless-browser proxy) to solve that — see Troubleshooting below for how to
   wire it up per-indexer.

### 5. Connect Prowlarr → Radarr/Sonarr

Prowlarr needs to push its indexers into Radarr/Sonarr. This is app-to-app
communication over the Docker network, not through your browser — so it uses
container names (`radarr`, `sonarr`, `prowlarr`), not `localhost`.

Get each app's API key (find it in the container's `config.xml`, or in its
web UI under **Settings → General → Security**):

```
grep -o '<ApiKey>[^<]*</ApiKey>' appdata/radarr/config.xml
grep -o '<ApiKey>[^<]*</ApiKey>' appdata/sonarr/config.xml
```

In Prowlarr:

1. **Settings → Apps → + Add Application → Radarr**
   - **Prowlarr Server:** `http://prowlarr:9696`
   - **Radarr Server:** `http://radarr:7878`
   - **API Key:** (from Radarr's config.xml)
   - **Test**, then **Save**.
2. **+ Add Application → Sonarr**
   - **Prowlarr Server:** `http://prowlarr:9696`
   - **Sonarr Server:** `http://sonarr:8989`
   - **API Key:** (from Sonarr's config.xml)
   - **Test**, then **Save**.

Once saved, Prowlarr automatically syncs its indexers into both apps — no need to
add indexers separately in Radarr/Sonarr.

### 6. Configure Radarr/Sonarr

**Radarr** (http://localhost:7878):

1. **Settings → Media Management → Root Folders** → **Add Root Folder** →
   `/data/media/movies` → Save.
2. **Settings → Download Clients** → **+** → **qBittorrent**:
   - **Host:** `qbittorrent` (container name, not localhost)
   - **Port:** `8080`
   - **Username:** `admin`
   - **Password:** (see local `CREDENTIALS.md`, not committed to git)
   - **Test**, then **Save**.

**Sonarr** (http://localhost:8989): same two steps, but:
- Root folder: `/data/media/tv`
- Download client: same qBittorrent host/port/credentials as above

**Quality profiles / custom formats** are synced from TRaSH Guides via
**Recyclarr** rather than configured by hand:

1. Generate configs from the official templates (already done for this repo):
   ```
   docker compose run --rm recyclarr config create --template hd-bluray-web --template web-1080p
   ```
   This writes `appdata/recyclarr/configs/hd-bluray-web.yml` (Radarr) and
   `appdata/recyclarr/configs/web-1080p.yml` (Sonarr) — real trash_ids pulled
   live from TRaSH's repo, not hand-typed.
2. Edit each generated file's `base_url`/`api_key` to point at the container
   (e.g. `http://radarr:7878`) and the app's API key (from its `config.xml`).
3. Run the sync:
   ```
   docker compose run --rm recyclarr sync
   ```
   This creates the custom formats and a quality profile
   (**"HD Bluray + WEB"** for Radarr, **"WEB-1080p"** for Sonarr) in each app.
4. In each app's UI, confirm the new profile exists under **Settings → Profiles**,
   and select it as the default (**Settings → Media Management**) or per-item
   when adding a movie/show.

Re-run `docker compose run --rm recyclarr sync` any time to pick up upstream
TRaSH Guide changes.

### 7. Configure Jellyfin

1. Open http://localhost:8096 → first-run wizard: pick a language, create your
   admin account.
2. **Add Media Library**:
   - **Content type:** Movies → folder `/data/media/movies`
   - **Content type:** TV Shows → folder `/data/media/tv`
3. Finish the wizard (remote access can stay off — only used via `localhost` here).
4. New media appears automatically as Radarr/Sonarr import it, or trigger
   **Dashboard → Libraries → Scan All Libraries** manually.

## How it works (technical)

Four things explain most of the setup decisions above. Each is covered in depth in
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**, which also has component and
request-lifecycle diagrams.

- **`localhost` vs container names** — containers reach each other by service name
  (`http://radarr:7878`); inside a container `localhost` means only itself. Your
  browser, being outside Docker, uses `http://localhost:7878`. This is the #1 source
  of "can't connect" mistakes when wiring the apps together.
  → [Networking model](docs/ARCHITECTURE.md#networking-model)
- **One shared `/data` root** — lets Radarr/Sonarr *hardlink* finished downloads into
  the library instead of copying: instant, zero extra disk space, and qBittorrent
  keeps seeding the same bytes. Requires one filesystem, hence one root.
  → [Storage model](docs/ARCHITECTURE.md#storage-model-why-hardlinks-instead-of-copies)
- **`PUID`/`PGID`/`TZ`** — make container-written files owned by your host user rather
  than root, and keep logs/scheduling on local time.
  → [Identity and permissions](docs/ARCHITECTURE.md#identity-and-permissions-puid-pgid-tz)
- **FlareSolverr** — a real headless browser that clears Cloudflare bot-checks for
  indexers whose TLS fingerprinting rejects plain HTTP clients. Register once under
  **Settings → Indexer Proxies** (`http://flaresolverr:8191`), then attach it to any
  indexer failing with a Cloudflare-shaped error.
  → [Why FlareSolverr exists](docs/ARCHITECTURE.md#why-flaresolverr-exists)

## Troubleshooting

- **"Unable to connect to indexer... DNS/SSL issues... IPv6"** — the container tried
  the indexer's IPv6 address, which has no real route in this environment (WSL2
  bridge network), and gave up before falling back to IPv4. Fixed by disabling IPv6
  at the container level (`sysctls: net.ipv6.conf.all.disable_ipv6=1` in
  `docker-compose.yml` for prowlarr/radarr/sonarr/flaresolverr) so the app only ever
  attempts IPv4.
- **"blocked by CloudFlare Protection"** or a TLS **"Connection reset by peer"** on a
  specific indexer (e.g. 1337x, EZTV) — that site's Cloudflare bot-detection is
  rejecting the plain HTTP client. Fix: add the indexer as a proxy target for
  **FlareSolverr** (see above), not an IPv6/DNS issue even though the two can look
  similar in the error text.
- **API key was not accepted** (private trackers) — the tracker's own account API
  key/passkey, not anything in this stack. Log into the tracker site directly, find
  your API key under your profile/security settings, and re-paste it into Prowlarr's
  indexer config.
- **`Could not find a part of the path '/data/media/movies/<Title> (Year)'`** on
  import — the "release" was actually a raw Blu-ray disc dump (a `BDMV/STREAM/*.m2ts`
  tree), not a single video file, so Radarr had nothing importable to rename. TRaSH's
  **BR-DISK** custom format is scored `-10000` to reject these, but custom formats
  match on the *release title text* — a disc dump whose title looks like a normal
  encode slips through and is only detectable after downloading. Fix: remove the queue
  item with **Blocklist and Search** so Radarr won't re-grab it and searches for a
  replacement.
- **Torrent stuck at 0% in `metaDL` state forever** — the magnet link has no reachable
  peers, so qBittorrent can't even fetch metadata. Note that Radarr's
  `autoRedownloadFailed` only fires on downloads the client reports as *failed*; a
  perpetually-stalled torrent is never "failed", so it sits there indefinitely and
  needs manual removal (**Blocklist and Search**). Check real seed counts with:
  ```
  docker exec qbittorrent curl -s -b /tmp/qb.cookie \
    http://localhost:8080/api/v2/torrents/info
  ```
- **Downloads are slow rather than stuck** — this is release availability, not client
  config. Speed is capped by how many seeders the chosen release has. Add more
  indexers so Radarr/Sonarr have a deeper pool to pick from; private trackers help
  most, since enforced seeding ratios keep releases alive for years where public
  torrents die off.

## Notes

- If `docker` commands need `sudo` (user not yet in the `docker` group for this
  session), use `sg docker -c "docker compose ..."` as a workaround until re-login.
