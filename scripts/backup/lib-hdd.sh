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

# Refuse to add to a Drive folder that has reached its size cap.
#
# The 5 TiB on Drive is shared: (2) config/databases, (2a) photos and files,
# and (2b) media. Media is the one that grows without bound and the one whose
# contents can be re-acquired, so it is the leg that gets a ceiling — a cap on
# film-data-bk is really a floor under everything else.
#
# Measured with `rclone size`, which walks the folder: ~3.5 minutes over 137k
# objects today. `--fast-list` was measured slower on this remote (4m34s vs
# 3m39s), so it is deliberately not used. That cost is paid once per day
# against a leg that already runs for ~10 minutes.
#
# A destination we cannot measure is an error, not a skip. Passing a cap we
# failed to verify would defeat the point, and if Drive is unreachable the
# snapshot was going to fail anyway — better to say why.
dest_within_cap() {
  local remote="${1:?usage: dest_within_cap <remote> <cap-bytes>}"
  local cap="${2:?usage: dest_within_cap <remote> <cap-bytes>}"
  local used

  used=$(rclone size "$remote" --json 2>/dev/null | jq -r '.bytes // empty')
  if [ -z "$used" ]; then
    echo "cannot measure $remote — refusing to snapshot without verifying the cap" >&2
    return 2
  fi

  CAP_USED_H=$(awk -v b="$used" 'BEGIN{printf "%.2f TiB", b/1024/1024/1024/1024}')
  CAP_MAX_H=$(awk  -v b="$cap"  'BEGIN{printf "%.2f TiB", b/1024/1024/1024/1024}')
  CAP_LEFT_H=$(awk -v b="$((cap - used))" 'BEGIN{printf "%.2f TiB", b/1024/1024/1024/1024}')

  [ "$used" -lt "$cap" ]
}
