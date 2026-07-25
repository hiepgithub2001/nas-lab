# qBittorrent — the download client

http://localhost:8080 · login in [`CREDENTIALS.md`](../CREDENTIALS.md)

qBittorrent does the actual downloading. Radarr/Sonarr send it torrents; it fetches
them into `/data/torrents`, then the *arr apps hardlink the finished file into the
library. You rarely open it directly — mostly to diagnose speed.

## Speed

**First, the counter-intuitive part: your internet connection is almost never the
bottleneck.** A torrent's speed is capped by its **seeders** — how many people are
sharing that exact release and how fast *they* can upload. A fast line cannot make a
poorly-seeded torrent fast.

Check the real story per torrent — the **Seeds** column:

| Seeds | Meaning |
|---|---|
| 0 | Dead torrent — will never finish, remove it |
| 1–5 | Poorly seeded — slow, may take days |
| 20+ | Healthy — should be fast |

To see it from the command line:

```
docker exec qbittorrent curl -s -b /tmp/qb.cookie \
  http://localhost:8080/api/v2/torrents/info
```

### If downloads are slow

1. **Check seeds.** If the active torrents have 0–5 seeds, that *is* the problem —
   nothing about qBittorrent settings will help. Get a better-seeded release
   (blocklist + search in Radarr/Sonarr) or a better indexer / private tracker.
2. **Avoid 4K unless you mean it.** 2160p releases are huge (5–15 GB/episode) and have
   far fewer seeders than 1080p. Grabbing 4K by accident is a common cause of a queue
   full of slow or dead torrents.
3. **Upload saturation** — only relevant on a *slow-upload* connection. If seeding is
   maxing your upload, download can starve. Cap upload to ~80% of capacity under
   **Options → Speed**. On a fast/symmetric line this is unnecessary; don't cap for no
   reason. Measure your upload with `speedtest-cli`.

### Supplemental trackers (helps thin swarms)

**Options → BitTorrent → "Automatically add these trackers to new downloads"**, paste:

```
curl -s https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt
```

Only affects torrents added *after* the change; does nothing for private trackers.

## Torrent queueing — how many run at once

**Options → BitTorrent → Torrent Queueing.** Three limits:

| Setting | Controls |
|---|---|
| **Max active downloads** | how many torrents can be *downloading* at once |
| **Max active uploads** | how many *completed* torrents can *seed* at once |
| **Max active torrents** | the *combined* ceiling across both |

Downloads and uploads are two lanes with their own limits; max active torrents is the
total cars allowed across both lanes. Example (8 / 10 / 20): 8 downloading + 10 seeding
= 18, under the 20 cap, so all run; but 15 + 10 = 25 would hit the cap and stop 5.

**"Do not count slow torrents"** (recommended on): a torrent stalled near 0 KB/s stops
counting against the download limit after a timeout, so dead torrents don't hog active
slots and block live ones.

## Stalled at 0% (`metaDL`)

A torrent stuck in `metaDL` at 0% can't even fetch its metadata — the magnet has no
reachable peers. It will never progress. This is *not* reported as "failed", so
Radarr/Sonarr won't auto-replace it. Remove it manually with **Blocklist and Search**
in Radarr/Sonarr (not just in qBittorrent, or it gets re-grabbed).

## Save paths

- **Default Save Path:** `/data/torrents`
- **Keep incomplete torrents in:** `/data/torrents/incomplete`

These are *container* paths (`/data` = the shared media root). Radarr/Sonarr mount the
same root, which is what lets them hardlink finished downloads into the library
instead of copying. See [ARCHITECTURE → storage](../ARCHITECTURE.md#storage-model-why-hardlinks-instead-of-copies).

## Password

If the login ever reverts to a generated temporary one (happens if a permanent one was
never set), recover it from the logs:

```
docker logs qbittorrent | grep -A2 "temporary password"
```

Then set a permanent password under **Options → WebUI → Authentication**.
