#!/usr/bin/env bash
# One-screen health report covering EVERY backup leg.
#
# Runs on either host. A leg you are standing on is reported live — timers, last
# exit status, in-flight progress, repository contents. Every other leg is
# reported from the heartbeat it left in the shared NFS directory
# (nas-lab/.backup-state/), since its repository and its systemd units are not
# visible from here.
#
# Answers, per leg:
#   1. Is the dump set fresh?  (the /mnt/ssd guards refuse to snapshot without it)
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
banner(){ echo; rule; printf '\033[1m%s\033[0m\n' "$*"; rule; }

# --- which host are we standing on -------------------------------------------
if mountpoint -q /mnt/nas-ssd 2>/dev/null; then
  HERE=local;   ROOT=/mnt/nas-ssd
elif [ -d /mnt/ssd/nas-lab ]; then
  HERE=offsite; ROOT=/mnt/ssd
else
  echo "Cannot tell which host this is: no /mnt/nas-ssd mount, no /mnt/ssd/nas-lab." >&2
  exit 2
fi
STATE_DIR="$ROOT/nas-lab/.backup-state"

# --- the leg table -----------------------------------------------------------
# Every leg is described here and nowhere else. Before this table there were
# three reporting paths — five `[ "$1" = local ]` accessor functions for the two
# /mnt/ssd legs, and a separate hardcoded reporter for the three /mnt/hdd ones,
# with the heartbeat file parsed by two near-identical copies. Adding a leg
# meant editing all of it; now it means adding a row.
#
# There are exactly two services, and every leg belongs to one of them — which
# is what their names already say. (1b) is service (1) applied to the second
# disk; (2a) and (2b) are service (2) applied to it. So the report has two
# sections and each lists its own legs, rather than a third section collecting
# the /mnt/hdd ones away from the service they belong to.
#
# Fields:
#   key      basename of the heartbeat file the leg writes (lib-state.sh)
#   svc      which service it belongs to: 1 or 2
#   style    how to report it when standing on its host:
#              full = timers, in-flight progress, repository contents
#              beat = heartbeat, in-flight and timer only
#            The /mnt/hdd legs are `beat` on purpose: (2a)/(2b) live on Drive,
#            and querying them costs an rclone bridge per call. Promoting one
#            later is a one-word edit here.
#   host     which host actually runs it — decides live vs heartbeat reporting
#   timer    systemd timer unit; the service is the same name with .service
#   fresh_h  full style: how old the newest snapshot may be
#   beat_h   beat style: how long since the last successful run
#   label    what this leg protects, printed above its detail
declare -A SVC_TITLE=(
  [1]="SERVICE (1) — local, NAS -> PC /mnt/f, twice per WSL boot"
  [2]="SERVICE (2) — offsite, NAS -> Google Drive, daily"
)
declare -A L_SVC L_STYLE L_HOST L_TIMER L_SRC L_DEST L_FRESH L_BEAT L_LABEL L_HINT
LEG_ORDER=()
while IFS='|' read -r key svc style host timer src dest fresh beat label hint; do
  case "$key" in ''|\#*) continue ;; esac
  LEG_ORDER+=("$key")
  L_SVC[$key]=$svc;     L_STYLE[$key]=$style;  L_HOST[$key]=$host
  L_TIMER[$key]=$timer; L_SRC[$key]=$src;      L_DEST[$key]=$dest
  L_FRESH[$key]=$fresh; L_BEAT[$key]=$beat
  L_LABEL[$key]=$label; L_HINT[$key]=$hint
done <<'LEGS'
local|1|full|local|nas-backup.timer|/mnt/nas-ssd|/mnt/f/nas-ssd-backup|6|24|(1)  /mnt/ssd — configuration and databases|
local-cloud|1|beat|local|nas-backup-cloud.timer|/mnt/nas-hdd/cloud|/mnt/f/cloud-bk|26|26|(1b) /mnt/hdd/cloud — photos and files|needs the /mnt/hdd NFS export and the PC-side mount; see README, "Service (1b) — setting it up"
offsite|2|full|offsite|nas-offsite.timer|/mnt/ssd|gdrive:nas-ssd-backup|30|30|(2)  /mnt/ssd — configuration and databases, 03:30|
offsite-cloud|2|beat|offsite|nas-offsite-cloud.timer|/mnt/hdd/cloud|gdrive:hdd-cloud-bk|26|26|(2a) /mnt/hdd/cloud — photos and files, 04:30|
offsite-film|2|beat|offsite|nas-offsite-film.timer|/mnt/hdd/film-data|gdrive:film-data-bk|26|26|(2b) /mnt/hdd/film-data — media, 05:30, only after (2a)|
LEGS

