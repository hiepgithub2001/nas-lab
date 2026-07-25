# Radarr — movie automation

http://localhost:7878 · login in [`CREDENTIALS.md`](../CREDENTIALS.md)

Radarr tracks the movies you want, picks the best release, sends it to qBittorrent, and
imports the finished file into `/data/media/movies`.

## Adding a movie

1. **Movies → + Add New**, search, pick the right match (check the **year** — remakes
   and same-named films are common).
2. On the add dialog:
   - **Root Folder:** `/data/media/movies` (the only one; already default)
   - **Quality Profile:** `HD Bluray + WEB` — the TRaSH-synced profile. **Use this
     one.** The built-in profiles (`Any`, `HD-1080p`, …) have no custom-format scoring
     and will grab junk.
   - **Minimum Availability:** `Released` (safe). `Announced` hunts for films not out
     yet and tends to find cam rips.
   - **Start search for missing movie:** tick it to begin immediately.
3. **Add Movie.**

## Quality profiles

The TRaSH-synced `HD Bluray + WEB` profile is what makes Radarr prefer good releases
and reject bad ones (cams, disc dumps, wrong-audio dual groups). It's maintained by
[Recyclarr](../ARCHITECTURE.md#components); re-sync to update:

```
docker compose run --rm recyclarr sync
```

**Avoid 4K unless you want it.** 2160p files are huge and far less seeded. If you keep
getting slow/dead downloads, check you didn't force a 2160p grab — 1080p downloads much
faster.

## When automatic picks are bad — Interactive Search

Open the movie → click the **magnifying glass**. You get every candidate with its
**size, seeders, indexer, and custom-format score**, plus the reason any was rejected.

Prefer a healthy **seeder count** over the highest score — a 2160p release with 1 seed
is worse in practice than a 1080p with 40. Click a row's download icon to force-grab
that specific release (this bypasses the profile, useful to override a rejection).

## Rejecting a bad release

The most common maintenance action — for a download that's dead, absurdly slow, or
failed to import:

1. **Activity → Queue**, find the row.
2. Click the **remove/trash** icon.
3. In the dialog:
   - **Remove from Download Client:** on
   - **Blocklist Release:** **`Blocklist and Search`** — remembers this exact release
     as bad so it's never re-grabbed, and immediately searches for a replacement.

Radarr does this **automatically** for downloads the client reports as *failed*
(`autoRedownloadFailed`), which is why you sometimes see several grabs chain by
themselves. It does **not** cover torrents merely *stalled* forever (0 seeds, `metaDL`)
— those need the manual removal above. See
[ARCHITECTURE → failure handling](../ARCHITECTURE.md#failure-handling-what-is-automatic-and-what-is-not).

## Import failed: "Could not find a part of the path"

If qBittorrent shows 100% but the film never reaches the library, and the error is
`Could not find a part of the path '/data/media/movies/<Title> (Year)'`:

The "release" was a raw **Blu-ray disc dump** (a `BDMV/STREAM/*.m2ts` folder tree), not
a single video file, so there was nothing importable to rename. TRaSH's **BR-DISK**
custom format scores these `-10000` to reject them, but custom formats match on the
*release title text* — a disc dump whose title looks like a normal encode slips through
and is only detectable after downloading. Fix: remove with **Blocklist and Search**.

## Removing a movie

- **Stop chasing it, keep the file:** toggle **Monitored** off.
- **Delete entirely:** open the movie → **Delete**, tick **delete files** to remove it
  from disk too. Note this also wipes its history, so do it after diagnosing anything.

## Notifying Jellyfin on import

Radarr can tell Jellyfin to scan the moment a film imports, so it appears immediately
instead of waiting on Jellyfin's 12-hour scan — see
[Jellyfin → a film downloaded but isn't here](jellyfin.md#a-film-downloaded-but-isnt-here).
