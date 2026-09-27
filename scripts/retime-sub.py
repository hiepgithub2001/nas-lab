#!/usr/bin/env python3
"""
Retime a subtitle onto a reference subtitle's timeline.

Built for the Stranger (2017) Season 1 Vietnamese tracks, where a good human
translation carries bad timing: most episodes sit at a constant offset, but
seven of them step — E06 by -338.7s at the 35 minute mark, E12 by -150.8s at
15 minutes — because the file was timed against a different cut. A single
shift fixes the first kind and does nothing for the second, so the offset is
modelled as piecewise constant rather than as one number.

The reference must be a subtitle extracted from the video itself (here the
embedded English track), which makes it the authoritative timeline by
definition. Nothing is matched on text: the two files are different languages,
so alignment rests entirely on the shape of the cue times.

Only timestamps and cue numbers are rewritten. Cue text and its markup are
copied through byte for byte, and every gap *within* a segment is preserved —
a segment is translated, never stretched. Cues are re-sorted and renumbered
afterwards because a step can reorder the pair straddling it.

Usage:
  retime-sub.py REFERENCE TARGET [-o OUTPUT] [--report] [--inplace]
"""
import argparse
import bisect
import re
import sys
from collections import Counter

TS = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)

# How far a cue may have drifted from the reference. Wide because the step
# offsets here reach -338s; a window of a few seconds hides them completely and
# makes a stepped file look like noise.
MAX_OFFSET = 400.0
# Two cues count as the same moment within this many seconds. Subtitlers round
# to frame boundaries and split lines differently between languages, so the
# tolerance has to absorb both.
TOLERANCE = 0.30
# An offset must explain at least this share of cues to be considered real.
MIN_SUPPORT = 0.04
# Cues either side used to decide which offset a cue belongs to. Long enough to
# outvote a run of unmatched cues, short enough to place a step within ~10 cues.
SMOOTH = 15
# A real cut produces hundreds of consecutive cues at the new offset. Shorter
# runs are coincidence: a title card that happens to sit near another cue, or a
# handful of cues in the transition either side of a step. Left alone they do
# real damage — on E14, already correctly timed, one cue matched 35.7s away and
# would have been flung across the episode. Runs below this are absorbed into
# whichever neighbour better explains them.
MIN_SEGMENT = 25


def parse(path):
    """Return (blocks, times). blocks are raw text; times are (start, end)."""
    raw = open(path, encoding="utf-8-sig", errors="replace").read().replace("\r", "")
    blocks, times = [], []
    for block in re.split(r"\n\s*\n", raw):
        if not block.strip():
            continue
        m = TS.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        blocks.append(block)
        times.append((start, end))
    return blocks, times


def fmt(t):
    if t < 0:
        t = 0.0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def candidate_offsets(ref, starts):
    """Offsets that explain a meaningful share of cues, strongest first.

    Every (cue, nearby reference cue) pair votes for the offset that would
    align them. A real offset collects one vote from most cues in its segment;
    coincidental pairs scatter across 8000 bins and never accumulate.
    """
    votes = Counter()
    for v in starts:
        lo = bisect.bisect_left(ref, v - MAX_OFFSET)
        hi = bisect.bisect_right(ref, v + MAX_OFFSET)
        for r in ref[lo:hi]:
            votes[round(v - r, 1)] += 1
    floor = max(3, int(MIN_SUPPORT * len(starts)))
    peaks = []
    for off, n in votes.most_common():
        if n < floor:
            break
        # Neighbouring bins are the same peak split by rounding.
        if any(abs(off - p) < 0.25 for p in peaks):
            continue
        peaks.append(off)
    return peaks


def label(ref, starts, offsets):
    """Pick, for each cue, which candidate offset actually lands it on a
    reference cue. None where no offset does."""
    out = []
    for v in starts:
        best = None
        for off in offsets:
            w = v - off
            i = bisect.bisect_left(ref, w)
            for j in (i - 1, i, i + 1):
                if 0 <= j < len(ref) and abs(ref[j] - w) <= TOLERANCE:
                    best = off
                    break
            if best is not None:
                break
        out.append(best)
    return out


def smooth(labels):
    """Majority vote over a window, so one ambiguous cue cannot open a segment.

    Unlabelled cues inherit the local majority; that is what carries the run of
    cues at a step boundary, where neither offset matches cleanly."""
    n = len(labels)
    out = []
    for i in range(n):
        window = labels[max(0, i - SMOOTH): min(n, i + SMOOTH + 1)]
        votes = Counter(x for x in window if x is not None)
        out.append(votes.most_common(1)[0][0] if votes else None)
    # Carry the nearest decision into any stretch nothing voted on.
    last = next((x for x in out if x is not None), 0.0)
    for i, x in enumerate(out):
        if x is None:
            out[i] = last
        else:
            last = x
    return out


def refine_boundaries(ref, starts, seg_labels):
    """Nudge each step to the cue where it actually happens.

    Smoothing places a boundary within a cue or two, not exactly: the window
    votes, and the cues straddling a cut match neither offset cleanly. One cue
    on the wrong side overlaps its neighbour and plays out of order, which is
    visible — on E07 a line appeared three seconds before the line preceding
    it. Each boundary is re-placed at whichever nearby split explains the most
    cues on both sides."""
    for _ in range(4):
        runs = segments(starts, seg_labels)
        moved = False
        for k in range(len(runs) - 1):
            a, b, o1 = runs[k]
            c, d, o2 = runs[k + 1]
            best, best_at = None, b
            for split in range(max(a, b - 6), min(d, b + 6) + 1):
                sc = explains(ref, starts, a, split, o1) + explains(ref, starts, split + 1, d, o2)
                if best is None or sc > best:
                    best, best_at = sc, split
            if best_at != b:
                for i in range(min(b, best_at) + 1, max(b, best_at) + 1):
                    seg_labels[i] = o1 if i <= best_at else o2
                moved = True
        if not moved:
            break
    return seg_labels


