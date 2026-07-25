# Jellyfin — watching

http://localhost:8096 · login in [`CREDENTIALS.md`](../CREDENTIALS.md)

Jellyfin serves the organised `/data/media` library for playback — the app you open to
actually watch.

## Watching

1. Open Jellyfin, log in.
2. Libraries: **Phim** → movies (`/data/media/movies`), **Chương Trình TV** → TV
   (`/data/media/tv`).
3. Click a title → **Play**. During playback, the **speech-bubble icon** picks a
   subtitle track.

## Watching on other devices

Jellyfin has apps for Android, iOS, Android TV, Fire TV, Roku, plus the web player.
Point them at your machine's **LAN address** (`http://<your-ip>:8096`), not `localhost`
(on a phone that means the phone). Find the address with `hostname -I`.

### Watching remotely

Off your home network, use Tailscale — nothing is exposed to the public internet. Full
guide: [REMOTE-ACCESS](../REMOTE-ACCESS.md). Short version: install Tailscale on the
device, sign in with the **same account** as the server, and point the Jellyfin app at
the server's tailnet address (`http://<machine>.<tailnet>.ts.net:8096`).

Remote playback is limited by your home **upload** bandwidth and by CPU transcoding
(no GPU is passed in) — set a bitrate cap in the app for remote playback, and prefer
1080p sources over 4K for anything you'll watch away from home.

## A film downloaded but isn't here

Work **backwards** through the pipeline — each check says whether to look further back.
Usually it's step 4.

1. **Finished downloading?** — qBittorrent shows `100%`, state `stalledUP`/`queuedUP`.
   Still going, or stuck at `metaDL` 0 seeds → that's the answer, see
   [qbittorrent.md](qbittorrent.md).
2. **Radarr/Sonarr imported it?** — Activity → History shows *imported*. `hasFile=false`
   at 100% means the **import failed** — check Activity → Queue for a warning, see
   [radarr.md → import failed](radarr.md#import-failed-could-not-find-a-part-of-the-path).
3. **File in the library folder?** — `ls "$DATA_ROOT/media/movies/"`. Present but not in
   Jellyfin ⇒ scan issue (step 4). Absent ⇒ import didn't finish (step 2).
4. **Force a scan** — **Dashboard → Libraries → Scan All Libraries.** If it appears, it
   was only scan timing.

### Quick lookup

| qBittorrent | `hasFile` | In Jellyfin | Meaning |
|---|---|---|---|
| <100% | false | no | still downloading — wait |
| 100% | **false** | no | **import failed** — check Radarr queue |
| 100% | true | **no** | **scan issue** — force a scan |
| 100% | true | yes | working |

### Make it automatic (recommended)

Jellyfin's scheduled scan runs only **every 12 hours** and its real-time watcher
doesn't reliably catch imports on this filesystem — so films can sit invisible for
hours. Fix it once by having Radarr/Sonarr notify Jellyfin on import:

1. **Jellyfin → Dashboard → API Keys → +**, create a key (name it `Radarr`).
2. **Radarr → Settings → Connect → + → Emby / Jellyfin**: Host `jellyfin`, Port `8096`,
   the API key, **Update Library on**, **On Import / On Upgrade on**. Test → Save.
3. Repeat in **Sonarr → Settings → Connect** (same key works).

A successful test means Jellyfin is reachable and the key accepted. After this, imports
appear immediately.

## Subtitles

### Turn them on by default

Do this once: **profile icon → Settings → Playback** (or **Subtitles**):

- **Subtitle language preference:** `English` (or `Vietnamese` once Bazarr fetches
  those)
- **Subtitle mode:** change `Default` → **`Always Play`**

### Why subtitles seem "missing" when they exist

Jellyfin ships with **Subtitle mode = `Default`**, which only auto-enables a track the
*file* flags as default. Many releases flag none, and with an empty language preference
Jellyfin has no basis to pick one — so a film plays with no subtitles even though
several tracks are right there. `Always Play` + a language preference fixes it by giving
Jellyfin a real basis to choose.

**The wrong-track trap:** with no language preference, Jellyfin tie-breaks among
same-language tracks arbitrarily and can land on the **director's commentary** or an
SDH track instead of plain dialogue — which reads as "subtitles are broken". Setting the
language preference makes it pick the right one. Check a file's tracks in its **Media
Info** panel.

### Embedded vs external, PGS transcoding

- A film's subtitles may be **embedded** in the `.mkv` or **external** `.srt` files —
  both play; see [bazarr.md](bazarr.md#embedded-vs-external).
- **PGS subtitles are images**, not text (common on Blu-ray rips). Jellyfin can't
  overlay them client-side — it burns them into the video, forcing a **CPU transcode**.
  Text formats (SRT/`subrip`, ASS) overlay with no transcode. If playback is smooth
  until you enable subtitles and then stutters, this is why — pick a text track or let
  Bazarr fetch an external `.srt`.
- If a subtitle file exists on disk but Jellyfin doesn't list it, **⋯ → Refresh
  metadata** on that item so it rescans for sidecar files.

### Dual-language subtitles

Jellyfin can't show two tracks at once. To display two languages together, Bazarr
merges them into one track — see [bazarr.md → dual-language](bazarr.md#dual-language-subtitles-post-processing).

## Transcoding — why playback sometimes stutters

Transcoding here is **CPU-only** (no GPU passed into the container), so it's expensive.
Common triggers:

- **4K HDR** (e.g. a 2160p remux) on a browser or a device that can't direct-play HEVC
  — one such transcode can peg the CPU, and HDR→SDR tone-mapping looks washed out.
- **DTS / TrueHD audio** the client can't decode. Browsers can't; a native app with a
  receiver can. (Options for these are under **Settings → Playback → Video Advanced**,
  but only enable if your device really supports them.)
- **PGS subtitles** (above).

Check what's happening during playback: **Dashboard → Activity** shows *Direct Play* vs
*Transcoding*. If it stutters, the fix is usually a native client (**Jellyfin Media
Player** on desktop, or a TV app) that can direct-play HEVC/HDR/TrueHD — or a smaller
source (1080p over 4K).

## Chapter markers and language

- The tick marks on the progress bar are the file's **chapter markers**, read from the
  `.mkv` (more chapters = denser marks). They're not configurable in the web client;
  they only vanish if the file has no chapter metadata.
- **UI/metadata language:** per-user display language is **profile icon → Settings →
  Display**; server-wide display + metadata language is **Dashboard → General**.
  Existing items keep their old metadata until you **⋯ → Refresh metadata → Replace
  all metadata**.

## Don't install the Jellyfin *server* on this PC

The stack already runs the Jellyfin **server** in Docker. The Windows installer's
"Basic Install" vs "Install as a Service" prompt is for a *second server* — you don't
want that (it'd fight for port 8096 with an empty library). To watch on the PC with
better 4K/HDR playback, install **Jellyfin Media Player** (a *client*) and point it at
`http://localhost:8096`.
