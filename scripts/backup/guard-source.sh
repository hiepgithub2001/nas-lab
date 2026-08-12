#!/usr/bin/env bash
# Refuse to snapshot if the source is missing or the SQLite dumps are stale.
# An empty snapshot plus a retention policy is how you delete a backup by
# accident, so this fails loudly rather than succeeding emptily.
#
# Usage: guard-source.sh <snapshot-root>
#   NAS: guard-source.sh /mnt/ssd          (local disk)
#   PC:  guard-source.sh /mnt/nas-ssd      (NFS mount — mountpoint is checked)
set -euo pipefail

ROOT="${1:?usage: guard-source.sh <snapshot-root>}"
MAX_AGE=7200   # 2h — dumps run hourly at :50

STAMP="$ROOT/nas-lab/appdata-dumps/current/.stamp"

# On the PC the root is an NFS mount; if it is not mounted the directory still
# exists and is empty, which is exactly the silent failure this guards against.
if [ "$ROOT" = /mnt/nas-ssd ]; then
  mountpoint -q "$ROOT" || { echo "NFS mount $ROOT is not mounted" >&2; exit 1; }
fi

[ -d "$ROOT/nas-lab" ] || { echo "no nas-lab/ under $ROOT — wrong root?" >&2; exit 1; }
[ -f "$STAMP" ] || { echo "no dump set at $STAMP — is dump-sqlite.timer running?" >&2; exit 1; }

age=$(( $(date +%s) - $(stat -c %Y "$STAMP") ))
[ "$age" -lt "$MAX_AGE" ] || {
  echo "dump set is ${age}s old (>${MAX_AGE}s) — dump-sqlite has stalled" >&2
  exit 1
}
