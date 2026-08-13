# Backup services — plan

Two independent backup services protecting `/mnt/ssd` on the NAS: a local copy
pulled to the PC twice per WSL session, and a daily encrypted offsite copy pushed
to Google Drive. **Both run Kopia.** One tool, one repository format, one
restore procedure.

Status: **decided, partly built.** Kopia on both legs, Google Drive for offsite,
accepted with eyes open on the tradeoff explained in [Why Google Drive despite
the rclone bridge](#why-google-drive-despite-the-rclone-bridge).

**Service (1) is live** as of 2026-08-13: repository created on `/mnt/f`, policy
applied, timer enabled, snapshots verified clean. **Service (2) is staged but
not running** — Kopia 0.23.1 and rclone 1.75.0 are installed on the NAS, and
what remains is the one-time Google OAuth authorization, which needs a browser
and cannot be scripted. See
[QUICK-START.md](QUICK-START.md#set-up-and-kick-start--service-2).

Architecture diagrams — how the pieces fit together — live in
[PLAN.md](PLAN.md). Step-by-step setup and day-to-day operation — checking
status, manual snapshots, failure alerts — live in
[QUICK-START.md](QUICK-START.md). **Getting data back out is
[RESTORE.md](RESTORE.md)**, kept separate because it is the one you read during
an outage; every command in it has been run and verified rather than written
from memory.

| | Service (1) — local | Service (2) — offsite |
|---|---|---|
| Tool | **Kopia** | **Kopia** |
| Runs on | **PC** (WSL) | **NAS** (`ubuntu-2404`) |
| Direction | pull `/mnt/nas-ssd` → `/mnt/f` | push `/mnt/ssd` → Google Drive |
| Frequency | 2× per WSL boot: at start, then 1h later | daily |
| Trigger | systemd timer, fires on WSL start | systemd timer |
| Reads source over | NFS | local disk |
| Scope | **everything readable** — ~8.2 GB | **everything readable** — ~8.2 GB |
| Purpose | fast restore, recent history | disaster recovery |

Both legs back up the same full data set — see [Why both legs back up
everything](#why-both-legs-back-up-everything) for the reversal from this
plan's earlier size-curated design.

Service (1) is not a continuous hourly job — it fires once at WSL startup and
once more an hour later, then stops until the next boot. See
[Scheduling](#scheduling) for why, and for what that does to service (1)'s
retention policy.

The two repositories are **separate and independent** — same tool, no shared
state. See [Two repositories, not one](#two-repositories-not-one).

## Why both legs back up everything

This plan originally curated what got backed up, and the two legs curated
differently. `/mnt/ssd` measures ~20 GB on disk, but a lot of it is regenerable
— Jellyfin's transcode cache, Ollama's model blobs — and the earlier version of
this document sized the offsite leg against Google Drive's **free 15 GB tier**,
trimming the regenerable bulk so the backup would fit without a storage
purchase. Service (1), writing to a 932 GB local drive, never had that
constraint and always kept everything it could read.

**That constraint is gone.** The Drive destination is a paid account with
**5 TB** of headroom, not the 15 GB free tier — several orders of magnitude
past anything `/mnt/ssd` will grow to. Curating for space was worth the
judgement calls against a real 15 GB ceiling; it buys nothing against 5 TB,
and it has a real cost the earlier version of this doc underweighted: a
restore that silently lacks whatever a past size-judgement decided was
"regenerable," discovered at the worst possible time. Completeness now beats
curation on both legs, so both legs apply the same policy.

**Both services exclude only what is physically impossible to back up** — not
size, not "will probably never need it," just unreadable paths (ext4's
`lost+found`, Beszel's root-owned SSH key — both `0600`/`0700 root:root`, and
the NFS export is `all_squash,anonuid=1001` so no client UID, root included,
can read them either) and things that aren't real files (sockets, a stale
lock, this system's own staging directories). One file,
`scripts/backup/excludes.txt`, shared verbatim by both legs, applied and
verified by `scripts/backup/apply-policy.sh local|offsite`.

Measured 2026-08-13: a full local snapshot is **7.8 GB across 11,008 files and
takes 59 seconds** over NFS, and the repository on `/mnt/f` holds 6.7 GB after
zstd and dedup. Subsequent runs re-read almost nothing, since large static
blobs like Ollama's models never change and dedupe to a single stored copy —
they only cost space on the *first* snapshot that sees them, and cost it
again only if their bytes actually change (a model update, not a re-run).

## The real risk is live SQLite, not the tool

Every service here stores its state in SQLite with a write-ahead log:

```
appdata/radarr/radarr.db                  6.6 M
appdata/sonarr/sonarr.db                 10.1 M
appdata/prowlarr/prowlarr.db             23.0 M
appdata/bazarr/db/bazarr.db               1.2 M
appdata/jellyfin/data/data/jellyfin.db    5.3 M
appdata/beszel/data/data.db               1.3 M
appdata/open-webui/webui.db               0.8 M
appdata/open-webui/vector_db/chroma.sqlite3
appdata/vn-dubbing/dubbing.sqlite3        1.2 M   (+ 4 M -wal right now)
```

A backup that copies `radarr.db` at 10:00:00 and `radarr.db-wal` at 10:00:03 has
captured two halves of different states. SQLite is crash-consistent only if the
copy is *atomic*; a file-by-file walk is not. The restore may open with silent
row loss, or not open at all.

**No backup tool fixes this.** Kopia, restic, rsync and Duplicati all have the
same problem, because it is a property of the source, not the transport. Any plan
that skips this step is protecting corrupted data on a schedule.

This is also the argument against the obvious cheap alternative — an hourly
`rsync` mirror. A mirror faithfully replicates a corrupted Radarr database within
the hour and overwrites the last good copy with it. Snapshot history is the
entire point of the exercise.

### Fix: dump before snapshot, on the NAS

A NAS-side script quiesces each database into a consistent file:

```bash
sqlite3 "$src" "VACUUM INTO '$dumps/radarr.db'"
```

`VACUUM INTO` takes a read lock, writes a single defragmented file, and does not
require stopping the container. Output goes to `/mnt/ssd/nas-lab/appdata-dumps/`.
Both services then back up **the dumps**, and exclude the live `*.db`, `*.db-wal`
and `*.db-shm` files entirely.

Why NAS-side and not from WSL: SQLite locking across NFS is unreliable, and
service (1) reads over NFS. Running the dump locally on the NAS sidesteps it, and
one dump serves both services.

Schedule: hourly at **:50**, so a fresh dump always precedes whichever runs
next — either of service (1)'s two per-boot runs, or service (2)'s daily run.

`appdata-dumps/` must be added to `.gitignore`.

qBittorrent is the exception — no SQLite. Its state is `BT_backup/` (`.torrent` +
`.fastresume` per torrent) plus `qBittorrent.conf`, which are ordinary files and
copy safely. Exclude its `ipc-socket`, `lockfile`, `logs/` and `GeoDB/`.

### The same problem, harder: Postgres

Added 2026-08-13 with the cloud stack. Immich and Nextcloud store their state in
Postgres, and the live data directory is worse than a live SQLite file on two
counts. It is `0700` owned by uid 999, the container's `postgres` user — which
**neither leg can read**, service (2) running as 1001 on the NAS and the NFS
export squashing every client UID including root to 1001 for service (1). Kopia
counts an unreadable entry as a fatal error, so leaving it in the snapshot fails
the unit on every run. And a file-level copy of a running cluster carries torn
pages and a WAL from a different moment; it would not restore even if readable.

Same shape of fix, different tool: `scripts/backup/dump-postgres.sh` runs
`pg_dump --clean --if-exists | gzip` through `docker exec`, hourly at **:45**,
publishing atomically to `appdata-dumps/postgres/current/` with its own
`.stamp`. The live directories are excluded; `guard-source.sh` enforces the same
two-hour staleness rule against that stamp once the directory exists.

Full detail, including restore commands, is in
[docs/cloud-services/README.md](../cloud-services/README.md#backups--read-this-before-trusting-the-setup).

## The tool landscape

Backup tools fall into four families, and picking the wrong *family* costs more
than picking the wrong tool inside one.

**Mirrors** (`rsync`, `rclone sync`, Syncthing) reproduce the source's current
state at the destination. They are fast, transparent, and restore with a plain
file copy — no tool needed. Their defining property is also their fatal one
here: a mirror has no memory. Delete a file, or corrupt a database, and the next
run faithfully propagates that to your only other copy. Versioning bolted on
(`rsync --link-dest`, `rclone --backup-dir`) is crude and easy to misconfigure.

**Snapshotters** (Kopia, restic, Borg, Duplicati, Duplicity) store
content-addressed, deduplicated, encrypted blobs and keep every point-in-time
snapshot until a retention policy expires it. Restoring requires the tool and
the passphrase. This is the family we need, because our threat model is
"Radarr's database silently corrupted itself and nobody noticed for a week."

**Filesystem-level** (ZFS / btrfs snapshot + `send`/`receive`) is the technically
superior answer and we cannot use it — see [What would change this
decision](#what-would-change-this-decision).

**Whole-machine imaging** (Proxmox Backup Server, UrBackup, Veeam Agent) backs up
the entire host, bare-metal-restorable. Real value at fleet scale; wildly
disproportionate for a few GB of application state on two machines.

### Candidates compared

| Tool | Family | Encrypted at rest | Dedup + compression | Point-in-time history | Reaches Google Drive | Verdict here |
|---|---|---|---|---|---|---|
| **Kopia** | snapshotter | ✅ | ✅ zstd | ✅ | ⚠️ via unmaintained rclone bridge — accepted, see below | **chosen — both services** |
| restic | snapshotter | ✅ | ✅ (since 0.14) | ✅ | ✅ first-class rclone backend | strong runner-up — rejected only to keep one tool |
| rclone | mirror | ✅ via `crypt` remote | ❌ | ⚠️ `--backup-dir` only | ✅ native | used as Kopia's *transport* to Drive, not as the backup tool |
| rsync | mirror | ❌ | ❌ (delta transfer only) | ⚠️ `--link-dest` hardlink trees | ❌ | rejected |
| Syncthing | continuous mirror | in transit only | ❌ | ⚠️ file versioning | ❌ | rejected — it is sync, not backup |
| BorgBackup 1.x | snapshotter | ✅ | ✅ | ✅ | ❌ SSH/local only | rejected |
| BorgBackup 2.0 | snapshotter | ✅ | ✅ | ✅ | ✅ via borgstore | rejected — still beta |
| Duplicati | snapshotter | ✅ | ✅ | ✅ | ✅ | rejected — restore reliability |
| Duplicity | snapshotter | ✅ GPG | ❌ dedup; incremental chains | ✅ | ✅ | rejected — chain fragility |
| ZFS / btrfs send | filesystem | ✅ native | ✅ | ✅ **atomic** | ❌ | unavailable — `/mnt/ssd` is ext4 |
| tar + cron | DIY | DIY | ❌ | full copies only | DIY | rejected |

### Why the rejections

**rsync** is the tool everyone reaches for first, and it is wrong here for two
independent reasons beyond the no-history problem. `/mnt/f` is 9p/drvfs onto
NTFS, which cannot represent ext4 ownership or POSIX permissions — an rsync
mirror would restore every file owned wrong, and this stack is specific about
that: `.env` sets `PUID=1001` on the NAS *precisely because* reusing the PC's
`1000` left appdata owned by a uid that host did not have. Second, rsync re-stats
the entire tree on every run; across NFS, hourly, that is real cost for no
benefit.

**Syncthing** is excellent at what it does, which is not this. Continuous
bidirectional sync means a deletion or corruption reaches the other side in
seconds — faster propagation of the exact failure we are defending against.

**Borg** is the most battle-tested deduplicating backup tool in this list, and
1.x reaches only SSH and local targets — no object storage. [Borg
2.0](https://www.borgbackup.org/releases/borg-2.0.html) fixes exactly that,
adding rclone and S3/B2 backends through `borgstore`, and it would be a genuine
contender. It has been in beta for years and is still shipping `2.0.0b23`
prereleases. Beta is not where the copy that survives a house fire should live.

**Duplicati** has the friendliest UI in the category and a long, well-documented
history of failing to restore. [Restore-blocking bugs were still being filed in
2026](https://forum.duplicati.com/t/unable-to-restore-backpup/21842). A backup
tool is judged on restores, not backups.

**Duplicity** predates content-addressed storage: it stores a full backup plus a
chain of GPG-encrypted incrementals, so one damaged volume mid-chain can
invalidate everything after it, and periodic full re-uploads are required to keep
chains short. Superseded by everything else in the snapshotter column.

**restic** loses nothing on merit — it is the one tool here that could replace
Kopia outright. It is rejected only because using both would mean two restore
procedures, and that cost is [discussed below](#what-one-tool-gives-up).

### Why Kopia

- **Metadata fidelity on a hostile destination.** Kopia stores ownership and
  permissions inside the repository, so restores are faithful regardless of what
  NTFS can express. This is the single biggest reason not to mirror onto `/mnt/f`.
- **Cheap repeat runs over NFS.** Its content-addressed local cache means an
  hourly run re-reads almost nothing across the network.
- **Retention as policy, not as a cron flag.** `kopia policy set` attaches
  hourly/daily/weekly/monthly rules to the source path; the snapshot command
  stays a one-liner and cannot drift from the retention intent. The same is true
  of the exclusion list — it lives in the repository policy, not in each
  invocation.

Kopia is also measurably faster than restic on large counts of small files, which
describes `appdata/` well — though at a few GB that is a tiebreaker, not an
argument.

## Why Google Drive despite the rclone bridge

This is the one place the plan accepts a real cost to keep a single tool, so it
is worth stating plainly rather than glossing over.

**Kopia cannot reach Google Drive cleanly. Neither of its two paths there is
good.**

**Native Drive backend** requires a Google *service account*, and Drive charges
uploaded files to their **owner** — which is the service account, not you.
Service accounts have no Drive quota on a consumer account, so uploads fail with
`storage quota exceeded` while your own 15 GB sits empty
([kopia#2656](https://github.com/kopia/kopia/issues/2656)). The documented fix is
a Shared Drive or domain-wide delegation, both Google Workspace–only. On a
personal Gmail account this is a hard blocker — this path is not used.

**The rclone bridge**, which is what this plan uses instead, works by Kopia
spawning `rclone serve webdav` as a subprocess and speaking WebDAV to it over
localhost. That indirection is where the known failures live: [`timed out
waiting for rclone to start`](https://github.com/kopia/kopia/issues/2573),
[missing WebDAV cert paths](https://github.com/kopia/kopia/issues/4429),
[`PutBlob() failed` against Drive](https://github.com/kopia/kopia/issues/1698).
It works for many people, and it is also the code path [Kopia's own
documentation declines to
maintain](https://kopia.io/docs/reference/command-line/common/repository-create-rclone/).

Accepting this path also means accepting that rclone is a second required
binary, present on whatever machine a restore happens from — "Kopia only" here
means one *backup format*, not one binary. See [Google Drive
specifics](#google-drive-specifics) for the two concrete failure modes to guard
against (the 7-day token expiry and the WebDAV startup race) and how this plan
mitigates each.

The alternative that avoids all of this — B2 or R2 via Kopia's native S3
backend, no rclone, no OAuth, no bridge — remains on the table. See [What would
change this decision](#what-would-change-this-decision) for when to revisit.

## Two repositories, not one

Both services run Kopia, but against **two separate repositories**, each
snapshotting its own source. The tempting simplification is to snapshot once and
use `kopia repository sync-to` to mirror the repository from Drive down to
`/mnt/f`. Do not: a synced repository is a byte-for-byte copy, so repository-level
corruption reaches both copies, and it means downloading from the cloud hourly to
maintain a local copy of data that is already local.

Two repositories means the local copy stays independent, and stays useful when
the internet is down.

## What one tool gives up

Honest accounting, because the earlier draft of this plan chose two tools
deliberately.

The dominant failure mode of deduplicating backup tools is not disk failure — it
is repository corruption or a format bug, and deduplication means a single bad
blob can poison many snapshots at once. Running Kopia on both legs means a Kopia
format bug could in principle affect both copies at once. Two independent formats
would rule that out.

What one tool buys in exchange:

- **One restore procedure**, learned once and tested once. Format diversity is
  only worth anything if *both* restore paths are actually drilled, and two
  untested restore paths are strictly worse than one tested one.
- **One set of credentials**, one config file layout, one command vocabulary for
  the parts that matter: snapshot creation, retention policy, restore, verify.
- **One dump script and one exclusion list**, shared verbatim by both services.

This plan does **not** get to drop rclone or the OAuth dance — see [Why Google
Drive despite the rclone bridge](#why-google-drive-despite-the-rclone-bridge).
That cost is paid regardless of which backup tool sits in front of it, because
it is Drive's cost, not restic's or Kopia's.

The residual risk is mitigated the way it should be: by [verification and restore
testing](#verification-and-restore-testing), which catches a corrupt repository
regardless of format. `kopia snapshot verify` on both legs is the control that
matters here, and it is now the same command on both.

## What would change this decision

- **`/mnt/ssd` moving to ZFS or btrfs.** A filesystem snapshot is atomic across
  the whole dataset, which solves the live-SQLite problem structurally — no dump
  step, no `VACUUM INTO`, no staleness guard. That is a better answer than
  anything above, and it is unavailable only because the volume is ext4.
- **Borg 2.0 reaching stable.** Its object-storage backends plus Borg's track
  record would make it a genuine contender for both legs.
- **The rclone bridge actually failing in practice** — a startup timeout or a
  failed snapshot traced to the WebDAV hop rather than to Drive itself. That is
  the concrete signal to stop tolerating it and move service (2) to B2 or R2 via
  Kopia's native S3 backend: no rclone, no OAuth, no bridge, same tool, same
  restore command.
- **The dataset growing large enough that price-per-GB starts to matter.**
  Irrelevant at the current few-GB scale against 5 TB, but if `/mnt/ssd` ever
  grows into the hundreds of GB, object storage (B2/R2, ~$6/TB/month) becomes
  the cheaper destination than Drive at scale.
- **A Kopia repository-format bug actually biting.** That is the scenario [one
  tool](#what-one-tool-gives-up) accepts. If it happens, adding restic on leg 2
  is a contained change — the dumps and exclusion list are tool-agnostic and
  would carry over unchanged.

## Service (1) — NAS → PC, twice per WSL boot

```
/mnt/nas-ssd  ──kopia snapshot──>  /mnt/f/nas-ssd-backup
   (NFS, read-only)                 (9p → NTFS)
```

### Scheduling

Deliberately not a continuous hourly job. WSL only backs the NFS mount while
it's up, and the two runs it fires — one right at boot, one an hour later — are
meant to catch "just started working" and "been working a while," not to run
indefinitely for as long as the terminal happens to stay open. After the second
run the timer goes quiet until the next boot.

`systemd=true` is already set in `/etc/wsl.conf` on this box, so a timer is the
clean answer. Two `OnBootSec=` lines, not `OnBootSec=` + `OnUnitActiveSec=` —
systemd treats repeated monotonic timer directives as additive, each firing once
per boot at its offset, rather than one re-arming the other:

```ini
[Timer]
OnBootSec=2min          # "when I open WSL" — WSL boot is the trigger
OnBootSec=62min          # second run, ~1h after the first
```

`OnUnitActiveSec=1h` is what would make this keep firing every hour
indefinitely — that is the thing being deliberately avoided here, so it is left
out. `Persistent=true` is also dropped: it only affects `OnCalendar=`-style
timers (catching up a missed wall-clock trigger), and both triggers here are
boot-relative, recomputed fresh every boot — there is nothing for it to catch up.

Kopia's own built-in scheduler is the wrong choice regardless — it requires a
long-running `kopia server` process, which is more moving parts than a timer.

Note: `systemctl is-system-running` currently reports **degraded** on this WSL.
Something already fails at boot. Worth diagnosing before adding units, though it
should not block them.

### Details

- Repository: `/mnt/f/nas-ssd-backup`, filesystem backend.
- Cache: `~/.cache/kopia` on **ext4**, never on `/mnt/f` — a cache on 9p defeats
  the point.
- Source path `/mnt/nas-ssd` — mount must be up; the unit should check and fail
  loudly rather than snapshot an empty directory. An empty snapshot plus a
  retention policy is how you delete a backup by accident.
- Password from a root-owned file, not a compose `.env`.

## Service (2) — daily NAS → Google Drive

```
/mnt/ssd  ──kopia snapshot──>  rclone serve webdav (localhost)  ──>  Google Drive
 (local)                        spawned by Kopia as a subprocess      (encrypted
                                                                        before leaving
                                                                        the house)
```

The offsite copy holds `.env` and `docs/CREDENTIALS.md`. It must be encrypted
client-side — Google must never receive plaintext. Kopia encrypts by default, so
this is satisfied with no extra layer. Do **not** additionally wrap it in an
rclone `crypt` remote — that only doubles CPU cost and complicates restore for no
gain, since rclone here is a transport, not the encryption boundary.

### Google Drive specifics

- **Create your own OAuth client_id.** rclone's default is shared across
  thousands of users and will throw HTTP 403 rate-limit errors. Google Cloud
  Console → new project → enable Drive API → OAuth consent screen → OAuth
  client ID (Desktop app).
- **Publish the consent screen to Production**, not Testing. Left in Testing,
  Google expires the refresh token every 7 days and the backup silently stops
  until someone notices and re-authorizes
  ([confirmed behavior](https://forum.rclone.org/t/rclone-google-drive-token-expires-every-week/22502) —
  it is a Drive policy on unverified apps, not an rclone bug). Production mode
  still shows an "unverified app" click-through warning during the one-time
  authorization, which is expected and fine for a personal-use app under 100
  users — full verification is not required.
- The NAS is headless, so the OAuth dance runs on the PC:
  `rclone authorize "drive" <client_id> <client_secret>`, then paste the printed
  token into `rclone config` on the NAS.
- 5 TB of paid Drive storage vs ~8 GB of data — no meaningful capacity concern.
- Drive's 750 GB/day upload cap is irrelevant at this scale.
- If `kopia snapshot create` ever fails with a timeout or connection error to
  `127.0.0.1`, that is the WebDAV subprocess, not Drive itself — see [Why Google
  Drive despite the rclone
  bridge](#why-google-drive-despite-the-rclone-bridge) before assuming the
  repository is damaged.

## Retention

| | latest | daily | weekly | monthly |
|---|---|---|---|---|
| Service (1) | 10 | 14 | 8 | 12 |
| Service (2) | — | 14 | 8 | 24 |

Service (1) drops `--keep-hourly` — with two runs per boot rather than a
continuous hourly cadence, an "hourly" retention bucket doesn't correspond to
anything real. `--keep-latest 10` covers the same need (several most-recent
snapshots for a fast, recent-history restore) without pretending to a cadence
the timer doesn't run.

Service (2) keeps a longer tail because it is the copy that answers "this got
corrupted three months ago and nobody noticed."

## No judgement calls left to make

The earlier version of this plan had a section here weighing `open-webui`
(890 MB of chat history and Chroma vector embeddings) — neither clearly
irreplaceable nor clearly regenerable — against the old 15 GB offsite ceiling,
and similar borderline calls for Radarr's `MediaCover/` and Jellyfin's
`data/metadata/` (re-fetchable, but slowly and against rate-limited APIs).

All three are backed up in full now, on both legs, along with everything else
under `/mnt/ssd` that `scripts/backup/excludes.txt` doesn't name as physically
unreadable. See [Why both legs back up
everything](#why-both-legs-back-up-everything) — there is no remaining
capacity constraint that would make trimming any of this worthwhile.

## Verification and restore testing

A backup that has never been restored is not a backup — it is an untested
assumption with a cron entry.

The cadences (weekly `kopia snapshot verify`, monthly with
`--verify-files-percent=5`, quarterly full drill), the commands, and the drill
log all live in **[RESTORE.md](RESTORE.md)** — one document rather than
scattered across three, because verification only matters as the rehearsal for
a restore. With one tool they are the same commands against both repositories,
which is most of the argument for consolidating on Kopia.

The quarterly drill is the only step that proves the SQLite dump logic works
end to end, and the one most likely to get skipped. Put it in the calendar, not
just in a doc.

## Failure notification

Silent failure is the normal way backups die: the timer stops firing, nobody
notices for six months. Both units get an `OnFailure=` handler.

Beszel already runs on both hosts and can watch the systemd units. Open question
at review: is a Beszel alert enough, or do you want a push notification? A backup
alert nobody reads is the same as no alert.

## Explicitly out of scope

- **`/mnt/hdd/film-data`** — 885 GB of media and torrents, deliberately not
  backed up. Re-downloadable, and 885 GB does not fit on `/mnt/f` (932 GB total)
  alongside anything else, nor in any sane Drive tier. If that judgement is wrong
  for some subset (personal media? hard-to-find releases?), say so at review —
  that would be a third service, not a change to these two.
- Bare-metal recovery of the NAS OS itself. This plan protects application state,
  not the operating system. Rebuilding the host is a documented-runbook problem,
  not a backup problem.

## Implementation checklist

Ordered by dependency:

1. ✅ `scripts/backup/dump-sqlite.sh` + hourly systemd timer on the NAS;
   `appdata-dumps/` added to `.gitignore`.
2. ✅ Verified a dumped `radarr.db` passes `PRAGMA integrity_check` while Radarr
   runs — `ok`, 134 movies / 6 series under live writes.
3. ✅ Exclusion list: `excludes.txt`, shared by both legs, impossibilities only
   — applied and verified by `apply-policy.sh`.
4. ✅ Service (1): Kopia installed, repo created on `/mnt/f`, policy applied,
   service + timer enabled. Snapshots clean; 7.8 GB in 59 s.
5. ⏳ Service (2): Kopia + rclone **installed on the NAS**. Remaining: own
   OAuth client_id published to Production, headless authorize, create repo,
   `apply-policy.sh offsite`, service + timer.
6. ⏳ `OnFailure=` handlers and Beszel alerting for both.
7. ⏳ Restore drill for each, and write the result up as `RESTORE.md` alongside
   this file.
8. ⏳ Observe service (1) firing across a real `wsl --shutdown` — the
   `OnBootSec` path has never been watched through an actual boot. See
   [PLAN.md](PLAN.md#verify-after-a-real-reboot).

Steps 1–3 are shared groundwork and land first.