def refine(ref, starts, seg_labels):
    """Re-estimate each segment's offset from its own matched cues.

    The vote grid is 0.1s; the median residual recovers the real value to the
    millisecond, which matters because a 50ms error is visible on short cues."""
    exact = {}
    for off in set(seg_labels):
        residuals = []
        for v, lab in zip(starts, seg_labels):
            if lab != off:
                continue
            w = v - off
            i = bisect.bisect_left(ref, w)
            near = [ref[j] for j in (i - 1, i, i + 1) if 0 <= j < len(ref)]
            if near:
                r = min(near, key=lambda q: abs(q - w))
                if abs(r - w) <= TOLERANCE:
                    residuals.append(w - r)
        if residuals:
            residuals.sort()
            exact[off] = off + residuals[len(residuals) // 2]
        else:
            exact[off] = off
    return exact


def segments(starts, seg_labels):
    runs = []
    for i, off in enumerate(seg_labels):
        if runs and runs[-1][2] == off:
            runs[-1][1] = i
        else:
            runs.append([i, i, off])
    return runs


def explains(ref, starts, a, b, off):
    """How many cues in [a, b] land on a reference cue at this offset."""
    n = 0
    for v in starts[a:b + 1]:
        w = v - off
        i = bisect.bisect_left(ref, w)
        if any(0 <= j < len(ref) and abs(ref[j] - w) <= TOLERANCE for j in (i - 1, i, i + 1)):
            n += 1
    return n


def absorb_short(ref, starts, seg_labels):
    """Merge runs shorter than MIN_SEGMENT into a neighbour.

    Repeats until stable: absorbing one short run can leave its neighbour
    adjacent to another, and merging those in turn is what collapses the
    alternating runs a transition zone produces."""
    while True:
        runs = segments(starts, seg_labels)
        if len(runs) < 2:
            return seg_labels
        short = [r for r in runs if r[1] - r[0] + 1 < MIN_SEGMENT]
        if not short:
            return seg_labels
        a, b, _ = min(short, key=lambda r: r[1] - r[0])
        idx = runs.index([a, b, seg_labels[a]])
        options = [runs[i][2] for i in (idx - 1, idx + 1) if 0 <= i < len(runs)]
        best = max(options, key=lambda o: explains(ref, starts, a, b, o))
        for i in range(a, b + 1):
            seg_labels[i] = best


def retime(ref_path, tgt_path):
    ref_blocks, ref_times = parse(ref_path)
    blocks, times = parse(tgt_path)
    if not ref_times or not times:
        raise SystemExit(f"empty subtitle: {ref_path if not ref_times else tgt_path}")

    ref = sorted(t[0] for t in ref_times)
    starts = [t[0] for t in times]

    offsets = candidate_offsets(ref, starts)
    if not offsets:
        raise SystemExit("no offset explains this pair — wrong episode?")
    seg_labels = absorb_short(ref, starts, smooth(label(ref, starts, offsets)))
    seg_labels = refine_boundaries(ref, starts, seg_labels)
    exact = refine(ref, starts, seg_labels)

    # A cue pushed entirely before zero belongs to footage this cut does not
    # have — E02's Vietnamese carries a "previously on" recap the BluRay drops,
    # which is most of its 66.6s. Piling those on 00:00:00 would stack eight
    # zero-length cues at the start, so they are discarded instead. A cue that
    # merely starts early is clamped: that is rounding, not missing footage.
    out, shifted, dropped = [], [], 0
    for block, (s, e), lab in zip(blocks, times, seg_labels):
        d = exact[lab]
        if e - d < 0:
            dropped += 1
            continue
        shifted.append((s - d, e - d, block))

    # A boundary can still land between two cues that overlap across the step.
    # Order is what the player actually depends on, so enforce it explicitly
    # rather than trusting the fit.
    shifted.sort(key=lambda x: (x[0], x[1]))
    for n, (s, e, block) in enumerate(shifted, 1):
        block = TS.sub(f"{fmt(s)} --> {fmt(e)}", block, count=1)
        # Reordering across a step, or dropping a cue, makes the original
        # index wrong. Players key off order rather than the number, but a
        # file whose counter jumps backwards is hard to diff against its
        # source, so renumber from one.
        block = re.sub(r"^\s*\d+\s*\n", f"{n}\n", block, count=1)
        out.append(block)

    runs = [
        (starts[a], starts[b], exact[off], b - a + 1) for a, b, off in segments(starts, seg_labels)
    ]
    return "\n\n".join(out) + "\n", runs, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("target")
    ap.add_argument("-o", "--output")
    ap.add_argument("--inplace", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    text, runs, dropped = retime(a.reference, a.target)
    if a.report:
        for s, e, off, n in runs:
            print(f"  {s/60:6.1f}m-{e/60:6.1f}m  shift {-off:+8.3f}s  ({n} cues)")
        if dropped:
            print(f"  dropped {dropped} cue(s) falling before the start of this cut")
    dest = a.output or (a.target if a.inplace else a.target + ".new")
    open(dest, "w", encoding="utf-8", newline="").write(text)
    print(f"wrote {dest}  ({len(runs)} segment{'s' if len(runs) != 1 else ''})")


if __name__ == "__main__":
    sys.exit(main())
