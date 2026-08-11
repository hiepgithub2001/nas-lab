# Backup services — implementation

Concrete setup for the two services designed in [README.md](README.md). Read that
first for *why*; this file is *how*.

Assumes the open decision went the recommended way — **Kopia** for service (1),
**restic + rclone** for service (2). If you pick differently, only stage 4
changes.

Everything lands in the shared checkout under `scripts/backup/`, so both hosts
read the same files.

| Stage | Host | What |
|---|---|---|
| 1 | NAS | SQLite dump script + hourly timer |
| 2 | both | Shared exclusion list |
| 3 | PC (WSL) | Kopia → `/mnt/f`, hourly |
| 4 | NAS | restic + rclone → Google Drive, daily |
| 5 | both | Failure alerts |
| 6 | both | Restore drill |

Stages 1–2 are groundwork and must land first. Until stage 1 exists, stages 3–4
would be backing up torn database files.

---

## Stage 1 — consistent SQLite dumps (NAS)

### `scripts/backup/dump-sqlite.sh`

```bash
#!/usr/bin/env bash
# Quiesce every SQLite database under appdata/ into a consistent dump set.
# Both backup services read the dumps and skip the live files entirely — a
# file-by-file copy of a live db + its -wal captures two different moments and
# may not restore.
set -euo pipefail

APPDATA=/mnt/ssd/nas-lab/appdata
DUMPS=/mnt/ssd/nas-lab/appdata-dumps

DBS=(
  radarr/radarr.db
  sonarr/sonarr.db
  prowlarr/prowlarr.db
  bazarr/db/bazarr.db
  jellyfin/data/data/jellyfin.db
  beszel/data/data.db
  beszel/data/auxiliary.db
  open-webui/webui.db
  vn-dubbing/dubbing.sqlite3
)

mkdir -p "$DUMPS"
staging="$(mktemp -d "$DUMPS/.staging.XXXXXX")"
trap 'rm -rf "$staging"' EXIT

for rel in "${DBS[@]}"; do
  src="$APPDATA/$rel"
  [ -f "$src" ] || { echo "skip (absent): $rel" >&2; continue; }

  # .dump suffix keeps these clear of the **/*.db exclusion in excludes.txt.
  out="$staging/${rel//\//__}.dump"

  # VACUUM INTO takes a read lock and writes one defragmented file. The
  # container keeps running. Opened read-write on purpose: a read-only handle
  # on a WAL database cannot create the -shm file and fails to open.
  sqlite3 "$src" "VACUUM INTO '$out'"

  sqlite3 "$out" "PRAGMA integrity_check" | grep -qx ok || {
    echo "FAILED integrity_check: $rel" >&2
    exit 1
  }
done

# Publish atomically — a backup that fires mid-run must never see a half-written
# dump set.
rm -rf "$DUMPS/.old"
[ -d "$DUMPS/current" ] && mv "$DUMPS/current" "$DUMPS/.old"
mv "$staging" "$DUMPS/current"
trap - EXIT
rm -rf "$DUMPS/.old"

# Freshness sentinel — stage 3's guard reads this mtime.
touch "$DUMPS/current/.stamp"
```

`chmod +x`, and add `appdata-dumps/` to `.gitignore`.

### Timer

`/etc/systemd/system/nas-dump-sqlite.service`:

```ini
[Unit]
Description=Quiesce appdata SQLite databases into a consistent dump set

[Service]
Type=oneshot
User=1001
ExecStart=/mnt/ssd/nas-lab/scripts/backup/dump-sqlite.sh
```

`/etc/systemd/system/nas-dump-sqlite.timer`:

```ini
[Unit]
Description=Hourly appdata SQLite dump

[Timer]
OnCalendar=*:50           # :50 so a fresh dump precedes the hourly PC pull
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nas-dump-sqlite.timer
sudo systemctl start nas-dump-sqlite.service   # prove it before trusting it
ls -la /mnt/ssd/nas-lab/appdata-dumps/current/
```

**Gate before stage 3:** with Radarr running, confirm the dump is sound —

```bash
sqlite3 /mnt/ssd/nas-lab/appdata-dumps/current/radarr__radarr.db.dump \
  "PRAGMA integrity_check; SELECT count(*) FROM Movies;"
```

`ok` plus a plausible row count means the quiesce works under live writes. If
this step is skipped, everything downstream is backing up an assumption.

---

## Stage 2 — shared exclusion list

`scripts/backup/excludes.txt`:

