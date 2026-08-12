#!/usr/bin/env bash
# Quiesce every SQLite database under appdata/ into a consistent dump set.
# Both backup services read the dumps and skip the live files entirely — a
# file-by-file copy of a live db + its -wal captures two different moments and
# may not restore.
set -euo pipefail

APPDATA=/mnt/ssd/nas-lab/appdata
DUMPS=/mnt/ssd/nas-lab/appdata-dumps

DBS=(
  radarr/radarr.db
  sonarr/sonarr.db
  prowlarr/prowlarr.db
  bazarr/db/bazarr.db
  jellyfin/data/data/jellyfin.db
  beszel/data/data.db
  beszel/data/auxiliary.db
  open-webui/webui.db
  open-webui/vector_db/chroma.sqlite3
  vn-dubbing/dubbing.sqlite3
)

mkdir -p "$DUMPS"
staging="$(mktemp -d "$DUMPS/.staging.XXXXXX")"
trap 'rm -rf "$staging"' EXIT

for rel in "${DBS[@]}"; do
  src="$APPDATA/$rel"
  [ -f "$src" ] || { echo "skip (absent): $rel" >&2; continue; }

  # .dump suffix keeps these clear of the **/*.db exclusion in excludes.txt.
  out="$staging/${rel//\//__}.dump"

  # VACUUM INTO takes a read lock and writes one defragmented file. The
  # container keeps running. Opened read-write on purpose: a read-only handle
  # on a WAL database cannot create the -shm file and fails to open.
  sqlite3 "$src" "VACUUM INTO '$out'"

  sqlite3 "$out" "PRAGMA integrity_check" | grep -qx ok || {
    echo "FAILED integrity_check: $rel" >&2
    exit 1
  }
done

# Freshness sentinel — the backup guards read this mtime. Written before the
# publish so it lands inside the directory being moved into place.
touch "$staging/.stamp"

# Publish atomically — a backup that fires mid-run must never see a half-written
# dump set.
rm -rf "$DUMPS/.old"
[ -d "$DUMPS/current" ] && mv "$DUMPS/current" "$DUMPS/.old"
mv "$staging" "$DUMPS/current"
trap - EXIT
rm -rf "$DUMPS/.old"

chmod 755 "$DUMPS/current"