printf '\033[1mBackup status\033[0m — %s, %s\n' "$(hostname)" "$(date '+%Y-%m-%d %H:%M:%S %Z')"
printf 'standing on the \033[1m%s\033[0m leg; the others are read from %s\n' "$HERE" "${STATE_DIR/#$ROOT/\$ROOT}"

# --- shared prerequisite ------------------------------------------------------
# One checker for both dump sets. They differ only in where they live, what
# their files are called, and which timer writes them — everything else, the 2h
# guard included, is the same rule from guard-source.sh.
check_dumps() {
  local title="$1" stamp="$2" glob="$3" timer="$4" age human n t
  head_ "$title"
  if [ ! -f "$stamp" ]; then
    bad "no dump set at $stamp — $timer has never run"
  else
    age=$(( $(date +%s) - $(stat -c %Y "$stamp") ))
    human="$((age/60))m"; [ "$age" -ge 3600 ] && human="$((age/3600))h$(((age%3600)/60))m"
    n=$(ls -1 "$(dirname "$stamp")"/$glob 2>/dev/null | wc -l)
    if [ "$age" -lt 7200 ]; then ok "$n dumps, $human old (guard allows <2h)"
    else bad "dump set is $human old (>2h) — both guards will refuse to snapshot"; fi
  fi
  # The dump timers live on the NAS, so this only means anything from there.
  if [ "$HERE" = offsite ]; then
    t=$(systemctl is-enabled "$timer" 2>&1)
    [ "$t" = enabled ] && ok "$timer enabled" || bad "$timer is $t"
  fi
}

check_dumps "SQLite dumps — shared prerequisite, both legs need this fresh" \
  "$ROOT/nas-lab/appdata-dumps/current/.stamp" '*.dump' nas-dump-sqlite.timer

# Postgres: same rule, separate set. Checked only once the directory exists, so
# a host without the cloud stack still reports clean. Without it the script
# reported "All checks passed" while dump-postgres could be dead and Immich's
# database silently unprotected — the live cluster is excluded from the
# snapshot, so a stale dump set means no database backup at all.
if [ -d "$ROOT/nas-lab/appdata-dumps/postgres" ]; then
  check_dumps "Postgres dumps — Immich and Nextcloud, live clusters are excluded" \
    "$ROOT/nas-lab/appdata-dumps/postgres/current/.stamp" '*.sql.gz' nas-dump-postgres.timer
fi

# --- heartbeat ----------------------------------------------------------------
# Single parser for the file lib-state.sh writes. Sets B_* and returns 1 when
# the leg has never completed a run.
read_beat() {
  local f="$1"
  # Cleared first: these are globals reused for every leg, and a leg with no
  # heartbeat would otherwise report the previous leg's host as its own.
  B_RESULT=; B_EXIT=; B_AT=; B_HUMAN=; B_DURATION=; B_HOST=; B_AGE=0
  [ -f "$f" ] || return 1
  B_RESULT=$(sed -n 's/^result=//p' "$f")
  B_EXIT=$(sed -n 's/^exit=//p' "$f")
  B_AT=$(sed -n 's/^finished_at=//p' "$f")
  B_HUMAN=$(sed -n 's/^finished_human=//p' "$f")
  B_DURATION=$(sed -n 's/^duration_s=//p' "$f")
  B_HOST=$(sed -n 's/^host=//p' "$f")
  B_AGE=$(( ($(date +%s) - ${B_AT:-0}) / 60 ))
  return 0
}

check_timer_enabled() {
  local t; t=$(systemctl is-enabled "$1" 2>&1)
  [ "$t" = enabled ] && ok "$1 enabled" || bad "$1 is $t"
}

