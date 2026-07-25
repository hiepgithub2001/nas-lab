# Transcoding, encoding & decoding — how streaming works

How Jellyfin gets a film from disk onto a screen, when it re-encodes vs streams
untouched, and how the RTX 4080 Super fits in. For general architecture see
[ARCHITECTURE](ARCHITECTURE.md); for day-to-day Jellyfin use see
[jellyfin.md](user-guide/jellyfin.md).

## The three ways a film reaches a client

When you press Play, Jellyfin picks the **cheapest** of three delivery methods that the
client can actually handle:

| Method | What happens | Work involved | Quality |
|---|---|---|---|
| **Direct Play** | the original file is streamed as-is | none | perfect (bit-for-bit) |
| **Direct Stream** (remux) | video kept as-is, only the *container* or *audio* is repackaged | light CPU | perfect video |
| **Transcode** | video is decoded and **re-encoded** to something the client supports | heavy — this is where the GPU earns its keep | slightly lossy |

Direct Play is always best. Transcoding is the **last resort**, used only when the
client can't cope with the original.

## Encode vs decode — two halves of a transcode

A transcode is not one operation, it's two:

```
  source file            DECODE              (scale / tonemap)            ENCODE            to client
 4K HEVC 16.7 Mbps  ──►  raw frames    ──►   1080p, HDR→SDR        ──►   H.264 5.6 Mbps  ──►  phone
                       (NVDEC on GPU)                                   (NVENC on GPU)
```

- **Decode** — turn the compressed source (HEVC/H.264/AV1…) into raw pixels. On this
  system the GPU's **NVDEC** block does this.
- **Encode** — compress those pixels back into a format the client wants (usually
  H.264). The GPU's **NVENC** block does this.
- Between them, optional **scaling** (4K→1080p) and **tone mapping** (HDR→SDR) also run
  on the GPU.

NVDEC and NVENC are **dedicated silicon**, separate from the CUDA/gaming cores — which
is why Jellyfin transcoding barely affects gaming and runs many times faster than CPU
software encoding.

## How Jellyfin decides: Direct Play or transcode?

It's a negotiation, decided per play:

1. **The client declares its capabilities** — the Jellyfin app sends a *device profile*
   listing the codecs, containers, resolutions and **max bitrate** it accepts.
2. **The server compares the file to that profile** and picks the least-work method
   the client supports.
3. If anything doesn't match, it transcodes — and records exactly *why* in a
   `TranscodeReasons` field.

### What forces a transcode (wakes the GPU)

Any single mismatch is enough:

| Trigger | Example |
|---|---|
| **Codec** the client can't decode | 4K **HEVC** or AV1 on an iPhone |
| **Bitrate** exceeds the client's limit | 16.7 Mbps source vs a 4 Mbps app setting → `ContainerBitrateExceedsLimit` |
| **Resolution** above what the client allows | 4K to a device capped at 1080p |
| **Container** the client won't accept | certain `.mkv` variants on iOS → `ContainerNotSupported` |
| **Audio** the client can't play or pass through | some TrueHD/DTS cases |
| **Subtitles** that must be burned in | image-based **PGS** subtitles |

The more capable the client, the less the GPU runs. A native TV app that decodes 4K
HEVC would Direct Play a file a phone has to transcode.

## Worked examples (verified on this system)

Three plays, three different outcomes — all correct:

| Film | Source | Client | Decision | GPU |
|---|---|---|---|---|
| Harry Potter: Prisoner of Azkaban | 1080p **H.264** | iPhone | **Direct Play** (video passed through, only audio remuxed) | idle — best case |
| Breaking Bad: Seven Thirty-Seven | 4K **HEVC** 16.7 Mbps | iPhone | **Transcode** → 1080p H.264 5.6 Mbps (`ContainerBitrateExceedsLimit`) | **NVENC ~48%, NVDEC ~65%** |
| LOTR: Fellowship | 4K **HEVC** | iPhone | **Transcode** (`ContainerNotSupported`, `VideoCodecTagNotSupported`) | NVENC + NVDEC |

