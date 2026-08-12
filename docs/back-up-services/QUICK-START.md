# Backup services — quick start

Everything needed to actually run this: how to **set up and kick-start** each
service the first time, and how to **operate** them day to day afterward —
checking status, manual snapshots, browsing history, restoring, alerting,
verifying. Read [README.md](README.md) for *why* the system is shaped this way,
and [PLAN.md](PLAN.md) for the architecture diagrams — this file is the
complete *how*, trading explanation for something you can paste in one go.

Two independent Kopia repositories exist. Which commands apply to which:

| | Service (1) — local | Service (2) — offsite |
|---|---|---|
| Host | PC (WSL) | NAS |
| Repo | `/mnt/f/nas-ssd-backup` | Google Drive, via rclone |
| Config | `~/.config/kopia/` (default) on the PC | `~/.config/kopia/` (default) on the NAS |
| Source snapshotted | `/mnt/nas-ssd` (NFS) | `/mnt/ssd` (local) |

Every command below is plain `kopia` — no `--config-file` flag, because each
host has exactly one repository at Kopia's default config location.

**Both services depend on stage 1 (the NAS-side SQLite dump timer) being
installed first.** If you haven't run this yet, do it before either kick-start
below — both guard scripts refuse to snapshot without a fresh dump:

```bash
# On the NAS
sudo cp /mnt/ssd/nas-lab/scripts/backup/systemd/nas-dump-sqlite.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nas-dump-sqlite.timer
sudo systemctl start nas-dump-sqlite.service
ls -la /mnt/ssd/nas-lab/appdata-dumps/current/   # should show one .dump per app + .stamp
```

**Gate before either kick-start below:** with Radarr running, confirm a dump is
actually sound, not just present —

```bash
sqlite3 /mnt/ssd/nas-lab/appdata-dumps/current/radarr__radarr.db.dump \
  "PRAGMA integrity_check; SELECT count(*) FROM Movies;"
```

`ok` plus a plausible row count means the quiesce works under live writes. If
this is skipped, both services end up backing up an unverified assumption.

**Both legs back up the same full data set.** Service (1) writes to a 930 GB
local drive; service (2)'s Drive destination has 5 TB. Neither has a size
ceiling worth trimming against, so `scripts/backup/excludes.txt` — one file,
shared by both — holds only what's physically impossible to back up (unreadable
files, sockets, this system's own transient state), not size or value
judgements.

