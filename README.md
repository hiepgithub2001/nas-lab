# self-host-film

Self-hosted media automation stack on bare metal + Docker (WSL2), based on
[TRaSH Guides](https://trash-guides.info/).

## How it fits together

1. **Prowlarr** searches indexers/trackers for releases and knows where to find them.
2. **Radarr** (movies) / **Sonarr** (TV) decide what to grab — track your wishlist,
   pick the best release per your quality profile, and send it to the download client.
3. **qBittorrent** downloads the actual files via BitTorrent into `/data/torrents`.
4. Radarr/Sonarr detect the finished download and **hardlink** it into
   `/data/media/movies` or `/data/media/tv` (renamed) — no duplicate disk usage,
   qBittorrent keeps seeding the original file.
5. **Jellyfin** serves the organized `/data/media` library for playback.

## Folder structure

Everything lives under one root so hardlinks work across containers:

```
/mnt/f/film-data
├── media/
│   ├── movies/
│   └── tv/
└── torrents/
    ├── movies/
    └── tv/
```

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

| Service     | URL                     | Purpose                  |
|-------------|-------------------------|---------------------------|
| Prowlarr    | http://localhost:9696   | Indexer manager           |
| Radarr      | http://localhost:7878   | Movie automation          |
| Sonarr      | http://localhost:8989   | TV automation              |
| qBittorrent | http://localhost:8080   | Download client            |
| Jellyfin    | http://localhost:8096   | Media playback              |

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
   - (Optional) enable **"Keep incomplete torrents in"** → `/data/torrents/incomplete`
6. **Save**.

### 4. Configure Prowlarr (indexers)

1. Open http://localhost:9696.
2. (Optional) **Settings → General** → set up authentication.
3. **Indexers → Add Indexer** → add your trackers (public, e.g. 1337x/YTS, or private
   tracker accounts). Test + Save each one.

### 5. Connect Prowlarr → Radarr/Sonarr

*(next step — not yet documented)*

### 6. Configure Radarr/Sonarr

- Root folder: `/data/media/movies` (Radarr) / `/data/media/tv` (Sonarr)
- Download client: qBittorrent
- Quality profiles / custom formats: per TRaSH Guides recommendations

*(next step — not yet documented)*

### 7. Configure Jellyfin

- Add library pointing at `/data/media`

*(next step — not yet documented)*

## Notes

- If `docker` commands need `sudo` (user not yet in the `docker` group for this
  session), use `sg docker -c "docker compose ..."` as a workaround until re-login.
