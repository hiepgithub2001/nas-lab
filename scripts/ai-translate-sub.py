#!/usr/bin/env python3
"""
Translate an English subtitle to Vietnamese with the local Ollama model.

Called by Bazarr's post-processing hook after each subtitle download. Runs only
when the English track just landed AND the movie/series carries the `ai-subtitle`
tag in Radarr/Sonarr, so the library opts in title by title rather than wholesale.

Writes a THIRD file; the English original is never modified:

  <video basename>.AI.vi.srt

Jellyfin reads the ".<title>.<lang>.srt" convention, so the track shows up named
"AI" under Vietnamese — distinguishable at a glance from a real Vietnamese
subtitle a provider supplied. merge-subs.py also finds it as the `vi`
counterpart, so chaining the two yields a dual EN-VI track as well.

Usage:
  ai-translate-sub.py <subtitle_path> <lang_code2> [options]

Options:
  --tag NAME      Radarr/Sonarr tag that opts a title in   (default: ai-subtitle)
  --model NAME    Ollama model                             (default: qwen3:8b)
  --batch N       Cues per request                         (default: 20)
  --context N     Preceding cues sent as context, untranslated (default: 3)
  --force         Overwrite an existing output file
  --dry-run       Report what would happen, translate nothing

Exits 0 in every "nothing to do" case, so it is safe to run on every download.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

BAZARR_CONFIG = "/config/config/config.yaml"
OLLAMA_URL = "http://ollama:11434/api/generate"

# Bazarr hands us its own language codes; only English input is translated.
EN_CODES = {"en", "eng"}

TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
# Trailing ".<lang>.srt" plus any .hi/.sdh/.forced flag — same shape merge-subs uses.
SUB_SUFFIX_RE = re.compile(
    r"\.([A-Za-z]{2,3})(-[A-Za-z]{2,4})?(\.(hi|sdh|forced|cc))?\.srt$", re.I
)
# HTML (<i>) and ASS override ({\i1}) markup. Never send these to the model: it
# rewrites, drops or "corrects" them. Strip, translate, put back.
MARKUP_RE = re.compile(r"(<[^>]+>|\{\\[^}]*\})")


def log(msg):
    print(f"ai-translate-sub: {msg}", flush=True)


# --- Bazarr / *arr lookup -------------------------------------------------

def arr_settings():
    """Read Sonarr+Radarr host and API key out of Bazarr's own config.

    Avoids duplicating the keys into the environment: Bazarr already holds them,
    and they stay in one place when rotated. Parsed by regex rather than a YAML
    library, which the container's system python does not necessarily have.
    """
    try:
        with open(BAZARR_CONFIG, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        log(f"cannot read {BAZARR_CONFIG}: {exc}")
        return {}

    out = {}
    for app in ("sonarr", "radarr"):
        block = re.search(rf"^{app}:\n((?:[ \t]+.*\n)+)", raw, re.M)
        if not block:
            continue
        fields = dict(
            re.findall(r"^[ \t]+(\w+):[ \t]*(.*?)[ \t]*$", block.group(1), re.M)
        )
        key = fields.get("apikey", "").strip("'\"")
        ip = fields.get("ip", "").strip("'\"")
        if not key or not ip:
            continue
        scheme = "https" if fields.get("ssl", "false") == "true" else "http"
        base = fields.get("base_url", "").strip("'\"").strip("/")
        out[app] = {
            "url": f"{scheme}://{ip}:{fields.get('port', '')}" + (f"/{base}" if base else ""),
            "key": key,
        }
    return out


def arr_get(app, path):
    req = urllib.request.Request(
        f"{app['url']}/api/v3/{path}", headers={"X-Api-Key": app["key"]}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def tagged(sub_path, tag_name):
    """True if the title owning this subtitle carries `tag_name`.

    Matches on filesystem path: Bazarr, Sonarr and Radarr all mount the library
    at the same /data root, so the *arr's `path` is a prefix of the subtitle's.
    The longest matching path wins, which keeps nested libraries honest.
    """
    settings = arr_settings()
    if not settings:
        return False, "no Sonarr/Radarr settings in Bazarr config"

    best = None  # (len(path), app_label, title, tag_ids, settings_entry)
    for app_name, endpoint in (("sonarr", "series"), ("radarr", "movie")):
        app = settings.get(app_name)
        if not app:
            continue
        try:
            items = arr_get(app, endpoint)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log(f"{app_name} query failed: {exc}")
            continue
        for item in items:
            root = (item.get("path") or "").rstrip("/")
            if root and sub_path.startswith(root + os.sep):
                cand = (len(root), app_name, item.get("title", "?"), item.get("tags", []))
                if best is None or cand[0] > best[0]:
                    best = cand

    if best is None:
        return False, "not found in Sonarr or Radarr"

    _, app_name, title, tag_ids = best
    if not tag_ids:
        return False, f"{title} has no tags"
    try:
        labels = {t["id"]: t["label"] for t in arr_get(settings[app_name], "tag")}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"tag lookup failed: {exc}"

    names = {labels.get(i, "") for i in tag_ids}
    if tag_name in names:
        return True, f"{title} [{app_name}] tagged {tag_name}"
    return False, f"{title} tagged {sorted(n for n in names if n)}, not {tag_name}"


# --- Subtitle IO ----------------------------------------------------------

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
        if lines[0].strip().isdigit() and len(lines) > 1:
            lines = lines[1:]
        m = TIME_RE.search(lines[0]) if lines else None
        if not m:
            continue
        text = "\n".join(lines[1:]).strip()
        if text:
            cues.append((to_ms(*m.group(1, 2, 3, 4)), to_ms(*m.group(5, 6, 7, 8)), text))
    return cues


def write_srt(path, cues):
    with open(path, "w", encoding="utf-8") as fh:
        for n, (start, end, text) in enumerate(cues, 1):
            fh.write(f"{n}\n{fmt(start)} --> {fmt(end)}\n{text}\n\n")


# --- Markup and line structure -------------------------------------------

def split_units(text):
    """Break a cue into the pieces that get translated independently.

    Two speakers in one cue ("- Get down!\\n- I'm hit!") must stay on separate
    lines or the dash convention breaks. Anything else is a wrapped sentence,
    where the line break is cosmetic and joining reads better in Vietnamese.
    """
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) > 1 and all(ln.lstrip().startswith(("-", "–", "—")) for ln in lines):
        return lines, True
    return [" ".join(lines)], False


def strip_markup(unit):
    """Return (bare_text, leading_markup, trailing_markup).

    Markup that wraps the whole unit is preserved around the translation. Markup
    in the middle is dropped: there is no reliable way to re-anchor it once the
    word order changes, and a mangled tag is worse than an absent one.
    """
    lead, trail = "", ""
    while True:
        m = MARKUP_RE.match(unit)
        if not m:
            break
        lead += m.group(0)
        unit = unit[m.end():]
    while True:
        m = list(MARKUP_RE.finditer(unit))
        if not m or m[-1].end() != len(unit):
            break
        trail = m[-1].group(0) + trail
        unit = unit[: m[-1].start()]
    return MARKUP_RE.sub("", unit).strip(), lead, trail


# --- Ollama ---------------------------------------------------------------

PROMPT = """You are translating film subtitles from English into Vietnamese.