```
# Regenerable — Jellyfin rebuilds this on demand (13 GB)
nas-lab/appdata/jellyfin/cache/
nas-lab/appdata/jellyfin/data/transcodes/

# Re-pullable model blobs (4.9 GB)
nas-lab/appdata/ollama/

# Logs and log databases
nas-lab/appdata/*/logs/
nas-lab/appdata/*/logs.db
nas-lab/appdata/qbittorrent/qBittorrent/logs/
nas-lab/appdata/qbittorrent/qBittorrent/GeoDB/

# Runtime junk that cannot be restored meaningfully
nas-lab/appdata/qbittorrent/qBittorrent/ipc-socket
nas-lab/appdata/qbittorrent/qBittorrent/lockfile
nas-lab/appdata/beszel/socket/

# Live databases — appdata-dumps/current/*.dump is the real backup
**/*.db
**/*.db-wal
**/*.db-shm
**/*.sqlite3
**/*.sqlite3-wal
**/*.sqlite3-shm

# Backup staging
nas-lab/appdata-dumps/.staging.*
nas-lab/appdata-dumps/.old
```

Decide now whether `open-webui/` (890 MB — chat history + chroma vectors) is in
or out. It is the only judgement call left in the set; everything else is
clearly keep or clearly regenerable.

Radarr's `MediaCover/` and Jellyfin's `data/metadata/` (117 MB) are borderline —
re-fetchable, but slowly and against rate-limited APIs. The list above **keeps**
them. Add them here if you would rather re-scrape than store.

---

## Stage 3 — service (1): Kopia → `/mnt/f`, hourly (PC/WSL)

### Install

```bash
curl -fsSL https://kopia.io/signing-key | sudo gpg --dearmor -o /etc/apt/keyrings/kopia.gpg
echo "deb [signed-by=/etc/apt/keyrings/kopia.gpg] http://packages.kopia.io/apt/ stable main" \
  | sudo tee /etc/apt/sources.list.d/kopia.list
sudo apt update && sudo apt install -y kopia
```

### Repository

```bash
install -d -m 700 ~/.config/kopia
# Store the password somewhere you will still have it after the PC dies.
# A backup you cannot decrypt is not a backup.
printf 'KOPIA_PASSWORD=%s\n' 'CHANGE_ME' > ~/.config/kopia/env
chmod 600 ~/.config/kopia/env

set -a; . ~/.config/kopia/env; set +a
kopia repository create filesystem --path /mnt/f/nas-backup/kopia

# Cache on ext4, never on /mnt/f — a cache across 9p defeats its purpose.
kopia cache set --directory ~/.cache/kopia
```

### Policy

```bash
kopia policy set /mnt/nas-ssd \
  --compression zstd \
  --keep-latest 24 --keep-hourly 24 --keep-daily 14 \
  --keep-weekly 8 --keep-monthly 12

while read -r line; do
  case "$line" in ''|\#*) continue ;; esac
  kopia policy set /mnt/nas-ssd --add-ignore "$line"
done < /mnt/nas-ssd/nas-lab/scripts/backup/excludes.txt

kopia policy show /mnt/nas-ssd
```

### Guard script

`scripts/backup/guard-source.sh` — the unit refuses to run without it:

```bash
#!/usr/bin/env bash
# Refuse to snapshot if the NFS mount is missing or the dumps are stale.
# An empty snapshot plus a retention policy is how you delete a backup by
# accident, so this fails loudly rather than succeeding emptily.
set -euo pipefail

STAMP=/mnt/nas-ssd/nas-lab/appdata-dumps/current/.stamp

mountpoint -q /mnt/nas-ssd || { echo "NFS mount /mnt/nas-ssd is not mounted" >&2; exit 1; }
[ -f "$STAMP" ] || { echo "no dump set at $STAMP — is stage 1 running?" >&2; exit 1; }

age=$(( $(date +%s) - $(stat -c %Y "$STAMP") ))
[ "$age" -lt 7200 ] || { echo "dump set is ${age}s old (>2h) — stage 1 has stalled" >&2; exit 1; }
```

### Unit and timer

`/etc/systemd/system/nas-backup.service`:

```ini
[Unit]
Description=Hourly Kopia snapshot of the NAS to F:
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=lehiep
EnvironmentFile=/home/lehiep/.config/kopia/env
ExecStartPre=/mnt/nas-ssd/nas-lab/scripts/backup/guard-source.sh
ExecStart=/usr/bin/kopia snapshot create /mnt/nas-ssd
ExecStartPost=/usr/bin/kopia maintenance run --safety full
```

`/etc/systemd/system/nas-backup.timer`:

```ini
[Unit]
Description=Hourly NAS backup to F:

[Timer]
OnBootSec=2min          # "when I open WSL" — WSL boot is the trigger
OnUnitActiveSec=1h
Persistent=true         # catch up one missed run if the PC was off

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nas-backup.timer
sudo systemctl start nas-backup.service
kopia snapshot list /mnt/nas-ssd
```

> `systemctl is-system-running` currently reports **degraded** on this WSL —
> something already fails at boot. Fix that before relying on `OnBootSec`, or
> the timer inherits a boot path you do not trust.

