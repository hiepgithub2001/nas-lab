#!/usr/bin/env python3
"""
Measure how far a subtitle has drifted from the timeline of the video.

The reference must be a subtitle extracted from the video file itself. That is
the whole point: a track muxed into the release is in sync by definition, so it
dates the timeline without anyone having to watch anything. A reference
downloaded from the same place as the target proves nothing.

Reports one offset per five minutes rather than a single number, because the
interesting failures are not constant. A file timed against a different cut
holds one offset for half an hour and then steps — Stranger S01E06 sits at
+0.3s until minute 33 and then jumps by -338.7s, which averages out to
something that looks almost fine and is unwatchable in practice.

Two modes:

  subtitle-drift.py REFERENCE TARGET
      Profile one pair. Exits 1 if any bin is off by more than --tolerance.

  subtitle-drift.py --scan DIR
      For every video in DIR, check each external subtitle against the English
      track inside that video, and say which episode the subtitle actually
      belongs to. Finds mis-filed subtitles — a whole season can be shifted by
      one, which no amount of retiming will fix.

Usage:
  subtitle-drift.py REFERENCE TARGET [--bin-minutes 5] [--tolerance 0.3] [--quiet]
  subtitle-drift.py --scan DIR [--lang vi]
"""
import argparse
import bisect
import glob
import os
import re
import subprocess
import sys
from collections import Counter

TS = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> ")

# Wide on purpose. A window of a few seconds cannot see a step of several
# minutes, and a stepped file measured through one looks like random noise
# rather than like two clean segments — which is exactly the wrong conclusion,
# because noise suggests re-downloading and a step is trivially repairable.
MAX_OFFSET = 400.0
# Two cues are the same moment within this. Absorbs frame rounding and the fact
# that two languages split the same dialogue into different numbers of lines.
NEAR = 0.25
# Below this share of a bin's cues agreeing, the estimate is not trustworthy —
# usually because the two files segment dialogue differently there.
MIN_CONFIDENCE = 0.35


def starts(path):
    text = open(path, encoding="utf-8-sig", errors="replace").read()
    return sorted(
        int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
        for h, m, s, ms in TS.findall(text)
    )


def near(ref, t):
    i = bisect.bisect_left(ref, t)
    return any(0 <= j < len(ref) and abs(ref[j] - t) <= NEAR for j in (i - 1, i, i + 1))


