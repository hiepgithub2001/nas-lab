#!/usr/bin/env python3
"""
Build one bottom-anchored, all-yellow dual-language subtitle track.

A sibling of merge-subs.py with a different goal. merge-subs.py's "topbottom"
layout separates the languages — English yellow at the bottom, the other white
at the top — which reads well on a TV and badly on a phone, where the top line
sits far from the eye and white on bright footage disappears.

This writes both languages stacked in a single block at the bottom, both in the
same yellow, using merge-subs.py's Bottom style verbatim so the two scripts
agree on position, size and colour.

Originals are never modified, and it exits quietly when the counterpart is
missing, so it is safe to run over a whole library.

Usage:
  bottom-yellow-gen.py <subtitle_path> <lang_code2> [options]

Options:
  --primary en      Language shown on the first line       (default: en)
  --secondary vi    Language shown on the second line      (default: vi)
  --vi WHICH        Which Vietnamese track to pair with when both exist:
                    ai | provider | auto                   (default: auto)
  --fontsize N      Override the shared size of 54         (default: 54)
  --force           Overwrite an existing output file

Output:
  <video basename>.Bottom Yellow <PRI>-<SEC>.<primary3>.ass

Jellyfin reads the ".<title>.<lang>.ass" convention, so the track appears named
"Bottom Yellow EN-VI" alongside merge-subs.py's "Dual EN-VI".
"""

import os
import re
import sys

# Canonical language -> every code that counts as it, in a CLI arg OR a filename.
LANG_ALIASES = {
    "en": {"en", "eng"},
    "vi": {"vi", "vie"},
    "zh": {"zh", "zt", "zs", "zho", "chi", "cht", "chs"},
    "fr": {"fr", "fra", "fre"},
    "es": {"es", "spa"},
    "de": {"de", "deu", "ger"},
    "ja": {"ja", "jpn"},
}
LANG3 = {"en": "eng", "vi": "vie", "zh": "zho", "fr": "fra",
         "es": "spa", "de": "deu", "ja": "jpn"}

TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
SUB_SUFFIX_RE = re.compile(
    r"\.([A-Za-z]{2,3})(-[A-Za-z]{2,4})?(\.(hi|sdh|forced|cc))?\.srt$", re.I
)


def log(msg):
    print(f"bottom-yellow-gen: {msg}", flush=True)


def canonical(code):
    code = (code or "").lower()
    for canon, aliases in LANG_ALIASES.items():
        if code in aliases:
            return canon
    return code[:2]


def to_ms(h, m, s, ms):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


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
        if lines[0].strip().isdigit() and len(lines) > 1:
            lines = lines[1:]
        m = TIME_RE.search(lines[0]) if lines else None
        if not m:
            continue
        text = "\n".join(lines[1:]).strip()
        if text:
            cues.append((to_ms(*m.group(1, 2, 3, 4)), to_ms(*m.group(5, 6, 7, 8)), text))
    return cues


