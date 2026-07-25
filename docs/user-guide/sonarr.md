# Sonarr — TV automation

http://localhost:8989 · login in [`CREDENTIALS.md`](../CREDENTIALS.md)

Sonarr is Radarr for TV — same idea, but it tracks *episodes* and *seasons*, which adds
a few wrinkles worth understanding.

## Adding a series

1. **Series → + Add New**, search, pick the match.
2. On the add dialog:
   - **Root Folder:** `/data/media/tv`
   - **Quality Profile:** `WEB-1080p` (the TRaSH-synced one)
   - **Monitor:** decides *which* episodes it chases — see below
   - **Start search for missing episodes:** tick it, or nothing downloads (the #1
     "I added a show but nothing happened" cause)
3. **Add Series.**

### The Monitor field

| Option | Meaning |
|---|---|
| `All Episodes` | every episode of every season — can be a huge download |
| `Future Episodes` | only episodes airing from now on |
| `Missing Episodes` | anything you don't already have |
| `First Season` | just season 1 — good for trying a show |
| `Latest Season` | only the current season |
| `None` | add but download nothing (pick episodes by hand later) |

For a show you're following week to week, `Future Episodes` is usually right — Sonarr
grabs each new episode automatically as it airs (via RSS Sync, below).

## "I added a series but nothing downloads"

Almost always one of two things:

1. **You didn't tick "search on add".** Adding a series only *downloads immediately* if
   that box was checked. Otherwise it waits. Fix: open the series → click the **search
   icon** (season or whole-series), or **wait for RSS** (new episodes only).
2. **It searched but found nothing acceptable.** Check **Activity → Queue** (empty?) and
   the series **History**. For an old completed series, individual-episode releases are
   scarce — see season packs below.

## Two ways episodes download

| Mechanism | Covers | Automatic? |
|---|---|---|
| **Search** (on-add, or the 🔍 button) | the back catalog (old seasons) | one-off |
| **RSS Sync** (every ~15 min) | new episodes as they're published | yes, ongoing |

RSS is forward-looking — it only catches releases published *after* you add the series.
So the back catalog always needs a **search**; new episodes come via **RSS**. RSS needs
each indexer's **"Enable RSS"** on (it is, by default).

## Downloading many episodes at once — season packs

A **season pack** is one torrent containing a whole season. To get many episodes in one
grab, do a **season search**, not an episode search: open the series → a **season** →
the search icon. Sonarr then looks for packs.

Why a season search sometimes still grabs nothing (real example, Breaking Bad on public
indexers):

| Rejection | Meaning |
|---|---|
| *"WEBDL-2160p is not wanted in profile"* | 4K packs exist but your 1080p profile blocks them (correct) |
| *"Multi-season releases are not supported"* | the only pack is a whole-series bundle (S01–S05), which Sonarr can't use |
| *"matches an alias for series with TVDB ID …"* | it's a different show, correctly rejected |

So for old series on public indexers, good single-season 1080p packs are often just not
available. Options: **Interactive Search** to hand-pick, widen the quality profile to
allow more, or use a **private tracker** (has proper single-season packs with seeders).

## Interactive Search

Open a season → **magnifying glass** → see every release with size, seeders, and
rejection reasons. Force-grab one by clicking its download icon (overrides the
profile). Prefer seeders over resolution.

## Everything else

Rejecting bad releases, failed imports, notifying Jellyfin — identical to Radarr, see
[radarr.md](radarr.md). qBittorrent-side speed/stall issues are in
[qbittorrent.md](qbittorrent.md).
