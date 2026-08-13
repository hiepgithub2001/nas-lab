#!/usr/bin/env bash
# Dump every Postgres database in the cloud stack to a consistent SQL file.
#
# This is the Postgres counterpart to dump-sqlite.sh, and it exists for a
# stronger reason than that one. A SQLite file copied live is *probably*
# corrupt; a Postgres data directory copied live is worse on two counts:
#
#   1. It is 0700 uid 999 (the container's postgres user). Neither backup leg
#      runs as that user — service (2) runs as 1001 on the NAS, and the NFS
#      export squashes every client UID to 1001 for service (1). Kopia counts
#      an unreadable entry as a fatal error, so leaving the live directory in
#      the snapshot makes the backup unit fail on *every* run.
#   2. Even if it could be read, a file-level copy of a running cluster
#      captures torn pages and a write-ahead log from a different moment. It
#      is not a backup, and Immich's own documentation says so.
#
# So the live directories are excluded in excludes.txt, and these dumps are
# what actually gets backed up and restored from. See docs/cloud-services/
# README.md and docs/back-up-services/RESTORE.md.
set -euo pipefail

DUMPS=/mnt/ssd/nas-lab/appdata-dumps/postgres

# container:username:database — the values come from docker-compose.cloud.yml.
DBS=(
  immich-postgres:immich:immich
  nextcloud-postgres:nextcloud:nextcloud
)

mkdir -p "$DUMPS"
staging="$(mktemp -d "$DUMPS/.staging.XXXXXX")"
trap 'rm -rf "$staging"' EXIT

dumped=0
for entry in "${DBS[@]}"; do
  IFS=: read -r container user db <<<"$entry"

  # A stopped container is not an error: the cloud stack may legitimately be
  # down, and the previous published dump set stays in place until it is up
  # again. An *absent* dump for a *running* database is the failure worth
  # shouting about, and that cannot happen — the loop below exits non-zero.
  if [ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null)" != "true" ]; then
    echo "skip (not running): $container" >&2
    continue
  fi

  out="$staging/${db}.sql.gz"

  # --clean --if-exists makes the dump self-contained: it drops what it is
  # about to recreate, so a restore into a non-empty database works.
  #
  # No -t. Immich's documented command includes it, and it is wrong: -t
  # allocates a TTY, which turns every \n in the dump into \r\n on the way to
  # the pipe. The result restores with subtle damage inside string literals.
  docker exec "$container" \
    pg_dump --clean --if-exists --username="$user" --dbname="$db" \
    | gzip > "$out"

  # Two-part check, mirroring dump-sqlite.sh's integrity_check. gzip -t proves
  # the container is intact; the trailer proves pg_dump ran to completion
  # rather than dying halfway and leaving a valid gzip of a partial dump.
  gzip -t "$out" || { echo "FAILED gzip integrity: $db" >&2; exit 1; }
  zcat "$out" | tail -5 | grep -q 'PostgreSQL database dump complete' || {
    echo "FAILED (truncated, no completion trailer): $db" >&2
    exit 1
  }

  dumped=$((dumped + 1))
done

# Freshness sentinel, read by guard-source.sh. Written before the publish so it
# lands inside the directory being moved into place.
touch "$staging/.stamp"

# Publish atomically — a backup firing mid-run must never see a half-written set.
rm -rf "$DUMPS/.old"
[ -d "$DUMPS/current" ] && mv "$DUMPS/current" "$DUMPS/.old"
mv "$staging" "$DUMPS/current"
trap - EXIT
rm -rf "$DUMPS/.old"

chmod 755 "$DUMPS/current"
echo "dumped $dumped database(s) to $DUMPS/current"
