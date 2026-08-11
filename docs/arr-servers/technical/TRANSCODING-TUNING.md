# Transcoding tuning — server-side settings for the NAS iGPU

What to set in Jellyfin's transcoding config, what each setting actually *does*, and
the measurements behind each recommendation. Written after profiling the NAS under two
concurrent 4K streams on **2026-08-12** (local, UTC+07).

For the general model of Direct Play vs transcode see [TRANSCODING](TRANSCODING.md);
for reading ffmpeg's own commands and logs see [FFMPEG](FFMPEG.md).

> **⚠️ Scope: Intel iGPU only.** Every recommendation here is specific to the **Iris Xe
> iGPU on the NAS** (`/dev/dri/renderD128`, QSV/VA-API). They are **not** valid for the
> PC's RTX 4080 Super — several would be wrong or actively harmful there. See
> [If Jellyfin moves back to the RTX 4080](#if-jellyfin-moves-back-to-the-rtx-4080).
>
> **Note:** [TRANSCODING.md](TRANSCODING.md) still describes the RTX 4080 / NVENC setup
> from before the 2026-08-10 migration. Jellyfin now runs on the **NAS**, on the Intel
> iGPU — every NVENC/NVDEC reference in that doc is stale. This page reflects the
> current hardware.

---

## TL;DR — the settings that matter

| Setting | Current | Recommended | Effect |
|---|---|---|---|
| `EnableIntelLowPowerHevcHwEncoder` | `false` | **`true`** | **4.75x throughput** (measured) |
| `EnableIntelLowPowerH264HwEncoder` | `false` | **`true`** | same, for H.264 output |
| `EncoderPreset` | `slow` | **`veryfast`** | stops requesting the expensive encode path |
| `EnableThrottling` | `false` | **`true`** | frees the GPU between segments — the multi-device fix |
| *client* quality setting — **LAN clients only** | capped | **Auto** | Direct Play *where the client can play the file* — no GPU at all |

The first one is worth more than everything else on this page combined. The last one
isn't a server setting at all, and applies only to clients on the LAN — see
[the caveat below](#the-client-side-win-and-its-limits), because it does **not** mean
"nobody ever needs to transcode."

File: `appdata/jellyfin/encoding.xml` (mounted into the container as `/config`).
Every setting also has a UI equivalent under **Dashboard → Playback → Transcoding**.

---

## The hardware, and why "the GPU" is too coarse a word

Jellyfin transcodes on the NAS's integrated GPU:

- **Host:** `ubuntu-2404`, Intel Core i5-13500H (Raptor Lake-H), 16 threads, 31 GB RAM
- **GPU:** Iris Xe iGPU, exposed at `/dev/dri/renderD128`, `card1`
- **Media:** single WD 10 TB 7200 rpm HDD (`/mnt/hdd`) — media *and* torrents
- **Transcode cache:** NVMe (`/mnt/ssd`) — correct, leave it there

### A GPU is not one processor

This is the part that makes the rest of the document make sense, so it's worth being
precise about.

"The GPU" is a package containing **several independent processors**, each specialised
for a different job, each with its own work queue, all running **concurrently**. Saying
"the GPU is at 90%" is as imprecise as saying "the server is busy" when you mean one
thread of one process is hot.

The queues are called **command streamers**. A command streamer is a hardware front-end
that consumes a ring buffer of commands the driver has written — functionally a work
queue with a dedicated consumer. Each engine has its own, which is why they can all be
busy at once, and why a tool can report a separate utilisation figure for each. The
abbreviations are just the names of those queues:

| Abbrev | Stands for | Drives | What it is |
|---|---|---|---|
| **RCS** | **R**ender **C**ommand **S**treamer | the EU array | **Programmable** shader cores — 3D, OpenCL, compute |
| **VCS** | **V**ideo **C**ommand **S**treamer | VDBOX / MFX | **Fixed-function** codec ASIC — decode & encode |
| **VECS** | **V**ideo **E**nhancement **CS** | VEBOX | **Fixed-function** filters — scale, denoise, tonemap |
| **BCS** | **B**litter **C**ommand **S**treamer | blitter | 2D memory copies (mostly legacy) |

This chip has **two** VCS engines (`VCS0`, `VCS1`), so decode and encode can occupy
separate codec blocks.

### Programmable vs fixed-function — the distinction that matters

For a software engineer the useful analogy is **`AES-NI` versus a software AES loop**.
Both compute the same answer. One is a general CPU executing instructions; the other is
a circuit that does only that one thing, at a fraction of the time and energy.

- **RCS is the general-purpose one.** The EU (Execution Unit) array is a pool of
  programmable SIMD cores. It runs *shaders* — actual programs. It can do anything,
  which is exactly why it's expensive: video work expressed as shader kernels burns a
  lot of silicon and power to do what dedicated hardware does nearly for free. This is
  also the engine games and compute workloads want.
- **VCS and VECS are the ASICs.** They aren't programmable. VDBOX implements H.264 /
  HEVC / AV1 / VP9 decode and encode directly in silicon; VEBOX implements scaling,
  deinterlacing, colour conversion and tonemapping. Enormously faster per watt, but
  only for the exact operations they were built for.

**The entire goal of hardware transcoding is to keep the work on VCS and VECS, and
leave RCS idle.** When RCS is your busiest engine during a transcode, something is
misconfigured — that is the finding this document is about.

### How a transcode flows through them

```
  read file          DECODE            FILTER              ENCODE           HLS segments
  from disk   ──►   4K HEVC     ──►   scale 4K→1080p  ──►  HEVC out   ──►   to client
                    ↓                 tonemap HDR→SDR      ↓
                  [ VCS ]              ↓                 [ VCS ]
                  VDBOX              [ VECS ]            VDBOX
                                     VEBOX
```

Each stage hands GPU-resident frames to the next — the pixels never leave video memory,
which is why `-hwaccel vaapi -hwaccel_output_format vaapi` matters. If a stage can't run
on its dedicated engine, frames get routed through **RCS** instead, and that stage
becomes the bottleneck for the whole pipeline.

### The two encode paths — where it went wrong

Encoding is not one operation. The expensive part is **motion estimation**: for every
block in a frame, search the reference frames for the closest match, so only the
difference needs storing. It's a brute-force search problem, and it dominates encode
cost.

Intel implements it two ways:

| Path | Motion estimation runs on | Cost |
|---|---|---|
| **VME** (Video Motion Estimation) — *default* | **shader kernels on the EU array (RCS)** | expensive |
| **VDEnc** ("low power") | dedicated fixed-function silicon inside VDBOX | cheap |

With VME, only the final bitstream packing happens in the codec block; the search — the
hard part — runs as a program on the general-purpose cores. That is why the profiling
found **RCS at 90% and the dedicated encoder idling at 20%**. The chip's purpose-built
encoder was sitting nearly unused while the shader array did its job for it, slowly.

`EncoderPreset=slow` makes this worse in the obvious way: a slower preset means a wider
motion search, and on the VME path that search is shader work on RCS.

**"Low power" is a badly chosen name.** It refers to the energy efficiency of the
fixed-function path — not to a throttled or reduced-performance mode. Measured here it
was **4.75x faster**, not slower.

### The engine loads, measured

Each row is a real `intel_gpu_top` sample on this host:

| Workload | RCS | VCS | VECS | Result |
|---|---|---|---|---|
| 1 stream, VME encode (current config) | **90%** | 20% | 0% | 2.59x — wrong engine saturated |
| low-power encode, scale only | low | **99%** | 0% | 12.3x — work on the codec ASIC |
| low-power encode **+ HDR tonemap** | low | ~70% | **~70%** | tonemapping correctly on VEBOX |

The third row confirms `EnableVppTonemapping=true` is doing its job: VECS goes from 0%
to ~70% the moment tonemapping enters the pipeline. Plain scaling doesn't need VEBOX at
all, which is why it reads 0% otherwise.

### The rule this gives you

Every tuning decision below reduces to one question: **does this setting push work onto
RCS?**

| Keep work on the ASICs | Avoid — pushes work to RCS |
|---|---|
| `EnableIntelLowPowerHevcHwEncoder=true` (VDEnc) | VME encode — the default |
| `EnableVppTonemapping=true` (VEBOX) | `EnableTonemapping=true` (OpenCL shaders) |
| hardware decode for all codecs | software decode |
| a fast `EncoderPreset` | `slow` — widens the shader motion search |

Both OpenCL tonemapping and VME encode land on the same engine. Enabling both would put
two of the three pipeline stages on the general-purpose cores, competing with each other.

---

## What was actually measured

Two devices streaming (LAN Chrome, remote iOS over Tailscale), both transcoding 4K
sources.

### Nothing except the GPU is under load

| Resource | Measured | Verdict |
|---|---|---|
| CPU | 80% idle, load 1.9 / 16 threads | idle |
| RAM | 26 GB of 31 GB available | idle |
| Media HDD | 5.5 MB/s read, **3–7% util**, 2 ms await | idle |
| Per-transcode disk read | ~2 MB/s each | trivial |
| LAN | 1 GbE, 14 Mbps TX | ~1% used |

Adding streams will not be limited by disk, CPU, RAM, or network for a long time yet.

### The GPU never idles, even with one stream

`rc6_residency_ms` (deep-idle residency) moved **0 ms in 10 s** with a single
transcode running — and again with two. The counter is live, not stuck: it had
accumulated 4,013,482 ms across 5,448,360 ms of uptime (73.7% idle since boot).

### The wrong engine was doing the work

`intel_gpu_top`, one stream running:

```
 RCS(render)   BCS   VCS(codec)   VECS(enhance)
    ~90%        0%      ~20%          0.00%
```

**The dedicated encoder sat at 20% while the general-purpose shader array was pegged
at 90%.** That is backwards, and it is caused by one setting.

### The cause, proven by A/B test

Same file, same filters, same preset, back to back:

| Path | Speed | fps | Engine |
|---|---|---|---|
| `low_power` off (current config) | 2.59x | 62 | RCS ~90% |
| `low_power=1` | **12.3x** | **297** | VCS ~99% |

Re-running `intel_gpu_top` during the low-power encode confirmed the shift: **VCS
jumped 20% → 99%**.

### Contention between streams

Before the fix, the tonemapping stream ran **2.78x alone → 1.94x** once a second
device joined. That was the practical ceiling: roughly 4 concurrent 4K transcodes.

---

## The settings, explained

### 1. `EnableIntelLowPowerHevcHwEncoder` / `EnableIntelLowPowerH264HwEncoder`

```xml
<EnableIntelLowPowerHevcHwEncoder>true</EnableIntelLowPowerHevcHwEncoder>
<EnableIntelLowPowerH264HwEncoder>true</EnableIntelLowPowerH264HwEncoder>
```

**What it means.** Intel GPUs have two different hardware encode implementations:

- **VME** (Video Motion Estimation) — the default. The bitstream work happens in the
  VDBOX, but **motion estimation runs as shader kernels on the EU array**, i.e. the
  render engine. Motion estimation is the most expensive part of encoding, so this
  path consumes a large amount of general GPU compute.
- **VDEnc / "low power"** — motion estimation runs on dedicated fixed-function silicon
  inside the VDBOX. The render engine is barely touched.

"Low power" is a confusing name. It refers to the power efficiency of the fixed-function
path, **not** to reduced performance or a throttled mode. It is faster, not slower.

**Why it matters here.** With it off, your transcodes burn the shader array — which
saturates at ~90% with a single stream — to do a job the chip has purpose-built
hardware for. This was the bottleneck.

**Trade-off — read this before enabling.** VDEnc is somewhat less bitrate-efficient
than VME at the same target bitrate, and does not support B-frames in some modes. At
identical bitrate, output is marginally softer. On Raptor Lake's Xe the gap is small
and this is the path most Jellyfin installs run. 4.75x throughput for a modest quality
delta is a good trade, but it is a real trade — judge it on your own screen.

**Requires** the iHD driver (in use: `iHD_drv_video.so`, VA-API 1.23.0) and Gen11+
hardware. Both satisfied.

---

### 2. `EncoderPreset`

```xml
<EncoderPreset>veryfast</EncoderPreset>   <!-- currently: slow -->
```

**What it means.** How much search effort the encoder spends per frame. Slower presets
search harder for redundancy, producing better quality per bit, at higher cost.

**Why change it.** On the VME path, `slow` makes the shader-based motion search as
expensive as it can possibly be — it is the multiplier on the problem above. On the
VDEnc path the preset barely applies, because the fixed-function block doesn't have
the same tunable search depth.

So: after enabling low-power, this matters much less. There is still no reason to keep
requesting the most expensive setting.

**Trade-off.** On hardware encoders the quality difference between presets is far
smaller than on software x264/x265 — the fixed-function block simply doesn't expose
that much variation. Little is lost.

---

### 3. `EnableThrottling`

```xml
<EnableThrottling>true</EnableThrottling>
<ThrottleDelaySeconds>180</ThrottleDelaySeconds>   <!-- already set -->
```

**What it means.** By default ffmpeg transcodes as fast as the hardware allows, all the
way to the end of the file, regardless of where the viewer is. Throttling pauses the
encoder once it is `ThrottleDelaySeconds` ahead of playback, and resumes when the
buffer drains.

**Why it matters for multiple devices.** This is the setting that specifically
addresses "several devices joined." Without it, every open session holds the GPU
permanently. Observed during profiling: a session **4 minutes** into a film had already
transcoded **11 minutes** of it, and would have continued to the end credits whether or
not anyone kept watching. Three such sessions compete at full tilt even if all three
viewers pause.

With throttling on, each session encodes a 3-minute buffer and then releases the
engine. Idle and paused sessions stop costing anything.

`ThrottleDelaySeconds` is already `180`; the feature is simply switched off.

**Trade-off.** Seeking beyond the buffered window has to wake the encoder, so a long
forward seek can take a moment longer to start. In exchange, concurrent capacity goes
up substantially.

---

### 4. Settings that are already correct — leave them

| Setting | Value | Why it's right |
|---|---|---|
| `HardwareAccelerationType` | `qsv` | correct API for this iGPU |
| `EnableHardwareEncoding` | `true` | hardware encode on |
| `EnableVppTonemapping` | `true` | HDR→SDR on the **VEBOX** block, not OpenCL shaders |
| `EnableTonemapping` | `false` | correct — this is the OpenCL path, which would hit RCS |
| `HardwareDecodingCodecs` | h264, hevc, vc1, av1 | full hardware decode coverage |
| `EnableDecodingColorDepth10Hevc` | `true` | needed for 10-bit HDR sources, which most of the library is |
| `PreferSystemNativeHwDecoder` | `true` | uses VA-API directly rather than via QSV shim |
| transcode cache path | NVMe | segments are write-heavy; keep them off the media HDD |

Note the tonemapping pair: `EnableVppTonemapping=true` with `EnableTonemapping=false`
is the *good* combination and is already set. The OpenCL tonemapper would add yet more
render-engine load.

---

### 5. `AllowHevcEncoding`

```xml
<AllowHevcEncoding>true</AllowHevcEncoding>
```

**What it means.** Whether the server may *output* HEVC. HEVC encodes are more
expensive than H.264 and not all clients accept them, but they need roughly half the
bitrate for equivalent quality — which matters for remote streaming over a limited
uplink.

**Recommendation: leave `true`.** With low-power encode enabled, the cost is no longer
the concern it was, and the bitrate saving is real for the Tailscale clients. Revisit
only if a specific client has trouble.

---

### 6. `EnableSegmentDeletion` — deliberately *not* recommended

The transcode cache has grown to **12 GB / 2197 files**. Segment deletion would prune
played HLS segments automatically.

However, in Jellyfin this is positioned as an **alternative** to throttling rather than
a companion to it, and the interaction between the two on seek-back is not something
this profiling verified. Since throttling is the setting that actually addresses GPU
contention, take throttling and clear the cache directly instead:

```bash
docker stop jellyfin
rm -rf /mnt/ssd/nas-lab/appdata/jellyfin/cache/transcodes/*
docker start jellyfin
```

Revisit segment deletion separately if disk growth becomes a real problem.

---

## The client-side win, and its limits

Both profiled sessions reported `TranscodeReasons: ["ContainerBitrateExceedsLimit"]` —
a **quality cap set in the client app**, not an incompatible file.

For the iOS session this is provable from the ffmpeg command line Jellyfin built:

```
-codec:v:0 hevc_qsv          # HEVC source → HEVC output
-codec:a:0 copy              # audio passed through untouched
scale_vaapi=w=1920:h=802     # only the resolution changed
```

Had the client been unable to decode HEVC, Jellyfin would have targeted `h264_qsv`
instead. It chose HEVC and copied the audio, so that device accepted both the source
codec and the source audio. Only resolution and bitrate were being changed.

**So: for LAN clients, set quality to `Auto`.** A Chrome or TV client on the gigabit
LAN can then Direct Play — original file, untouched, zero GPU — on a link running at
about 1% utilisation.

### This does not generalise to phones or remote playback

Setting `Auto` everywhere would be wrong, for two separate reasons.

**1. Many devices genuinely cannot play a 4K source.** The reason list is per device
*and* per file. Real incompatibilities that force a transcode regardless of any
bitrate setting:

| Cause | Typical case |
|---|---|
| No HEVC decode, or no 4K-level profile | many Android phones, older tablets, browsers |
| Dolby Vision Profile 5 / 7 | several 4K releases in this library |
| HDR10 to an SDR screen | requires tonemapping — most phones |
| TrueHD / DTS-HD MA audio | almost all phones — but this is **audio-only**, video still Direct Streams |

Note the last row: an audio-only mismatch produces a **Direct Stream** (remux), which
leaves the video untouched and costs almost nothing. Only a video mismatch is expensive.

**2. Even a fully compatible phone should not pull a 4K remux.** These files run
20–60 Mbps. Over Tailscale that is bounded by home *upload* bandwidth, not by the GPU,
and a phone screen gains nothing visible from 4K. A bitrate cap on mobile and remote
clients is **correct** — it is the setting doing its job.

### The practical rule

| Client | Quality setting | Result |
|---|---|---|
| LAN desktop / TV | **Auto** | Direct Play, zero GPU |
| Phone on wifi at home | modest cap (e.g. 20 Mbps) | transcode, but cheap after the encoder fix |
| Phone on cellular / remote | keep a low cap | transcode — correct and necessary |

For everything in the bottom two rows, transcoding is the right behaviour and cannot be
configured away. That is precisely why the low-power encoder fix matters: the goal is
not to eliminate transcodes, it is to make the unavoidable ones **cheap**. Removing the
LAN transcodes and speeding up the rest are complementary, not alternatives.

---

## Secondary findings

### GPU is power-clamped (~17%)

The iGPU requests 1450 MHz and runs at **1200 MHz**, with `throttle_reason_pl4=1`
asserted in 11 of 12 samples.

PL4 is the *peak power* limit. Notably **PL1/PL2 (the sustained 54 W budget) are clear**,
thermal is clear, and the CPU is 75% idle — so this is **not** qBittorrent or any other
workload stealing the power budget. The iGPU hits the peak-current limit on its own.

Worth ~17% of clock. Against the 475% from the encoder-path fix, not worth chasing.

### Media and torrents share one spindle

`/mnt/hdd` holds both `media` (1 TB) and `torrents` (1 TB, hardlinked). qBittorrent is
capped at 6900 KB/s upload but uncapped on download. At 3–7% disk utilisation this is
currently irrelevant, but seeding random-reads on a single 7200 rpm HDD is the **next**
bottleneck after the GPU, likely somewhere past 6 concurrent streams.

Also note qBittorrent's upload competes with remote Tailscale viewers for the same home
uplink — a more immediate concern than disk for remote playback quality.

---

## Applying it in the web UI

**Prefer the UI over editing `encoding.xml` directly.** Saving from the dashboard
applies immediately to new playback sessions with no container restart; editing the XML
by hand requires `docker restart jellyfin`, and Jellyfin will overwrite your file if
anyone touches the UI afterwards.

Labels below are verbatim from **Jellyfin 10.11.11**, the version running here.

### Where

**Dashboard → Playback → Transcoding**
(gear icon top-right → Dashboard → *Playback* in the left sidebar → *Transcoding* tab)

### What to set

Working down the page in the order the controls appear:

| UI label | Set to | Status | XML key |
|---|---|---|---|
| **Hardware acceleration** | `Intel QuickSync (QSV)` | ✅ already | `HardwareAccelerationType` |
| **QSV Device** | `/dev/dri/renderD128` | ✅ already | `QsvDevice` |
| **Enable hardware decoding for** | tick H264, HEVC, HEVC 10bit, VC1, AV1 | ✅ already | `HardwareDecodingCodecs` |
| **Prefer OS native DXVA or VA-API hardware decoders** | on | ✅ already | `PreferSystemNativeHwDecoder` |
| **Enable hardware encoding** | on | ✅ already | `EnableHardwareEncoding` |
| **Allow encoding in HEVC format** | on | ✅ leave | `AllowHevcEncoding` |
| **Enable Intel Low-Power H.264 hardware encoder** | **ON** | 🔴 **change** | `EnableIntelLowPowerH264HwEncoder` |
| **Enable Intel Low-Power HEVC hardware encoder** | **ON** | 🔴 **change** | `EnableIntelLowPowerHevcHwEncoder` |
| **Enable Tone mapping** | **off** | ✅ already | `EnableTonemapping` |
| **Enable VPP Tone mapping** | **on** | ✅ already | `EnableVppTonemapping` |
| **Encoding preset** | `veryfast` | 🔴 **change** (from `slow`) | `EncoderPreset` |
| **Throttle Transcodes** | **ON** | 🔴 **change** | `EnableThrottling` |
| **Throttle after** | `180` | ✅ already | `ThrottleDelaySeconds` |
| **Delete segments** | off | ✅ leave off | `EnableSegmentDeletion` |
| **Transcoding thread count** | `Auto` | ✅ already | `EncodingThreadCount` |
| **Transcode path** | leave blank (NVMe default) | ✅ already | — |

Four toggles to change. Everything else is already correct — the point of listing them
is so you can confirm rather than wonder.

Note the two tone-mapping switches are **not** duplicates and the current pairing is
right. Jellyfin's own help text: *"Enable VPP Tone mapping — Full Intel driver based
tone-mapping… This has a higher priority compared to another OpenCL implementation."*
The plain **Enable Tone mapping** is that OpenCL path, which runs on the render engine.
Leave it off.

### ⚠️ One prerequisite before enabling Low-Power

Jellyfin's own help text under those toggles reads:

> *"Low-Power Encoding can keep unnecessary CPU-GPU sync. On Linux they must be
> disabled if the **i915 HuC firmware is not configured**."*

VDEnc needs the GPU's **HuC** (HEVC/H.264 microcontroller) firmware loaded by the i915
driver. Without it, enabling these toggles **breaks transcoding** — playback fails to
start rather than running slowly.

**On this host HuC is loaded and working**, verified empirically: a `-low_power 1`
encode completed 1039 frames at 297 fps. If it were missing, encoder initialisation
would have failed outright.

To check explicitly (needs root):

```bash
sudo dmesg | grep -i huc          # expect "HuC firmware ... authenticated" / "HuC enabled"
sudo cat /sys/module/i915/parameters/enable_guc   # 2 or 3 = HuC enabled
```

Re-check this after any kernel or firmware upgrade. If HuC ever stops loading, turn
both Low-Power toggles back off.

### Saving

Click **Save**. The change applies to **new playback sessions only** — any stream
already running keeps its existing ffmpeg process and its old settings. To test, stop
and restart playback on one client. No container restart is needed.

**Change one thing at a time.** Enable the two Low-Power toggles first, verify, then do
the preset and throttling. If something breaks you want to know which switch did it.

---

## Verifying — in the UI

### Per-stream, in the player

In the web player, open the **Playback Info** overlay (player settings → *Playback
Info*). The field to read is **Play method**, which shows one of three states —
Jellyfin's own definitions:

| Play method | Meaning | GPU cost |
|---|---|---|
| **Direct playing** | *"The source file is entirely compatible with this client and the session is receiving the file without modifications."* | none |
| **Direct streaming** | *"The video stream is compatible… but has an incompatible audio format (DTS, Dolby TrueHD, etc.)… Only the audio stream will be transcoded."* | negligible |
| **Transcoding** | video is being re-encoded | the expensive one |

This is the fastest way to answer "why is my server working so hard" for any given
device: if it says Direct playing, that stream costs nothing.

### Server-wide

**Dashboard → home** lists every active session with its play method and, for
transcodes, the reason. That is where the `ContainerBitrateExceedsLimit` finding in this
document came from — a bitrate cap in the client, not an incompatible file.

## Verifying — on the command line

**Engine placement** (the real proof). Start one transcode, then:

```bash
intel_gpu_top -l -s 1000 -o - | head -8
```

Expect **VCS high, RCS low** — the inverse of the before-table. If RCS is still ~90%,
low-power mode did not engage; check HuC.

**Throughput:**

```bash
cd /mnt/ssd/nas-lab/appdata/jellyfin/log
grep -oE 'speed= *[0-9.]+x' "$(ls -t FFmpeg.Transcode-*.log | head -1)" | tail -5
```

Expect a large jump over the ~2.5x baseline.

**Rollback:** flip the toggles back in the UI, or restore `encoding.xml.bak` and restart
the container. Nothing here is destructive or one-way.

---

## Client configuration — how to make playback smooth

Server settings make transcodes *cheap*. Client settings decide whether a transcode
happens **at all**, which is worth far more. A Direct Play stream costs the server
nothing.

### First: the cap is stored per client, not on the server

The maximum-bitrate setting lives **inside each client app**. The web player keeps it in
the browser's own storage — so **Chrome and Edge on the same PC each need setting
separately**, and configuring one does nothing for the other. There is no server-side
switch that fixes this for everyone (see [the one server-side
lever](#the-one-server-side-lever) below for the closest thing).

### What to set, per device

| Device | Where | Set to |
|---|---|---|
| **Jellyfin Web** (Chrome, Edge, …) on LAN | play something → player gear → **Quality** | **Auto** — set in *each* browser |
| **Phone / tablet on home wifi** | app Settings → playback quality / **Maximum Bitrate** | **high or Auto** for wifi |
| **Any client away from home** | the same app's cellular/remote setting | **8–15 Mbps** |
| **TV / native apps** | app's quality setting | **Auto** on LAN |

On the LAN this is free money: a gigabit link running at ~1% utilisation has no reason
to re-encode anything. Away from home the cap is **correct** — there the constraint is
home upload bandwidth, not the GPU.

### Reading the transcode reason — a decoder ring

Jellyfin records *why* each stream is transcoding, visible in **Dashboard → home** or
the player's **Playback Info** overlay. The reason tells you whether there is anything
worth doing:

| Reason | What it means | Video re-encoded? | Action |
|---|---|---|---|
| **`ContainerBitrateExceedsLimit`** | a **client quality cap**, not an incompatible file | **yes — expensive** | raise the client's quality setting |
| **`AudioCodecNotSupported`** | audio format the client can't decode (DTS, TrueHD…) | **no — video copied** | **none. This is the good state** |
| **`VideoCodecNotSupported`** | the client genuinely cannot decode this video | yes — unavoidable | nothing on the server; it's a device limit |
| **`ContainerNotSupported`** | container mismatch only | no — remuxed | none, it's cheap |

**`AudioCodecNotSupported` on its own is a success, not a warning.** Observed on this
server:

```
reasons: ['AudioCodecNotSupported']
video: hevc 3840x2160   IsVideoDirect=True     ← full 4K passed through untouched
audio: aac ch=2         IsAudioDirect=False    ← only this converted
src audio: DTS-HD Master Audio 5.1 @ 3567 kbps
```

That is **Direct Stream**: the 4K HEVC video is copied bit-for-bit and only the audio is
converted, because no browser licenses DTS. Unfixable and nearly free — an audio encode,
not a video one.

Contrast the same client 30 seconds later after the quality cap came back:

```
reasons: ['ContainerBitrateExceedsLimit']  vDirect=False  out=2560x1440
ffmpeg: -codec:v:0 hevc_qsv  scale_vaapi=w=2560 + procamp + tonemap
```

Same file, same client — now a full 4K→1440p encode **with HDR tonemapping**. The single
setting is the difference between near-zero GPU and the most expensive job the box runs.

| State | Video work | GPU cost |
|---|---|---|
| `AudioCodecNotSupported` | copied bit-for-bit | ~zero |
| `ContainerBitrateExceedsLimit` | 4K→1440p encode + tonemap | the expensive one |

### The one server-side lever

**Dashboard → Playback → Transcoding → "Internet streaming bitrate limit"** (currently
`0` = unlimited) caps *remote* clients centrally, regardless of what their app requests.
With four users on this server, relying on everyone to configure their own app will not
hold. 15–20 Mbps is a sane safety net.

> **⚠️ Tailscale gotcha.** `LocalNetworkSubnets` is **empty**, so Jellyfin classifies LAN
> from its own interfaces only. The Tailscale interface is a `/32`, so a phone reaching
> the server on its `100.x` address is counted **remote — even when it is sitting on the
> home wifi**. Verified: `iphone-14-pro-max` connects as `100.73.94.96` while physically
> at `192.168.31.74`, with Tailscale reporting `direct 192.168.31.74:41641` (a LAN path,
> not the relay).
>
> So a server-side remote limit would also clamp that phone at home. Either:
> - **add `100.64.0.0/10`** to Dashboard → Networking → LAN networks, so Tailscale peers
>   count as local — but then the phone is uncapped on cellular too, and you depend on
>   the app's own per-network setting; or
> - **leave networking alone**, set the limit, and accept the phone is capped at home.

### Leave these alone

Per-user policy (**Dashboard → Users → *user***) currently has
`EnableVideoPlaybackTranscoding`, `EnableAudioPlaybackTranscoding` and
`EnablePlaybackRemuxing` all `true`. That is correct. Turning them off does not force
Direct Play — it makes incompatible files **fail to play** instead.

**"Fallback max stream bitrate (Mbps)"** is only used *"when ffprobe is unable to
determine the source stream bitrate"*. Leave at default.

### Verifying

In the player, open **Playback Info** and read **Play method**:

| Play method | Cost |
|---|---|
| **Direct playing** | none — the goal |
| **Direct streaming** | negligible — audio-only conversion |
| **Transcoding** | the expensive one — check the reason |

### If playback still stutters

Work down this list; the server is rarely the answer:

1. **Check the play method first.** If it says Direct playing, the server is doing
   nothing and the problem is the network or the client.
2. **Check the reason.** `ContainerBitrateExceedsLimit` → raise the client cap.
3. **Check `gpu-watch.sh`.** If `rc6` shows the GPU mostly idle, it is not a server
   capacity problem.
4. **Consider seeking.** Every seek outside the transcode buffer kills ffmpeg and starts
   a new one — see the throttling note below.

### Seeking and the throttle buffer

With `Throttle Transcodes` on, a transcode stays only `ThrottleDelaySeconds` (180s)
ahead of playback. Measured: `playing 6443s | encoded to 6640s | buffer +197s`.

Seek beyond that window and the content does not exist yet, so Jellyfin kills ffmpeg and
respawns it at the new position. One session here spawned **6 ffmpeg processes in under
two minutes** while being scrubbed. Each respawn re-initialises the hardware pipeline
before the first segment appears, which is felt as "slow to start".

If you seek a lot, raise **Throttle after** to 600–900s — a larger buffer absorbs most
seeks while still capping GPU use. Note this cost did not exist before throttling was
enabled, when ffmpeg raced to the end of the file. **A Direct Play stream has no ffmpeg
at all, so it has none of this behaviour** — another reason to prefer it on the LAN.

---

## Monitoring the GPU

### The one command to use

```bash
./scripts/gpu-watch.sh          # live, refreshes every 3s
./scripts/gpu-watch.sh 10       # every 10s
./scripts/gpu-watch.sh --once   # one snapshot, for logging or cron
```

Combines everything worth watching in one view — per-engine utilisation, clock and
throttle state, each live ffmpeg (with whether `low_power` is engaged and its current
`speed=`), and every Jellyfin session with its play method and transcode reason.

Healthy output during an HDR transcode looks like:

```
  clock  1203 / 1455 MHz   pkg 12.83W   rc6 0%   throttled: pl4
  RCS   ######################..  92.87%   render / shaders — should be LOW
  VCS   #############...........  55.04%   codec ASIC — decode + encode
  VECS  ###########.............  48.08%   VEBOX — scale / tonemap
  pid 54993   low_power   preset=veryfast  speed=6.28x
  Chrome    DirectPlay   Mission: Impossible II    -
  hitran    Transcode    Interstellar              ContainerBitrateExceedsLimit
```

**What to look for:**

| Reading | Meaning |
|---|---|
| `low_power` green on every ffmpeg | the encoder fix is engaged |
| **VCS busy, RCS low** | encode is on the dedicated ASIC — correct |
| **RCS high + an ffmpeg without `low_power`** | encode fell back to shaders — the bug this doc is about |
| **RCS high + all ffmpeg on `low_power`** | filter/VPP work, not encode — see the open question below |
| `rc6 100%`, everything 0% | GPU genuinely idle — often a **throttle-paused** transcode, which is correct behaviour |
| `throttled: pl4` | the ~17% peak-power clamp; expected on this chip |

### Interactive

`intel_gpu_top` on its own gives a live curses view including per-process attribution:

```bash
intel_gpu_top          # add -p for physical engines rather than classes
```

### One-off checks

```bash
# Is the GPU idle at all? 0 delta = never idled
cat /sys/class/drm/card1/power/rc6_residency_ms

# Is a transcode throttle-paused? (expected when a client is buffered ahead)
tail -3 "$(ls -t /mnt/ssd/nas-lab/appdata/jellyfin/log/FFmpeg.Transcode-*.log | head -1)" \
  | tr '\r' '\n' | tail -2
#  → "Transcoding is paused. Press [u] to resume."
```

> **Cross-check your tooling.** `intel_gpu_top` block-buffers when its stdout is a pipe,
> so `timeout 5 intel_gpu_top -l | tail -1` silently returns its bogus all-zero first
> row. `gpu-watch.sh` writes to a temp file and drops that row for this reason. When a
> reading looks surprising, verify against `rc6_residency_ms` — it needs no special
> permission and cannot be fooled by buffering.

### Beszel does not cover this

The `beszel-agent` container has **no GPU access** — `HostConfig.Devices` is `null` and
it is not privileged, so `/dev/dri` is not visible to it. It reports CPU, RAM, disk and
network only. Adding GPU panels would need `/dev/dri` passed through in
`docker-compose.yml` plus Beszel Intel-GPU support in the agent image (unverified — the
image is distroless and could not be inspected). Until then, `gpu-watch.sh` is the tool.

## Open question — render engine load on HDR sources

With the low-power encoder confirmed active, a single AV1 HDR10 → 1080p transcode with
tonemapping still shows **RCS ~93%**, alongside VCS ~55% and VECS ~48%.

Encode is definitively on the codec ASIC now (`-low_power 1`, and speed went 1.94x →
6.28x), so the render load is **filter/VPP work, not encode**. What has *not* been
isolated is which part: the VA-API filter chain (`scale_vaapi` + `procamp_vaapi` +
`tonemap_vaapi`), the `hwmap=derive_device=qsv` VAAPI→QSV interop, or AV1 decode.

To isolate it, with no other streams running, compare the same encode:

1. an **HEVC HDR** source with tonemapping (isolates AV1 decode)
2. the **same AV1** source without tonemapping (isolates the tonemap filter)

If RCS stays high in (2), the interop or scaler is responsible; if it drops, tonemapping
on this driver is partly shader-based despite VEBOX being engaged.

Not currently a bottleneck — throughput is 6.28x realtime — but it is the thing that
will cap concurrent HDR streams, so it is worth knowing before the next capacity
question.

## Reproducing the profiling

`intel_gpu_top` needs permission to open the i915 PMU. `perf_event_paranoid` is `4` on
this host, so grant the capability to the binary rather than loosening the sysctl
system-wide:

```bash
sudo apt install -y intel-gpu-tools
sudo setcap cap_perfmon+ep /usr/bin/intel_gpu_top
```

Useful unprivileged counters (no capability needed):

```bash
# GPU deep-idle residency — delta of 0 over N seconds means the GPU never idled
cat /sys/class/drm/card1/power/rc6_residency_ms

# Requested vs actual clock, and why it is being held back
cd /sys/class/drm/card1/gt/gt0
cat rps_act_freq_mhz punit_req_freq_mhz
for f in throttle_reason_*; do echo "$f = $(cat $f)"; done
```

Live session state, including *why* each stream is transcoding:

```bash
curl -s -H "X-Emby-Token: <jellyfin-api-key>" http://localhost:8096/Sessions \
  | python3 -m json.tool | grep -A2 TranscodeReasons
```

A/B testing an encode path directly, bypassing Jellyfin (`-low_power 1` toggles the
path; `-f null -` discards output so only encode cost is measured):

```bash
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -stats \
  -init_hw_device vaapi=va:/dev/dri/renderD128,driver=iHD \
  -init_hw_device qsv=qs@va -filter_hw_device qs \
  -hwaccel vaapi -hwaccel_output_format vaapi -i "<file>" -t 45 \
  -map 0:0 -c:v hevc_qsv -preset slow -low_power 1 -b:v 3680000 \
  -vf 'scale_vaapi=w=1920:h=802:format=nv12,hwmap=derive_device=qsv,format=qsv' \
  -an -f null -
```

---

## If Jellyfin moves back to the RTX 4080

Everything above is **Intel-specific**. Applying it to an NVIDIA GPU would range from
no-op to broken. The old NVENC config is still on disk as
`appdata/jellyfin/encoding.xml.nvenc.bak` (2026-08-09) — a useful reference, and proof
of how different the two are.

### What does *not* carry over

| Setting | On Intel (here) | On NVENC |
|---|---|---|
| `HardwareAccelerationType` | `qsv` | `nvenc` |
| **Intel Low-Power encoder toggles** | the headline fix | **meaningless — ignored** |
| `EnableVppTonemapping` | `true` (VEBOX) | **`false`** — Intel driver feature |
| `EnableTonemapping` (OpenCL) | `false` | **`true`** — the correct path on NVIDIA |
| `EnableEnhancedNvdecDecoder` | n/a | `true` |

Note the tone-mapping pair is **exactly inverted** between the two. Copying this
document's values onto an NVIDIA host would disable HDR tone mapping entirely.

### The bottleneck this document is about cannot occur on NVENC

The whole finding here is that Intel's default **VME** encode path runs motion
estimation as shaders on the render engine. **NVIDIA has no equivalent split.** NVENC is
a single dedicated encoder block that does motion estimation in fixed-function silicon
always — there is no "low power" alternative to switch to, because there is no
shader-based mode to switch away from. The CUDA cores are not involved in encoding at
all.

So on the 4080 there is no engine-placement mistake to make, and nothing here would have
produced a 4.75x gain.

### Different tooling

`intel_gpu_top`, RC6 residency, and the RCS/VCS/VECS engine names are all **i915
driver** concepts and do not exist on NVIDIA. The equivalents:

```bash
# Encoder / decoder block utilisation
nvidia-smi --query-gpu=utilization.gpu,utilization.encoder,utilization.decoder \
           --format=csv -l 1

# Which processes hold the encoder
nvidia-smi pmon -s um
```

Read `utilization.encoder` where this document reads VCS, and `utilization.decoder`
where it reads decode.

One NVIDIA-specific ceiling with no Intel equivalent: **GeForce drivers cap concurrent
NVENC sessions** (historically 3, raised to 5 and then higher in later drivers). The
limit is driver-version dependent, so verify against the installed driver rather than
assuming — on Intel the constraint is throughput, not a session count.

### What *does* carry over

- **`Throttle Transcodes`** — vendor-neutral, same benefit for concurrent streams
- **All client-side advice** — Direct Play, quality caps, per-device settings
- **The `TranscodeReasons` diagnostic method**
- **The general principle** — keep work on dedicated media blocks, off general-purpose
  compute

---

## Expected outcome

| | Before | After |
|---|---|---|
| Encode runs on | render engine (shaders) | dedicated VDBOX encoder |
| Single 4K→1080p transcode | 2.59x realtime | ~12x realtime |
| Practical concurrent 4K streams | ~4 | 12+ |
| Paused/idle sessions | hold GPU permanently | release it after 3 min buffer |
| LAN clients at `Auto` quality | transcode | Direct Play, zero GPU |