# When does this timer fire next?
#
# Not parsed out of `systemctl list-timers` by column number, which is what this
# did before: that table's columns are variable-width and the timezone is its
# own field on the NAS but part of the timestamp on the PC, so the old awk
# printed "next run: Sun 2026-08-16 03:58:46  (in UTC)" there and
# "next run: - - Sat  (in 2026-08-15)" here. `systemctl show` gives the values
# directly instead.
next_run_line() {
  local tmr="$1" rt mono btime when
  rt=$(systemctl show "$tmr" -p NextElapseUSecRealtime --value 2>/dev/null)
  if [ -n "$rt" ]; then info "next run: $rt"; return; fi

  # Boot-relative timers (OnBootSec) have no realtime elapse — systemd reports
  # microseconds since boot, and "infinity" once every firing for this boot is
  # behind us. That is the normal steady state for both of the PC's legs.
  mono=$(systemctl show "$tmr" -p NextElapseUSecMonotonic --value 2>/dev/null)
  if [ -z "$mono" ] || [ "$mono" = infinity ]; then
    info "next run: none left this boot — fires again after the next WSL start"
    return
  fi
  btime=$(awk '/^btime/{print $2}' /proc/stat 2>/dev/null)
  if [ -n "$btime" ]; then
    when=$(( btime + mono / 1000000 ))
    info "next run: $(date -d "@$when" '+%a %Y-%m-%d %H:%M:%S %Z')"
  fi
}

# Is a snapshot of THIS leg's source running right now?
#
# Two false positives to avoid here, and they pull in opposite directions:
#
#   pgrep -f 'kopia snapshot create'  also matches shells and wrappers that
#     merely mention the string — including this script — and then computes
#     rate and ETA from the wrapper's runtime. Observed 2026-08-13: a leftover
#     wait-loop shell reported as a 6h snapshot.
#   pgrep -x kopia                    matches the binary, but ANY subcommand of
#     it: `kopia mount`, `kopia server start`, `kopia snapshot list`. Observed
#     2026-08-13: a leftover `kopia mount` from a restore drill reported as
#     "snapshot IN PROGRESS ... 40KB/s, eta ~3565m", with the numbers scraped
#     from an already-finished run's log.
#
# So: match the binary with -x, then read /proc to confirm this process is a
# `snapshot create` of this leg's source specifically. Matching only on
# "snapshot create" was a third false positive, and a more misleading one, since
# the numbers were real: on 2026-08-15 the NAS reported service (2) as
# "IN PROGRESS, 133GB written" against an 8.4 GB source, because leg (2b) was
# midway through film-data on the same host.
#
# $2 is the source size in bytes for an ETA, or empty to skip it — the /mnt/hdd
# legs are hundreds of GB and not worth a `du` on every status call.
# Returns 1 when nothing is running.
snapshot_pid() {
  local root="$1" _p cmd
  for _p in $(pgrep -x kopia 2>/dev/null); do
    cmd=$(tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null)
    case "$cmd" in *"snapshot create $root "*) echo "$_p"; return 0 ;; esac
  done
  return 1
}

