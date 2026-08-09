# Vietnamese text-to-speech — planning

Self-hosted Vietnamese TTS on the same box as the film stack. **Nothing is
deployed yet.** This doc records the candidate evaluation, what the published
evidence does and doesn't cover, and the bake-off that settles it.

Status: **candidate chosen provisionally (VieNeu-TTS), pending a local A/B.**

Speech-ML terms used throughout are defined in the [Glossary](#glossary) at the
end — this field has its own vocabulary and none of it is assumed here.

## Candidates

| | VieNeu-TTS | VietTTS | F5-TTS-VN | viXTTS |
|---|---|---|---|---|
| Built for VN | from scratch | CosyVoice FT | F5 fine-tune | XTTS-v2 FT |
| License | **Apache 2.0** | code Apache, **models CC BY-NC** | permissive | Coqui CPML |
| Training data | 10,000h+ bilingual | undisclosed | 100–150h | viVoice |
| Preset voices | 7 (Northern + Southern) | 24 | — | — |
| Voice cloning | 3–5s ref | yes | yes | yes, weak (see below) |
| Server | Docker, Gradio + LMDeploy | Docker, **OpenAI `/v1/audio/speech`** | none | none |
| Runs on CPU | **yes** (GGUF Q4) | no | no | no |
| Activity | 618 commits, v3 in early access | 24 commits | moderate | stale |

**VieNeu-TTS is the pick.** It is the only candidate that is both purpose-built
for Vietnamese and permissively licensed — VietTTS ships its pretrained weights
under CC BY-NC, which forecloses anything commercial later. v2 is a **300M**
parameter model trained on 10,000+ hours of bilingual data, does en-vi
code-switching, and ships Northern (Bình, Tuyên, Hương, Ngọc) and Southern
(Nguyên, Đoan) voices — dual-accent coverage is rare, most Vietnamese corpora
are Northern-heavy.

**VietTTS is the fallback** and wins on one axis that may matter more than the
license: it exposes `/v1/audio/speech` directly, so [Open WebUI](../technical/LLM-SUBTITLES.md)
picks it up as a custom TTS engine with no glue code. It also normalises text
through Vinorm, which is not a small thing (see below). If the bake-off is close,
integration cost decides it.

## What the benchmarks actually say

The only rigorous Vietnamese comparison is the [zero-shot Vietnamese TTS
paper](https://arxiv.org/html/2506.01322v1). Three metrics, and they measure
genuinely different things:

- **WER** (word error rate, lower better) — feed the generated audio back through
  speech recognition and diff the transcript against the original script. It is
  an automated correctness check. **4% ≈ one wrong word in 25.** This is the
  metric that catches wrong tones, so for Vietnamese it is the important one.
- **MOS** (mean opinion score, 1–5, higher better) — human listeners rate how
  natural it sounds, averaged. A survey, not a computation: the ± figures are
  real error bars, and MOS from two different papers is not comparable.
- **Spk sim** (speaker similarity) — when cloning a voice, how much the output
  actually resembles the reference person. Independent of correctness: a clone
  can be perfectly intelligible and still sound like a stranger.

A model can win one and lose another, which is exactly what happens below:

| Test set | Model | WER | MOS | Spk sim |
|---|---|---|---|---|
| Long-form, seen | XTTS-v2ᴾᴬᴮ | **4.16** | **4.20** | 3.55 |
| | viXTTS | 4.23 | 4.05 | 2.88 |
| Long-form, unseen | XTTS-v2ᴾᴬᴮ | **4.31** | 3.89 | 3.56 |
| | viXTTS | 5.17 | 3.85 | 2.63 |
| **Short sentences** | VALL-Eᴾᴬᴮ | **12.63** | 3.44 | 3.35 |
| | VoiceCraftᴾᴬᴮ | 13.53 | **3.85** | 3.25 |
| | XTTS-v2ᴾᴬᴮ | 37.81 | 2.79 | 3.03 |
| | viXTTS | 37.81 | **2.37** | 2.48 |

MOS 4.20 on long-form is the realistic ceiling for open Vietnamese TTS — good
enough for narration. Commercial (FPT.AI, Viettel, Vbee, Azure `vi-VN`) still
edges it on pure naturalness.

**Sentence length flips the ranking, and that is the finding to act on.** On
short utterances the whole XTTS family — including viXTTS, the model most
recommended for Vietnamese online — collapses to **37.81 WER / MOS 2.37**. Around
one word in three wrong. The paper blames the architecture: XTTS-v2 "often
generates redundant or rambling speech at the end of the output" on short input.

So viXTTS is disqualified for anything conversational regardless of reputation,
and fine for audiobooks. Its speaker similarity is also worst in class
(2.63–2.88) — the cloning does not sound much like the reference.

**The honest gap: VieNeu-TTS publishes no WER, MOS or CER.** Not in the repo, the
model card, or the docs site. F5-TTS-VN is likewise absent from the paper. The
pick above rests on architecture, license, training scale and development
activity — *not* measured quality. That is exactly why the bake-off below exists
rather than a decision on paper.

## VRAM: this GPU is already contended

The 4080 Super has 16 GB, ~3 GB gone to Windows at idle, ~5.6 GB more when
`qwen3:8b` is resident, and a 4K HDR transcode wants headroom on top. Jellyfin
losing that contest is not hypothetical — see
[the GPU transcode outage](../incidents/2026-07-29-jellyfin-gpu-transcode-outage.md),
and `OLLAMA_KEEP_ALIVE=5m` exists in the compose file for the same reason.

This is what makes VieNeu's CPU path a real architectural advantage rather than a
footnote: **GGUF Q4 on CPU costs zero VRAM**, finishing a generation in ~7s. A
third GPU tenant on this box is a liability; a CPU tenant is free. Start on CPU,
move to GPU only if latency demands it.

Models go on the ext4 root (~883 GB free), **never `/mnt/f`** — that volume is at
**4.4 GB free** (927 of 932 GB is media, verified no duplicates or orphans) and
is 9p-mounted besides.

## Deployment sketch

Not yet applied to `docker-compose.yml`. Port 8298 is free on this box (8080 is
qbittorrent, 3000 open-webui, 8096 jellyfin).

Verified against the upstream `docker/Dockerfile.serve` and
`docker/docker-compose.prod.yml`:

| | |
|---|---|
| Docker Hub namespace | **`pnnbao`**, not `pnnbao97` (that's the GitHub user; the image 404s) |
| Tags | `latest` (Gradio UI, **:7860**), `serve` (LMDeploy API, **:23333**) |
| Tag freshness | `latest` built 2026-05-07, `serve` 2026-01-15 — both lag the repo |
| Image size | 7.4–7.7 GB, almost entirely CUDA 12.8 + PyTorch |
| **Weights** | **not baked in** — pulled from HF at first run into `HF_HOME` |
| GPU | **required** for the Docker path (`FROM nvidia/cuda`, `--group gpu`) |

**The image does not ship the model.** `Dockerfile.serve` ends with
`CMD ["--model", "pnnbao-ump/VieNeu-TTS", "--tunnel"]` — the model is a runtime
argument naming a HF repo, downloaded on first start. The cache volume below is
therefore mandatory, not an optimisation: without it every restart re-downloads
the weights.

**Override the default command.** That same CMD passes `--tunnel`, which opens a
public [bore](https://github.com/ekzhang/bore) tunnel to the internet. This box
is reachable over Tailscale and should not be publishing anything to bore.pub.

The earlier "CPU-only by design" plan does not survive contact with the image —
Docker deployment is GPU-only upstream. That is acceptable anyway: at 300M
parameters the model wants roughly **0.6 GB of VRAM**, against 12.6 GB free. It
is not a meaningful third tenant next to Ollama and NVENC. The GGUF/CPU path
still exists, but via `pip`, not this image.

```yaml
  # Vietnamese TTS. GPU, but a trivial tenant: ~0.6 GB VRAM at 300M params.
  # Weights are NOT in the image — first start pulls them from HuggingFace.
  vieneu-tts:
    image: pnnbao/vieneu-tts:latest
    container_name: vieneu-tts
    restart: unless-stopped
    logging: *default-logging
    environment:
      - TZ=${TZ}
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility
    volumes:
      # Model cache on ext4 root (~883 GB free). /mnt/f has 4.4 GB and is 9p.
      # Mandatory: without it the weights re-download on every restart.
      - ${CONFIG_ROOT}/vieneu-tts:/root/.cache/huggingface
      - /usr/lib/wsl:/usr/lib/wsl:ro
    ports:
      # Gradio listens on 7860 in-container; 8298 is free on the host.
      - 8298:7860
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

Reachable on the tailnet at `admin-pc-1.tail9dbb76.ts.net:8298` with no port
forwarding, same as everything else in [REMOTE-ACCESS.md](../REMOTE-ACCESS.md).
Start with `latest` — the Gradio UI is what the bake-off needs for listening.
Switch to `serve` (`:23333`) only once a winner is picked and an API is wanted.

If Open WebUI integration is wanted, VieNeu's endpoint is **not** documented as
OpenAI-compatible (the image serves Gradio on 7860 and LMDeploy on 23333) — it
needs a ~50-line FastAPI shim translating `/v1/audio/speech`, or switch to
VietTTS and accept the non-commercial licence.

## The bake-off

The published evidence does not cover the two models most likely to win, and a
paper's MOS will not tell us whether *our* text sounds right. Deploy VieNeu-TTS,
VietTTS and F5-TTS-VN side by side and feed all three identical Vietnamese input:

1. **A long paragraph** — the case the benchmarks cover, sanity check.
2. **A short interactive line** — probes the failure mode that destroys the XTTS
   family. Any candidate that rambles or trails off here is out.
3. **Numbers, dates and English tech terms mixed in** — probes normalisation,
   which is the most common real-world break and is largely model-independent.

Judge by ear. Keep the winner, delete the rest.

## Four things that will bite

**1. Tone errors are word errors.** Vietnamese has 6 tones; a wrong tone is a
different word, not an accent quirk. This is what the WER column is really
measuring — treat it as the primary metric, not MOS.

**2. Text normalisation breaks more demos than the acoustic model does.**
Numbers, dates, currency, abbreviations, English terms. A model with excellent
MOS still sounds broken reading "2026" or "GPU" wrong. VietTTS handles this via
Vinorm; if VieNeu wins the bake-off, budget for a normalisation pass in front of
it and test case 3 above specifically.

**3. Accent has to match the audience.** Most Vietnamese corpora are
Northern-heavy. Decide Northern or Southern *before* the bake-off and pin the
preset voice, or the comparison measures accent preference rather than quality.

**4. v3 is early access.** VieNeu v3 Turbo (48 kHz, 10,000h+) is not the stable
release. Plan on v2 and treat v3 as an upgrade to re-test, not the starting
point.

## Open questions

- **Use case.** Interactive (assistant, Open WebUI replies) favours low latency
  and rules out anything XTTS-derived. Long-form (narration, dubbing the library)
  favours quality and reopens XTTS-v2. This is the single biggest determinant and
  it is still unanswered.
- **Northern or Southern.** Blocks the bake-off voice selection.
- **Commercial use ever?** If yes, VietTTS is out on licence and the fallback
  becomes F5-TTS-VN plus a hand-rolled server.

## Before building anything custom

Azure `vi-VN` and FPT.AI are better than every option here on naturalness, cost
cents per run, and need no VRAM, no container and no maintenance. If this is for
a handful of clips rather than bulk or private data, the honest answer is to use
one of them and skip this doc entirely. Self-hosting earns its keep on volume,
privacy, or offline operation — not on quality.

## Glossary

### Evaluation

| Term | Meaning |
|---|---|
| **WER** | Word error rate. Transcribe the generated audio and diff it against the input script. Automated, objective. Lower better; 4% ≈ 1 wrong word in 25. |
| **CER** | Same idea at character level. Useful for Vietnamese because a missing diacritic changes one character, not the whole word. |
| **MOS** | Mean opinion score. Humans rate naturalness 1–5, averaged. Subjective; only comparable *within* one study. |
| **Speaker similarity** | How closely a cloned voice matches the reference speaker. Separate from whether the words are correct. |
| **Zero-shot** | The model handles a voice it was never trained on — you supply 3–5s of reference audio at request time. Closer to a prompt than to a fine-tune; no training run, no weight changes. |
| **Corpus** | The training dataset: recorded speech plus matching transcripts. "10,000 hours" means audio duration, and it is the main driver of quality. |

### Models

| Term | Meaning |
|---|---|
| **Parameters** (300M, 0.5B) | Model size — the count of learned weights. Bigger usually sounds better but costs more memory and latency. 300M is small; `qwen3:8b` next door is 8B. |
| **Fine-tune (FT)** | Take an existing trained model and continue training it on new data — here, teaching a multilingual model Vietnamese. Cheap, but it inherits the base model's flaws (this is exactly why viXTTS breaks on short sentences). |
| **From scratch** | Trained on Vietnamese from the start rather than adapted. More expensive, no inherited baggage. |
| **XTTS-v2 / VALL-E / VoiceCraft / CosyVoice / F5** | Model families, roughly the "frameworks" of TTS. Each Vietnamese model above is one of these trained or fine-tuned on Vietnamese data. |
| **PAB / PhoAudiobook** | A 941-hour Vietnamese audiobook corpus. "XTTS-v2ᴾᴬᴮ" = XTTS-v2 trained on it. |
| **Quantization / Q4** | Storing weights at lower precision (4-bit instead of 16- or 32-bit). ~4× smaller and CPU-friendly, at a small quality cost. |
| **GGUF** | The file format those quantized weights ship in — same ecosystem Ollama already uses. |
| **ONNX** | A portable model format that runs without PyTorch, which is how VieNeu gets a lightweight CPU path. |

### Speech

| Term | Meaning |
|---|---|
| **Voice cloning** | Generating speech in a specific person's voice from a short reference clip. |
| **Reference audio** | That clip — 3–5s here. Quality matters: background noise in the reference shows up in the output. |
| **Preset voice** | A voice the model already ships, no reference clip needed. |
| **Code-switching** | Mixing languages in one sentence — "cái GPU này chạy nhanh". Models that don't support it mangle the English words. |
| **Text normalization** | Preprocessing that rewrites text into how it is *spoken*: "2026" → "hai nghìn không trăm hai mươi sáu", "GPU" → "gi-pi-u". Runs before the model, and is a frequent source of bad output that has nothing to do with the model. |
| **Vinorm** | The Vietnamese library that does the above. |
| **G2P / phonemizer** | Grapheme-to-phoneme: converts spelling into pronunciation units before synthesis. |
| **Sample rate** (24/48 kHz) | Audio resolution. 24 kHz is fine for speech; 48 kHz is studio-grade and doubles the data. |
| **RTF / real-time factor** | Generation speed vs audio length. "2× real-time" = 10s of speech in 5s. Below 1× cannot stream live. |

### Serving

| Term | Meaning |
|---|---|
| **Inference** | Running the trained model to produce output, as opposed to training it. |
| **Latency vs throughput** | Time to first response vs total volume per hour. Interactive use optimises latency; batch jobs optimise throughput. They pull in opposite directions. |
| **VRAM** | GPU memory. The hard constraint on this box — 16 GB shared between Jellyfin transcodes, Ollama and anything added. |
| **OpenAI-compatible API** | Implements the same HTTP contract as OpenAI's service, so existing clients work by changing the base URL. Same trick Ollama uses on `:11434`. |
| **`/v1/audio/speech`** | OpenAI's specific TTS endpoint: POST text, receive audio. |
| **LMDeploy** | A model-serving framework (comparable to vLLM) that wraps a model in an HTTP API. |
| **Gradio** | A quick web-UI framework for ML demos — good for clicking around, not for programmatic use. |
| **Shim** | A thin adapter translating one API shape into another. Here: accept OpenAI-format requests, forward them to VieNeu's native API. |

### Licenses

| Term | Meaning |
|---|---|
| **Apache 2.0** | Permissive. Use, modify, ship commercially. No practical restrictions. |
| **CC BY-NC** | Non-commercial only. Fine for personal use, blocks any revenue-generating use later. Applies to VietTTS's *weights* even though its code is Apache. |
| **CPML** | Coqui's restrictive licence on XTTS-v2 — non-commercial, with additional terms. |
