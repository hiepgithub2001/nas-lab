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
CONFIG=/home/lehiep/.config/kopia/film-data.config
LEG_NAME=offsite-film
MAX_CLOUD_AGE=$(( 26 * 3600 ))   # 26h — (2a) runs daily, with slack

. "$SCRIPT_DIR/lib-state.sh"
. "$SCRIPT_DIR/lib-hdd.sh"
STATE_STARTED="$(date +%s)"
trap 'record_state $? "$LEG_NAME" /mnt/ssd' EXIT

set -a; . ~/.config/kopia/env; set +a

# --- gate on the priority leg ------------------------------------------------
# Skipping is success, not failure: a skipped film backup while the photo
# backup is broken is the system behaving correctly, and failing here would
# only bury the alert that actually matters under a second one.
if ! read -r cloud_result cloud_age < <(leg_result offsite-cloud); then
  echo "service (2a) has never run — skipping film backup until photos are safe" >&2
  exit 0
fi

if [ "$cloud_result" != ok ]; then
  echo "service (2a) last run FAILED — skipping film backup; fix the photo backup first" >&2
  exit 0
fi

if [ "$cloud_age" -ge "$MAX_CLOUD_AGE" ]; then
  echo "service (2a) last succeeded $((cloud_age/3600))h ago (>26h) — skipping film backup" >&2
  exit 0
fi

guard_hdd_source "$SOURCE"

# The first run uploads ~915 GB and will take as long as the uplink takes —
# likely days, not hours. That is expected and safe to leave running: Kopia
# checkpoints as it goes, a later run resumes rather than restarting, and the
# systemd timer will not launch a second copy while this one is still active.
kopia snapshot create "$SOURCE" --config-file="$CONFIG"
kopia maintenance run --safety full --config-file="$CONFIG"