report_inflight() {
  local root="$1" src="${2:-}" pid
  pid=$(snapshot_pid "$root") || return 1

  local el log n raw rate eta errs
  el=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
  ok "snapshot IN PROGRESS (pid $pid, ${el:-0}s elapsed)"
  # Prefer the log belonging to THIS pid — kopia embeds it in the filename
  # (kopia-<date>-<pid>-snapshot-create.0.log) — so the byte counts can never be
  # read from a run that already finished. Fall back to newest if absent.
  log=$(ls -t ~/.cache/kopia/cli-logs/*-"$pid"-snapshot-create*.log 2>/dev/null | head -1)
  [ -n "$log" ] || log=$(ls -t ~/.cache/kopia/cli-logs/*snapshot-create*.log 2>/dev/null | head -1)
  [ -n "$log" ] || return 0

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
  return 0
}

# --- report a leg from its heartbeat -----------------------------------------
report_beat() {
  local key="$1" mine="$2" lim
  if read_beat "$STATE_DIR/$key.state"; then
    if [ "$B_RESULT" = ok ]; then ok "last run on $B_HOST succeeded (exit $B_EXIT)"
    else bad "last run on $B_HOST FAILED (exit $B_EXIT)"; fi
    info "finished:  $B_HUMAN  (${B_AGE}m ago)"
    [ -n "$B_DURATION" ] && info "took:      ${B_DURATION}s"

    lim=$(( ${L_BEAT[$key]} * 60 ))
    [ "$B_AGE" -gt "$lim" ] && \
      warn "no successful run in $((B_AGE/60))h — expected within ${L_BEAT[$key]}h"
  else
    warn "$key has never completed a run"
    # The hint tells you how to set the leg up, so it is wrong to print it while
    # the very first run is under way — that setup has evidently just been done.
    if [ "$mine" = 1 ] && snapshot_pid "${L_SRC[$key]}" >/dev/null; then
      info "its first run is under way now — this clears when that run finishes"
    else
      [ -n "${L_HINT[$key]}" ] && info "${L_HINT[$key]}"
      [ "$mine" = 1 ] || info "or it ran before heartbeats existed — check that host with backup-status.sh"
    fi
  fi

  # Deliberately also checked when there is no heartbeat: a leg's very first run
  # is exactly the case where one is in flight and nothing has been recorded
  # yet. Only meaningful for legs whose process would be on this host.
  [ "$mine" = 1 ] && report_inflight "${L_SRC[$key]}"

  # A timer check is only meaningful for units that exist on this host.
  if [ "$mine" = 1 ]; then check_timer_enabled "${L_TIMER[$key]}"
  elif [ -n "$B_HOST" ]; then info "full detail: run backup-status.sh on $B_HOST"; fi
}

# --- report a leg we are standing on, in full --------------------------------
report_live() {
  local key="$1" root dest svc tmr
  root="${L_SRC[$key]}"; dest="${L_DEST[$key]}"
  tmr="${L_TIMER[$key]}"; svc="${tmr%.timer}.service"

  # timer
  if [ "$(systemctl is-enabled "$tmr" 2>&1)" = "not-found" ]; then
    bad "$tmr not installed — nothing runs on a schedule"
    info "install: sudo cp $ROOT/nas-lab/scripts/backup/systemd/${tmr%.timer}.{service,timer} /etc/systemd/system/"
  else
    local en ac
    en=$(systemctl is-enabled "$tmr" 2>/dev/null); ac=$(systemctl is-active "$tmr" 2>/dev/null)
    [ "$en" = enabled ] && [ "$ac" = active ] && ok "$tmr enabled, active" \
      || warn "$tmr enabled=$en active=$ac"
    next_run_line "$tmr"
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
  # The `du` is the expensive part and it is what --quick exists to avoid: it
  # walks the source over NFS, and during a large run it competes with that run
  # for the link. Observed 2026-08-15: `--quick` blocked past 120s here while
  # leg (1b) pulled its first 34 GB. Skipped under --quick, which only costs the
  # ETA — report_inflight still reports the run, its rate and its byte count.
  local src=""
  [ "$QUICK" = 1 ] || src=$(du -sb "$root/nas-lab" 2>/dev/null | cut -f1)
  report_inflight "$root" "$src" || info "no snapshot running right now"

  [ "$QUICK" = 1 ] && { info "repository: skipped (--quick)"; return; }

  # repository
  [ -f ~/.config/kopia/env ] && { set -a; . ~/.config/kopia/env; set +a; }
  local snaps rc count newest nsec agemin lim pol keep nrules bad_snaps
  # Take kopia's exit status, not a grep for "error" in its output. Every
  # snapshot that recorded unreadable files is listed with an `errors:N` field,
  # so matching on the word made a healthy repository holding one bad snapshot
  # report as unreachable — which is how services (1) and (2) read FAIL on
  # 2026-08-15 while the repositories were fine.
  snaps=$(kopia snapshot list "$root" 2>&1); rc=$?
  snaps=$(echo "$snaps" | grep -v "not actively tested")
  if [ "$rc" -ne 0 ] || echo "$snaps" | grep -qi "not connected"; then
    bad "cannot query repository at $dest"
    echo "$snaps" | head -3 | sed 's/^/        | /'
  elif ! echo "$snaps" | grep -q '^  '; then
    warn "repository reachable but has NO completed snapshots yet"
  else
    count=$(echo "$snaps" | grep -c '^  ')
    ok "$count snapshot(s) retained"
    # Reported separately, because it is a real finding and no longer hidden
    # behind the false "unreachable" above: these snapshots are usable, but
    # they are missing whatever could not be read at the time.
    bad_snaps=$(echo "$snaps" | grep -c 'errors:[1-9]')
    [ "${bad_snaps:-0}" -gt 0 ] && \
      warn "$bad_snaps of them recorded unreadable files (errors:N below) — they age out with retention"
    echo "$snaps" | grep '^  ' | tail -3 | sed 's/^  /        /'
    newest=$(echo "$snaps" | grep '^  ' | tail -1 | awk '{print $1" "$2}')
    nsec=$(date -d "$newest" +%s 2>/dev/null)
    if [ -n "$nsec" ]; then
      agemin=$(( ($(date +%s) - nsec) / 60 ))
      lim=$(( ${L_FRESH[$key]} * 60 ))
      if [ "$agemin" -lt "$lim" ]; then ok "newest snapshot $((agemin/60))h$((agemin%60))m old"
      else warn "newest snapshot $((agemin/60))h$((agemin%60))m old (expected < ${L_FRESH[$key]}h)"; fi
    fi
  fi
  pol=$(kopia policy show "$root" 2>&1 | grep -v "not actively tested")
  keep=$(echo "$pol" | sed -n 's/^ *\(Annual\|Monthly\|Weekly\|Daily\|Hourly\|Latest\) snapshots: *\([0-9]*\).*/\1=\2/p' | tr '\n' ' ')
  [ -n "$keep" ] && info "retention: $keep"
  nrules=$(echo "$pol" | sed -n '/Ignore rules/,/Read ignore rules/p' | sed '1d;$d' | grep -c .)
  info "exclusions: ${nrules:-0} ignore rules in force"

  # storage. A destination with a remote prefix (gdrive:) is Drive; anything
  # else is a path on this machine.
  [ -n "$src" ] && info "source:     $(numfmt --to=iec "$src")B at $root/nas-lab"
  case "$dest" in
    *:*)
      local sz b c about
      sz=$(rclone size "$dest" --json 2>/dev/null)
      b=$(echo "$sz" | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')
      c=$(echo "$sz" | sed -n 's/.*"count":\([0-9]*\).*/\1/p')
      [ -n "$b" ] && info "repository: $(numfmt --to=iec "$b")B in ${c:-0} objects on Drive"
      about=$(rclone about "${dest%%:*}:" 2>/dev/null)
      [ -n "$about" ] && echo "$about" | sed -n 's/^\(Used\|Free\|Total\):[[:space:]]*/        drive \1: /p' | head -3
      ;;
    *)
      [ -d "$dest" ] && info "repository: $(du -sh "$dest" 2>/dev/null | cut -f1) at $dest"
      df -h "$(dirname "$dest")" 2>/dev/null | awk 'NR==2 {print "        drive free: "$4" of "$2}'
      ;;
  esac
}