Rules:
- Translate meaning, not word for word. Use natural spoken Vietnamese.
- Choose pronouns from the relationship between the speakers, using the context.
- Keep each translation on ONE line. Do not merge or split entries.
- Output exactly {n} lines, each "<number>. <Vietnamese>". Nothing else.
{context}
Translate these {n} lines:
{lines}"""


def ollama(model, prompt, timeout=600):
    body = json.dumps({
        "model": model,
        "stream": False,
        # Qwen3 otherwise emits reasoning blocks that would have to be stripped.
        "think": False,
        "options": {"temperature": 0.3},
        "prompt": prompt,
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp).get("response", "").strip()


NUMBERED_RE = re.compile(r"^\s*(\d+)\s*[.):\]]?\s*(.+?)\s*$")


def parse_numbered(reply, n):
    """Pull `n` translations out of the reply, indexed by the number the model used.

    Asked for "no numbering" the model numbers anyway, and asked to number it
    sometimes skips one — so parse by the number it actually emitted rather than
    trusting position, and let the caller decide what to do about gaps.
    """
    out = {}
    for line in reply.split("\n"):
        m = NUMBERED_RE.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        if 1 <= idx <= n and idx not in out:
            out[idx] = m.group(2).strip()
    return out


def translate_batch(model, units, context):
    """Translate a list of bare strings. Returns a list the same length.

    Falls back to translating the stragglers one at a time when the batch reply
    is short or misnumbered, so one bad response costs seconds rather than the
    whole file.
    """
    ctx = ""
    if context:
        ctx = "\nEarlier lines, for context only — do not translate them:\n" + "\n".join(
            f"  {c}" for c in context
        ) + "\n"
    numbered = "\n".join(f"{i}. {u}" for i, u in enumerate(units, 1))
    prompt = PROMPT.format(n=len(units), context=ctx, lines=numbered)

    got = {}
    try:
        got = parse_numbered(ollama(model, prompt), len(units))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log(f"batch request failed ({exc}); falling back to single lines")

    result, missed = [], 0
    for i, unit in enumerate(units, 1):
        if i in got:
            result.append(got[i])
            continue
        single = PROMPT.format(n=1, context=ctx, lines=f"1. {unit}")
        try:
            one = parse_numbered(ollama(model, single, timeout=120), 1)
            result.append(one.get(1) or unit)
            missed += 0 if one.get(1) else 1
        except (urllib.error.URLError, OSError, ValueError):
            result.append(unit)  # keep English rather than lose the cue
            missed += 1
    return result, missed


# --- Main -----------------------------------------------------------------

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

    sub_path, lang = args[0], args[1].lower()
    tag_name = opts.get("tag", "ai-subtitle")
    model = opts.get("model", "qwen3:8b")
    batch_size = int(opts.get("batch", 20))
    context_size = int(opts.get("context", 3))
    force, dry = "force" in flags, "dry-run" in flags

    if lang not in EN_CODES:
        return 0  # only English is a translation source
    if not sub_path.lower().endswith(".srt"):
        log(f"not an .srt, skipping: {os.path.basename(sub_path)}")
        return 0
    if ".dual " in os.path.basename(sub_path).lower():
        return 0  # our own merged output, not a source

    ok, why = tagged(sub_path, tag_name)
    if not ok:
        log(f"skip — {why}")
        return 0
    log(why)

    out_path = SUB_SUFFIX_RE.sub("", sub_path) + ".AI.vi.srt"
    if os.path.exists(out_path) and not force:
        log(f"exists, skipping: {os.path.basename(out_path)} (use --force)")
        return 0

    cues = parse_srt(sub_path)
    if not cues:
        log(f"no cues parsed from {os.path.basename(sub_path)}")
        return 0

    # Flatten cues to translation units, remembering how to rebuild each cue.
    units, layout = [], []  # layout: (n_units, split_lines, [(lead, trail)])
    for _, _, text in cues:
        pieces, split = split_units(text)
        marks = []
        for piece in pieces:
            bare, lead, trail = strip_markup(piece)
            units.append(bare)
            marks.append((lead, trail))
        layout.append((len(pieces), split, marks))

    log(f"{len(cues)} cues -> {len(units)} units, {model}, batches of {batch_size}")
    if dry:
        log(f"dry run — would write {os.path.basename(out_path)}")
        return 0

    translated, missed = [], 0
    for start in range(0, len(units), batch_size):
        chunk = units[start:start + batch_size]
        context = units[max(0, start - context_size):start]
        done, miss = translate_batch(model, chunk, context)
        translated.extend(done)
        missed += miss
        log(f"  {min(start + batch_size, len(units))}/{len(units)}")

    # Rebuild cues, restoring the markup that wrapped each unit.
    out_cues, pos = [], 0
    for (start, end, _), (count, split, marks) in zip(cues, layout):
        parts = []
        for j in range(count):
            lead, trail = marks[j]
            parts.append(f"{lead}{translated[pos + j]}{trail}")
        pos += count
        out_cues.append((start, end, ("\n" if split else " ").join(parts)))

    write_srt(out_path, out_cues)
    log(f"wrote {os.path.basename(out_path)} ({len(out_cues)} cues"
        + (f", {missed} untranslated" if missed else "") + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
