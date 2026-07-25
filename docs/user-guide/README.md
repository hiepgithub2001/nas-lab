# User Guide

Day-to-day use, one page per service. For first-time setup see
[QUICKSTART](../QUICKSTART.md); for how the parts fit together see
[ARCHITECTURE](../ARCHITECTURE.md). Logins are in [`CREDENTIALS.md`](../CREDENTIALS.md).

## The flow

```
You add a title in Radarr/Sonarr
        │
        ▼
Prowlarr searches indexers ──► Radarr/Sonarr pick the best release
        │
        ▼
qBittorrent downloads it ──► Radarr/Sonarr hardlink into the library
        │
        ▼
Bazarr fetches subtitles ──► Jellyfin plays it
```

In normal use you touch **two** apps: **Radarr/Sonarr** to ask for something, and
**Jellyfin** to watch it. The rest run in the background.

## Per-service guides

| Guide | Use it for |
|---|---|
| [Prowlarr](prowlarr.md) | Indexers, Cloudflare/FlareSolverr, connection errors |
| [Radarr](radarr.md) | Adding movies, quality profiles, failed imports |
| [Sonarr](sonarr.md) | Adding TV, season packs, why nothing downloads |
| [qBittorrent](qbittorrent.md) | Download speed, queue limits, stalled torrents |
| [Bazarr](bazarr.md) | Subtitles, providers, dual-language tracks |
| [Jellyfin](jellyfin.md) | Watching, subtitle display, transcoding, remote |

## Watching from outside the home

Remote access is via Tailscale — see [REMOTE-ACCESS](../REMOTE-ACCESS.md) for the full
guide, or [Jellyfin → watching remotely](jellyfin.md#watching-remotely) for the short
version.

## When something's wrong

Each service guide has its own troubleshooting section. The most common issues:

| Symptom | Guide |
|---|---|
| Added a title, nothing downloads | [Sonarr](sonarr.md) / [Radarr](radarr.md) — usually needs a search |
| Downloaded, but not in Jellyfin | [Jellyfin](jellyfin.md#a-film-downloaded-but-isnt-here) |
| Downloads are slow or stuck | [qBittorrent](qbittorrent.md#speed) |
| Indexer won't connect | [Prowlarr](prowlarr.md#connection-errors) |
| No subtitles | [Bazarr](bazarr.md) + [Jellyfin subtitles](jellyfin.md#subtitles) |
