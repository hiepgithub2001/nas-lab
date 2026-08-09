# Local LLM — model capacity planning

What this box can actually run, how fast, and where AirLLM-style layer streaming
stops being a clever trick and starts being a waste of a weekend.

Current deployment: `qwen3:8b` on Ollama, sized for subtitle translation — see
[LLM-SUBTITLES.md](LLM-SUBTITLES.md). This doc is about **what else could go
there**, particularly for coding.

Status: **planning. Only `qwen3:8b` has been measured on this box** (101 tok/s
warm — the anchor every estimate below is derived from). Confidence marked per
claim. Terms in the [Glossary](#glossary).

## The finding

The interesting question is not "how big a model fits" — it's **dense or MoE**,
because that single property changes the streaming math by ~20×:

> A **dense** 70B must move all ~40 GB of weights per token. At this box's
> measured 6.5 GB/s that is a **6.2 s/token floor** — before any compute.
>
> An **MoE** 80B activates only ~3B parameters per token, ~2 GB. Same disk, same
> box: **~0.3 s/token floor**. Twenty times better, from a *bigger* model.

That is the whole case for and against AirLLM here. AirLLM on dense weights is a
demo, not a tool. AirLLM-style streaming on a sparse MoE is genuinely borderline
useful. Published measurements agree: ~292 s/token for dense 70B on an RTX 6000
Ada, and the observation that "a 671B MoE can need less streaming bandwidth per
token than a 70B dense model"
([AirLLM review](https://abrarqasim.com/blog/airllm-the-hype-vs-the-reality/)).

## This box, measured

Verified 2026-08-09, film stack running, model unloaded:

| | Measured | Note |
|---|---|---|
| GPU | RTX 4080 Super, **16376 MiB** | ~3.3 GB held at idle → **~12.8 GB usable** |
| VRAM bandwidth | 736 GB/s (spec) | the real ceiling on token rate |
| WSL RAM | **15 GB total, ~12 GB free** | host has 32 GB; `.wslconfig` caps WSL at 16 |
| Disk read | **6.5 GB/s** | `dd iflag=direct`, 2 GB — this is the AirLLM ceiling |
| PCIe | gen 4 × 16 | ~25 GB/s practical, VRAM↔RAM transfers |
| Disk free | **883 GB** on ext4 `/` | `/mnt/f` is 100 % full — never put weights there |
| Anchor | **`qwen3:8b` → 101 tok/s** | 5.2 GB resident, all 37 layers on GPU |

That anchor is worth keeping: 5.2 GB at 736 GB/s gives a 141 tok/s theoretical
ceiling, and we measure 101 — **~72 % bandwidth efficiency**. Every estimate
below applies that same 0.72 factor, which makes them derived rather than guessed.

The GPU is shared. Ollama competes with Jellyfin's NVENC transcodes, which is why
`OLLAMA_KEEP_ALIVE=5m` exists and why
[the transcode outage](../arr-servers/incidents/2026-07-29-jellyfin-gpu-transcode-outage.md)
happened. **~9 GB is the largest model that coexists safely with a 4K HDR
transcode**; beyond that you are choosing between watching something and running
the model.

## Four tiers

Confirmed available on Ollama as of 2026-08-09:

### Tier 1 — fits VRAM, survives a transcode (≤ 9 GB)

| Model | Size | Arch | Est. tok/s | Confidence |
|---|---|---|---|---|
| `qwen3:8b` | 5.2 GB | dense 8B | **101 measured** | — |
| `qwen2.5-coder:14b` | ~9 GB | dense 14B | **~59** | moderate |
| `deepcoder:14b` | ~9 GB | dense 14B | ~59 | low–moderate |
| `yi-coder:9b` | ~5 GB | dense 9B | ~95 | low–moderate |

### Tier 2 — fits VRAM alone, not during a transcode (9–12.8 GB)

| Model | Size | Arch | Est. tok/s | Confidence |
|---|---|---|---|---|
| `deepseek-coder-v2:16b` | ~10 GB | **MoE**, 2.4B active | ~90–120 | low |

MoE punches above its size here: 16B of quality at roughly 2.4B of bandwidth cost.

### Tier 3 — VRAM + RAM split (13–28 GB)

| Model | Size | Arch | Est. tok/s | Confidence |
|---|---|---|---|---|
| `qwen3-coder:30b` | **19 GB** | **MoE**, 3.3B active, 256K ctx | **~12–25** | low |
| `qwen2.5-coder:32b` | ~19 GB | dense 32B | ~3–8 | low |

**The two rows above are the same size and differ ~4× in speed.** Both spill
~7 GB into system RAM. The dense model must drag all 19 GB through that split
every token; the MoE only touches its 3.3B active slice. This is the doc's thesis
in a single comparison.

### Tier 4 — beyond RAM, disk streaming (> 28 GB)

| Model | Size | Arch | Est. tok/s | Verdict |
|---|---|---|---|---|
| `qwen3-coder-next` | **52 GB** | **MoE**, 80B/3B active | ~1–4 | borderline |
| `codellama:70b` | ~40 GB | dense 70B | **~0.16** | **pointless** |
| `qwen3-coder:480b` | 290 GB | MoE | ≪1 | no |

`qwen3-coder-next` is the only genuinely interesting AirLLM candidate on this
box, and the reason is entirely its 3B active slice.

## The AirLLM math

AirLLM keeps weights on disk and streams each layer to VRAM as its turn comes.
VRAM stops being the constraint; **disk bandwidth becomes it**. Derived from the
measured 6.5 GB/s:

```
floor (s/token) = bytes touched per token / 6.5 GB/s
```

| Model | Bytes/token | Floor | Realistic | Usable? |
|---|---|---|---|---|
| Dense 70B Q4 | 40 GB | 6.2 s | 8–15 s | no |
| Dense 70B fp16 | 140 GB | 21.5 s | 30 s+ | no |
| **MoE 80B-A3B Q4** | **~2 GB** | **0.31 s** | 0.3–1 s | **borderline** |

The 21.5 s figure independently reproduces the published "hard floor near 20
seconds per token" for Gen4 NVMe — our measured disk is slightly faster than the
7 GB/s they assumed, and lands slightly under. **The arithmetic checks out**,
which is reason to trust the MoE row too.

Two things make the MoE number better in practice than the table implies:
**expert caching** (12 GB of free RAM holds a lot of hot experts, and routing is
skewed — some experts are used far more often), and **attention layers stay
resident** since they run every token regardless of routing.

And one thing makes it worse: **routing is unpredictable per token**, so you
cannot prefetch the way block swap does for video. A cache miss is a full disk
round trip in the critical path.

## Recommendation

**Daily driver: `qwen2.5-coder:14b`.** ~9 GB, fully VRAM-resident, est. ~59 tok/s,
and it is the largest coding model that still coexists with a 4K transcode. It is
the consensus 16 GB pick for 2026
([LaoZhang](https://blog.laozhang.ai/en/posts/best-local-coding-llm-16gb-vram))
and it does fill-in-the-middle properly, which matters for editor autocomplete in
a way chat benchmarks do not measure.

**Quality ceiling: `qwen3-coder:30b`.** 19 GB, MoE with 3.3B active, **256K
context**. It spills ~7 GB into RAM and drops to an estimated 12–25 tok/s — but
that context window is the real prize, and no dense model that fits this box gets
near its quality. Pull it second, keep both, pick per task.

**Skip AirLLM.** Not because the technique is bad, but because the only model
where it pays off here — `qwen3-coder-next` at 52 GB — lands at an estimated 1–4
tok/s. That is a batch tool, not a coding assistant: fine for "refactor this
overnight", useless for a conversation. And `codellama:70b` at ~0.16 tok/s is
roughly **six seconds per token**; a 500-token answer is fifty minutes.

If you want the 80B anyway, wait for the RAM cap change below before trying.

### The lever that costs nothing

`.wslconfig` caps WSL at 16 GB of a 32 GB host. **Raising it to 24 GB adds ~9 GB
of expert cache**, which is exactly what the MoE tiers are starved of — it moves
`qwen3-coder:30b` closer to fully resident and materially improves the 80B's
cache hit rate. Same lever flagged in
[the video-generation doc](../video-generation/README.md); it helps both.

Tradeoff: Windows keeps ~8 GB worst case, and WSL returns memory only gradually
(`autoMemoryReclaim=gradual`). Do it if Windows is idle during model use.

## Pulling and testing

No compose change needed — Ollama is already running and models are just pulls:

```bash
docker exec ollama ollama pull qwen2.5-coder:14b
docker exec ollama ollama ps          # confirm 100% GPU, not CPU
```

`ollama ps` is the check that matters. A model silently falling back to CPU is
the difference between 59 tok/s and 3, and it will not tell you.

Measure honestly — the existing doc's warning applies: **the ~20 s cold start
dominates a single short request**. Benchmark warm, or you will conclude the GPU
is broken.

```bash
curl -s http://localhost:11434/api/generate -d '{
  "model": "qwen2.5-coder:14b",
  "stream": false,
  "think": false,
  "options": {"temperature": 0.2},
  "prompt": "Write a Python function that ..."
}' | jq '.eval_count / (.eval_duration/1e9)'
```

That last expression prints actual tokens/sec, which is the only number in this
document that will not be an estimate once you run it.

## Wiring it to an editor

Ollama already speaks the OpenAI API on `:11434`, so editor integrations need no
extra server. Point Continue.dev (or similar) at
`http://admin-pc-1.tail9dbb76.ts.net:11434/v1` — reachable from any tailnet
device with no port forwarding, same as everything in
[REMOTE-ACCESS.md](../arr-servers/REMOTE-ACCESS.md).

Worth knowing before wiring it in: **autocomplete and chat want different
models**. Autocomplete fires on every keystroke pause and needs sub-second
latency — a 1.5–3B FIM model is the right tool. Chat can afford the 14B. Running
the 14B for both makes typing feel broken.

## Five things that will bite

1. **Silent CPU fallback.** VRAM fragmentation or a concurrent transcode pushes
   layers to CPU. Speed drops ~20×, no error. `ollama ps` shows the split.
2. **Context is not free.** The 30B's 256K window is a *capability*, not a
   default — filling it costs GB of KV cache on top of the weights and can turn
   a fitting model into a spilling one mid-conversation.
3. **Cold start after `KEEP_ALIVE=5m`.** ~20 s for 5.2 GB, proportionally worse
   for 19 GB. Expect ~60 s+ on the 30B.
4. **The Jellyfin collision is real, not theoretical.** Precedent:
   [2026-07-29](../arr-servers/incidents/2026-07-29-jellyfin-gpu-transcode-outage.md).
   Tier 2+ models and film night do not mix.
5. **Disk fills quietly.** These are 9–52 GB each and Ollama never garbage
   collects. 883 GB is plenty until four pulls of Tier 4 later. `ollama rm` what
   you are not using.

## Verify before trusting this

Written against a **January 2026 knowledge cutoff, seven months stale**. What was
verified live and what was not:

- **Verified today:** all hardware measurements, the 6.5 GB/s disk figure, and
  every model name/size against Ollama's library (`qwen3-coder:30b` = 19 GB MoE
  3.3B active; `qwen3-coder-next` = 52 GB, 80B/3B active).
- **Derived, not measured:** every tok/s estimate except `qwen3:8b`. All scale
  the measured 72 % bandwidth efficiency — sound method, but untested at other
  sizes, and Tier 3/4 splits add variables the model does not capture.
- **Known tension:** one source reports `qwen2.5-coder:14b` at **34 tok/s** on an
  RTX 4080 where this doc derives **~59**. Their card is slightly slower, not
  40 % slower, so the gap is probably quantization or context depth. **Assume 34
  until measured here.**
- **Not verified:** quality rankings between coding models. Nobody's benchmark
  matches your codebase; a two-day A/B on real tasks beats any leaderboard.

Cheapest way to settle all of it: `ollama pull qwen2.5-coder:14b`, run the curl
above warm, and replace the first estimate in this doc with a fact.

## Glossary

### Architecture

| Term | Meaning |
|---|---|
| **Dense** | Every parameter runs for every token. Simple, predictable, and why a dense 70B is hopeless to stream — no shortcuts exist. |
| **MoE** (Mixture of Experts) | The feed-forward layers are split into many "experts"; a router picks a few per token. **All** weights must be *available*, but only the active slice is *touched*. |
| **Active parameters** (A3B) | The slice that actually runs per token. `qwen3-coder:30b` is 30B total / 3.3B active — memory cost of 30B, bandwidth cost of ~3.3B. |
| **Router** | Chooses experts per token. Its unpredictability is why MoE streaming cannot prefetch. |
| **FIM** (fill-in-the-middle) | Completing code with context on *both* sides, not just before. What editor autocomplete needs; not all coding models support it. |
| **Context window** | How much text the model can consider at once. 256K ≈ a medium codebase — but the KV cache for it costs real VRAM. |
| **KV cache** | Per-token attention state, held in VRAM alongside weights. Grows with conversation length; a frequent cause of mid-session OOM. |

### Memory and streaming

| Term | Meaning |
|---|---|
| **VRAM** | GPU memory. 16 GB here, ~12.8 usable, ~736 GB/s. Where a model wants to live entirely. |
| **Memory bandwidth bound** | Token generation reads every active weight once per token, so speed ≈ bandwidth ÷ active bytes. Why the estimates here are arithmetic rather than guesswork. |
| **AirLLM** | Streams layers from **disk** per token, so VRAM stops being the limit and disk bandwidth becomes it. Brilliant for MoE, near-useless for dense. |
| **Layer streaming / offload** | The general family: keep weights elsewhere (RAM or disk), move them in as needed. AirLLM is the disk-backed extreme; Ollama's CPU offload is the mild version. |
| **Expert caching** | Keeping frequently-routed MoE experts in RAM so most tokens avoid disk. The single biggest factor in whether a streamed MoE is usable. |
| **Quantization / Q4_K_M** | Lower-precision weights. Q4 ≈ 0.5 bytes/param vs 2 at fp16 — 4× less memory *and* 4× less bandwidth, so it makes models both fit and run faster. |
| **Cold start** | Time to load weights into VRAM before the first token. ~20 s for 5.2 GB here; scales with size. |
| **`ollama ps`** | Shows whether a model is on GPU, CPU, or split. The first thing to check when speed is wrong. |

---

Sources: model specs from [Ollama's library](https://ollama.com/library/qwen3-coder) ·
AirLLM measurements from
[Abrarqasim](https://abrarqasim.com/blog/airllm-the-hype-vs-the-reality/) and
[Medium](https://rohit-shirke.medium.com/airllm-and-70b-on-a-4gb-gpu-whats-actually-going-on-3bf0e102252e) ·
16 GB coding-model picks from
[LaoZhang](https://blog.laozhang.ai/en/posts/best-local-coding-llm-16gb-vram) and
[InsiderLLM](https://insiderllm.com/guides/best-local-coding-models-2026/).