def merge(primary, secondary):
    """
    Split both timelines on every boundary, then emit one cue per interval that
    has text. Handles partial overlap, which naive zipping gets wrong — and the
    Vietnamese tracks here are independently sourced, so their cue boundaries do
    not line up with the English at all.
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


def ass_time(ms):
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:d}:{m:02d}:{s:02d}.{ms // 10:02d}"  # ASS uses centiseconds


# One style only. Copied from merge-subs.py's Bottom style so both scripts place
# text identically: Alignment 2 (bottom centre), 60px side margins, 40px up from
# the bottom edge, against a 1920x1080 coordinate space. &H0000FFFF is ASS's
# BGR-ordered yellow; the black outline is what keeps it legible over bright
# footage, which is the whole point on a phone.
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bottom,Arial,{fontsize},&H0000FFFF,&H00000000,&H00000000,0,0,1,2,1,2,60,60,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(path, cues, fontsize):
    def line(start, end, text):
        # ASS has no HTML markup — <i> would render as literal text. Escape the
        # source's braces first so the override codes inserted below survive,
        # then map italics to ASS and drop any other tag.
        text = text.replace("{", "(").replace("}", ")")
        text = re.sub(r"<\s*i\s*>", r"{\\i1}", text, flags=re.I)
        text = re.sub(r"<\s*/\s*i\s*>", r"{\\i0}", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("\n", "\\N")
        return f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Bottom,,0,0,0,,{text}\n"

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(ASS_HEADER.format(fontsize=fontsize))
        for start, end, text in cues:
            fh.write(line(start, end, text))


def stem_of(path):
    """Strip the trailing .<lang>.srt, and the .AI marker the translator adds."""
    base = SUB_SUFFIX_RE.sub("", os.path.basename(path))
    return re.sub(r"\.AI$", "", base, flags=re.I)


def find_counterpart(sub_path, want, prefer="auto"):
    """Find the sibling subtitle for `want`, choosing deliberately between the
    provider track and the AI translation when both exist.

    merge-subs.py takes whichever os.listdir happens to yield last, which makes
    the pairing arbitrary once ai-translate-sub.py has run. Here the choice is
    explicit and reported.
    """
    directory = os.path.dirname(sub_path) or "."
    base = os.path.basename(sub_path)
    stem = stem_of(sub_path)
    alt = "|".join(re.escape(a) for a in LANG_ALIASES.get(want, {want}))

    ai, provider = None, None
    for f in sorted(os.listdir(directory)):
        if not f.lower().endswith(".srt") or f == base or not f.startswith(stem):
            continue
        tail = f[len(stem):].lower()
        if not re.search(rf"\.({alt})\b", tail):
            continue
        if re.search(r"\.(hi|sdh|forced|cc)\b", tail):
            continue  # flag variants are never the main track
        if ".ai." in tail:
            ai = os.path.join(directory, f)
        else:
            provider = os.path.join(directory, f)

    if prefer == "ai":
        return ai, "ai"
    if prefer == "provider":
        return provider, "provider"
    # auto: the AI track is the one the user asked for by tagging the title
    return (ai, "ai") if ai else (provider, "provider")


def main():
    argv = sys.argv[1:]
    args = [a for a in argv if not a.startswith("--")]
    flags = {a.lstrip("-") for a in argv if a.startswith("--") and "=" not in a}
    opts = dict(
        a.lstrip("-").split("=", 1) for a in argv if a.startswith("--") and "=" in a
    )
    if len(args) < 2:
        print(__doc__)
        return 2

    sub_path, lang = args[0], canonical(args[1])
    primary = canonical(opts.get("primary", "en"))
    secondary = canonical(opts.get("secondary", "vi"))
    prefer = opts.get("vi", "auto")
    fontsize = opts.get("fontsize", "54")
    force = "force" in flags

    if lang not in (primary, secondary):
        return 0
    if ".bottom yellow " in os.path.basename(sub_path).lower():
        return 0  # our own output
    if ".dual " in os.path.basename(sub_path).lower():
        return 0  # merge-subs.py's output

    # Work from the primary track, whichever side triggered us.
    if lang == primary:
        pri_path = sub_path
        sec_path, which = find_counterpart(sub_path, secondary, prefer)
    else:
        sec_path, which = sub_path, "trigger"
        pri_path, _ = find_counterpart(sub_path, primary)

    if not pri_path or not sec_path:
        missing = secondary if not sec_path else primary
        log(f"no {missing} counterpart for {os.path.basename(sub_path)} yet")
        return 0

    out = (f"{os.path.join(os.path.dirname(pri_path) or '.', stem_of(pri_path))}"
           f".Bottom Yellow {primary.upper()}-{secondary.upper()}"
           f".{LANG3.get(primary, primary)}.ass")
    if os.path.exists(out) and not force:
        log(f"exists, skipping: {os.path.basename(out)} (use --force)")
        return 0

    pri, sec = parse_srt(pri_path), parse_srt(sec_path)
    if not pri or not sec:
        log(f"one side empty ({len(pri)} {primary}, {len(sec)} {secondary}), skipping")
        return 0

    cues = merge(pri, sec)
    if not cues:
        log("merged empty, skipping")
        return 0

    write_ass(out, cues, fontsize)
    log(f"wrote {os.path.basename(out)} — {len(cues)} cues, "
        f"{secondary} from the {which} track, size {fontsize}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
