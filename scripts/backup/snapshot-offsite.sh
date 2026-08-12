#!/usr/bin/env bash
# Service (2): push-snapshot /mnt/ssd into the Kopia repo on Google Drive
# (via the rclone bridge Kopia spawns itself). Runs on the NAS only, daily.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ROOT=/mnt/ssd
LEG_NAME=offsite

. "$SCRIPT_DIR/lib-state.sh"
STATE_STARTED="$(date +%s)"
trap 'record_state $?' EXIT

set -a; . ~/.config/kopia/env; set +a

"$SCRIPT_DIR/guard-source.sh" "$ROOT"
kopia snapshot create "$ROOT"
kopia maintenance run --safety full
