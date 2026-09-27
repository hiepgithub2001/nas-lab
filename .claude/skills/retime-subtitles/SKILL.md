---
name: retime-subtitles
description: Diagnose and repair out-of-sync external subtitles for a film or TV season, including files that hold one offset for half an hour and then step. Use when the user says subtitles are late/early/drifting/out of sync, asks whether a subtitle is any good, says the wrong lines are showing, or asks to fix/resync subtitles for a title. Also covers detecting subtitles filed against the wrong episode.
---

# Retime external subtitles against the video's own timeline

Repairs `.srt` sidecars whose text is right but whose timing is wrong. The
library lives under `/mnt/hdd/film-data/media`; the tools are
`scripts/subtitle-drift.py` (measure) and `scripts/retime-sub.py` (fix).

## The idea this rests on

**The reference must come out of the video file itself.** Almost every release
carries a muxed English subtitle track, and a track muxed into a release cannot
be out of sync with it. Extract that, and you have the authoritative timeline
without watching a frame. A reference downloaded from the same place as the
broken file proves nothing.

Nothing is matched on text — the two files are different languages. Alignment
rests entirely on the *shape* of the cue times, which is language-independent
and turns out to be more than enough signal.

## Before you retime anything: three checks that can end the job

Work through these in order. Two of the three finish the task without a single
timestamp being rewritten, and skipping them wastes effort on the wrong problem.

### 1. Is the correct subtitle already inside the video?

```bash
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error \
  -show_entries stream=index,codec_type:stream_tags=language \
  -of csv=p=0 "/data/media/<path>/<file>.mkv"
```

Translate the host path `/mnt/hdd/film-data/media/...` to `/data/media/...` —
the container mounts it there, and there is no ffmpeg on the host.

If a track in the target language is already muxed in, **stop**. It is in sync
by construction and almost always a better translation than anything a sidecar
provides. Netflix WEB-DL releases in particular ship official subtitles in a
dozen languages. Tell the user to delete the sidecars and let Jellyfin use the
embedded track — Jellyfin prefers an external file over an embedded one, so a
bad sidecar actively shadows a good internal track.

### 2. Does the subtitle even belong to this video?

```bash
python3 scripts/subtitle-drift.py --scan "<season directory>" --lang vi
```

This extracts each video's English track and reports which video every sidecar
actually matches. A whole season can be shifted by one, or scrambled outright.
**Retiming a mis-filed subtitle produces a perfectly synchronised wrong file** —
always rule this out first. The fix is renaming or re-downloading, not retiming.

### 3. Is this file a sidecar you are allowed to touch?

Leave **`*.AI.vi.srt`** alone. Those are written by `scripts/ai-translate-sub.py`
as *text-to-speech source* for vn-dubbing, not as display subtitles. What looks
broken in them is deliberate and documented in that script: the all-caps
`TÔI`/`BẠN` is a pronoun gloss (the model is forbidden from guessing Vietnamese
relationship pronouns, because one cue does not carry the speakers' relative
age and status), and the unwrapped long lines are because TTS does not wrap.
Rewrapping or de-glossing them breaks the dubbing pipeline. They also inherit
their timing cue-for-cue from the embedded English track, so they are already
in sync and never need this skill.

## Step 1 — extract the reference and measure

```bash
V="/mnt/hdd/film-data/media/tv/<Show>/Season 1/<Episode>.mkv"
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -v error -y \
  -i "/data/media/<...>.mkv" -map 0:<eng index> -c:s srt /tmp/ref.srt
docker cp jellyfin:/tmp/ref.srt ./ref.srt

python3 scripts/subtitle-drift.py ref.srt "<target>.vi.srt"
```

Read the per-five-minute profile, not the average. The output tells you which
of three cases you are in:

- **PASS** — in sync, nothing to do.
- **FAIL, 1 distinct offset** — a constant shift. Easy.
- **FAIL, several distinct offsets** — the file was timed against a different
  cut. This is the case a single shift cannot fix and the retimer exists for.

A `?` in a bin means the two files segment dialogue differently there, not that
they are misaligned — low confidence is normal and harmless if the bins around
it agree.