---

## Stage 4 — service (2): restic + rclone → Google Drive, daily (NAS)

### Own OAuth client_id first

rclone's built-in client ID is shared across thousands of users and will throw
HTTP 403 rate-limit errors. Ten minutes of setup avoids a class of failure that
is miserable to diagnose later:

1. Google Cloud Console → new project.
2. Enable the **Google Drive API**.
3. OAuth consent screen → External → add your own account as a test user.
4. Credentials → OAuth client ID → **Desktop app**. Keep the ID and secret.

### Authorize headlessly

The NAS has no browser, so run this **on the PC**:

```bash
rclone authorize "drive" "<client_id>" "<client_secret>"
```

It opens a browser and prints a token blob. Paste that into `rclone config` on
the NAS when it asks for the token (choose "No" at the auto-config prompt).

```bash
# On the NAS
sudo apt install -y rclone restic
rclone config    # name the remote: gdrive
rclone lsd gdrive:   # prove it before going further
```

### Repository

```bash
install -d -m 700 ~/.config/restic
printf '%s' 'CHANGE_ME_DIFFERENT_FROM_KOPIA' > ~/.config/restic/password
chmod 600 ~/.config/restic/password

export RESTIC_REPOSITORY=rclone:gdrive:nas-backup/restic
export RESTIC_PASSWORD_FILE=~/.config/restic/password
restic init
```

Both passwords belong in a password manager **off these machines**. If the house
burns down with the only copy of the offsite passphrase in it, the offsite copy
is decoration.

### Backup script

`scripts/backup/offsite.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

export RESTIC_REPOSITORY=rclone:gdrive:nas-backup/restic
export RESTIC_PASSWORD_FILE=/root/.config/restic/password

STAMP=/mnt/ssd/nas-lab/appdata-dumps/current/.stamp
age=$(( $(date +%s) - $(stat -c %Y "$STAMP") ))
[ "$age" -lt 7200 ] || { echo "dump set stale (${age}s)" >&2; exit 1; }

restic backup /mnt/ssd \
  --exclude-file=/mnt/ssd/nas-lab/scripts/backup/excludes.txt \
  --exclude-caches \
  --tag nightly

restic forget --tag nightly \
  --keep-daily 14 --keep-weekly 8 --keep-monthly 24 \
  --prune
```

restic encrypts before anything leaves the house, so Google never sees plaintext
of `.env` or `docs/CREDENTIALS.md`. Do **not** additionally wrap this in an
rclone `crypt` remote — double encryption buys nothing and complicates restore.

### Unit and timer

`/etc/systemd/system/nas-offsite.service`:

```ini
[Unit]
Description=Daily encrypted offsite backup to Google Drive
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/mnt/ssd/nas-lab/scripts/backup/offsite.sh
```

`/etc/systemd/system/nas-offsite.timer`:

```ini
[Timer]
OnCalendar=03:30
RandomizedDelaySec=30m    # avoid hitting Drive at the same second every night
Persistent=true

[Install]
WantedBy=timers.target
```

---

## Stage 5 — failure alerts

Silent failure is the normal way backups die: the timer stops firing and nobody
notices for six months. Both services get an `OnFailure=` handler.

Add to both `.service` files:

```ini
OnFailure=backup-alert@%n.service
```

`/etc/systemd/system/backup-alert@.service` — a template that reports which unit
failed. Route it to whichever channel you will actually read; Beszel already runs
on both hosts and can watch unit state, and the Telegram plugin is already
configured on the PC.

**Also alert on silence, not just failure.** A timer that never fires never
fails. Check `systemctl list-timers nas-backup.timer` shows a sane `NEXT`, and
have Beszel alert if the last snapshot age exceeds ~3 hours.

---

## Stage 6 — verification and restore drill

| Cadence | Service (1) | Service (2) |
|---|---|---|
| Weekly | `kopia snapshot verify` | `restic check` |
| Monthly | — | `restic check --read-data-subset=5%` |
| Quarterly | full restore drill | full restore drill |

The drill, per service:

```bash
# Kopia
kopia snapshot restore <snapshot-id> /tmp/restore-test

# restic
restic restore latest --target /tmp/restore-test

# The step that actually proves stage 1 works:
sqlite3 /tmp/restore-test/.../radarr__radarr.db.dump "PRAGMA integrity_check"
```

Then the real test: stop Radarr, move its `radarr.db` aside, drop the restored
dump in its place, start it, and confirm your library and quality profiles are
intact. Write down how long it took and what you had to look up — that becomes
`RESTORE.md`, which is what you will actually be reading during an outage, at
which point you will not be in a mood to reverse-engineer this file.

A backup that has never been restored is an untested assumption with a timer
attached.
