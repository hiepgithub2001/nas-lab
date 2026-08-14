#!/usr/bin/env bash
# Shared helpers for the /mnt/hdd legs.
#
# The SSD legs guard themselves with guard-source.sh, which checks the SQLite
# and Postgres dump sets. Neither applies here: /mnt/hdd holds only ordinary
# files (photo originals, media), and the databases that describe them live on
# the SSD and are already covered by that guard. What /mnt/hdd needs instead is
# protection against the *other* way a backup deletes data — snapshotting an
# empty or unmounted source, then letting retention prune the real snapshots
# that came before it.

# Refuse to snapshot a source that is missing or empty.
#
# An empty snapshot is not a harmless no-op. Kopia records it as the newest
# state of the source, and the retention policy then ages out the older, real
# snapshots behind it. A drive that failed to mount looks exactly like a
# directory whose contents were all deleted, and neither Kopia nor rclone can
# tell the difference — so this refuses loudly rather than succeeding emptily.
guard_hdd_source() {
  local src="${1:?usage: guard_hdd_source <path>}"

  [ -d "$src" ] || { echo "source $src does not exist" >&2; return 1; }

  # /mnt/hdd is a separate physical disk. If it fails to mount, the mountpoint
  # still exists as an empty directory on the root filesystem.
  mountpoint -q /mnt/hdd || { echo "/mnt/hdd is not mounted" >&2; return 1; }

  # -mindepth 1 -> the directory itself does not count as content.
  if [ -z "$(find "$src" -mindepth 1 -print -quit 2>/dev/null)" ]; then
    echo "source $src is empty — refusing to snapshot over real history" >&2
    return 1
  fi
}

# Read a leg's recorded outcome from the shared heartbeat directory.
# Prints "ok" or "fail" on stdout, plus the age in seconds on stderr-free
# stdout line 2. Returns non-zero if the leg has never run.
leg_result() {
  local leg="${1:?usage: leg_result <leg-name>}"
  local f=/mnt/ssd/nas-lab/.backup-state/"$leg".state
  [ -f "$f" ] || return 1
  local result age
  result="$(sed -n 's/^result=//p' "$f")"
  age=$(( $(date +%s) - $(sed -n 's/^finished_at=//p' "$f") ))
  echo "$result $age"
}
