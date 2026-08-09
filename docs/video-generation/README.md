# Local video generation — capacity planning

What this box can actually generate, how fast, and what `blocks_to_swap` really
costs. **Nothing is deployed yet.** This doc sizes the problem before any weights
get downloaded, because the weights are 8–30 GB each and the binding constraint
here is not the one most guides assume.

Status: **planning. No model has been run on this box — every speed figure below
is derived or estimated, not measured.** Confidence is marked per claim.

Terms are defined in the [Glossary](#glossary) at the end.

## The finding

Every "run video models on 16 GB VRAM" guide assumes you are VRAM-bound and tells
you to enable block swap. On this box that advice is half right and dangerous in
the other half:

> **This machine is RAM-bound, not VRAM-bound.** Block swap does not make memory
> pressure disappear — it *moves* it from 16 GB of VRAM into 15 GB of WSL system
> RAM, which is smaller, already occupied, and backed by a swapfile on disk.

NVIDIA's own guidance for this model class is **16 GB VRAM plus 64 GB system
RAM** ([TechieHub summary](https://techiehub.blog/best-local-ai-video-generator/)).
This box has 16 GB VRAM and **15 GB** of WSL RAM. The VRAM half is fine. The RAM
half is off by 4×, and that is the whole story of this document.

## This box, measured

Verified 2026-08-09, idle, film stack running:

| | Measured | Note |
|---|---|---|
| GPU | RTX 4080 Super, **16376 MiB** | 3467 MiB held at idle → **~12.3 GB usable** |
| WSL RAM | **15 GB total, ~12 GB free** | host has 32 GB; `.wslconfig` caps WSL at 16 GB |
| WSL swap | 4 GB total, **2 GB already used** | `D:\WSL\swap.vhdx` — disk, not RAM |
| PCIe | **gen 4 × 16** | ~31.5 GB/s theoretical, ~25 GB/s real with pinned memory |
| Disk for weights | **883 GB free** on ext4 `/` | `/mnt/f` is **100 % full** (4.4 GB) — never put weights there |

Two of those numbers do the damage. **12 GB free RAM** is the ceiling for
everything block swap wants to hold, and **swap is already 2 GB used at idle**,
so the pagefile is not a safety net that is sitting untouched — it is already in
play.

The GPU is also not exclusively yours. Ollama's `qwen3:8b` takes ~5.6 GB when
resident and Jellyfin wants NVENC headroom; losing that contest is documented in
[the transcode outage](../arr-servers/incidents/2026-07-29-jellyfin-gpu-transcode-outage.md),
and it is why `OLLAMA_KEEP_ALIVE=5m` exists in the compose file. A video
generation run is a *third* tenant, and by far the greediest.

## Candidates

Model quality rankings below reflect published claims and 2026 round-ups, **not**
local testing. See [Verify before trusting this](#verify-before-trusting-this).

| | LTX-2.3 | Wan2.1-14B | HunyuanVideo 1.5 | Wan2.1-1.3B | CogVideoX-5B |
|---|---|---|---|---|---|
| Params | undisclosed | 14B | ~13B | 1.3B | 5B |
| Weights (fp8) | fits 16 GB | ~14 GB | ~13 GB | ~1.3 GB | ~5 GB |
| Weights (GGUF Q4) | — | **~8 GB** | ~7 GB | ~0.8 GB | ~3 GB |
| Stated VRAM floor | **16 GB** | 24 GB unquantised | **14 GB** | ~6 GB | 16 GB |
| Native audio | **yes** | no | no | no | no |
| Fits here *without* swap | yes | no | marginal | **yes, easily** | marginal |
| Licence | check | **Apache 2.0** | check | **Apache 2.0** | check |

**Wan2.2 / Mochi-1 / SkyReels-V2 are out** — all three are reported to need 24 GB
VRAM, and closing that gap requires swap depths this box does not have the RAM
to back.

## Estimated throughput

**None of this is measured.** Figures are extrapolated from the 4080 Super's
~52 TFLOPS fp16 (roughly 3090 Ti-class for diffusion) against published run times
on comparable cards. Treat as order-of-magnitude planning numbers, not promises.

| Model | Output | `blocks_to_swap` | Est. time | Confidence |
|---|---|---|---|---|
| Wan2.1-1.3B | 480p, 81 fr, 25 steps | 0 | **2–4 min** | moderate |
| CogVideoX-5B | 480p, 49 fr, 50 steps | 0–10 | 4–8 min | low–moderate |
| LTX-2.3 | 720p + audio | 0–10 | unknown | **low** — post-cutoff model |
| Wan2.1-14B Q4 | 480p, 81 fr, 20 steps | 20 | **10–18 min** | low–moderate |
| Wan2.1-14B fp8 | 480p, 81 fr, 20 steps | 30–40 | 20–40 min | low |
| HunyuanVideo fp8 | 720p, 129 fr, 30 steps | 30–40 | 25–50 min | low |

The pattern that matters: **the jump from 1.3B to 14B costs roughly 5×, and swap
depth is not what drives it** — parameter count and resolution are. Swap is
cheap right up until it isn't, which is the next section.

## What `blocks_to_swap` actually does

A video diffusion transformer is a stack of near-identical blocks — Wan 14B has
**40**. All 40 run every denoising step, but only one runs at a time. Block swap
exploits that: keep *N* blocks in CPU RAM, pull each to VRAM just before its turn,
evict it after. `prefetch_blocks=1` (the default) loads block *n+1* while *n*
computes, hiding transfer behind compute.

It is the same bargain [AirLLM](#glossary) makes for language models, one tier
less extreme: **AirLLM streams from disk, block swap streams from RAM.**

### The PCIe cost is small

Derived from measured PCIe 4.0 ×16 bandwidth, Wan 14B at fp8:

```
block size   = 14 GB / 40 blocks        ≈ 350 MB
transfer     = 350 MB / 25 GB/s         ≈ 14 ms per block
```

| `blocks_to_swap` | Transfer per 20-step run | vs. a 12-min generation |
|---|---|---|
| 10 | 2.8 s | 0.4 % |
| 20 (default) | 5.6 s | 0.8 % |
| 40 (all) | 11.2 s | 1.6 % |

**Single-digit percent, and prefetch hides most of even that.** If you have the
RAM, block swap is nearly free — this is why the guides recommend it so casually.

### The RAM cost is not

Every swapped block occupies CPU RAM for the entire run. Against ~12 GB free:

| Config | Blocks in RAM | + T5-XXL encoder | Total | Verdict |
|---|---|---|---|---|
| Wan 14B Q4, swap 20 | 4.0 GB | 4.7 GB | **8.7 GB** | fits, ~3 GB headroom |
| Wan 14B Q4, swap 40 | 8.0 GB | 4.7 GB | **12.7 GB** | **over budget** |
| Wan 14B fp8, swap 20 | 7.0 GB | 4.7 GB | **11.7 GB** | at the edge |
| Wan 14B fp8, swap 40 | 14.0 GB | 4.7 GB | **18.7 GB** | **hopeless** |

Note the inversion: **raising `blocks_to_swap` to fix a VRAM error moves you
*toward* the RAM cliff, not away from danger.** The two limits pull opposite
directions and this box is narrow on both.

### What the cliff looks like

Exceed free RAM and WSL pages the overflow to `D:\WSL\swap.vhdx`. Block swap
silently becomes disk swap, and the 14 ms transfer becomes a page-fault round
trip — **roughly 10–50× worse**, with no error message. Symptoms:

- Generation time jumps from minutes to hours with no config change.
- `free -g` shows swap climbing past its current 2 GB.
- The whole box gets sluggish — Jellyfin stutters, SSH lags.

**`use_non_blocking=True` makes this worse, not better.** It requires pinned
(page-locked) RAM, which *cannot* be paged out, so instead of degrading it tends
to trigger an allocation failure or an OOM kill. On a 15 GB WSL, leave it off
until you have measured headroom.

## Recommendation

**Balanced pick: Wan2.1-14B at GGUF Q4 with `blocks_to_swap=20`.** It is the
best quality this box can reach while staying inside both budgets — 8 GB of
weights against 12.3 GB VRAM, 8.7 GB of RAM against ~12 GB free, roughly 3 GB of
headroom on each side. Apache 2.0, so nothing forecloses later. Est. 10–18 min
per 480p clip.

Two flanking choices:

- **Wan2.1-1.3B for iteration.** 2–4 min per clip, no swap, no contention. Use it
  to find the prompt, then re-run the keeper on the 14B. This two-tier workflow
  matters more than any single model choice — most clips are discards, and
  discovering that after 15 minutes is the actual cost.
- **LTX-2.3 is the one to evaluate first** if the 2026 claims hold. Native
  audio+video in one pass is a capability the Wan/Hunyuan line does not have at
  all, and it is built for a 16 GB card rather than squeezed onto one. But it is
  post-cutoff and unverified here — treat the table above as a hypothesis.

**Skip HunyuanVideo and anything needing 24 GB.** Reaching them requires swap
depths this box cannot back with RAM, and the failure mode is the silent cliff.

### The highest-leverage change is not a model

`.wslconfig` caps WSL at 16 GB of a 32 GB host. **Raising that cap to 24 GB
buys ~9 GB of block-swap headroom and costs nothing** — it moves the RAM cliff
out past every config in the table above, including fp8 at swap 40.

The tradeoff is real: Windows keeps ~8 GB in WSL's worst case, and WSL only
returns memory gradually (`autoMemoryReclaim=gradual`). Do it only if Windows is
otherwise idle during generation runs, and revert if Windows starts paging.
This is a bigger quality unlock than any choice between the models listed here.

## Deployment sketch

Not yet applied to `docker-compose.yml`. **Port 8188 is free** (9696 prowlarr,
8191 flaresolverr, 7878 radarr, 8989 sonarr, 6767 bazarr, 8080 qbittorrent,
8096 jellyfin, 11434 ollama, 3000 open-webui, 8091 in use).

```yaml
  # Video generation. Fourth GPU tenant on this box — start it manually,
  # do not `restart: unless-stopped`, and do not run it during playback.
  # See the RAM cliff note above before touching blocks_to_swap.
  comfyui:
    image: ghcr.io/comfyanonymous/comfyui:latest-cu124
    container_name: comfyui
    restart: "no"
    logging: *default-logging
    environment:
      - TZ=${TZ}
    volumes:
      # Weights on ext4 root. /mnt/f has 4.4 GB free and is 9p-mounted.
      - ${CONFIG_ROOT}/comfyui/models:/app/models
      - ${CONFIG_ROOT}/comfyui/output:/app/output
    ports:
      - 8188:8188
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

Reachable on the tailnet at `admin-pc-1.tail9dbb76.ts.net:8188`, no port
forwarding, same as everything else in
[REMOTE-ACCESS.md](../arr-servers/REMOTE-ACCESS.md).

`restart: "no"` is deliberate — an unattended restart of a job that eats 12 GB of
RAM is how the film stack goes down while nobody is watching.

## Five things that will bite

1. **The silent RAM cliff.** Covered above. It presents as "suddenly slow", not
   as an error. Watch `free -g` before blaming the model.
2. **T5-XXL is a hidden 4.7 GB.** The text encoder is a separate ~11B model. It
   runs once per prompt and can be offloaded or freed after — but if left
   resident it eats the headroom you budgeted for blocks.
3. **Weights on `/mnt/f` will fail.** That volume has 4.4 GB free and is 9p —
   slow enough to matter even if it fit. Root only.
4. **Concurrent Jellyfin transcode.** A 4K HDR transcode plus a generation run
   will lose to each other. Precedent:
   [2026-07-29](../arr-servers/incidents/2026-07-29-jellyfin-gpu-transcode-outage.md).
5. **VRAM fragmentation across runs.** ComfyUI holds allocations between
   generations; a workflow that fits cold can OOM on the third run. Restart the
   container between model switches rather than debugging it.

## Verify before trusting this

Written against a **January 2026 knowledge cutoff, seven months stale** in a
field that moves monthly. Specifically unverified:

- **LTX-2.3 entirely** — post-cutoff. Params, real VRAM floor, and speed all come
  from one 2026 round-up, not from testing or primary docs.
- **HunyuanVideo 1.5's "14 GB floor"** — same single source.
- **Every time in the throughput table** — extrapolated from other GPUs.
- Whether something better than all of these shipped since.

The cheap way to settle it: pull **Wan2.1-1.3B** first (~3 GB, fits with room to
spare), confirm the toolchain works end to end, and get one real measured number
on this box. Then scale up. Do not download a 30 GB checkpoint to discover the
pipeline is broken.

## Glossary

### Memory and offloading

| Term | Meaning |
|---|---|
| **VRAM** | Memory on the GPU. 16 GB here, ~12.3 GB usable. Fast (~700 GB/s) and the thing models must fit into to compute. |
| **System RAM** | Main memory. 15 GB in WSL of a 32 GB host. Where block swap parks blocks. ~10× slower than VRAM to reach, but vastly larger — normally. |
| **Block swap** | Keeping *N* transformer blocks in system RAM and moving each to VRAM just before it computes. Trades PCIe bandwidth and RAM for VRAM. |
| **`blocks_to_swap`** | How many blocks live in RAM rather than VRAM. Higher = less VRAM, more RAM, slightly slower. Default 20; Wan 14B has 40 total. |
| **`prefetch_blocks`** | Loads block *n+1* during block *n*'s compute so transfer hides behind work. Default 1, which absorbs most of the swap penalty. |
| **`use_non_blocking`** | Asynchronous transfers using pinned RAM. Faster, but pinned pages cannot be swapped out — dangerous on a RAM-tight box. |
| **Pinned / page-locked memory** | RAM the OS is forbidden to page out. Required for async DMA; on a 15 GB WSL it converts "slow" into "OOM". |
| **CPU offload** | The lighter cousin: park whole components (text encoder, VAE) in RAM, not per-block. Less aggressive, less risky. |
| **AirLLM** | Streams LLM layers from **disk**, fitting 70B–405B models on small cards at seconds-per-token. Same idea as block swap, one tier more extreme. Does not apply to diffusion models. |
| **Paging / pagefile** | When RAM runs out, the OS moves pages to disk (`D:\WSL\swap.vhdx` here). Transparent, no error, 10–50× slower. The cliff. |

### Models and formats

| Term | Meaning |
|---|---|
| **Parameters** (1.3B, 14B) | Count of learned weights. Drives both quality and memory. ~2 bytes each at bf16, ~1 at fp8, ~0.5 at Q4. |
| **Quantization** | Storing weights at lower precision. fp8 halves size vs bf16; Q4 quarters it. Small quality cost, large memory win. |
| **GGUF** | The container format quantized weights ship in — same ecosystem Ollama uses on `:11434`. |
| **Diffusion transformer (DiT)** | The architecture behind current video models: a stack of identical blocks, denoising a latent over N steps. The block stack is what makes swapping possible. |
| **Denoising steps** | Iterations from noise to output. More = better, linearly slower. 20–30 typical. |
| **Latent** | The compressed space the model works in. Video latents are large — a source of VRAM spikes independent of weights. |
| **VAE** | Encodes to / decodes from latent space. The decode step spikes memory hard; **tiled decoding** chunks it. |
| **T5-XXL** | The ~11B text encoder most of these models use to read prompts. ~4.7 GB at fp8, and easy to forget in a memory budget. |
| **MoE** (Wan2.2) | Mixture of Experts — only some parameters activate per step, but **all** must be resident. Cheap compute, expensive memory. Why Wan2.2 needs 24 GB. |

### Output

| Term | Meaning |
|---|---|
| **Frames / fps** | 81 frames at 16 fps ≈ a 5-second clip. Memory and time scale with frame count. |
| **480p / 720p** | Output resolution. 720p is ~2.25× the pixels of 480p and costs roughly that in both time and memory. |
| **T2V / I2V** | Text-to-video vs image-to-video. I2V takes a still as the first frame and generally gives more control. |
| **IC-LoRA** | Conditioning adapters (depth, pose, edge maps) for steering output structure rather than describing it in words. |

---

Sources for the 2026 model landscape:
[TechieHub](https://techiehub.blog/best-local-ai-video-generator/) ·
[LTX blog](https://ltx.io/blog/open-source-video-generation-models-guide) ·
[WillItRunAI](https://willitrunai.com/blog/video-generation-gpu-guide-2026) ·
block swap mechanics from
[WanVideoWrapper docs](https://deepwiki.com/kijai/ComfyUI-WanVideoWrapper/6.2-block-swapping-and-device-management).