The GPU sat idle for the H.264 film (the phone played it directly) and worked for the
4K HEVC ones (the phone couldn't). That is the system behaving exactly as designed —
the GPU is a safety net, not something that runs on everything.

## This system's hardware pipeline

- **GPU:** NVIDIA RTX 4080 Super (Ada) — NVENC/NVDEC support H.264, HEVC, **and AV1**.
- **Host:** WSL2 sees the GPU natively (`nvidia-smi` works); the
  **nvidia-container-toolkit** bridges it into the Jellyfin Docker container.
- **Compose:** the `jellyfin` service reserves the GPU via
  `deploy.resources.reservations.devices` plus `NVIDIA_VISIBLE_DEVICES=all` and
  `NVIDIA_DRIVER_CAPABILITIES=all`.
- **Jellyfin:** hardware acceleration set to **NVENC**, with hardware decode, 10-bit
  HEVC decode, and BT.2390 HDR→SDR tone mapping enabled.

See [Enabling GPU transcoding](#enabling-gpu-transcoding-reference) for the exact steps.

## Verifying the GPU is actually working

Three independent checks, from easiest to most direct:

**1. Jellyfin Dashboard → Activity** — during playback each stream shows its method.
Look for **Transcode (hw)** or a hardware label. `Direct Play` means no transcode
(GPU not needed); `Transcode` with no "hw" means it fell back to CPU (bad).

**2. The GPU utilization** — while a *transcode* is active:

```
docker exec jellyfin nvidia-smi \
  --query-gpu=utilization.encoder,utilization.decoder --format=csv,noheader
```

Non-zero encoder/decoder % = the GPU is transcoding. (Under WSL the
`encoder.stats.sessionCount` field often reads 0 even when working — trust the
utilization %, not the session count.)

**3. The session API** — the definitive source, shows the decision and reasons:

```
docker exec jellyfin curl -s "http://localhost:8096/Sessions" \
  -H "Authorization: MediaBrowser Token=<token>"
```

Key fields: `TranscodingInfo.HardwareAccelerationType` (should be `nvenc`),
`IsVideoDirect` (false = video is being encoded), and `TranscodeReasons`.

## Quality and the client bitrate trap

The single most common "why does it look soft?" cause is a **low client-side quality
setting**, not the server.

Each Jellyfin app has its own **quality selector** with a max bitrate. If that's set
low (e.g. 4 Mbps), the app tells the server to cap there, and the server transcodes the
source *down* to fit — even a pristine 4K file gets crushed to a soft 1080p. Real
example from this system: a 16.7 Mbps 4K source delivered at 5.6 Mbps because the iOS
app was set to ~4 Mbps.

**Fix:** raise the quality in the *app* (tap the player → quality → 20-40 Mbps or Auto),
not on the server. With a fast connection and this GPU there's no reason to cap low.

### Should you set a server-side bitrate limit?

**Dashboard → Playback → Streaming → "Internet streaming bitrate limit (Mbps)"** caps
*all* remote clients. It's optional:

- **Set it** (e.g. 20) as a backstop so a client on weak cellular can't request more
  than the link sustains — cheap insurance, invisible quality cost on a phone.
- **Leave it off** if you also watch remotely on big screens, where a low server cap
  would limit them too, and you trust clients to self-cap.

Do **not** set it low (4-8 Mbps) — that forces needless transcodes and softens quality
for no benefit on a capable setup.

## Why the GPU matters here specifically

Without it, transcoding runs on CPU: a single 4K HDR transcode can peg every core and
still stutter, and HDR tone-mapping looks washed out. With NVENC, the same job runs at
a fraction of load, many times faster than realtime — you can watch 4K HDR remotely on
a phone, smoothly, which is the whole point of the setup. During playback you can see
the transcode race *ahead* of the playback position (the orange bar leading the blue on
the Now Playing card) — that lead is the GPU encoding faster than you're watching.

## Enabling GPU transcoding (reference)

How this was set up, in case it needs redoing on a fresh machine:

**1. Install the NVIDIA Container Toolkit** in WSL (needs `sudo`):

```
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

**2. Verify Docker can see the GPU:**

```
docker run --rm --gpus all ubuntu:22.04 nvidia-smi --query-gpu=name --format=csv,noheader
# -> NVIDIA GeForce RTX 4080 SUPER
```

**3. The `jellyfin` service in `docker-compose.yml`** already carries the GPU
reservation and the `NVIDIA_*` env vars — `docker compose up -d jellyfin` recreates it
with GPU access.

**4. In Jellyfin → Dashboard → Playback → Transcoding:** Hardware acceleration =
**NVIDIA NVENC**, tick HEVC (and AV1 only if your clients support it), enable HDR tone
mapping. Confirm the container has the encoders:

```
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -encoders | grep nvenc
# -> h264_nvenc, hevc_nvenc, av1_nvenc
```

### Encoder preset

For NVENC on a 4080 there is huge speed headroom, so favour quality: set **Encoding
preset** to `slow` (a clear quality gain at the same bitrate; still far faster than
realtime). Presets slower than `slow` hit diminishing returns on NVENC.
