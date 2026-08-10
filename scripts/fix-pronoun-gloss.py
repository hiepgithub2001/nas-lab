#!/usr/bin/env python3
"""
One-off migration: strip the English word from the pronoun gloss in existing
AI subtitles.

  "I (TÔI)"   -> "TÔI"
  "YOU (BẠN)" -> "BẠN"

ai-translate-sub.py stopped emitting the English word on 2026-08-09 because
these files now feed text-to-speech, which reads "I (TÔI)" aloud as "I TÔI".
Files written before that date still carry the old form; this rewrites them in
place so old and new subtitles speak alike.

Safe to re-run — already-migrated files report 0 replacements. Nothing else in
the file is touched: timings, numbering, markup and every other line are left
byte-for-byte alone.

Usage:
  fix-pronoun-gloss.py [PATH ...] [--dry-run]

PATH may be a file or a directory (searched recursively for *.AI.vi.srt).
Defaults to /mnt/hdd/film-data/media (NAS).
"""
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path("/mnt/hdd/film-data/media")
PATTERN = "*.AI.vi.srt"

# The gloss only ever follows its own English word, so anchoring on the pair is
# unambiguous. A preceding "-" or quote is part of the dialogue, not the gloss,
# and the lookbehind keeps it: only a letter before "I"/"YOU" disqualifies a
# match, so "HAI (TÔI)" — were it ever to occur — is left alone.
SUBS = (
    (re.compile(r"(?<![^\W\d_])I \(TÔI\)"), "TÔI"),
    (re.compile(r"(?<![^\W\d_])YOU \(BẠN\)"), "BẠN"),
)


def migrate(path, dry_run=False):
    """Rewrite one file. Returns the number of replacements made."""
    original = path.read_text(encoding="utf-8")
    text = original
    n = 0
    for regex, replacement in SUBS:
        text, count = regex.subn(replacement, text)
        n += count
    if n and not dry_run:
        # Same encoding and newline convention the translator wrote.
        path.write_text(text, encoding="utf-8", newline="")
    return n


def targets(args):
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            yield from sorted(p.rglob(PATTERN))
        elif p.is_file():
            yield p
        else:
            print(f"  ! not found: {p}", file=sys.stderr)


def main(argv):
    dry_run = "--dry-run" in argv
    paths = [a for a in argv if not a.startswith("--")] or [str(DEFAULT_ROOT)]

    files = list(targets(paths))
    if not files:
        print("no matching subtitles found")
        return 0

    total = changed = 0
    for f in files:
        try:
            n = migrate(f, dry_run)
        except OSError as exc:
            print(f"  ! {f}: {exc}", file=sys.stderr)
            continue
        total += n
        changed += bool(n)
        if n:
            print(f"  {n:5d}  {f.name}")

    verb = "would replace" if dry_run else "replaced"
    print(f"\n{verb} {total} gloss(es) in {changed} of {len(files)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
