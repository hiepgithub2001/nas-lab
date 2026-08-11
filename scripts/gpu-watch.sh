#!/usr/bin/env bash
# Live GPU + transcode monitor for the NAS Jellyfin stack (Intel Iris Xe / i915).
#
# Shows, in one view:
#   - per-engine GPU utilisation (RCS / VCS / VECS) — which silicon is doing the work
#   - GPU clock and whether it is being power-throttled
#   - every active Jellyfin session, its play method, and why it is transcoding
#   - each live ffmpeg: encode speed, and whether the low-power encoder is engaged
#
# The key thing to watch: during a transcode, VCS/VECS should be busy and RCS should
# NOT be. High RCS means work has landed on the general-purpose shader cores.
# See docs/arr-servers/technical/TRANSCODING-TUNING.md.
#
# Usage:
#   ./scripts/gpu-watch.sh          # refresh every 3s until Ctrl-C
#   ./scripts/gpu-watch.sh 10       # refresh every 10s
#   ./scripts/gpu-watch.sh --once   # print one snapshot and exit (for logging/cron)
#
# Requires intel_gpu_top with permission to read the i915 PMU:
#   sudo apt install -y intel-gpu-tools
#   sudo setcap cap_perfmon+ep /usr/bin/intel_gpu_top

set -uo pipefail

JELLYFIN_URL="${JELLYFIN_URL:-http://localhost:8096}"
# Key is already recorded in docs/arr-servers/CREDENTIALS.md; override via env if it rotates.
API_KEY="${JELLYFIN_API_KEY:-3f9e151673bc446eab5fd83f752e9728}"
CARD="${CARD:-/sys/class/drm/card1}"

ONCE=0
INTERVAL=3
case "${1:-}" in
    --once) ONCE=1 ;;
    ''"") ;;
    *[!0-9]*) echo "usage: $0 [seconds|--once]" >&2; exit 2 ;;
    *) INTERVAL="$1" ;;
esac

if ! command -v intel_gpu_top >/dev/null 2>&1; then
    echo "intel_gpu_top not installed:  sudo apt install -y intel-gpu-tools" >&2
    exit 1
fi

B_RED=$'\e[31m'; B_GRN=$'\e[32m'; B_YEL=$'\e[33m'; B_DIM=$'\e[2m'; B_OFF=$'\e[0m'; B_BLD=$'\e[1m'

# bar <percent> <good|bad>
# "bad" engines (RCS) are coloured red when busy; "good" engines green.
bar() {
    local pct=${1%%.*} kind=$2 width=24 filled color
    [ -z "$pct" ] && pct=0
    filled=$(( pct * width / 100 ))
    [ "$filled" -gt "$width" ] && filled=$width
    [ "$filled" -lt 0 ] && filled=0
    if [ "$kind" = bad ]; then
        if   [ "$pct" -ge 60 ]; then color=$B_RED
        elif [ "$pct" -ge 25 ]; then color=$B_YEL
        else color=$B_DIM; fi
    else
        if   [ "$pct" -ge 25 ]; then color=$B_GRN
        else color=$B_DIM; fi
    fi
    # printf '%*s' handles a zero width correctly; a format-only printf would not.
    printf '%s%s%s%s' "$color" \
           "$(printf '%*s' "$filled" '' | tr ' ' '#')" \
           "$(printf '%*s' "$((width - filled))" '' | tr ' ' '.')" "$B_OFF"
}

