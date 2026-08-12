#!/bin/sh
# Single entry point for Bazarr's post-processing hook.
#
# Bazarr allows exactly one command, and on Linux it runs it through
# shlex.split with shell=False (subtitles/post_processing.py) — so "cmd1 ; cmd2"
# is NOT chainable there; the ';' would arrive as a literal argument. This
# wrapper is that one command and calls each hook in turn.
#
# Order matters: translating first means merge-subs.py finds the freshly written
# .AI.vi.srt as the Vietnamese counterpart and can build the dual EN-VI track in
# the same pass, instead of waiting for the next download to fire.
#
# Neither hook is allowed to fail the run — a subtitle that is downloaded but
# unprocessed is recoverable, whereas a non-zero exit just fills Bazarr's log.
#
# Configure in Bazarr → Settings → Subtitles → Post-processing:
#   /config/scripts/bazarr-postprocess.sh "{{subtitles}}" "{{subtitles_language_code2}}"
#
# Anything after those two arguments is forwarded verbatim to merge-subs.py, so
# the layout can be overridden from Bazarr's UI without editing this file. With
# nothing extra given the defaults below apply — they are the ones the docs
# describe, and leaving them out is what produced flat stacked .srt tracks.

set -u

if [ $# -lt 2 ]; then
    echo "usage: $0 <subtitle_path> <lang_code2> [merge-subs options...]" >&2
    exit 2
fi

DIR=$(dirname "$0")
SUB=$1
LANG=$2
shift 2

if [ $# -eq 0 ]; then
    set -- --primary en --layout topbottom
fi

python3 "$DIR/ai-translate-sub.py" "$SUB" "$LANG" || echo "ai-translate-sub: failed (continuing)" >&2
python3 "$DIR/merge-subs.py" "$SUB" "$LANG" "$@" || echo "merge-subs: failed (continuing)" >&2

exit 0
