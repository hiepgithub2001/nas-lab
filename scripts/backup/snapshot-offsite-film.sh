#!/usr/bin/env bash
# Service (2b): push-snapshot /mnt/hdd/film-data into gdrive:film-data-bk.
# Runs on the NAS only, daily, and always *after* service (2a).
#
# Second in priority, deliberately. This is ~915 GB of media that can be
# re-acquired; /mnt/hdd/cloud is photos that cannot. Drive has 4.99 TB free
# today so both fit, but if that ever stops being true, the leg that fails must
# be this one — so it runs last and refuses to start unless the cloud leg has
# just succeeded.
#
# That ordering is enforced twice over: the timer fires an hour later, and the
# check below reads (2a)'s heartbeat. Belt and braces, because a partial film
# upload consuming the quota that (2a) needed the next morning would invert the
# priority this whole design exists to express.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
SOURCE=/mnt/hdd/film-data
DEST=gdrive:film-data-bk
CONFIG=/home/lehiep/.config/kopia/film-data.config
LEG_NAME=offsite-film
MAX_CLOUD_AGE=$(( 26 * 3600 ))   # 26h — (2a) runs daily, with slack

# Ceiling on what this leg may occupy on Drive. The account is 5 TiB exactly
# (rclone about: 5497558138880 bytes), so capping media at 3.5 TiB reserves
# 1.5 TiB for (2), (2a) and anything else that lands there.
#
# Binary units throughout, because that is what the quota itself is measured in
# and what `rclone size` reports; 3.5 TB decimal would be 3.18 TiB and would
# leave a different reserve than the one intended.
MAX_DEST_BYTES=$(( 7 * 1024 * 1024 * 1024 * 1024 / 2 ))   # 3.5 TiB

. "$SCRIPT_DIR/lib-state.sh"
. "$SCRIPT_DIR/lib-hdd.sh"
STATE_STARTED="$(date +%s)"
trap 'record_state $? "$LEG_NAME" /mnt/ssd' EXIT

set -a; . ~/.config/kopia/env; set +a

# --- gate on the priority leg ------------------------------------------------
# Skipping is success, not failure: a skipped film backup while the photo
# backup is broken is the system behaving correctly, and failing here would
# only bury the alert that actually matters under a second one.
skip() {
  echo "$1" >&2
  STATE_RESULT=skipped
  STATE_REASON="$1"
  exit 0
}

if ! read -r cloud_result cloud_age < <(leg_result offsite-cloud); then
  skip "service (2a) has never run — skipping film backup until photos are safe"
fi

if [ "$cloud_result" != ok ]; then
  skip "service (2a) last run was $cloud_result — skipping film backup; fix the photo backup first"
fi

if [ "$cloud_age" -ge "$MAX_CLOUD_AGE" ]; then
  skip "service (2a) last succeeded $((cloud_age/3600))h ago (>26h) — skipping film backup"
fi

guard_hdd_source "$SOURCE"

# --- gate on the destination cap ---------------------------------------------
# Checked before the upload rather than during it, because Kopia has no notion
# of a byte budget and cannot be stopped cleanly partway. The consequence is
# that a run starting just under the cap may finish just over it, by at most
# one run's incremental — the reserve is a floor with a little give, not a hard
# wall. Sized for that: the cap sits 1.5 TiB below the quota, and a daily
# incremental over stable media is orders of magnitude smaller.
cap_rc=0
dest_within_cap "$DEST" "$MAX_DEST_BYTES" || cap_rc=$?
[ "$cap_rc" -eq 2 ] && exit 1   # could not measure — a real failure, not a decision
[ "$cap_rc" -ne 0 ] && \
  skip "$DEST is at $CAP_USED_H of its $CAP_MAX_H cap — skipping to keep 1.5 TiB free for the other legs"
echo "$DEST at $CAP_USED_H of $CAP_MAX_H cap, $CAP_LEFT_H headroom — proceeding"

# The first run uploads ~915 GB and will take as long as the uplink takes —
# likely days, not hours. That is expected and safe to leave running: Kopia
# checkpoints as it goes, a later run resumes rather than restarting, and the
# systemd timer will not launch a second copy while this one is still active.
kopia snapshot create "$SOURCE" --config-file="$CONFIG"
kopia maintenance run --safety full --config-file="$CONFIG"