snapshot() {
    # intel_gpu_top block-buffers when stdout is a pipe, so a timeout would kill it
    # before valid samples flush and leave only its bogus first row (all zeros).
    # Write to a file instead, and drop that first row.
    local raw tmp
    tmp=$(mktemp) || return
    timeout 5 intel_gpu_top -l -s 1000 -o "$tmp" >/dev/null 2>&1
    raw=$(grep -E '^[[:space:]]*[0-9]' "$tmp" | tail -n +2 | tail -1)
    rm -f "$tmp"

    local req act rc6 pkg rcs bcs vcs vecs
    read -r req act _ rc6 pkg rcs _ _ bcs _ _ vcs _ _ vecs _ <<<"$raw"

    if [ -z "${rcs:-}" ]; then
        echo "  ${B_RED}cannot read i915 PMU${B_OFF} — grant access with:"
        echo "      sudo setcap cap_perfmon+ep \$(command -v intel_gpu_top)"
        return
    fi

    # Clock + throttle state
    local thr="" reason
    for reason in pl1 pl2 pl4 thermal prochot; do
        [ "$(cat "$CARD/gt/gt0/throttle_reason_$reason" 2>/dev/null || echo 0)" = 1 ] \
            && thr="$thr $reason"
    done
    [ -n "$thr" ] && thr="${B_YEL}throttled:${thr}${B_OFF}" || thr="${B_DIM}not throttled${B_OFF}"

    printf '%s┌─ GPU ─────────────────────────────────────────────────────%s\n' "$B_BLD" "$B_OFF"
    printf '  clock  %s / %s MHz   pkg %sW   rc6 %s%%   %s\n' \
           "${act:-?}" "${req:-?}" "${pkg:-?}" "${rc6:-?}" "$thr"
    printf '  %-5s %s %6s%%  %s\n' "RCS"  "$(bar "${rcs:-0}"  bad)"  "${rcs:-0}"  "${B_DIM}render / shaders — should be LOW${B_OFF}"
    printf '  %-5s %s %6s%%  %s\n' "VCS"  "$(bar "${vcs:-0}"  good)" "${vcs:-0}"  "${B_DIM}codec ASIC — decode + encode${B_OFF}"
    printf '  %-5s %s %6s%%  %s\n' "VECS" "$(bar "${vecs:-0}" good)" "${vecs:-0}" "${B_DIM}VEBOX — scale / tonemap${B_OFF}"
    printf '  %-5s %s %6s%%\n'     "BCS"  "$(bar "${bcs:-0}"  good)" "${bcs:-0}"

    # Advisory on high render-engine load. The right advice depends on whether the
    # low-power encoder is already engaged, so distinguish the two cases.
    local any_vme=0 p
    for p in $(pgrep -x ffmpeg 2>/dev/null); do
        tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q -- '-low_power 1' || any_vme=1
    done
    if [ "${rcs%%.*}" -ge 60 ] 2>/dev/null; then
        if [ "$any_vme" = 1 ]; then
            printf '  %s! RCS busy and an ffmpeg is NOT using low_power — encode is on the%s\n' "$B_YEL" "$B_OFF"
            printf '  %s  shader cores. Enable the Intel Low-Power encoder toggles.%s\n' "$B_YEL" "$B_OFF"
        else
            printf '  %s! RCS busy despite low_power — this is filter/VPP work, not encode%s\n' "$B_DIM" "$B_OFF"
            printf '  %s  (expected with HDR tonemapping). Encode itself is on VCS.%s\n' "$B_DIM" "$B_OFF"
        fi
    fi

    # Live ffmpeg processes
    printf '%s├─ ffmpeg ──────────────────────────────────────────────────%s\n' "$B_BLD" "$B_OFF"
    local found=0 pid args lp preset speed logdir
    logdir=/mnt/ssd/nas-lab/appdata/jellyfin/log
    for pid in $(pgrep -x ffmpeg 2>/dev/null); do
        found=1
        args=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)
        case "$args" in *"-low_power 1"*) lp="${B_GRN}low_power${B_OFF}" ;;
                        *)                lp="${B_RED}VME (no low_power)${B_OFF}" ;; esac
        preset=$(printf '%s' "$args" | grep -oE '\-preset [a-z]+' | head -1 | cut -d' ' -f2)
        speed=$(grep -oE 'speed= *[0-9.]+x' \
                "$(ls -t "$logdir"/FFmpeg.Transcode-*.log 2>/dev/null | head -1)" 2>/dev/null \
                | tail -1 | tr -d ' ')
        printf '  pid %-7s %-28s preset=%-9s %s\n' "$pid" "$lp" "${preset:-?}" "${speed:-}"
    done
    [ "$found" = 0 ] && printf '  %snone running%s\n' "$B_DIM" "$B_OFF"

    # Jellyfin sessions
    printf '%s├─ Jellyfin sessions ───────────────────────────────────────%s\n' "$B_BLD" "$B_OFF"
    curl -s --max-time 4 -H "X-Emby-Token: $API_KEY" "$JELLYFIN_URL/Sessions" 2>/dev/null \
    | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print("  (Jellyfin API unreachable)"); sys.exit()
n=0
for s in d:
    ni=s.get("NowPlayingItem")
    if not ni: continue
    n+=1
    ts=s.get("TranscodingInfo") or {}
    pm=(s.get("PlayState") or {}).get("PlayMethod") or "?"
    mark={"DirectPlay":"\033[32m","DirectStream":"\033[32m"}.get(pm,"\033[33m")
    why=",".join(ts.get("TranscodeReasons") or []) or "-"
    name=(ni.get("Name") or "")[:26]
    dev=(s.get("DeviceName") or "?")[:12]
    print(f"  {dev:<12} {mark}{pm:<13}\033[0m {name:<26} {why}")
if not n: print("  \033[2mnothing playing\033[0m")
'
    printf '%s└───────────────────────────────────────────────────────────%s\n' "$B_BLD" "$B_OFF"
}

if [ "$ONCE" = 1 ]; then
    snapshot
    exit 0
fi

trap 'printf "\n"; exit 0' INT
while true; do
    out=$(snapshot)
    clear
    printf '%s  nas-lab GPU monitor%s   %s   %sCtrl-C to quit%s\n\n' \
           "$B_BLD" "$B_OFF" "$(date '+%H:%M:%S')" "$B_DIM" "$B_OFF"
    printf '%s\n' "$out"
    sleep "$INTERVAL"
done
