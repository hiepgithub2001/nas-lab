# User Guide — day-to-day usage

How to add films and shows, follow a download, and watch. For installing the stack
see the [README](../README.md); for how the pieces fit together see
[ARCHITECTURE.md](ARCHITECTURE.md).

Logins for every app are in `CREDENTIALS.md` (local only, not in git).

## The short version

```
Add in Radarr/Sonarr  →  it downloads by itself  →  appears in Jellyfin  →  watch
```

You interact with **two** apps in normal use: **Radarr/Sonarr** to ask for something,
and **Jellyfin** to watch it. Prowlarr, qBittorrent, FlareSolverr and Recyclarr run in
the background and need no attention unless something breaks.

---

## Adding a movie

1. Open **Radarr** → http://localhost:7878
2. Click **Movies** → **+ Add New** (or use the search bar at the top).
3. Type the title, pick the right match from the results (check the year — remakes and
   same-named films are common).
4. On the add dialog, set:
   - **Root Folder:** `/data/media/movies` — the only one configured, already the default.
   - **Quality Profile:** `HD Bluray + WEB` — the TRaSH-synced profile. The other
     profiles (`Any`, `HD-1080p`, …) are Radarr's built-in defaults and have **no**
     custom-format scoring, so they will happily grab junk releases. Use
     `HD Bluray + WEB`.
   - **Monitor:** `Movie Only` (normal choice).
   - **Minimum Availability:** `Released` is a sane default. `Announced` will start
     hunting for films that aren't out yet and tends to find cam rips.
   - **Start search for missing movie:** tick this if you want it to begin immediately.
5. Click **Add Movie**.

Radarr now asks Prowlarr for releases, scores them against the profile, sends the
winner to qBittorrent, and imports it when finished. No further action needed.

## Adding a TV show

Same flow in **Sonarr** → http://localhost:8989, with these differences:

- **Root Folder:** `/data/media/tv`
- **Quality Profile:** `WEB-1080p` (the TRaSH-synced one)
- **Monitor** decides *which episodes* it chases, and this is the field people get
  wrong:

  | Option | Meaning |
  |---|---|
  | `All Episodes` | Every episode of every season — can be a very large download |
  | `Future Episodes` | Only episodes airing from now on |
  | `Missing Episodes` | Anything you don't already have |
  | `First Season` | Just season 1 — good for trying out a show |
  | `Latest Season` | Only the current season |
  | `None` | Add to library, download nothing (choose episodes by hand later) |

For an ongoing show you're following week to week, `Future Episodes` is usually what
you want. Sonarr will then grab each new episode automatically as it airs.

## Watching

1. Open **Jellyfin** → http://localhost:8096, log in.
2. Your two libraries:
   - **Phim** → movies (`/data/media/movies`)
   - **Chương Trình TV** → TV shows (`/data/media/tv`)
3. Click a title → **Play**.

Newly imported media usually appears within a minute or two. If it hasn't:
**Dashboard → Libraries → Scan All Libraries**.

### Watching on other devices

Jellyfin has apps for Android, iOS, Android TV, Fire TV, Roku, and a web player.
Point them at your machine's LAN address (`http://<your-ip>:8096`) rather than
`localhost` — `localhost` on a phone means the phone itself. Find the address with:

```
hostname -I
```

Note this only works on the same network. To watch from outside your home, see
**[REMOTE-ACCESS.md](REMOTE-ACCESS.md)** — set up with Tailscale, so nothing is
exposed to the public internet.

---

## Subtitles

Subtitles are handled by **Bazarr** → http://localhost:6767. It watches everything in
Radarr and Sonarr, searches subtitle providers, and drops `.srt` files next to each
video. Jellyfin picks those up automatically — no per-film work once it's configured.

