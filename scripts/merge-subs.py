#!/usr/bin/env python3
"""
Merge two subtitle files into one dual-language track.

Called by Bazarr's post-processing hook after each subtitle download. Given the
subtitle that was just written, it looks for its counterpart in the other
language next to the video and, if found, writes a THIRD file containing both
languages stacked per cue.

Originals are never modified. If the counterpart is missing, it exits quietly —
so it is safe to run on every download, whichever language arrives first.

Usage:
  merge-subs.py <subtitle_path> <lang_code2> [--primary en] [--secondary vi]

Output:
  <video basename>.Dual <PRI>-<SEC>.<primary3>.srt
e.g.
  John Wick (2014).Dual EN-VI.eng.srt

Jellyfin reads the ".<title>.<lang>.srt" convention, so the track shows up
named "Dual EN-VI" with the primary language attached.
"""

import os
import re
import sys

# Canonical language -> every code that counts as it, in a CLI arg OR a filename.
# Bazarr uses its own codes: zt = Chinese Traditional, zs = Simplified.
LANG_ALIASES = {
    "en": {"en", "eng"},
    "vi": {"vi", "vie"},
    "zh": {"zh", "zt", "zs", "zho", "chi", "cht", "chs"},
    "fr": {"fr", "fra", "fre"},
    "es": {"es", "spa"},
    "de": {"de", "deu", "ger"},
    "ja": {"ja", "jpn"},
}
# canonical -> 3-letter code used in the output filename
LANG3 = {"en": "eng", "vi": "vie", "zh": "zho", "fr": "fra", "es": "spa", "de": "deu", "ja": "jpn"}


def canonical(code):
    """Map any Bazarr/ISO code to its canonical language, e.g. 'zt' -> 'zh'."""
    code = (code or "").lower()
    for canon, aliases in LANG_ALIASES.items():
        if code in aliases:
            return canon
    return code[:2]

TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def to_ms(h, m, s, ms):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


def fmt(ms):
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(path):
    """Return [(start_ms, end_ms, text)], tolerant of BOM and stray blank lines."""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(path, encoding=enc) as fh:
                raw = fh.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        return []

    cues = []
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n").strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        # optional numeric index line
        if lines[0].strip().isdigit() and len(lines) > 1:
            lines = lines[1:]
        m = TIME_RE.search(lines[0]) if lines else None
        if not m:
            continue
        start = to_ms(*m.group(1, 2, 3, 4))
        end = to_ms(*m.group(5, 6, 7, 8))
        text = "\n".join(lines[1:]).strip()
        if text:
            cues.append((start, end, text))
    return cues


def merge(primary, secondary):
    """
    Split both timelines on every boundary, then emit one cue per interval that
    has text. Handles partial overlap, which naive zipping gets wrong.
    """
    bounds = sorted({t for c in primary + secondary for t in (c[0], c[1])})
    out = []
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        if hi - lo < 40:  # drop slivers below ~1 frame
            continue
        mid = (lo + hi) / 2
        top = [c[2] for c in primary if c[0] <= mid < c[1]]
        bot = [c[2] for c in secondary if c[0] <= mid < c[1]]
        if not top and not bot:
            continue
        text = "\n".join(["\n".join(top), "\n".join(bot)]).strip("\n")
        # extend the previous cue instead of emitting a duplicate
        if out and out[-1][2] == text and out[-1][1] >= lo - 40:
            out[-1] = (out[-1][0], hi, text)
        else:
            out.append((lo, hi, text))
    return out


def write_srt(path, cues):
    with open(path, "w", encoding="utf-8") as fh:
        for n, (start, end, text) in enumerate(cues, 1):
            fh.write(f"{n}\n{fmt(start)} --> {fmt(end)}\n{text}\n\n")


def ass_time(ms):
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:d}:{m:02d}:{s:02d}.{ms // 10:02d}"  # ASS uses centiseconds


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bottom,Arial,54,&H0000FFFF,&H00000000,&H00000000,0,0,1,2,1,2,60,60,40,1
Style: Top,Arial,48,&H00FFFFFF,&H00000000,&H00000000,0,0,1,2,1,8,60,60,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(path, bottom_cues, top_cues):
    """Primary (English) sits at the bottom in yellow; secondary at the top in
    white. Each keeps its own timing — independent event streams, no merge."""

    def line(start, end, style, text):
        text = text.replace("\n", "\\N").replace("{", "(").replace("}", ")")
        return f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}\n"

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(ASS_HEADER)
        for start, end, text in bottom_cues:
            fh.write(line(start, end, "Bottom", text))
        for start, end, text in top_cues:
            fh.write(line(start, end, "Top", text))


SUB_SUFFIX_RE = re.compile(
    r"\.([A-Za-z]{2,3})(-[A-Za-z]{2,4})?(\.(hi|sdh|forced|cc))?\.srt$", re.I
)


