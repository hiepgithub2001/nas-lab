#!/usr/bin/env python3
"""
Restyle each subtitle language into its own bottom-anchored yellow track.

Not a merge. Given a video's English and Vietnamese subtitles, this writes two
independent single-language tracks:

  <video>.English-custom.eng.ass
  <video>.Vietnamese-custom.vie.ass

Both use merge-subs.py's Bottom style verbatim — Arial 54, yellow with a black
outline, bottom centre, 40px up from the edge — which is the size and position
that reads well on a phone. The difference from merge-subs.py is that nothing is
stacked: each language stands alone, so the viewer picks one from Jellyfin's
subtitle menu and gets large yellow text at the bottom either way.

Existing tracks are left alone: the source .srt files, and merge-subs.py's
"Dual EN-VI" track, are never touched.

When both a provider Vietnamese track and an AI translation exist, --vi decides
which one becomes Vietnamese-custom.

Usage:
  bottom-yellow-gen.py <subtitle_path> <lang_code2> [options]

Options:
  --langs en,vi     Languages to emit a custom track for   (default: en,vi)
  --vi WHICH        Which Vietnamese source to restyle:
                    ai | provider | auto                   (default: auto)
  --fontsize N      Override the shared size of 54         (default: 54)

Jellyfin reads the ".<title>.<lang>.ass" convention, so the tracks appear named
"English-custom" and "Vietnamese-custom" beside the originals.
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
# The human-readable half of the output filename, which is what Jellyfin shows.
LANG_NAME = {"en": "English", "vi": "Vietnamese", "zh": "Chinese", "fr": "French",
             "es": "Spanish", "de": "German", "ja": "Japanese"}

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
    """Strip the trailing .<lang>.srt, and the .AI marker the translator adds, so
    every output lands on the video's own basename."""
    base = SUB_SUFFIX_RE.sub("", os.path.basename(path))
    return re.sub(r"\.AI$", "", base, flags=re.I)


def sources_for(sub_path, wanted, prefer="auto"):
    """Map each wanted language to the .srt that should become its custom track.

    Scans the siblings sharing the video's basename. Where a language has both a
    provider file and an AI translation, `prefer` decides; "auto" takes the AI
    one, since it only exists because the title was tagged for it.
    """
    directory = os.path.dirname(sub_path) or "."
    stem = stem_of(sub_path)
    found = {}

    for f in sorted(os.listdir(directory)):
        if not f.lower().endswith(".srt") or not f.startswith(stem):
            continue
        m = SUB_SUFFIX_RE.search(f)
        if not m:
            continue
        canon = canonical(m.group(1))
        if canon not in wanted:
            continue
        if m.group(4):
            continue  # hi/sdh/forced/cc variants are never the main track
        tail = f[len(stem):].lower()
        kind = "ai" if ".ai." in tail else "provider"
        found.setdefault(canon, {})[kind] = os.path.join(directory, f)

    picked = {}
    for canon, variants in found.items():
        if prefer in variants:
            picked[canon] = (variants[prefer], prefer)
        elif prefer == "auto":
            kind = "ai" if "ai" in variants else "provider"
            picked[canon] = (variants[kind], kind)
        # an explicit --vi=ai with no AI file means: don't guess, skip it
    return picked


def main():
    argv = sys.argv[1:]
    args = [a for a in argv if not a.startswith("--")]
    opts = dict(
        a.lstrip("-").split("=", 1) for a in argv if a.startswith("--") and "=" in a
    )
    if len(args) < 2:
        print(__doc__)
        return 2

    sub_path, lang = args[0], canonical(args[1])
    wanted = {canonical(c) for c in opts.get("langs", "en,vi").split(",") if c.strip()}
    prefer = opts.get("vi", "auto")
    fontsize = opts.get("fontsize", "54")

    if lang not in wanted:
        return 0
    base = os.path.basename(sub_path).lower()
    if "-custom." in base:
        return 0  # our own output
    if ".dual " in base:
        return 0  # merge-subs.py's output

    picked = sources_for(sub_path, wanted, prefer)
    if not picked:
        log(f"no source tracks for {os.path.basename(sub_path)}")
        return 0

    for canon in sorted(picked):
        src, kind = picked[canon]
        cues = parse_srt(src)
        if not cues:
            log(f"{canon}: {os.path.basename(src)} parsed empty, skipping")
            continue

        out = (f"{os.path.join(os.path.dirname(src) or '.', stem_of(src))}"
               f".{LANG_NAME.get(canon, canon.upper())}-custom"
               f".{LANG3.get(canon, canon)}.ass")
        verb = "replaced" if os.path.exists(out) else "wrote"
        write_ass(out, cues, fontsize)
        log(f"{verb} {os.path.basename(out)} — {len(cues)} cues "
            f"from the {kind} track, size {fontsize}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