def profile(ref, tgt, bin_seconds):
    """Local offset per time bin, as (minute, offset|None, confidence).

    Every cue votes for the offset that would align it with each reference cue
    within MAX_OFFSET. A real offset collects one vote from nearly every cue in
    its segment; coincidences scatter across 8000 bins and never pile up.
    """
    votes, counts = {}, Counter()
    for v in tgt:
        b = int(v // bin_seconds)
        counts[b] += 1
        lo = bisect.bisect_left(ref, v - MAX_OFFSET)
        hi = bisect.bisect_right(ref, v + MAX_OFFSET)
        for r in ref[lo:hi]:
            votes.setdefault(b, []).append(round(v - r, 1))
    out = []
    for b in sorted(votes):
        ds = votes[b]
        best = max(set(ds), key=ds.count)
        conf = ds.count(best) / counts[b]
        out.append((b * bin_seconds / 60, best if conf >= MIN_CONFIDENCE else None, conf))
    return out


# This host has no ffmpeg of its own; the only build present is the one inside
# the Jellyfin container, which sees the library at /data/media. Rather than
# make the caller care, translate the path and run there. Falls straight
# through to a host ffmpeg if one is ever installed.
CONTAINER = "jellyfin"
CONTAINER_FFMPEG = "/usr/lib/jellyfin-ffmpeg"
HOST_MEDIA = "/mnt/hdd/film-data/media"
CONTAINER_MEDIA = "/data/media"


def _runner(video):
    """Return (argv_prefix, video_path_as_the_tool_will_see_it, tmp_prefix)."""
    from shutil import which
    if which("ffprobe") and which("ffmpeg"):
        return [], video, ""
    inner = video
    if video.startswith(HOST_MEDIA):
        inner = CONTAINER_MEDIA + video[len(HOST_MEDIA):]
    return ["docker", "exec", CONTAINER], inner, CONTAINER_FFMPEG + "/"


def embedded_track(video, lang):
    """Extract the first subtitle stream tagged `lang` to a temp file.

    This is the only trustworthy reference: a track muxed into the release
    cannot be out of sync with it.
    """
    pre, path, tool = _runner(video)
    probe = subprocess.run(
        pre + [tool + "ffprobe", "-v", "error", "-select_streams", "s",
               "-show_entries", "stream=index:stream_tags=language",
               "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    idx = None
    for line in probe.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2 and parts[1].lower().startswith(lang):
            idx = parts[0]
            break
    if idx is None:
        return None
    name = f"subdrift-{os.getpid()}-{abs(hash(video)) % 10**8}-{idx}.srt"
    inner_out = f"/tmp/{name}"
    r = subprocess.run(
        pre + [tool + "ffmpeg", "-v", "error", "-y", "-i", path,
               "-map", f"0:{idx}", "-c:s", "srt", inner_out],
        capture_output=True,
    )
    if r.returncode != 0:
        return None
    if not pre:
        return inner_out if os.path.exists(inner_out) else None
    host_out = f"/tmp/{name}"
    cp = subprocess.run(["docker", "cp", f"{CONTAINER}:{inner_out}", host_out],
                        capture_output=True)
    subprocess.run(pre + ["rm", "-f", inner_out], capture_output=True)
    return host_out if cp.returncode == 0 and os.path.exists(host_out) else None


def cmd_profile(a):
    ref, tgt = starts(a.reference), starts(a.target)
    if not ref or not tgt:
        print("empty subtitle file", file=sys.stderr)
        return 2
    rows = profile(ref, tgt, a.bin_minutes * 60)
    known = [o for _, o, _ in rows if o is not None]
    if not a.quiet:
        for minute, off, conf in rows:
            shown = f"{off:+7.2f}s" if off is not None else "      ?"
            print(f"  {minute:6.0f}m  {shown}  ({conf * 100:3.0f}% of cues agree)")
    if not known:
        print("no offset explains this pair anywhere — likely the wrong episode")
        return 1
    worst = max(abs(o) for o in known)
    steps = len({round(o, 1) for o in known})
    print(f"\n  {len(known)}/{len(rows)} bins measurable, "
          f"{steps} distinct offset(s), worst |offset| {worst:.2f}s")
    if worst <= a.tolerance:
        print("  PASS — in sync")
        return 0
    print(f"  FAIL — exceeds {a.tolerance}s"
          + ("; stepped, needs piecewise retiming" if steps > 1 else "; constant, one shift fixes it"))
    return 1


def cmd_scan(a):
    videos = sorted(
        f for f in glob.glob(os.path.join(a.scan, "*"))
        if f.lower().endswith((".mkv", ".mp4", ".m4v"))
    )
    if not videos:
        print(f"no video files in {a.scan}", file=sys.stderr)
        return 2
    print("extracting reference tracks...", file=sys.stderr)
    refs = {}
    for v in videos:
        t = embedded_track(v, "eng")
        if t:
            refs[v] = starts(t)
    if not refs:
        print("no embedded English track in any video — cannot verify", file=sys.stderr)
        return 2

    print(f"{'subtitle':<44}{'belongs to':<30}{'score'}")
    problems = 0
    for v in videos:
        stem = os.path.splitext(os.path.basename(v))[0]
        for sub in sorted(glob.glob(os.path.join(a.scan, f"{stem}*.{a.lang}.srt"))):
            tgt = starts(sub)
            if not tgt:
                continue
            best = (0.0, None)
            for rv, ref in refs.items():
                # Score each candidate video at its own best global offset, so a
                # correct-but-shifted subtitle is not mistaken for a wrong one.
                votes = Counter()
                for x in tgt[:500]:
                    lo = bisect.bisect_left(ref, x - MAX_OFFSET)
                    hi = bisect.bisect_right(ref, x + MAX_OFFSET)
                    for r in ref[lo:hi]:
                        votes[round(x - r, 1)] += 1
                if not votes:
                    continue
                off = votes.most_common(1)[0][0]
                score = 100 * sum(1 for x in tgt if near(ref, x - off)) / len(tgt)
                if score > best[0]:
                    best = (score, rv)
            owner = os.path.basename(best[1]) if best[1] else "?"
            ok = best[1] == v
            if not ok:
                problems += 1
            print(f"{os.path.basename(sub)[:43]:<44}"
                  f"{('(this video)' if ok else owner[:28]):<30}{best[0]:.0f}%")
    print(f"\n{problems} subtitle(s) belong to a different video"
          if problems else "\nevery subtitle matches its own video")
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("reference", nargs="?")
    ap.add_argument("target", nargs="?")
    ap.add_argument("--scan", metavar="DIR")
    ap.add_argument("--lang", default="vi")
    ap.add_argument("--bin-minutes", type=float, default=5.0)
    ap.add_argument("--tolerance", type=float, default=0.3)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.scan:
        return cmd_scan(a)
    if not (a.reference and a.target):
        ap.print_help()
        return 2
    return cmd_profile(a)


if __name__ == "__main__":
    sys.exit(main())