def stem_of(path):
    """Strip the trailing .<lang>.srt (and any .hi/.forced/.sdh flag)."""
    return SUB_SUFFIX_RE.sub("", os.path.basename(path))


def find_counterpart(sub_path, want):
    """Find a sibling subtitle whose filename carries the `want` language code."""
    directory = os.path.dirname(sub_path) or "."
    base = os.path.basename(sub_path)
    stem = stem_of(sub_path)
    aliases = LANG_ALIASES.get(want, {want})
    alt = "|".join(re.escape(a) for a in aliases)
    best = None
    for f in os.listdir(directory):
        if not f.lower().endswith(".srt") or f == base:
            continue
        if not f.startswith(stem):
            continue
        tail = f[len(stem):].lower()
        if re.search(rf"\.({alt})\b", tail):
            # prefer the plain track over hi/sdh/forced variants
            if best is None or not re.search(r"\.(hi|sdh|forced|cc)\b", tail):
                best = os.path.join(directory, f)
    return best


def sibling_languages(sub_path):
    """Every canonical language present as a .srt beside sub_path, excluding the
    file itself. Used when no --secondary is given: pair primary with each."""
    directory = os.path.dirname(sub_path) or "."
    base = os.path.basename(sub_path)
    stem = stem_of(sub_path)
    found = {}
    for f in os.listdir(directory):
        if not f.lower().endswith(".srt") or f == base or not f.startswith(stem):
            continue
        m = SUB_SUFFIX_RE.search(f)
        if not m:
            continue
        canon = canonical(m.group(1))
        # prefer plain track over hi/sdh/forced when the same language repeats
        is_flagged = bool(m.group(4))
        if canon not in found or not is_flagged:
            found[canon] = os.path.join(directory, f)
    return found


def make_pair(pri_path, sec_path, primary_lang, secondary_lang, layout):
    """Write one Dual <PRI>-<SEC> file. Returns the output path, or None."""
    pri, sec = parse_srt(pri_path), parse_srt(sec_path)
    if not pri or not sec:
        print(f"merge-subs: {primary_lang}/{secondary_lang} one side empty, skipping")
        return None

    stem = SUB_SUFFIX_RE.sub("", pri_path)
    label = f"Dual {primary_lang.upper()}-{secondary_lang.upper()}"
    pri3 = LANG3.get(primary_lang, primary_lang)

    if layout == "topbottom":
        out = f"{stem}.{label}.{pri3}.ass"
        write_ass(out, bottom_cues=pri, top_cues=sec)  # primary bottom, secondary top
        print(f"merge-subs: wrote {os.path.basename(out)} "
              f"(bottom={len(pri)} top={len(sec)} cues)")
    else:
        merged = merge(pri, sec)
        if not merged:
            print(f"merge-subs: {label} merged empty, skipping")
            return None
        out = f"{stem}.{label}.{pri3}.srt"
        write_srt(out, merged)
        print(f"merge-subs: wrote {os.path.basename(out)} ({len(merged)} cues)")
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = dict(
        zip(
            [a.lstrip("-") for a in sys.argv[1:] if a.startswith("--")],
            [a for a in sys.argv[1:] if not a.startswith("--")][2:],
        )
    )
    if len(args) < 2:
        print(__doc__)
        return 2

    sub_path = args[0]
    lang = canonical(args[1])
    primary_lang = canonical(opts.get("primary", "en"))
    layout = opts.get("layout", "stacked")  # "stacked" (.srt) or "topbottom" (.ass)

    # --- Mode 1: explicit pair (--secondary given) ---
    if "secondary" in opts:
        secondary_lang = canonical(opts["secondary"])
        if lang not in (primary_lang, secondary_lang):
            return 0
        other = secondary_lang if lang == primary_lang else primary_lang
        counterpart = find_counterpart(sub_path, other)
        if not counterpart:
            print(f"merge-subs: no {other} counterpart for "
                  f"{os.path.basename(sub_path)} yet")
            return 0
        pri_path = sub_path if lang == primary_lang else counterpart
        sec_path = counterpart if lang == primary_lang else sub_path
        make_pair(pri_path, sec_path, primary_lang, secondary_lang, layout)
        return 0

    # --- Mode 2: pair primary against every other language present ---
    siblings = sibling_languages(sub_path)
    siblings[lang] = sub_path  # include the file that just arrived

    if primary_lang not in siblings:
        # the download that fired isn't primary and no primary file exists yet
        print(f"merge-subs: no {primary_lang} track present, nothing to pair")
        return 0

    pri_path = siblings[primary_lang]
    others = [l for l in siblings if l != primary_lang]
    if not others:
        print("merge-subs: only the primary language present, nothing to pair")
        return 0

    for sec_lang in sorted(others):
        make_pair(pri_path, siblings[sec_lang], primary_lang, sec_lang, layout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