`scripts/backup/apply-policy.sh local\|offsite` applies it and verifies the
result — it's step 4 of each kick-start below. See [Changing what's backed
up](#changing-whats-backed-up) before editing it.

---

## Set up and kick-start — Service (1)

Run on the **PC**, inside WSL. Edit the password line, then paste the rest as
one block.

```bash
# 1. edit this line first
KOPIA_PW='CHANGE_ME_TO_A_REAL_PASSWORD'

# 2. install kopia
curl -fsSL https://kopia.io/signing-key | sudo gpg --dearmor -o /etc/apt/keyrings/kopia.gpg
echo "deb [signed-by=/etc/apt/keyrings/kopia.gpg] http://packages.kopia.io/apt/ stable main" \
  | sudo tee /etc/apt/sources.list.d/kopia.list
sudo apt update && sudo apt install -y kopia

# 3. repo + cache
install -d -m 700 ~/.config/kopia
printf 'KOPIA_PASSWORD=%s\n' "$KOPIA_PW" > ~/.config/kopia/env
chmod 600 ~/.config/kopia/env
set -a; . ~/.config/kopia/env; set +a
kopia repository create filesystem --path /mnt/f/nas-ssd-backup
kopia cache set --cache-directory ~/.cache/kopia

# 4. retention + exclusions (sets both, then verifies they landed)
/mnt/nas-ssd/nas-lab/scripts/backup/apply-policy.sh local

# 5. install + enable the timer
sudo cp /mnt/nas-ssd/nas-lab/scripts/backup/systemd/nas-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nas-backup.timer

# 6. run it now and check
sudo systemctl start nas-backup.service
kopia snapshot list /mnt/nas-ssd
```

What it does: installs Kopia → creates an encrypted repo at
`/mnt/f/nas-ssd-backup` with its own password (not your Linux login) → pins the
metadata cache on WSL's own ext4, never on the 9p drive → attaches retention and
the shared exclusion list to `/mnt/nas-ssd` so future runs need no flags →
installs the timer that fires at boot+2min and boot+62min, then goes quiet until
next boot → runs one snapshot immediately instead of waiting.

**Password:** the last line of step 3 fails loudly if `/mnt/f` isn't mounted —
confirm with `ls /mnt/f` first if unsure. Put `KOPIA_PW` in a password manager
immediately after; a repository password that only exists on the PC that just
died is the same as not having a backup.

Expect step 6 to print one snapshot with today's date. If it errors, check
`journalctl -u nas-backup.service -n 50` — the usual cause is the NFS mount
being down or the NAS-side dump being stale (see the stage-1 block above).

> `systemctl is-system-running` may report **degraded** on this WSL —
> something already fails at boot, unrelated to this timer. Worth diagnosing
> before you rely on `OnBootSec` (`systemctl --failed` shows what), though it
> shouldn't block installing the unit.

---

## Set up and kick-start — Service (2)

Run on the **NAS**, with one manual detour through your browser on the PC for
Google's OAuth flow — that part can't be scripted.

### A. One-time Google Cloud setup (manual, in a browser)

1. [Google Cloud Console](https://console.cloud.google.com/) → new project.
2. APIs & Services → Library → enable the **Google Drive API**.
3. **Google Auth Platform → Audience** (Google split the old single "OAuth
   consent screen" page into separate Branding / Audience / Clients tabs — the
   test-user list and publishing status both now live under **Audience**, not
   under "OAuth consent screen"). Add `tranlehiep2203@gmail.com` (the account
   that owns the destination Drive) as a test user → click **Publish App**
   (status: **In production**). This step is not optional, and skipping it
   fails two different ways depending on when you hit it:
   - **Before publishing**, authorizing with any account *not* on the test
     user list fails immediately with `Error 403: access_denied` — "app has
     not completed Google's verification process... only developer-approved
     testers" — even the account that owns the project.
   - **Left in Testing even for an approved tester**, Google expires the
     refresh token every 7 days and the backup silently stops until someone
     notices and re-authorizes.

   Publishing to Production fixes both. You'll still see an "unverified app"
   click-through warning during authorization below — that's expected and
   fine; full Google verification is not required for personal use under 100
   users.
4. Credentials → Create credentials → OAuth client ID → **Desktop app**. Keep
   the client ID and secret for step B.

### B. Authorize (PC browser → NAS config)

Install rclone on **both** hosts from upstream, not from apt — Ubuntu 24.04
ships 1.60.1 (2022), and the token blob produced by step B has to be understood
by the other machine's rclone, so version skew across the two is worth avoiding:

```bash
curl https://rclone.org/install.sh | sudo bash   # run on the PC and on the NAS
```

```bash
# On the PC — opens a browser, prints a token blob
rclone authorize "drive" "<client_id>" "<client_secret>"
```

```bash
# On the NAS — paste the client_id/secret and the token blob when asked;
# choose "No" at the auto-config prompt
rclone config    # name the remote: gdrive
rclone lsd gdrive:   # confirm it works before continuing
```

### C. Repository, policy, timer (NAS — one block)

```bash
# 1. edit this line first
KOPIA_PW='CHANGE_ME_DIFFERENT_FROM_SERVICE_1'

# 2. install kopia — its own apt repo, exactly as on the PC.
# Kopia is not in Ubuntu's archive; `apt install kopia` alone fails here.
# (rclone was already installed from upstream in step B.)
curl -fsSL https://kopia.io/signing-key | sudo gpg --dearmor -o /etc/apt/keyrings/kopia.gpg
echo "deb [signed-by=/etc/apt/keyrings/kopia.gpg] http://packages.kopia.io/apt/ stable main" \
  | sudo tee /etc/apt/sources.list.d/kopia.list
sudo apt update && sudo apt install -y kopia

# 3. repo + cache
install -d -m 700 ~/.config/kopia
printf 'KOPIA_PASSWORD=%s\n' "$KOPIA_PW" > ~/.config/kopia/env
chmod 600 ~/.config/kopia/env
set -a; . ~/.config/kopia/env; set +a
kopia repository create rclone --remote-path gdrive:nas-ssd-backup
kopia cache set --cache-directory ~/.cache/kopia

# 4. retention + exclusions (applies excludes.txt, then verifies it landed)
/mnt/ssd/nas-lab/scripts/backup/apply-policy.sh offsite

# 5. install + enable the timer
sudo cp /mnt/ssd/nas-lab/scripts/backup/systemd/nas-offsite.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nas-offsite.timer

# 6. run it now and check
sudo systemctl start nas-offsite.service
kopia snapshot list /mnt/ssd
```

**Password must differ from service (1)'s** — two independent repositories,
two independent secrets, both in a password manager off these machines.

**If step 6 errors with a timeout or connection refused against `127.0.0.1`**,
that's the `rclone serve webdav` subprocess Kopia spawns for Drive — not Drive
itself, and not a sign the repository is damaged. Re-run once before
escalating; see [Why Google Drive despite the rclone
bridge](README.md#why-google-drive-despite-the-rclone-bridge) for the known
GitHub issues behind it.

**If offsite backups stop silently a week or so after setup**, the OAuth
consent screen was left in "Testing" — re-check it's published to Production
(step A.3) and re-run `rclone authorize`.

---

## Failure alerts

**Open decision, not yet implemented.** Silent failure is the normal way
backups die: the timer stops firing and nobody notices for six months. Both
services should get an `OnFailure=` handler — but pointing it at a channel
nobody reads is the same as no alert, so this needs a real destination first
(email, Telegram, a webhook) rather than a bespoke integration built blind.

In the meantime, both are visible without one:

```bash
systemctl --failed                              # anything currently failed
systemctl list-timers nas-backup.timer nas-offsite.timer nas-dump-sqlite.timer
```

Beszel already runs on both hosts and, per
[MONITORING.md](../arr-servers/technical/MONITORING.md), tracks systemd unit
state on this machine (37 units currently) because the agent mounts the
systemd socket — so a failed backup unit should already show up in its
dashboard, it just isn't wired to push a notification yet.

**When a channel is chosen**, the mechanism is:

```ini
# add to nas-backup.service, nas-offsite.service, nas-dump-sqlite.service
OnFailure=backup-alert@%n.service
```

with `/etc/systemd/system/backup-alert@.service` as a oneshot template that
reports `%i` (the failed unit's name) to that channel.

**Also alert on silence, not just failure.** A timer that never fires never
fails. `systemctl list-timers` shows `NEXT`; a Beszel alert on last-snapshot-age
exceeding ~3h (service 1, while WSL is up) or ~30h (service 2) would catch
that, once a channel exists to send it through.

---

## Day-to-day operation

Everything below assumes both services are already kick-started above.

### Is it actually running?

One command, on either host:

```bash
scripts/backup/backup-status.sh           # full report
scripts/backup/backup-status.sh --quick   # instant; skips repository queries
```

It reports **both legs** and exits non-zero if anything is wrong, so it also
works as a monitor probe. Per leg it checks: dump-set freshness, whether the
timer is installed/enabled and when it fires next, whether the last run
succeeded, any snapshot currently in progress (with bytes written, transfer
rate and a rough ETA), snapshot count and the age of the newest one, the
retention and exclusion rules actually in force, and space used at both ends —
including Drive quota.

Use `--quick` on the NAS when you just want timer and dump health: every
`kopia` call on the offsite leg spawns its own `rclone serve webdav` bridge to
Drive and takes 20–40s, so the full report is slow there by nature.

**How it sees the other host.** Service (1)'s repository is on the PC's
`/mnt/f` and its timers are in the PC's systemd — invisible from the NAS, and
vice versa. So each leg writes a heartbeat (`result`, `exit`, `finished_at`,
`duration_s`) into `nas-lab/.backup-state/<leg>.state` on the NFS export after
every run, and the status script reads the other leg's from there. That means
the remote half of the report is **as of that leg's last completed run**, not
live — it will say so, and tell you to run the script on that host for detail.
A leg that has never run since heartbeats existed shows as a warning rather
than silently passing.

The underlying commands, if you want them directly:

```bash
systemctl list-timers 'nas-*' --all   # schedules, last/next fire
systemctl --failed                    # anything currently broken
journalctl -u nas-offsite.service -n 50
kopia snapshot list /mnt/ssd          # the real proof: what's in the repo
```

There is no alert channel wired up yet — [failure alerts are an open
decision](#failure-alerts) — so checking is manual until that lands.
`backup-status.sh` is what an eventual alert would run.

### Take a snapshot right now

Don't wait for the timer — useful before/after a config change you want a
recovery point for.

```bash
# Service (1), on the PC
/mnt/nas-ssd/nas-lab/scripts/backup/snapshot-local.sh

# Service (2), on the NAS
/mnt/ssd/nas-lab/scripts/backup/snapshot-offsite.sh
```

Both scripts run the freshness guard first and will refuse to snapshot (exit
non-zero, message on stderr) if the SQLite dump set is stale or the source isn't
mounted — that's working as intended, not a bug to work around.

To force a fresh SQLite dump first (e.g. right before a manual snapshot, rather
than waiting for the next `:50`):

```bash
# On the NAS
/mnt/ssd/nas-lab/scripts/backup/dump-sqlite.sh
```

### Getting data back out

**All of it lives in [RESTORE.md](RESTORE.md)** — browsing history, restoring a
file or the whole tree, the SQLite swap procedure, verification cadence, the
quarterly drill, and the disaster scenarios for both legs. It is deliberately
one self-contained document, because it is the one you read during an outage
and cross-referencing three files at 2 a.m. is how mistakes happen. Every
command in it has been run and verified rather than written from memory.

Two things from it are worth knowing *before* you need them, since both fail
silently:

- **Restore as root, or ownership comes back wrong.** Kopia stores uid/gid in
  the repository but can only apply them as root. Restoring as `lehiep` gives
  every file `1000:1000` with no warning; the stack runs as `PUID=1001`, so the
  containers then can't read their own state.
- **There is no `--path` flag.** The source argument is `<rootID>/subpath`.

### Changing what's backed up

**One list, shared by both legs.** `scripts/backup/excludes.txt` holds only
what's physically impossible to back up — unreadable root-owned paths,
sockets, this system's own staging dirs. No size or value judgements: service
(1) has 930 GB of local disk and service (2)'s Drive destination has 5 TB, so
neither leg needs to trim the working set to fit.

The list lives in each **repository's policy**, not in the file, so editing the
file changes nothing until it's applied. Re-apply on the host that owns the
repo:

```bash
scripts/backup/apply-policy.sh local     # on the PC  -> /mnt/nas-ssd
scripts/backup/apply-policy.sh offsite   # on the NAS -> /mnt/ssd
```

That script clears the ignore list, re-adds every rule in a single call, then
**diffs the resulting policy against the source files and exits non-zero on a
mismatch**. Expect a final line like `policy for /mnt/nas-ssd: 7 ignore rules,
matches excludes.txt`.

Every part of that shape exists because of a specific failure:

- **The clear step**, because `--add-ignore` only ever *adds* — without it, a
  rule you delete from the file lingers in the policy forever.
- **One invocation for all the adds**, not a loop. A per-rule loop is one
  process per rule, each reading the whole policy manifest, appending, and
  writing it back; a later write based on a stale read silently discards
  earlier rules while still printing `- adding "..." to "ignore rules"` for
  every one. Observed 2026-08-13 on the PC: 18 rules reported added, 16
  landed. It's also drastically slower on the offsite leg, where every kopia
  process spawns its own `rclone serve webdav` bridge to Drive.
- **Clear and add as two separate calls**, because combining `--clear-ignore`
  with `--add-ignore` in one call applies the clear and *silently drops every
  add* — leaving an empty ignore list. Observed 2026-08-13 on the NAS.
- **The verify step**, because all three of the above failure modes are
  silent. Never trust the apply output; trust the diff.

To back up a new SQLite database, add its path to the `DBS` array at the top of
`scripts/backup/dump-sqlite.sh`.

### Expected behavior

Both services are `Type=oneshot` systemd units whose `ExecStart` is a single
script (`snapshot-local.sh` or `snapshot-offsite.sh`), run start to finish
under `set -euo pipefail`. That shape has one consequence worth knowing before
you read a failure: **systemd cannot tell you which step failed** — every
internal failure (guard check, `kopia snapshot create`, `kopia maintenance
run`) surfaces identically as

```
Job for nas-backup.service failed because the control process exited with error code.
```

`systemctl status <service>` and `journalctl -xeu <service>` show more, but the
fastest way to see the *actual* error is to run the script directly, which
prints everything live instead of through the journal:

```bash
bash -x /mnt/nas-ssd/nas-lab/scripts/backup/snapshot-local.sh   # or snapshot-offsite.sh, on the NAS
```

**What success looks like:** exit `0`, systemd shows the unit as
`inactive (dead)` (not `failed`) after it runs, and `kopia snapshot create`
prints `Created snapshot with root ... in <time>` with **no** `fatal error(s)`
line. `kopia snapshot list <root>` gains one new entry.

**The failure mode that looks like success, and isn't:** Kopia creates a
snapshot and reports it as `Created snapshot ... ` **even when some files
couldn't be read** — it treats unreadable files as per-file fatal errors, not
a reason to abort the whole snapshot. But it still exits **non-zero** overall
when any fatal errors occurred, which `set -e` then treats as script failure —
so systemd reports the generic "control process exited" message even though a
snapshot *was* created. The tell is in the script's own output (or
`journalctl`), not the exit code: look for lines like

```
! Error when processing "lost+found": cannot create iterator: unable to read directory: open /mnt/nas-ssd/lost+found: permission denied
Found 2 fatal error(s) while snapshotting ...
```

This is a *permissions* problem, not a Kopia or guard problem — some file or
directory under the snapshot root isn't readable by the backup user (usually
because a container generated it as `root` despite `PUID=1001`, or it's
filesystem furniture like ext4's `lost+found`). The fix is always the same:
add the path to `scripts/backup/excludes.txt` — the *common* list, since an
unreadable path is unreadable from both legs — then re-run
`apply-policy.sh local` (or `offsite`). See [Changing what's backed
up](#changing-whats-backed-up).

Two things not to do instead. Don't chown files inside a live container's data
directory, since the container may depend on that ownership. And don't reach
for running the backup as `root` to read them: the NFS export is
`all_squash,anonuid=1001`, so every client UID including root arrives at the
server as 1001, and service (2) already runs as 1001 on the NAS directly. There
is no user either leg can run as that can read a `0600 root:root` file.

### Troubleshooting

| Symptom | Likely cause | Where to look |
|---|---|---|
| `snapshot-local.sh` exits with "NFS mount ... is not mounted" | `/mnt/nas-ssd` isn't up on the PC | Check the NFS mount, not Kopia |
| `snapshot-*.sh` exits with "dump set is stale" | `nas-dump-sqlite.timer` isn't firing | `systemctl status nas-dump-sqlite.timer` on the NAS |
| systemd reports "control process exited with error code" but gives no detail | Generic — could be the guard, Kopia, or a permissions error inside the snapshot itself | [Expected behavior](#expected-behavior) above — run the script directly with `bash -x` to see the real error |
| `kopia snapshot create` prints `! Error when processing "..."` / `permission denied` / `Found N fatal error(s)` | A file or directory under the snapshot root isn't readable by the backup user (often `root`-owned despite `PUID=1001`, or ext4's `lost+found`) | Add the path to `scripts/backup/excludes.txt` and re-apply — see [Changing what's backed up](#changing-whats-backed-up) |
| `kopia snapshot create` on the NAS times out or errors against `127.0.0.1` | The `rclone serve webdav` subprocess Kopia spawns for Drive, not Drive itself | [Why Google Drive despite the rclone bridge](README.md#why-google-drive-despite-the-rclone-bridge) — retry once before escalating |
| Offsite backups silently stopped a week or so after setup | OAuth consent screen was left in "Testing" — refresh token expired after 7 days | Re-run `rclone authorize`, and check the consent screen is published to **Production** |
| `repository create s3`-style errors mentioning bucket access (if this ever migrates to B2/R2) | Application key missing `listBuckets` capability | See [kopia#5329](https://github.com/kopia/kopia/issues/5329) |
| Restored `.db` won't open / app shows missing data | Restored the live `.db` instead of the dump, or left a stale `-wal` next to it | Use `appdata-dumps/current/*.dump`, and delete `-wal`/`-shm` after restoring |

### Passwords

Two independent Kopia repository passwords exist — one per host — stored in
`~/.config/kopia/env` on each machine. **Neither lives in this repository or in
`.env`.** Both must also live in a password manager off these two machines; a
password that only exists on the machine that just died is not a backup.
