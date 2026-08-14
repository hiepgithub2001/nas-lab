#!/usr/bin/env bash
# Service (1b): pull-snapshot /mnt/nas-hdd/cloud (NFS) into a Kopia repo on the
# PC's /mnt/f. Runs on the PC (WSL) only.
#
# NOT YET ACTIVE. It needs two one-time steps that have not been done:
#   1. On the NAS, export /mnt/hdd over NFS (only /mnt/ssd is exported today).
#   2. On the PC, mount it at /mnt/nas-hdd and create the repository.
# See docs/back-up-services/README.md, "Service (1b)".
#
# film-data/ is deliberately not covered by this leg. It is ~915 GB against the
# PC's 930 GB /mnt/f, which already holds the SSD repository — it does not fit,
# and letting it try would fill the disk and break the leg that does fit.
# Service (2b) sends film-data to Drive instead.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
SOURCE=/mnt/nas-hdd/cloud
CONFIG="$HOME/.config/kopia/hdd-cloud.config"
LEG_NAME=local-cloud

. "$SCRIPT_DIR/lib-state.sh"
STATE_STARTED="$(date +%s)"

# Heartbeat goes to the NAS over the *SSD* export, which is the shared one and
# is mounted read-write; /mnt/nas-hdd is exported read-only.
trap 'record_state $? "$LEG_NAME" /mnt/nas-ssd' EXIT

set -a; . ~/.config/kopia/env; set +a

# The NFS-mount check that guard-source.sh does for /mnt/nas-ssd, applied here:
# an unmounted export leaves an empty directory behind, which is indistinguishable
# from "the user deleted everything" and would age out real snapshots.
mountpoint -q /mnt/nas-hdd || { echo "NFS mount /mnt/nas-hdd is not mounted" >&2; exit 1; }
[ -d "$SOURCE" ] || { echo "source $SOURCE does not exist" >&2; exit 1; }
[ -n "$(find "$SOURCE" -mindepth 1 -print -quit 2>/dev/null)" ] || {
  echo "source $SOURCE is empty — refusing to snapshot over real history" >&2
  exit 1
}

kopia snapshot create "$SOURCE" --config-file="$CONFIG"
kopia maintenance run --safety full --config-file="$CONFIG"
