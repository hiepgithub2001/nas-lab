#!/usr/bin/env bash
# Service (1): pull-snapshot /mnt/nas-ssd (NFS) into the Kopia repo on /mnt/f.
# Runs on the PC (WSL) only. Twice per WSL boot — see the timer, not this script,
# for why it isn't hourly.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ROOT=/mnt/nas-ssd
LEG_NAME=local

. "$SCRIPT_DIR/lib-state.sh"
STATE_STARTED="$(date +%s)"
trap 'record_state $?' EXIT

set -a; . ~/.config/kopia/env; set +a

"$SCRIPT_DIR/guard-source.sh" "$ROOT"
kopia snapshot create "$ROOT"
kopia maintenance run --safety full
