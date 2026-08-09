# Local LLM — model capacity planning

What this box can run, how fast, and which models are worth having on disk for
which job. Covers general reasoning, Vietnamese, coding, vision and embeddings —
not one model, a small library.

Current deployment: `qwen3:8b` on Ollama, chosen for Vietnamese subtitle
translation — see [LLM-SUBTITLES.md](LLM-SUBTITLES.md). Open WebUI on `:3000` is
the chat front end.

Status: **planning. Only `qwen3:8b` has been measured on this box** (101 tok/s
warm — the anchor every estimate below derives from). Confidence marked per
claim. Terms in the [Glossary](#glossary).

## You are picking a library, not a model

Ollama loads one model at a time and swaps on demand. That changes the question:
**models do not have to fit together, only individually**, and disk is the cheap
resource here (883 GB free). Keeping six models for six jobs costs nothing but
download time — the constraint is that each must fit VRAM *alone*, and that
switching between them costs a cold start.

So the goal is a set: something fast for routine work, something strong for hard
questions, a coder, a vision model, an embedder. Not one compromise model.

## The finding

The thing that decides what is streamable is not parameter count and not even
dense-vs-MoE. It is **active bytes per token**:

| Model | Total | Active | Bytes/token | Floor at 6.5 GB/s |
|---|---|---|---|---|
| `llama3.1:70b` | 40 GB | dense — all of it | 40 GB | **6.2 s** |
| `qwen3:235b` | 142 GB | **22B** of 235B | ~13 GB | **2.0 s** |
| `qwen3-coder-next` | 52 GB | **3B** of 80B | ~2 GB | **0.31 s** |

`qwen3:235b` is a Mixture of Experts and is still hopeless to stream, because
22B active is most of a dense 70B's cost. `qwen3-coder-next` is nearly three
times its size on disk and streams **six times faster**, because only 3B
activates.

**MoE is not the property that matters — a small active slice is.** That single
number decides whether AirLLM-style streaming is a tool or a demo, and it is why
the recommendation at the bottom is an A3B model rather than the biggest one.

## This box, measured

Verified 2026-08-09, film stack running, model unloaded:

| | Measured | Note |
|---|---|---|
| GPU | RTX 4080 Super, **16376 MiB** | ~3.3 GB held at idle → **~12.8 GB usable** |
| VRAM bandwidth | 736 GB/s (spec) | the real ceiling on token rate |
| WSL RAM | **15 GB total, ~12 GB free** | host has 32 GB; `.wslconfig` caps WSL at 16 |
| Disk read | **6.5 GB/s** | `dd iflag=direct` — the AirLLM ceiling |
| PCIe | gen 4 × 16 | ~25 GB/s, VRAM↔RAM |
| Disk free | **883 GB** | `/mnt/f` is 100 % full — never put weights there |
| Anchor | **`qwen3:8b` → 101 tok/s** | 5.2 GB resident, all 37 layers on GPU |

The anchor calibrates everything else: 5.2 GB against 736 GB/s gives a 141 tok/s
ceiling, and we measure 101 — **~72 % bandwidth efficiency**. Every dense
estimate below is `530 / size_GB`, which reproduces the measurement exactly and
makes the numbers derived rather than guessed.

The GPU is shared with Jellyfin's NVENC transcodes — see
[the transcode outage](../arr-servers/incidents/2026-07-29-jellyfin-gpu-transcode-outage.md)
and the `OLLAMA_KEEP_ALIVE=5m` that exists because of it. **~9 GB is the largest
model that coexists safely with a 4K HDR transcode.**

## What fits

Sizes confirmed against Ollama's library on 2026-08-09.

### Tier 1 — fits VRAM, survives a transcode (≤ 9 GB)

| Model | Size | Arch | Est. tok/s | Good for |
|---|---|---|---|---|
| `qwen3:8b` | 5.2 GB | dense, thinking | **101 measured** | current default, Vietnamese |
| `qwen3:14b` | 9.3 GB | dense, thinking | ~57 | general, a real step up from 8b |
| `qwen2.5-coder:14b` | ~9 GB | dense | ~59 | coding, does FIM |
| `qwen3-vl:8b` | 6.1 GB | vision, 256K ctx | ~87 | images |
| `deepseek-r1:14b` | ~9 GB | dense, thinking | ~57 | hard reasoning, slow by design |
| `qwen3:4b` | 2.5 GB | dense, **256K ctx** | ~210 | fast pass, long documents |
| `nomic-embed-text` | 274 MB | embedding | n/a | RAG in Open WebUI |

### Tier 2 — fits VRAM alone, not during a transcode (9–12.8 GB)

| Model | Size | Arch | Est. tok/s | Note |
|---|---|---|---|---|
| `gpt-oss:20b` | 14 GB | MoE, 128K ctx | ~40–70 | **marginally over** — small spill to RAM |

MXFP4-quantised at 4.25 bits/param, which is how 20B lands at 14 GB. Sits just
past the usable VRAM line; worth trying, expect a little offload.

### Tier 3 — VRAM + RAM split (13–28 GB)

| Model | Size | Arch | Est. tok/s | Good for |
|---|---|---|---|---|
| `qwen3:30b` | **19 GB** | **MoE, 3B active**, 256K | ~20–40 | **general ceiling** |
| `qwen3-coder:30b` | 19 GB | **MoE, 3.3B active**, 256K | ~20–40 | coding ceiling |
| `qwen3-vl:30b` | 20 GB | vision, 256K | ~20–40 | vision ceiling |
| `qwen3:32b` | 20 GB | **dense** | **~3–8** | avoid — see below |

**`qwen3:30b` and `qwen3:32b` are the same size and differ roughly 5×.** Both
spill ~7 GB into RAM; the dense 32B drags all 20 GB through that split every
token, the MoE touches ~2 GB. Same disk footprint, different universe. This is
the doc's thesis in two adjacent rows.

### Tier 4 — beyond RAM, disk streaming (> 28 GB)

| Model | Size | Active | Est. tok/s | Verdict |
|---|---|---|---|---|
| `qwen3-coder-next` | 52 GB | 3B of 80B | ~1–4 | borderline, batch only |
| `gpt-oss:120b` | 65 GB | MoE | ~1–3 | borderline |
| `qwen3:235b` | 142 GB | **22B** | ≪1 | no — active slice too big |
| `llama3.1:70b` | 40 GB | dense | **~0.16** | **pointless** |

## The AirLLM math

AirLLM keeps weights on disk and streams each layer in as its turn comes. VRAM
stops being the limit; **disk bandwidth becomes it**:

```
floor (s/token) = active bytes per token / 6.5 GB/s
```

Our 6.5 GB/s independently reproduces the published "hard floor near 20 seconds
per token" for a dense 70B at fp16 on Gen4 NVMe — 140 GB / 6.5 = 21.5 s. The
arithmetic checks out, which is reason to trust the sparse rows too.

Two things help in practice: **expert caching** (12 GB of free RAM holds a lot of
hot experts, and routing is skewed) and **attention layers stay resident**. One
thing hurts: **routing is unpredictable**, so unlike video block swap you cannot
prefetch — a cache miss is a full disk round trip in the critical path.

## Recommendation

A four-model library covering the jobs, none of which fight each other because
Ollama swaps on demand:

| Job | Model | Why |
|---|---|---|
| **Everyday + Vietnamese** | `qwen3:14b` (9.3 GB) | biggest step up from the current 8b that still survives a transcode; Qwen remains the strongest of its size on Vietnamese, which is what the subtitle pipeline needs |
| **Coding** | `qwen2.5-coder:14b` (~9 GB) | largest coder that fits alongside a transcode, and does fill-in-the-middle properly — chat models do not |
| **Hard questions** | `qwen3:30b` (19 GB) | MoE, 3B active, **256K context**. Spills to RAM and still lands ~20–40 tok/s. Nothing dense that fits comes near its quality |
| **Images** | `qwen3-vl:8b` (6.1 GB) | cheap to keep, opens a capability the others simply lack |

Add `nomic-embed-text` (274 MB) if you want Open WebUI's RAG over these docs or
the library — it is small enough that there is no reason not to.

**Keep `qwen3:8b`** until `qwen3:14b` is measured against it on real subtitle
work. The existing pipeline is tuned around it, and 101 tok/s versus ~57 is a
real cost if the translation quality gain turns out to be small.

**Skip AirLLM.** The technique is sound but the only model here where it pays —
`qwen3-coder-next` at ~1–4 tok/s — is a batch tool, not an assistant. And
`llama3.1:70b` at ~6 s/token means fifty minutes for a 500-token answer.

**Avoid every dense model above ~14B.** `qwen3:32b` is the trap: it looks like a
reasonable step up from 14b and is roughly 10× slower than the MoE beside it.

### The lever that costs nothing

`.wslconfig` caps WSL at 16 GB of a 32 GB host. **Raising it to 24 GB adds ~9 GB
of expert cache** — exactly what the Tier 3 MoE models are starved of, and it
moves `qwen3:30b` toward fully resident. Same lever as in
[the video-generation doc](../video-generation/README.md); it helps both.

Tradeoff: Windows keeps ~8 GB worst case, and WSL returns memory only gradually
(`autoMemoryReclaim=gradual`). Do it if Windows is idle during model use.

## Pulling and testing

No compose change needed — models are just pulls:

```bash
docker exec ollama ollama pull qwen3:14b
docker exec ollama ollama ps          # confirm 100% GPU, not CPU
```

`ollama ps` is the check that matters. A model silently falling back to CPU is
the difference between 57 tok/s and 3, and nothing will tell you.

Measure warm — **the ~20 s cold start dominates a single short request**:

```bash
curl -s http://localhost:11434/api/generate -d '{
  "model": "qwen3:14b",
  "stream": false,
  "think": false,
  "options": {"temperature": 0.3},
  "prompt": "Dịch sang tiếng Việt: ..."
}' | jq '.eval_count / (.eval_duration/1e9)'
```

That prints real tokens/sec — the only number here that stops being an estimate
once you run it. `"think": false` matters on Qwen3 and DeepSeek-R1, which
otherwise emit reasoning blocks you must strip.

## Wiring it up

Ollama speaks the OpenAI API on `:11434`, so nothing extra is needed:

- **Chat** — Open WebUI on `:3000` already picks up every pulled model in its
  dropdown. Model switching is a menu, not a config change.
- **Editors** — point Continue.dev at
  `http://admin-pc-1.tail9dbb76.ts.net:11434/v1`, reachable from any tailnet
  device with no port forwarding
  ([REMOTE-ACCESS.md](../arr-servers/REMOTE-ACCESS.md)).
- **Autocomplete wants a different model than chat.** It fires on every keystroke
  pause and needs sub-second latency — a 1.5–3B FIM model. Using the 14B for both
  makes typing feel broken.

## Six things that will bite

1. **Silent CPU fallback.** VRAM pressure pushes layers to CPU; ~20× slower, no
   error. `ollama ps` shows the split.
2. **Context is not free.** A 256K window is a capability, not a default —
   filling it costs GB of KV cache *on top of* weights and can turn a fitting
   model into a spilling one mid-conversation.
3. **Thinking models burn tokens invisibly.** `deepseek-r1` and Qwen3 with
   thinking on generate long reasoning traces before answering. Real latency is
   several times what tok/s suggests.
4. **Cold start scales with size.** ~20 s for 5.2 GB, expect ~60 s+ for 19 GB —
   paid on every swap between models, and `KEEP_ALIVE=5m` guarantees swaps.
5. **The Jellyfin collision is real.** Precedent:
   [2026-07-29](../arr-servers/incidents/2026-07-29-jellyfin-gpu-transcode-outage.md).
   Tier 2+ and film night do not mix.
6. **Ollama never garbage collects.** 883 GB is plenty until a few Tier 4 pulls.
   `ollama rm` what you stopped using.

## Verify before trusting this

Written against a **January 2026 knowledge cutoff, seven months stale**.

- **Verified live today:** all hardware measurements, the 6.5 GB/s disk figure,
  and every model name, size and architecture against Ollama's library —
  including `qwen3:30b` (19 GB, 3B active), `qwen3-coder-next` (52 GB, 80B/3B)
  and `gpt-oss:20b` (14 GB, MXFP4), all of which postdate the cutoff.
- **Derived, not measured:** every tok/s figure except `qwen3:8b`. Dense
  estimates use the measured 72 % efficiency and should be close. **Tier 3 MoE
  split estimates are the weakest numbers here** — VRAM/RAM splits add variables
  the model does not capture, so treat ~20–40 as a range, not a prediction.
- **Known tension:** one source reports `qwen2.5-coder:14b` at **34 tok/s** on an
  RTX 4080 where this derives ~59. Assume 34 until measured here.
- **Not verified:** quality rankings, including the claim that Qwen is best at
  Vietnamese — that came from the original model choice in
  [LLM-SUBTITLES.md](LLM-SUBTITLES.md), not from a benchmark. Nobody's
  leaderboard matches your subtitles; an A/B on real files settles it.

Cheapest way to start: `ollama pull qwen3:14b`, run the curl above warm against a
real subtitle line, and replace the first estimate in this doc with a fact.

## Glossary

### Architecture

| Term | Meaning |
|---|---|
| **Dense** | Every parameter runs for every token. Predictable, and why a dense 70B cannot be streamed usefully — there are no shortcuts. |
| **MoE** (Mixture of Experts) | Feed-forward layers split into many "experts"; a router picks a few per token. All weights must be *available*, only the active slice is *touched*. |
| **Active parameters** (A3B) | The slice that runs per token — the number that actually predicts speed. `qwen3:30b` is 30B total / 3B active: memory cost of 30B, bandwidth cost of 3B. |
| **Router** | Picks experts per token. Its unpredictability is why MoE streaming cannot prefetch. |
| **Thinking / reasoning models** | Generate hidden reasoning before answering (`deepseek-r1`, Qwen3 with thinking on). Better on hard problems, much slower in wall-clock. `"think": false` disables it. |
| **Vision / VLM** | Accepts images alongside text. `qwen3-vl` here. |
| **Embedding model** | Turns text into vectors for search and RAG. Tiny, no generation, used to *retrieve* context for another model. |
| **FIM** (fill-in-the-middle) | Completing code with context on both sides. What editor autocomplete needs; not all coding models support it. |
| **Context window** | How much text the model can consider at once. 256K ≈ a medium codebase — but the KV cache for it costs real VRAM. |
| **KV cache** | Per-token attention state held in VRAM beside the weights. Grows with conversation length; a common cause of mid-session OOM. |

### Memory and streaming

| Term | Meaning |
|---|---|
| **VRAM** | GPU memory. 16 GB here, ~12.8 usable, ~736 GB/s. Where a model wants to live entirely. |
| **Memory bandwidth bound** | Generation reads every active weight once per token, so speed ≈ bandwidth ÷ active bytes. Why these estimates are arithmetic, not guesswork. |
| **AirLLM** | Streams layers from **disk** per token; VRAM stops being the limit and disk bandwidth becomes it. Viable only when the active slice is small. |
| **Expert caching** | Keeping hot MoE experts in RAM so most tokens avoid disk. The single biggest factor in whether a streamed MoE is usable. |
| **Quantization / Q4_K_M / MXFP4** | Lower-precision weights. Q4 ≈ 0.5 bytes/param vs 2 at fp16 — 4× less memory *and* 4× less bandwidth, so models both fit and run faster. MXFP4 is the 4.25-bit format `gpt-oss` ships in. |
| **Cold start** | Time to load weights into VRAM before the first token. ~20 s for 5.2 GB, scaling with size. Paid on every model swap. |
| **`ollama ps`** | Shows whether a model is on GPU, CPU or split. First thing to check when speed is wrong. |

---

Sources: model specs verified against
[Ollama's library](https://ollama.com/library) ·
AirLLM measurements from
[Abrarqasim](https://abrarqasim.com/blog/airllm-the-hype-vs-the-reality/) and
[Medium](https://rohit-shirke.medium.com/airllm-and-70b-on-a-4gb-gpu-whats-actually-going-on-3bf0e102252e) ·
16 GB picks cross-checked against
[LaoZhang](https://blog.laozhang.ai/en/posts/best-local-coding-llm-16gb-vram) and
[InsiderLLM](https://insiderllm.com/guides/best-local-coding-models-2026/).
