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

Remote playback of a 4K file transcodes down to fit your connection — handled on the
GPU (see [Transcoding](../technical/TRANSCODING.md)), so it's smooth. The main limit is
your home **upload** bandwidth; set the streaming quality in the app to match (20-40
Mbps is plenty for 1080p to a phone).

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

Transcoding on this system runs on the **RTX 4080 Super via NVENC**, so it's fast — a
4K HDR transcode that would peg a CPU runs at a fraction of load. Full detail (how the
Direct Play vs transcode decision is made, encode/decode, verification) is in
**[TRANSCODING.md](../technical/TRANSCODING.md)**. The essentials:

- A file is **transcoded** only when the client can't play the original — wrong codec
  (4K HEVC/AV1 on a phone), too-high bitrate, unsupported container, or image-based
  **PGS** subtitles. Otherwise it's **Direct Play** (untouched, best quality).
- If playback looks **soft**, it's almost always the **client app's quality setting**
  set too low, not the server — raise it in the app (tap player → quality → 20-40 Mbps
  or Auto). See [TRANSCODING → the client bitrate trap](../technical/TRANSCODING.md#quality-and-the-client-bitrate-trap).
- Check what's happening live: **Dashboard → Activity** shows *Direct Play* vs
  *Transcode (hw)*. The `(hw)` confirms it's on the GPU.

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
