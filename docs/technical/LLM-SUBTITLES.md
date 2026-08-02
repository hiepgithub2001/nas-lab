# Local LLM — English → Vietnamese subtitles

An [Ollama](https://ollama.com/) server on the same GPU as Jellyfin, used to
translate subtitles for films where Bazarr can't find a Vietnamese track.

**http://localhost:11434** — OpenAI-compatible, no auth. Also on the tailnet.

| | |
|---|---|
| Server | `ollama/ollama:latest`, in `docker-compose.yml` |
| Model | `qwen3:8b` (5.2 GB on disk, ~5.6 GB resident) |
| Models path | `appdata/ollama` — ext4 root, ~900 GB free |

Qwen is the pick over Llama or Gemma at this size: markedly stronger on
Vietnamese. Vietnamese-specific tunes (Vistral, PhoGPT) exist but follow
instructions less reliably, which matters more than raw fluency for this job.

## Measured performance

Verified on the RTX 4080 Super, all 37 layers on GPU (`ollama ps` → `100% GPU`):

| | |
|---|---|
| Generation, warm | **101 tok/s** |
| Prompt eval | 81 tok in 0.18s |
| Cold start | **~20s** to load 5.6 GB into VRAM |

The cold start dominates short requests — the first call measured 4.1 tok/s
end-to-end purely because of it. Don't benchmark a single small request after
an idle period and conclude the GPU isn't being used; check `ollama ps` instead.

Rough budget: a feature film is ~1,500 subtitle lines. Batched ~20 lines per
request, that's ~75 requests of a few seconds each — **around five minutes per
film**.

## VRAM: this shares a GPU with Jellyfin

The 4080 Super has 16 GB total, ~3 GB used at idle by Windows. A resident model
holds another ~5.6 GB, and a 4K HDR transcode wants headroom on top.

`OLLAMA_KEEP_ALIVE=5m` in the compose file unloads the model once idle, so it
isn't squatting on VRAM during the hours you're actually watching something. The
cost is the ~20s cold start on the next request, which is the right trade for a
batch job.

Watch it in [Beszel](MONITORING.md) — the GPU chart reports VRAM used vs total.
If you move to a 14B model (~9 GB resident) that headroom gets genuinely tight;
`qwen3:14b` is one `ollama pull` away if translation quality needs it, but check
VRAM against a live transcode before making it the default.

## Calling it

```bash
curl -s http://localhost:11434/api/generate -d '{
  "model": "qwen3:8b",
  "stream": false,
  "think": false,
  "options": {"temperature": 0.3},
  "prompt": "..."
}'
```

`"think": false` matters — Qwen3 otherwise emits reasoning blocks you'd have to
strip. Low temperature keeps translations literal rather than inventive.

## Two things that will bite a translation pipeline

**1. ASS override codes get mangled.** 154 `.ass` files in this library carry
styling tags like `{\i1}`, and `merge-subs` exists precisely to handle them. A
model handed a tagged line will rewrite, drop or "correct" those tags. Strip
them out, translate the bare text, reinsert them — never pass raw tagged lines
to the model.

**2. Line-by-line loses the context Vietnamese needs.** Pronoun choice depends
on who is speaking to whom and their relative status — information that isn't in
a single line. Translate in batches with surrounding lines as context, and keep
timestamps out of the model's reach entirely; they are structure, not text.

Prompt behaviour worth knowing: asked for "no numbering", the model still
numbered its output. Parse defensively rather than trusting format instructions.

## Before building anything custom

**Bazarr already translates subtitles** (Google Translate backed). Quality on
idiomatic dialogue is clearly worse than this model, but it is instant, free and
already wired in. Worth trying on a few films first — it may be good enough for
much of the library, leaving the LLM for the ones that matter.
