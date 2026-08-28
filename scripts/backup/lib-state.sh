#!/usr/bin/env bash
# Shared heartbeat: each leg records the outcome of its own run into a file on
# the NAS, so either host can report on BOTH legs.
#
# Why this exists: service (1)'s repository lives on the PC's /mnt/f and its
# timers live in the PC's systemd — none of which the NAS can see. The NFS
# export (/mnt/ssd, rw, all_squash,anonuid=1001) is the one piece of storage
# both hosts share, so it is where each leg leaves a note about how it went.
#
# Source this, then call record_state at exit:
#   . "$SCRIPT_DIR/lib-state.sh"
#   trap 'record_state $?' EXIT
#
# Deliberately best-effort: a failure to write the heartbeat must never fail
# the backup itself, so every write is `|| true`.

# STATE_ROOT is the snapshot root of whichever host we are on; the state
# directory always resolves to the same physical place on the NAS.
state_dir() { echo "${1%/}/nas-lab/.backup-state"; }

record_state() {
  local exit_code="${1:-0}" leg="${2:-$LEG_NAME}" root="${3:-$ROOT}"
  local dir; dir="$(state_dir "$root")"
  local started="${STATE_STARTED:-}" now; now="$(date +%s)"
  local result

  # Three outcomes, not two. A leg that deliberately stood down — the priority
  # gates in (2b), or its destination cap — exits 0 because nothing is broken,
  # but recording that as "ok" would make backup-status.sh report a leg that has
  # silently stopped backing up as healthy. `skipped` is how a leg says "I chose
  # not to run", and STATE_REASON carries the why straight into the report.
  #
  # Only honoured on a clean exit: a script that crashed cannot label itself
  # skipped, so a failure can never be dressed up as a decision.
  result="$([ "$exit_code" -eq 0 ] && echo ok || echo fail)"
  [ "$exit_code" -eq 0 ] && [ -n "${STATE_RESULT:-}" ] && result="$STATE_RESULT"

  mkdir -p "$dir" 2>/dev/null || return 0

  {
    echo "leg=$leg"
    echo "host=$(hostname)"
    echo "finished_at=$now"
    echo "finished_human=$(date -d "@$now" '+%Y-%m-%d %H:%M:%S %Z')"
    echo "exit=$exit_code"
    echo "result=$result"
    [ -n "${STATE_REASON:-}" ] && echo "reason=$STATE_REASON"
    [ -n "$started" ] && echo "duration_s=$((now - started))"
  } > "$dir/$leg.state.tmp" 2>/dev/null || return 0

  mv "$dir/$leg.state.tmp" "$dir/$leg.state" 2>/dev/null || true
}
