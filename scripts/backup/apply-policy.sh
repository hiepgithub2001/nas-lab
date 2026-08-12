#!/usr/bin/env bash
# Apply retention + exclusion policy to this host's Kopia repository, then
# prove it landed.
#
# Why this exists rather than a paste-in loop: a per-rule loop of
# `kopia policy set --add-ignore` runs one process per rule, and each one reads
# the whole policy manifest, appends, and writes it back. Run back-to-back, a
# later write can be based on a stale read and silently discard earlier rules —
# while still printing `- adding "..." to "ignore rules"` for every one of them.
# Observed 2026-08-13 on the PC: 18 rules reported added, 16 landed.
#
# The fix is to pass every rule to a SINGLE invocation: one read-modify-write,
# no race window. That also matters enormously on the offsite leg, where each
# kopia process spawns its own `rclone serve webdav` bridge to Drive — a
# per-rule loop there costs a bridge startup per rule and takes minutes.
#
# The verify step stays regardless: it diffs the resulting policy against the
# source files and exits non-zero on any mismatch. Safe to re-run.
#
# Both legs back up the same full data set — the only exclusions are things
# that are physically impossible to back up (unreadable files, sockets), not
# size or value judgements. See excludes.txt for why.
#
# Usage: apply-policy.sh local|offsite
#   PC:  apply-policy.sh local     -> /mnt/nas-ssd
#   NAS: apply-policy.sh offsite   -> /mnt/ssd
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

set -a; . ~/.config/kopia/env; set +a

case "${1:-}" in
  local)
    ROOT=/mnt/nas-ssd
    LISTS=("$SCRIPT_DIR/excludes.txt")
    RETENTION=(--keep-latest 10 --keep-daily 14 --keep-weekly 8 --keep-monthly 12)
    ;;
  offsite)
    ROOT=/mnt/ssd
    LISTS=("$SCRIPT_DIR/excludes.txt")
    RETENTION=(--keep-daily 14 --keep-weekly 8 --keep-monthly 24)
    ;;
  *)
    echo "usage: apply-policy.sh local|offsite" >&2
    exit 2
    ;;
esac

# Clear first, in its own invocation. --clear-ignore and --add-ignore cannot be
# combined: kopia applies the clear and silently drops the adds, leaving an
# empty ignore list. (Caught by the verify step below on 2026-08-13; the run
# printed "removing all from ignore rules" and no "adding" lines at all.)
kopia policy set "$ROOT" --compression zstd "${RETENTION[@]}" --clear-ignore

# Then every rule in ONE invocation — a single read-modify-write, so no rule can
# be lost to a stale read, and only one rclone bridge startup on the Drive leg.
IGNORE_ARGS=()
while read -r line; do
  case "$line" in ''|\#*) continue ;; esac
  IGNORE_ARGS+=(--add-ignore "$line")
done < <(cat "${LISTS[@]}")

kopia policy set "$ROOT" "${IGNORE_ARGS[@]}"

want="$(grep -hvE '^[[:space:]]*(#|$)' "${LISTS[@]}" | sort -u)"
got="$(kopia policy show "$ROOT" \
        | sed -n '/Ignore rules/,/Read ignore rules/p' \
        | sed '1d;$d;s/^[[:space:]]*//' | sort -u)"

if [ "$want" != "$got" ]; then
  echo "FAILED: policy for $ROOT does not match the exclusion lists" >&2
  echo "  < wanted (from files)   > got (from policy)" >&2
  diff <(printf '%s\n' "$want") <(printf '%s\n' "$got") >&2 || true
  exit 1
fi

echo "policy for $ROOT: $(printf '%s\n' "$want" | wc -l) ignore rules, matches ${LISTS[*]##*/}"