Note that many releases already ship with **embedded** subtitles inside the video
file. Those work in Jellyfin without Bazarr doing anything, and Bazarr treats that
language as already satisfied — see
[embedded vs external](#embedded-vs-external-subtitles) below.

One-time setup (languages, providers, Radarr/Sonarr connections) is
**[README step 7](../README.md#7-configure-bazarr-subtitles)**. The rest of this
section assumes that is done.

### Everyday use

Once set up it runs by itself: Bazarr checks new imports, downloads the best matching
subtitle, and retries on a schedule for anything it couldn't find (subtitles often
appear days after a release).

To intervene on a single film — open it in Bazarr and either:

- **Search** (magnifying glass) — pick from available subtitles manually, useful when
  the automatic pick is out of sync.
- **Upload** — supply your own `.srt`.

**Wrong timing?** Subtitles are cut for a specific release. A subtitle timed for the
Theatrical cut will drift on an Extended cut. Fix by picking a subtitle whose release
name matches your file, or nudge it in Jellyfin's player (subtitle offset).

### Watching with subtitles in Jellyfin

**Per film:** during playback, click the **speech-bubble icon** in the player controls
and choose a subtitle track.

**Turn them on permanently** — do this once and every film gets subtitles
automatically:

> Jellyfin → click your **profile icon → Settings → Playback** (or **Subtitles**):
>
> - **Subtitle language preference:** `English` — or `Vietnamese` once Bazarr is
>   fetching those
> - **Subtitle mode:** change `Default` → **`Always Play`**

#### Why subtitles seem "missing" when they exist

This is the usual confusion, and nothing is broken when it happens. Jellyfin ships
with **Subtitle mode = `Default`**, which means *only auto-enable a track the file
itself flags as default*. Many releases flag **no** track as default, and with an
empty language preference Jellyfin then has no reason to turn anything on — so a film
plays with no subtitles even though several tracks are sitting right there.

`Always Play` overrides that: Jellyfin enables a matching subtitle whenever one
exists, regardless of the default flag.

To check what a file actually contains, look at the film's **Media Info** panel in
Jellyfin, which lists every audio and subtitle stream.

#### Embedded vs external subtitles

Two different things, and it matters for troubleshooting:

- **Embedded** — inside the `.mkv` itself, present the moment the film imports. Bazarr
  counts these as already satisfying a language, so it will *not* download a duplicate.
- **External** — the `.srt` files Bazarr writes next to the video.

So a film can have working English subtitles with no `.srt` on disk at all. If Bazarr
reports a language as present but you cannot find a subtitle file, it is embedded.

#### PGS subtitles force a transcode

Blu-ray rips often carry `PGSSUB` subtitles, which are **images rather than text**.
Jellyfin cannot overlay those in the client — it has to burn them into the video,
which forces a CPU transcode (there is no GPU passed into the container). Text formats
(`subrip`/SRT, `ASS`) overlay with no transcode.

If playback is smooth until you enable subtitles and then stutters, this is why.
Workarounds: pick a text-based track if the file has one, or let Bazarr fetch an
external `.srt`, which is always text.

If a subtitle file exists on disk but Jellyfin does not list it, refresh that item's
metadata (**⋯ → Refresh metadata**) so Jellyfin rescans for sidecar files.

## Following a download

Three places to look, in order of usefulness:

**1. Radarr/Sonarr → Activity → Queue** — the one that matters. Shows progress, and
any import problem as a warning icon on the row (hover for the message).

**2. qBittorrent** → http://localhost:8080 — raw torrent view: real download speed,
**seeds and peers**. This is where you see *why* something is slow.

**3. Radarr/Sonarr → Activity → History** — what was grabbed, imported, or failed,
and when. Useful after the fact.

Current state, from the command line:

```
docker exec radarr curl -s -H "X-Api-Key: <radarr-key>" \
  http://localhost:7878/api/v3/queue
```

### Reading the state

| What you see | Meaning | Do |
|---|---|---|
| `downloading`, healthy speed | Working normally | Wait |
| `metaDL`, 0% , 0 seeds | Dead torrent, no peers — will never finish | Remove + **Blocklist and Search** |
| Slow but progressing, few seeds | Poorly-seeded release; valid but may take days | Let it run, or blocklist to try another |
| Warning icon on the queue row | Import failed | Hover for reason; often needs blocklist + search |
| Finished in qBittorrent, absent from Jellyfin | Import or scan didn't happen | Check Radarr History, then scan Jellyfin |

## Rejecting a bad release and trying another

The most common maintenance action. Use it when a download is dead, absurdly slow, or
failed to import:

1. **Activity → Queue**, find the row.
2. Click the **trash/remove** icon.
3. In the dialog:
   - **Remove from Download Client:** on — also deletes it from qBittorrent.
   - **Blocklist Release:** choose **`Blocklist and Search`** — remembers this exact
     release as bad so it is never re-grabbed, and immediately searches for a
     replacement.

Radarr also does this automatically for downloads that *fail*
(`autoRedownloadFailed`), which is why you may see several grabs chain by themselves.
It does **not** cover torrents merely stalled forever — those need the manual removal
above. See
[failure handling](ARCHITECTURE.md#failure-handling-what-is-automatic-and-what-is-not).

## Choosing a release yourself

When automatic picks keep disappointing, take the wheel:

1. Open the movie/series → click the **magnifying glass / Interactive Search**.
2. You get every candidate with its **size, seeders, indexer, and custom-format
   score**, plus a reason for any that were rejected.
3. Prefer a healthy **seeder count** over the highest score — a 2160p release with
   1 seed is worse in practice than a 1080p release with 40.
4. Click the download icon on the row to grab that specific release.

## Removing something

- **Stop chasing it, keep the file:** open the item and toggle **Monitored** off.
- **Delete it entirely:** open the movie/series → **Delete**, and tick
  **delete files** if you want the media gone from disk too.
- Deleting a movie in Radarr also wipes its history, so do it after you've finished
  diagnosing anything.

---

## Routine upkeep

**Refresh TRaSH quality rules** — occasionally, to pick up upstream scoring changes:

```
docker compose run --rm recyclarr sync
```

**Update the containers** — the images are pinned to `latest` (except Recyclarr):

```
docker compose pull && docker compose up -d
```

**Watch disk space** — 1080p films run 2–20 GB, 2160p far more, and seeding keeps the
original copy in `/data/torrents` alongside the hardlinked library entry (the hardlink
itself costs nothing; the seeding copy is the same bytes, not a second copy).

```
df -h /mnt/f
du -sh /mnt/f/film-data/*
```

**Improve results** — if searches keep returning dead or low-quality releases, the fix
is more or better sources, not settings: add indexers in Prowlarr, or use a private
tracker where enforced seeding ratios keep releases healthy for years. See
[why speed is a sourcing problem](ARCHITECTURE.md#why-download-speed-is-a-sourcing-problem-not-a-config-problem).

## When something's wrong

Error messages and their causes are collected in the
[README troubleshooting section](../README.md#troubleshooting) — Cloudflare blocks,
IPv6/DNS failures, disc-dump import errors, stalled torrents.

Quick checks:

```
docker compose ps                  # is everything running?
docker compose logs -f radarr      # live logs for one service
```

Each app also has its own health panel at **System → Status**, which surfaces
misconfiguration (unreachable download client, missing root folder, indexer errors)
before it silently costs you a download.
