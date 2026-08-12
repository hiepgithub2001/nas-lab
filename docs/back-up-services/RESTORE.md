# Backup services — restore

The doc you read during an outage. It assumes you are stressed, in a hurry, and
not in the mood to reverse-engineer [README.md](README.md) — so it front-loads
the commands and explains afterward.

Everything below has been **run and verified on 2026-08-13** against the real
service (1) repository, not written from memory. Where a command was verified,
its measured output is shown. Service (2)'s repository does not exist yet, so
its commands are marked **UNVERIFIED** and carry the one extra trap that leg has.

> **The single most important line in this file:** restore with `sudo`, or
> every file comes back owned by the wrong user and the containers will not
> start. See [Ownership is the trap](#ownership-is-the-trap).

## Emergency path

Lost one file, service (1) alive, you have 30 seconds:

```bash
set -a; . ~/.config/kopia/env; set +a                  # loads KOPIA_PASSWORD
kopia snapshot list /mnt/nas-ssd                       # pick a snapshot ID
sudo env HOME="$HOME" KOPIA_PASSWORD="$KOPIA_PASSWORD" \
  kopia snapshot restore <ID>/nas-lab/<path> /tmp/restore
```

Then copy out of `/tmp/restore` by hand. Restoring into `/tmp` first is
deliberate: an in-place restore can overwrite things you did not mean to touch,
and there is no undo.

## What you need before you can restore anything

| | Service (1) — local | Service (2) — offsite |
|---|---|---|
| Repository | `/mnt/f/nas-ssd-backup` | `gdrive:nas-ssd-backup` |
| Host it lives on | PC (WSL) | NAS |
| Needs `kopia` | yes | yes |
| Needs `rclone` | no | **yes** |
| Needs the repo password | yes | yes, a **different** one |
| Needs Google OAuth | no | **yes, re-doable from scratch** |

**Both passwords are unrecoverable.** They are not tied to an account, there is
no reset, and if the only copy was `~/.config/kopia/env` on the machine that
died, the repository is permanently opaque blobs. They must already be in your
password manager — see [PLAN.md](PLAN.md#secrets--why-kopia_pw-matters).

## Step 0 — connect to a repository

On a machine that is already set up, the repo is already connected and you can
skip this. On a **fresh** machine:

```bash
# Service (1) — verified
export KOPIA_PASSWORD='...'
kopia repository connect filesystem --path /mnt/f/nas-ssd-backup

# Service (2) — UNVERIFIED, and see the warning below
export KOPIA_PASSWORD='...'          # the OTHER password
kopia repository connect rclone --remote-path gdrive:nas-ssd-backup
```

> **Service (2) has a chicken-and-egg problem.** Reaching the Drive repository
> needs a working rclone remote, whose OAuth token lives in
> `~/.config/rclone/rclone.conf` on the NAS — which is **outside `/mnt/ssd` and
> therefore not in either backup**. If the NAS is gone, that file is gone. You
> must be able to redo the authorization from scratch, which needs the Google
> OAuth **client ID and secret** from
> [QUICK-START.md](QUICK-START.md#a-one-time-google-cloud-setup-manual-in-a-browser)
> step A. **Put those in the password manager next to the repo passwords** — if
> they are lost too, the offsite copy is unreachable even though it is intact.

## Step 1 — find the snapshot

```bash
kopia snapshot list /mnt/nas-ssd          # or /mnt/ssd on the NAS
kopia snapshot list /mnt/nas-ssd --all    # every retained snapshot, not just the latest per source
```

Verified output shape:

```
2026-08-13 02:05:12 +07 kd311e32f7c2642fc6e4abab6381298a9 7.8 GB drwxr-xr-x files:11008 dirs:1618
```

That long `k…` string is the **root ID**, and it is what every restore command
takes. A snapshot with an `errors:N` field restored fewer files than it walked —
prefer one without.

To see what was in a snapshot *before* committing to a restore, mount it
read-only. This is the fast way to answer "was this file still there on
Tuesday" without restoring anything (verified working in WSL — `/dev/fuse` is
present):

```bash
mkdir -p /tmp/browse
kopia mount <ID> /tmp/browse &
ls /tmp/browse/nas-lab            # look around with ordinary tools
fusermount -u /tmp/browse         # always unmount when done
```

### Or use the web UI

Kopia ships a browser UI that does all of this — snapshot history, browsing,
and restore — without memorising flags. Useful when you are hunting for the
right snapshot rather than executing a known recovery.

```bash
kopia server start --ui --insecure --address http://127.0.0.1:51515 \
  --server-username=admin --server-password='<pick one>'
```

Then open `http://127.0.0.1:51515` — from Windows too, since WSL forwards
localhost. Verified: returns HTTP 200. `--insecure` is required; without it the
server refuses to start with `TLS not configured. To start server without
encryption pass --insecure`. Bound to `127.0.0.1`, so it is not reachable from
the network.

The UI is also the only way to watch a snapshot's **live progress**. Kopia
suppresses its progress line when stdout is not a terminal, so a run started by
systemd logs only "Snapshotting…" and "Created snapshot" — no percentage. Run
`snapshot-local.sh` by hand, or watch the UI.

## Step 2 — restore

**The source argument is `<rootID>` optionally followed by a path inside the
snapshot.** There is no `--path` flag; passing one fails with
`kopia: error: unknown long flag '--path'`.

```bash
# one file
kopia snapshot restore <ID>/nas-lab/docker-compose.yml /tmp/restore/one.yml

# one directory
kopia snapshot restore <ID>/nas-lab/appdata/radarr /tmp/restore/radarr

# the entire snapshot
kopia snapshot restore <ID> /tmp/restore
```

Verified: restoring `<ID>/nas-lab/appdata-dumps/current` produced
`Restored 11 files, 1 directories and 0 symbolic links (55.4 MB)` in **0.9 s**.

### Ownership is the trap

Kopia stores ownership inside the repository — that is a large part of why it
was chosen over `rsync` onto NTFS. But it can only *apply* that ownership if the
restore runs as root. Measured, restoring the same file two ways:

| Restore run as | Resulting owner | Correct? |
|---|---|---|
| `lehiep` (uid 1000) | `lehiep:lehiep` | ✗ silently wrong |
| `root` via `sudo` | `1001:1001` | ✓ matches source |

There is **no warning** in the unprivileged case. It looks like a clean restore.

This matters because the whole stack runs as `PUID=1001`. Restore `appdata/`
as uid 1000 and every container loses access to its own state — Radarr comes up
empty, qBittorrent forgets every torrent. So:

```bash
sudo env HOME="$HOME" KOPIA_PASSWORD="$KOPIA_PASSWORD" \
  kopia snapshot restore <ID>/nas-lab/appdata/radarr /tmp/restore/radarr
```

`env HOME="$HOME"` is required — plain `sudo` sets `HOME=/root`, where Kopia
finds no repository config and fails to connect.

Verify before trusting it:

```bash
stat -c '%n %u:%g %a' /tmp/restore/radarr/radarr.db      # want 1001:1001
```

## Step 3 — restore a SQLite-backed app

Radarr, Sonarr, Prowlarr, Bazarr, Jellyfin, Beszel, open-webui, vn-dubbing.

**Restore the dump, not the live `.db`.** Both are in the snapshot, and picking
the wrong one is the most likely way to turn a recoverable outage into data
loss. The live `radarr.db` was copied while Radarr was writing to it and may be
torn against its `-wal`; `appdata-dumps/current/*.dump` was produced by
`VACUUM INTO` under a read lock and is internally consistent. See
[README.md](README.md#the-real-risk-is-live-sqlite-not-the-tool).

```bash
# 1. get the dump set
sudo env HOME="$HOME" KOPIA_PASSWORD="$KOPIA_PASSWORD" \
  kopia snapshot restore <ID>/nas-lab/appdata-dumps/current /tmp/restore/dumps

# 2. prove it before stopping anything — must print exactly: ok
sqlite3 /tmp/restore/dumps/radarr__radarr.db.dump \
  "PRAGMA integrity_check; SELECT count(*) FROM Movies;"
```

Verified: `ok`, and `134` movies — matching the live database at snapshot time.

```bash
# 3. put it back (on the NAS)
docker compose stop radarr
cp /tmp/restore/dumps/radarr__radarr.db.dump \
   /mnt/ssd/nas-lab/appdata/radarr/radarr.db
rm -f /mnt/ssd/nas-lab/appdata/radarr/radarr.db-wal \
      /mnt/ssd/nas-lab/appdata/radarr/radarr.db-shm
chown 1001:1001 /mnt/ssd/nas-lab/appdata/radarr/radarr.db
docker compose start radarr
```

**Deleting `-wal`/`-shm` is not optional.** A stale WAL next to a restored
database makes SQLite try to replay writes belonging to a different generation
of that file.

Dump filenames are `<app>__<path with / replaced by __>.dump`. The full list is
the `DBS` array at the top of `scripts/backup/dump-sqlite.sh`:

```
radarr__radarr.db.dump              prowlarr__prowlarr.db.dump
sonarr__sonarr.db.dump              bazarr__db__bazarr.db.dump
jellyfin__data__data__jellyfin.db.dump
beszel__data__data.db.dump          beszel__data__auxiliary.db.dump
open-webui__webui.db.dump           open-webui__vector_db__chroma.sqlite3.dump
vn-dubbing__dubbing.sqlite3.dump
```

Confirm the app comes up clean — library present, quality profiles intact —
before believing the restore.

## Disaster scenarios

### The NAS died, the PC is fine

The common case, and the one service (1) exists for. The PC's repository holds
everything, is local, and needs no network.

1. Rebuild the NAS host and its Docker stack (a runbook problem, not a backup
   problem — the OS is deliberately [out of
   scope](README.md#explicitly-out-of-scope)).
2. Restore the whole tree as root into a staging directory, then move
   `nas-lab/` into place on the new `/mnt/ssd`.
3. Replace each live `*.db` with its dump, per [step 3](#step-3--restore-a-sqlite-backed-app).
4. Beszel's `id_ed25519` is **not** in the backup — it is 0600 root:root and
   unreadable by any user the backup can run as. Let Beszel generate a new
   keypair and re-register the agents with the new public key.

### The PC died, the NAS is fine

Nothing to restore — the NAS *is* the source of truth. Reinstall WSL, then
follow [QUICK-START.md](QUICK-START.md#set-up-and-kick-start--service-1) to
rebuild service (1). The old repository on `F:` is still readable with the old
password if you want its history; connect to it rather than recreating it.

### Both died — fire, theft, ransomware

This is the only scenario service (2) exists for, and the only one where the
Drive copy is the sole survivor. You will need, from the password manager and
nowhere else: the **service (2) repo password**, and the Google **OAuth client
ID + secret**. With those, on any machine: install kopia and rclone, redo
`rclone authorize`, `kopia repository connect rclone`, restore.

**Until service (2) is actually running, this scenario is unprotected.** Both
current copies live in the same building.

## Verify without restoring

| Cadence | Command |
|---|---|
| Weekly | `kopia snapshot verify` — metadata + structure |
| Monthly | `kopia snapshot verify --verify-files-percent=5` — reads 5% of real blob data |
| Quarterly | a real restore drill, below |

Verified on the local repo: `Finished processing 11561 objects (2.2 GB)`, no
errors.

## The drill

A backup that has never been restored is an untested assumption with a timer
attached. Quarterly, do the whole thing end to end against **each** repository:
restore the dump set, `PRAGMA integrity_check`, stop Radarr, swap the database
in, start it, confirm the library is intact.

Then update the log below. If a command in this file turned out to be wrong,
fix it here first — that is the entire point of the drill.

### Drill log

| Date | Repo | Scope | Result |
|---|---|---|---|
| 2026-08-13 | service (1) | dump set + single file, restore only | ✅ 11 files / 55.4 MB in 0.9 s; restored `radarr.db` dump `integrity_check` = `ok`, 134 movies. Ownership confirmed 1001:1001 under `sudo`, wrong without it. **Not** yet swapped into a live Radarr. |
| | service (2) | | not yet — repository does not exist |
