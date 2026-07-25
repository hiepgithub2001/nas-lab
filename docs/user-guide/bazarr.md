# Bazarr — subtitles

http://localhost:6767 · login in [`CREDENTIALS.md`](../CREDENTIALS.md)

Bazarr watches the Radarr/Sonarr libraries, searches subtitle providers, and writes
`.srt` files next to each video. Jellyfin picks them up automatically. No per-film work
once it's set up.

## Setup essentials

1. **Settings → Languages** — add your languages, make a **Language Profile**, and
   **enable it as the Default Language Profile for both Movies and Series**. Skip that
   last step and Bazarr silently ignores everything (items get no profile → no search,
   no error).
2. **Settings → Providers** — add at least two; each has its own coverage and limits.
3. **Settings → Radarr / Sonarr** — connect with container names (`radarr`, `sonarr`)
   and API keys, then **restart Bazarr** (connection settings are read only at startup).
   Confirm it worked: `docker compose logs bazarr | grep SignalR` should show both
   clients "connected and waiting for events".

Its library stays **empty until something is imported** — it tracks media on disk, not
your wishlist. That's expected, not a fault.

## Embedded vs external

- **Embedded** — subtitles inside the `.mkv` itself, present the moment the film
  imports. Watchable in Jellyfin, but **not a file** on disk.
- **External** — the `.srt` files Bazarr downloads next to the video.

Two consequences that cause most confusion:

- A film can have **working English subtitles with no `.srt` on disk** — they're
  embedded. If Bazarr reports a language present but you can't find a file, it's
  embedded.
- With **"Treat Embedded Subtitles as Downloaded"** on (Settings → Subtitles →
  Embedded Subtitles Handling), Bazarr counts an embedded language as satisfied and
  **won't download an external file** for it. That's fine for watching, but it starves
  anything that needs a real `.srt` (like the dual-subtitle merge below). Turn it
  **off** to force external downloads.

## Providers — coverage and the usual failures

Provider status is at **System → Providers**. Common blockers seen in practice:

| Provider | Note |
|---|---|
| **OpenSubtitles.com** | broadest catalogue incl. Vietnamese; needs a **real free account** and one **website login** before the API works. `admin`/`admin123` is not a valid account — it throttles on `AuthenticationError`. |
| **Zimuku** | good Chinese, but requires a **paid AntiCaptcha key** — fails with *"AntiCaptcha key not given"* without it. |
| **Assrt** | strong Chinese, only needs a **free API token** from assrt.net — the low-friction Chinese option. |
| BSplayer / SuperSubtitles | "Good" status but thin coverage for VI/ZH. |

If a language stays missing, it's almost always the provider, not Bazarr. A search
across broken providers just repeats the same failure.

## Re-scan / re-check / re-download

| Want | Do |
|---|---|
| Bazarr to notice new files on disk | **System → Tasks → "Sync with Radarr/Sonarr"** |
| Re-search providers for all missing | **Wanted** tab → **Search All** |
| Re-search one film | open it → 🔍 on the language row |
| Replace a wrong/out-of-sync subtitle | open it → language row → pick a different result, or delete the `.srt` and re-search |

## Dual-language subtitles (post-processing)

Jellyfin can't show two subtitle tracks at once. To get e.g. English + Vietnamese
together, `scripts/merge-subs.py` combines two `.srt` files into one **new** track. It
**never edits the originals** — the single-language tracks stay selectable.

Deployed at `/config/scripts/merge-subs.py` (host `appdata/bazarr/scripts/`). Two
layouts:

| `--layout` | Output | On screen |
|---|---|---|
| `topbottom` | `.ass` | primary (English) bottom in **yellow**, secondary top in **white** |
| `stacked` | `.srt` | both stacked at the bottom |

Colours/positions are baked into the `.ass` (Jellyfin's subtitle-appearance settings
won't override them). To change them, edit the two `Style:` lines in
`scripts/merge-subs.py` — the 4th field is the colour in ASS `&HAABBGGRR` form
(`&H0000FFFF` = yellow, `&H00FFFFFF` = white).

**Wire it in** — **Settings → Subtitles → Post-Processing**, enable **Custom
Post-Processing**, command:

```
python3 /config/scripts/merge-subs.py "{{subtitles}}" "{{subtitles_language_code2}}" --primary en --layout topbottom
```

`{{subtitles}}` is the file Bazarr just downloaded, `{{subtitles_language_code2}}` its
language code — Bazarr fills both in. With **no `--secondary`**, it pairs the primary
(`en`) against **every** other language present, so one hook produces `Dual EN-VI`,
`Dual EN-ZH`, etc. Save — no restart needed.

**Requirements and gotchas:**

- It merges **files only** — a language embedded in the `.mkv` has no file to pair. To
  fix, turn off "Treat Embedded Subtitles as Downloaded" so Bazarr downloads a real
  `.en.srt` ([above](#embedded-vs-external)).
- Post-processing fires only on **new** downloads. To merge files already on disk, run
  it by hand:
  ```
  docker exec bazarr python3 /config/scripts/merge-subs.py \
    "/data/media/movies/<Film>/<Film>.en.srt" en --primary en --layout topbottom
  ```
  then refresh that item's metadata in Jellyfin so the new track appears.
- Bazarr's Chinese codes (`zt` Traditional, `zs` Simplified) are handled — they map to
  `zh` automatically.

## No authentication by default

Bazarr ships with `auth.type = None` — its web UI is open to anyone who can reach port
6767 (its API stays key-protected). Fine on localhost; set a login under **Settings →
General → Security** before enabling [remote access](../REMOTE-ACCESS.md).
