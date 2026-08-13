#!/usr/bin/env bash
# One-screen health report covering BOTH backup legs.
#
# Runs on either host. The leg you are standing on is reported live — timers,
# last exit status, in-flight progress, repository contents. The other leg is
# reported from the heartbeat it left in the shared NFS directory
# (nas-lab/.backup-state/), since its repository and its systemd units are not
# visible from here.
#
# Answers, per leg:
#   1. Is the SQLite dump set fresh?  (both legs refuse to snapshot without it)
#   2. Is the timer installed, enabled, scheduled?
#   3. Did the last run succeed?
#   4. Is a snapshot running right now, how fast, how far?
#   5. How many snapshots, how old is the newest, what policy is in force?
#   6. How much space, here and at the destination?
#
# Exit code is 0 only if nothing is wrong, so this is usable from a monitor.
#
# Usage: backup-status.sh [--quick]
#   --quick   skip repository/destination queries. On the offsite leg every
#             kopia call spawns its own `rclone serve webdav` bridge to Drive
#             and takes ~20-40s, so --quick keeps this instant when you only
#             want timer/dump/heartbeat health.
set -uo pipefail

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

PROBLEMS=0
ok()    { printf '  \033[32mOK\033[0m    %s\n' "$*"; }
warn()  { printf '  \033[33mWARN\033[0m  %s\n' "$*"; PROBLEMS=$((PROBLEMS+1)); }
bad()   { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; PROBLEMS=$((PROBLEMS+1)); }
info()  { printf '        %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }
rule()  { printf '\033[1m%s\033[0m\n' "════════════════════════════════════════════════════════════"; }

# --- which leg are we standing on -------------------------------------------
if mountpoint -q /mnt/nas-ssd 2>/dev/null; then
  HERE=local;   ROOT=/mnt/nas-ssd
elif [ -d /mnt/ssd/nas-lab ]; then
  HERE=offsite; ROOT=/mnt/ssd
else
  echo "Cannot tell which host this is: no /mnt/nas-ssd mount, no /mnt/ssd/nas-lab." >&2
  exit 2
fi
STATE_DIR="$ROOT/nas-lab/.backup-state"

leg_service() { [ "$1" = local ] && echo nas-backup.service || echo nas-offsite.service; }
leg_timer()   { [ "$1" = local ] && echo nas-backup.timer   || echo nas-offsite.timer; }
leg_dest()    { [ "$1" = local ] && echo /mnt/f/nas-ssd-backup || echo gdrive:nas-ssd-backup; }
leg_root()    { [ "$1" = local ] && echo /mnt/nas-ssd || echo /mnt/ssd; }
leg_title()   { [ "$1" = local ] \
                  && echo "SERVICE (1) — local, PC/WSL -> /mnt/f, twice per WSL boot" \
                  || echo "SERVICE (2) — offsite, NAS -> Google Drive, daily 03:30"; }

printf '\033[1mBackup status\033[0m — %s, %s\n' "$(hostname)" "$(date '+%Y-%m-%d %H:%M:%S %Z')"
printf 'standing on the \033[1m%s\033[0m leg; the other is read from %s\n' "$HERE" "${STATE_DIR/#$ROOT/\$ROOT}"

# --- shared prerequisite -----------------------------------------------------
head_ "SQLite dumps — shared prerequisite, both legs need this fresh"
STAMP="$ROOT/nas-lab/appdata-dumps/current/.stamp"
if [ ! -f "$STAMP" ]; then
  bad "no dump set at $STAMP — nas-dump-sqlite.timer has never run"
else
  age=$(( $(date +%s) - $(stat -c %Y "$STAMP") ))
  human="$((age/60))m"; [ "$age" -ge 3600 ] && human="$((age/3600))h$(((age%3600)/60))m"
  n=$(ls -1 "$(dirname "$STAMP")"/*.dump 2>/dev/null | wc -l)
  if [ "$age" -lt 7200 ]; then ok "$n dumps, $human old (guard allows <2h)"
  else bad "dump set is $human old (>2h) — both guards will refuse to snapshot"; fi
fi
if [ "$HERE" = offsite ]; then
  t=$(systemctl is-enabled nas-dump-sqlite.timer 2>&1)
  [ "$t" = enabled ] && ok "nas-dump-sqlite.timer enabled" \
                     || bad "nas-dump-sqlite.timer is $t"
fi

# --- per-leg report ----------------------------------------------------------
report_live_leg() {
  local leg="$1" root dest svc tmr
  root="$(leg_root "$leg")"; dest="$(leg_dest "$leg")"
  svc="$(leg_service "$leg")"; tmr="$(leg_timer "$leg")"

  info "snapshotting $root -> $dest   (live, this host)"

  # timer
  if [ "$(systemctl is-enabled "$tmr" 2>&1)" = "not-found" ]; then
    bad "$tmr not installed — nothing runs on a schedule"
    info "install: sudo cp $root/nas-lab/scripts/backup/systemd/${tmr%.timer}.{service,timer} /etc/systemd/system/"
  else
    local en ac
    en=$(systemctl is-enabled "$tmr" 2>/dev/null); ac=$(systemctl is-active "$tmr" 2>/dev/null)
    [ "$en" = enabled ] && [ "$ac" = active ] && ok "$tmr enabled, active" \
      || warn "$tmr enabled=$en active=$ac"
    systemctl list-timers "$tmr" --all --no-pager 2>/dev/null \
      | awk 'NR==2 && NF {print "        next run: "$1" "$2" "$3"  (in "$4")"}'
  fi

  # last run
  if [ "$(systemctl is-enabled "$svc" 2>&1)" = "not-found" ]; then
    warn "$svc not installed"
  else
    local st rs cd
    st=$(systemctl is-active "$svc" 2>/dev/null)
    rs=$(systemctl show "$svc" -p Result --value 2>/dev/null)
    cd=$(systemctl show "$svc" -p ExecMainStatus --value 2>/dev/null)
    case "$st:$rs" in
      activating:*|active:*) ok "currently running" ;;
      inactive:success)      ok "last run succeeded (exit $cd)" ;;
      failed:*|*:exit-code)  bad "last run FAILED (result=$rs exit=$cd)"
                             info "why: journalctl -u $svc -n 50"
                             journalctl -u "$svc" -n 3 --no-pager -o cat 2>/dev/null | sed 's/^/        | /' ;;
      *)                     warn "state=$st result=$rs — never run?" ;;
    esac
  fi

  # in-flight
  #
  # Match the kopia BINARY (-x kopia), not any command line containing the
  # string "kopia snapshot create" (-f). A plain `pgrep -f` also matches shells
  # and scripts that merely mention it — including this script and any wrapper
  # waiting on it — which reports a phantom snapshot in progress, and then
  # computes rate and ETA from that wrapper's runtime against a stale log.
  # Observed 2026-08-13: a leftover wait-loop shell reported as a 6h snapshot.
  local src; src=$(du -sb "$root/nas-lab" 2>/dev/null | cut -f1)
  if pgrep -x kopia 2>/dev/null | grep -q .; then
    local pid el log n raw rate eta errs
    pid=$(pgrep -x kopia | head -1)
    el=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
    ok "snapshot IN PROGRESS (pid $pid, ${el:-0}s elapsed)"
    log=$(ls -t ~/.cache/kopia/cli-logs/*snapshot-create*.log 2>/dev/null | head -1)
    if [ -n "$log" ]; then
      n=$(grep -c PutBlob "$log" 2>/dev/null)
      raw=$(grep PutBlob "$log" 2>/dev/null | sed -n 's/.*"length":\([0-9]*\).*/\1/p' \
            | awk '{s+=$1} END {print s+0}')
      info "written:  $(numfmt --to=iec "${raw:-0}")B in ${n:-0} blobs"
      if [ "${el:-0}" -gt 0 ] && [ "${raw:-0}" -gt 0 ]; then
        rate=$(( raw / el ))
        info "rate:     $(numfmt --to=iec "$rate")B/s"
        if [ -n "$src" ] && [ "$src" -gt "$raw" ] && [ "$rate" -gt 0 ]; then
          eta=$(( (src - raw) / rate ))
          info "eta:      ~$((eta/60))m left (rough; dedup and compression vary)"
        fi
      fi
      errs=$(grep -c '"error":"[^n]' "$log" 2>/dev/null)
      [ "${errs:-0}" -gt 0 ] && warn "$errs blob write error(s) this run"
      info "follow:   tail -f $log | grep PutBlob"
    fi
  else
    info "no snapshot running right now"
  fi

  [ "$QUICK" = 1 ] && { info "repository: skipped (--quick)"; return; }

  # repository
  [ -f ~/.config/kopia/env ] && { set -a; . ~/.config/kopia/env; set +a; }
  local snaps count newest nsec agemin lim pol keep nrules
  snaps=$(kopia snapshot list "$root" 2>&1 | grep -v "not actively tested")
  if echo "$snaps" | grep -qi "error\|not connected"; then
    bad "cannot query repository at $dest"
    echo "$snaps" | head -3 | sed 's/^/        | /'
  elif ! echo "$snaps" | grep -q '^  '; then
    warn "repository reachable but has NO completed snapshots yet"
  else
    count=$(echo "$snaps" | grep -c '^  ')
    ok "$count snapshot(s) retained"
    echo "$snaps" | grep '^  ' | tail -3 | sed 's/^  /        /'
    newest=$(echo "$snaps" | grep '^  ' | tail -1 | awk '{print $1" "$2}')
    nsec=$(date -d "$newest" +%s 2>/dev/null)
    if [ -n "$nsec" ]; then
      agemin=$(( ($(date +%s) - nsec) / 60 ))
      [ "$leg" = offsite ] && lim=$((30*60)) || lim=$((6*60))
      if [ "$agemin" -lt "$lim" ]; then ok "newest snapshot $((agemin/60))h$((agemin%60))m old"
      else warn "newest snapshot $((agemin/60))h$((agemin%60))m old (expected < $((lim/60))h)"; fi
    fi
  fi
  pol=$(kopia policy show "$root" 2>&1 | grep -v "not actively tested")
  keep=$(echo "$pol" | sed -n 's/^ *\(Annual\|Monthly\|Weekly\|Daily\|Hourly\|Latest\) snapshots: *\([0-9]*\).*/\1=\2/p' | tr '\n' ' ')
  [ -n "$keep" ] && info "retention: $keep"
  nrules=$(echo "$pol" | sed -n '/Ignore rules/,/Read ignore rules/p' | sed '1d;$d' | grep -c .)
  info "exclusions: ${nrules:-0} ignore rules in force"

  # storage
  [ -n "$src" ] && info "source:     $(numfmt --to=iec "$src")B at $root/nas-lab"
  if [ "$leg" = offsite ]; then
    local sz b c about
    sz=$(rclone size "$dest" --json 2>/dev/null)
    b=$(echo "$sz" | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')
    c=$(echo "$sz" | sed -n 's/.*"count":\([0-9]*\).*/\1/p')
    [ -n "$b" ] && info "repository: $(numfmt --to=iec "$b")B in ${c:-0} objects on Drive"
    about=$(rclone about gdrive: 2>/dev/null)
    [ -n "$about" ] && echo "$about" | sed -n 's/^\(Used\|Free\|Total\):[[:space:]]*/        drive \1: /p' | head -3
  else
    [ -d /mnt/f/nas-ssd-backup ] && \
      info "repository: $(du -sh /mnt/f/nas-ssd-backup 2>/dev/null | cut -f1) at /mnt/f/nas-ssd-backup"
    df -h /mnt/f 2>/dev/null | awk 'NR==2 {print "        drive free: "$4" of "$2}'
  fi
}

report_remote_leg() {
  local leg="$1" f="$STATE_DIR/$1.state"
  info "snapshotting $(leg_root "$leg") -> $(leg_dest "$leg")   (not visible from here)"
  if [ ! -f "$f" ]; then
    warn "no heartbeat at $f"
    info "that leg has not completed a run since heartbeats were added,"
    info "or it has never run. Check on that host: backup-status.sh"
    return
  fi
  local result exit_code finished_at finished_human duration host age
  # shellcheck disable=SC1090
  result=$(sed -n 's/^result=//p' "$f")
  exit_code=$(sed -n 's/^exit=//p' "$f")
  finished_at=$(sed -n 's/^finished_at=//p' "$f")
  finished_human=$(sed -n 's/^finished_human=//p' "$f")
  duration=$(sed -n 's/^duration_s=//p' "$f")
  host=$(sed -n 's/^host=//p' "$f")

  age=$(( ($(date +%s) - ${finished_at:-0}) / 60 ))
  if [ "$result" = ok ]; then ok "last run on $host succeeded (exit $exit_code)"
  else bad "last run on $host FAILED (exit $exit_code)"; fi
  info "finished:  $finished_human  (${age}m ago)"
  [ -n "$duration" ] && info "took:      ${duration}s"

  local lim; [ "$leg" = offsite ] && lim=$((30*60)) || lim=$((24*60))
  [ "$age" -gt "$lim" ] && warn "that leg has not run in $((age/60))h — expected within $((lim/60))h"
  info "full detail: run backup-status.sh on $host"
}

for leg in local offsite; do
  echo; rule; printf '\033[1m%s\033[0m\n' "$(leg_title "$leg")"; rule
  if [ "$leg" = "$HERE" ]; then report_live_leg "$leg"; else report_remote_leg "$leg"; fi
done

echo
if [ "$PROBLEMS" -eq 0 ]; then printf '\033[32mAll checks passed.\033[0m\n'
else printf '\033[31m%d problem(s) found.\033[0m\n' "$PROBLEMS"; fi
exit $(( PROBLEMS > 0 ))
