#!/usr/bin/env bash
# Service (2a): push-snapshot /mnt/hdd/cloud into its own Kopia repo on Google
# Drive (gdrive:hdd-cloud-bk). Runs on the NAS only, daily.
#
# This is the priority leg of the two /mnt/hdd backups. It holds the Immich
# photo library and Nextcloud user files — the least replaceable data on this
# machine, and until this existed it was the only data with no backup at all.
# The databases that index it live on the SSD and are covered separately by
# the dump sets; those metadata backups are worthless without these files.
#
# Its own repository, and therefore its own config file: Kopia connects one
# repository per config, so the default ~/.config/kopia/repository.config stays
# bound to the SSD repo and this leg passes --config-file explicitly. Same
# KOPIA_PASSWORD for all three repos, read from ~/.config/kopia/env.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
SOURCE=/mnt/hdd/cloud
CONFIG=/home/lehiep/.config/kopia/hdd-cloud.config
LEG_NAME=offsite-cloud

. "$SCRIPT_DIR/lib-state.sh"
. "$SCRIPT_DIR/lib-hdd.sh"
STATE_STARTED="$(date +%s)"

# The heartbeat always lands on /mnt/ssd, not on this leg's own source — that
# is the one filesystem both hosts share, and where backup-status.sh reads it.
trap 'record_state $? "$LEG_NAME" /mnt/ssd' EXIT

set -a; . ~/.config/kopia/env; set +a

guard_hdd_source "$SOURCE"

# Nothing is excluded here on purpose. Immich's thumbs/ and encoded-video/ are
# regenerable, and an earlier version of the backup plan would have trimmed
# them — but that plan was sized against Drive's free 15 GB tier and was
# reversed once the destination became a paid 5 TB account. See "Why both legs
# back up everything" in docs/back-up-services/README.md. Restoring a library
# that silently lacks whatever a past judgement called regenerable, discovered
# during an outage, costs more than the storage does.
kopia snapshot create "$SOURCE" --config-file="$CONFIG"
kopia maintenance run --safety full --config-file="$CONFIG"