> **Never narrow the offset search window.** `subtitle-drift.py` searches ±400s
> deliberately. Measured through a ±15s window, a file that steps by −338s looks
> like random noise, which reads as "corrupt, re-download" when the truth is
> "two clean segments, trivially repairable". This exact mistake sent an earlier
> pass at Stranger S1 to the wrong conclusion on five episodes.

## Step 2 — retime

```bash
python3 scripts/retime-sub.py ref.srt "<target>.vi.srt" -o fixed.srt --report
```

`--report` prints the segments it found and the shift applied to each. Sanity-check
it against the profile from Step 1: the segment boundaries should sit where the
offset changed. Write to a separate file first; only install after Step 3.

The retimer rewrites timestamps and cue numbers and nothing else. It drops cues
that land before zero — those correspond to footage this cut does not have,
typically a "previously on" recap the BluRay dropped — and it re-sorts, because
a step can reorder the pair of cues straddling it.

## Step 3 — validate, including by content

Numbers first:

```bash
python3 scripts/subtitle-drift.py ref.srt fixed.srt --quiet
```

Then **read actual dialogue at a timestamp that was previously broken**. This is
the check that catches a confident, well-formed, entirely wrong answer:

```bash
grep -A2 "^00:45:0" ref.srt
grep -A2 "^00:45:0" fixed.srt
```

The two should be saying the same thing. Pick a moment inside each segment the
retimer found, not just the opening — the opening is usually fine in a file
that breaks later.

## Step 4 — back up, then install

Confirm the sidecar is not hardlinked to seeding torrent data before writing in
place. Media under `/mnt/hdd/film-data` is hardlinked between `media/` and
`torrents/`, and editing through the wrong path corrupts an active seed:

```bash
stat -c '%h links  %n' "<target>.vi.srt"     # must be 1
```

Back up to `/mnt/hdd/manual-backups/<title>-vi-pre-retime-$(date +%Y%m%d-%H%M)/`,
matching the existing convention there, then copy the fixed files over the
originals. Keep the filenames identical — Jellyfin reads sidecar content at
playback time, so unchanged names mean the new timings take effect immediately
with no library rescan.

Note in the summary that `manual-backups` is **not** covered by any offsite
backup leg.

## Step 5 — regenerate anything derived from the subtitle

If `*.Dual EN-VI.eng.ass` files exist beside the video, they embed a *copy* of
the Vietnamese and still carry the old timings:

```bash
python3 scripts/merge-subs.py "<episode>.en.srt" en \
  --primary en --secondary vi --layout topbottom
```

Check the reported `top=` cue count matches the retimed `.vi.srt`, not the
`.AI.vi.srt`. If it matches the AI file, the wrong counterpart was picked —
see `find_counterpart` in that script.

## Pitfalls worth knowing before you hit them

**Judge a subtitle on two axes, not one.** Translation quality and timing are
independent, and the better-reading file is often the worse-timed one. Say which
is which rather than calling a file "good" or "bad" — on Stranger S1 the human
translation was the right words at the wrong times while the machine translation
was the right times in the wrong register, and only naming both made the choice
obvious.

**A single-cue match is never a segment.** An episode already correctly timed
will still throw up coincidental matches hundreds of seconds away. `retime-sub.py`
absorbs runs under 25 cues for this reason; if you ever find yourself hand-editing
around a one-cue segment, that is the bug, not a real cut.

**Check the whole season before concluding anything about one episode.**
Duplicate-content sidecars across episodes (`md5sum *.vi.srt | sort`) are a
strong tell that the problem is filing, not timing.

**The last bin of a profile is often a false alarm.** The closing credit cue
names the translator, which differs per language, so it legitimately does not
align. Ignore a lone outlier in the final bin when every other bin is clean.

## Worked example

Stranger (2017) Season 1, repaired 2026-09-27. Sixteen episodes: eight needed a
constant shift, eight were stepped — E06 by +338.8s at minute 33, E12 by +150.8s
at minute 15, E07 across four segments. All sixteen ended within 0.2s of the
embedded English track in every bin. Season 2 needed no retiming at all: every
video already carried an official Netflix Vietnamese track, and its fifteen
mis-filed sidecars were a Step-2 problem, not a timing one.