# --- dispatch ----------------------------------------------------------------
report_leg() {
  local key="$1" mine=0
  [ "$HERE" = "${L_HOST[$key]}" ] && mine=1
  if [ "$mine" = 1 ] && [ "${L_STYLE[$key]}" = full ]; then
    info "${L_SRC[$key]} -> ${L_DEST[$key]}   (live, this host)"
    report_live "$key"
  else
    [ "$mine" = 1 ] \
      && info "${L_SRC[$key]} -> ${L_DEST[$key]}   (this host, heartbeat only)" \
      || info "${L_SRC[$key]} -> ${L_DEST[$key]}   (not visible from here)"
    report_beat "$key" "$mine"
  fi
}

# Two sections, one per service, each listing its own legs in table order. A
# service is only as good as its weakest leg — service (2) is not "fine"
# because /mnt/ssd reached Drive if the photos did not — so they are reported
# together, under one heading, rather than split by which disk they read.
for svc in 1 2; do
  banner "${SVC_TITLE[$svc]}"
  first=1
  for key in "${LEG_ORDER[@]}"; do
    [ "${L_SVC[$key]}" = "$svc" ] || continue
    [ "$first" = 1 ] || echo
    first=0
    printf '        \033[1m%s\033[0m\n' "${L_LABEL[$key]}"
    report_leg "$key"
  done
done

echo
if [ "$PROBLEMS" -eq 0 ]; then printf '\033[32mAll checks passed.\033[0m\n'
else printf '\033[31m%d problem(s) found.\033[0m\n' "$PROBLEMS"; fi
exit $(( PROBLEMS > 0 ))
