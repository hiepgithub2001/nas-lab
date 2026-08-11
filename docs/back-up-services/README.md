# Backup services — plan

Two independent backup services protecting `/mnt/ssd` on the NAS: an hourly local
copy pulled to the PC, and a daily encrypted offsite copy pushed to Google Drive.

Status: **plan, for review. Nothing here is built yet.** One open decision is
flagged in [Tool for service (2)](#open-decision-tool-for-service-2); everything
else is a recommendation ready to implement.

Step-by-step setup — scripts, unit files, exact commands — lives in
[IMPLEMENTATION.md](IMPLEMENTATION.md).

| | Service (1) — local | Service (2) — offsite |
|---|---|---|
| Runs on | **PC** (WSL) | **NAS** (`ubuntu-2404`) |
| Direction | pull `/mnt/nas-ssd` → `/mnt/f` | push `/mnt/ssd` → Google Drive |
| Frequency | hourly | daily |
| Trigger | systemd timer, fires on WSL start | systemd timer |
| Reads source over | NFS | local disk |
| Purpose | fast restore, recent history | disaster recovery |

They share no code path and no repository. That is deliberate — see
[Why two tools is a feature](#why-two-tools-is-a-feature).

## The finding: this is ~600 MB of data, not 20 GB

`/mnt/ssd` measures 20 GB, but almost all of it is regenerable. Measured
2026-08-12:

| Path | Size | Keep? |
|---|---|---|
| `appdata/jellyfin/cache` | **13 G** | ✗ regenerable on demand |
| `appdata/ollama` | **4.9 G** | ✗ re-pullable model blobs |
| `appdata/open-webui` | 890 M | ~ chat history + chroma vectors — your call |
| `appdata/radarr` | 165 M | ✓ (minus `logs.db`, `MediaCover/`) |
| `appdata/jellyfin/data/metadata` | 117 M | ~ re-fetchable, but slow and rate-limited |
| `appdata/recyclarr` | 78 M | ✓ |
| `appdata/sonarr` | 75 M | ✓ (minus `logs.db`) |
| `appdata/prowlarr` | 58 M | ✓ (minus `logs.db` — 7.4 M of its 58 M) |
| `appdata/jellyfin/data/data` | 17 M | ✓ `jellyfin.db` — users, watch history |
| `appdata/qbittorrent` | 15 M | ✓ `BT_backup/` is the critical part |
| `appdata/bazarr` | 9.2 M | ✓ |
| `appdata/beszel` | 5.4 M | ✓ |
| `appdata/vn-dubbing` | 1.6 M | ✓ `dubbing.sqlite3` |
| repo (`docs/`, `scripts/`, `services/`, compose files, `.env`) | ~700 K | ✓ |

**Irreplaceable set: roughly 600 MB**, or ~1.5 GB if you keep `open-webui`.

This is the number that makes the whole plan cheap. Service (2) fits inside
Google's free 15 GB tier with room for a year of version history — no Google One
subscription needed. Service (1) becomes a sub-minute job.

The exclusions are not an optimisation, they are the design. Backing up 13 GB of
Jellyfin cache hourly across 9p to NTFS would be slow, would churn the drive, and
would protect nothing.

## The real risk is live SQLite, not the tool

Every service here stores its state in SQLite with a write-ahead log:

```
appdata/radarr/radarr.db           6.6 M
appdata/sonarr/sonarr.db          10.1 M
appdata/prowlarr/prowlarr.db      23.0 M
appdata/bazarr/db/bazarr.db        1.2 M
appdata/jellyfin/data/data/jellyfin.db   5.3 M
appdata/beszel/data/data.db        1.3 M
appdata/open-webui/webui.db        0.8 M
appdata/vn-dubbing/dubbing.sqlite3 1.2 M   (+ 4 M -wal right now)
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

Schedule: hourly at **:50**, so a fresh dump always precedes service (1)'s
hourly run and service (2)'s daily run.

`appdata-dumps/` must be added to `.gitignore`.

qBittorrent is the exception — no SQLite. Its state is `BT_backup/` (`.torrent` +
`.fastresume` per torrent) plus `qBittorrent.conf`, which are ordinary files and
copy safely. Exclude its `ipc-socket`, `lockfile`, `logs/` and `GeoDB/`.

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
disproportionate for 600 MB of application state on two machines.

### Candidates compared

| Tool | Family | Encrypted at rest | Dedup + compression | Point-in-time history | Reaches Google Drive | Verdict here |
|---|---|---|---|---|---|---|
| **Kopia** | snapshotter | ✅ | ✅ zstd | ✅ | ⚠️ experimental / unmaintained bridges | **chosen — service (1)** |
| **restic** | snapshotter | ✅ | ✅ (since 0.14) | ✅ | ✅ first-class rclone backend | **chosen — service (2)** |
| **rclone** | mirror | ✅ via `crypt` remote | ❌ | ⚠️ `--backup-dir` only | ✅ native, 70+ providers | used as restic's *transport*, not as the backup tool |
| rsync | mirror | ❌ | ❌ (delta transfer only) | ⚠️ `--link-dest` hardlink trees | ❌ | rejected |
| Syncthing | continuous mirror | in transit only | ❌ | ⚠️ file versioning | ❌ | rejected — it is sync, not backup |
| BorgBackup 1.x | snapshotter | ✅ | ✅ | ✅ | ❌ SSH/local only | rejected |
| BorgBackup 2.0 | snapshotter | ✅ | ✅ | ✅ | ✅ via borgstore/rclone | rejected — still beta |
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
1.x reaches only SSH and local targets — no Google Drive. [Borg
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

### Why Kopia for service (1)

Against restic, on this specific leg, Kopia wins on three concrete points:

- **Metadata fidelity on a hostile destination.** Kopia stores ownership and
  permissions inside the repository, so restores are faithful regardless of what
  NTFS can express. This is the single biggest reason not to mirror.
- **Cheap repeat runs over NFS.** Its content-addressed local cache means an
  hourly run re-reads almost nothing across the network.
- **Retention as policy, not as a cron flag.** `kopia policy set` attaches
  hourly/daily/weekly/monthly rules to the source path; the snapshot command
  stays a one-liner and cannot drift from the retention intent.

Kopia is also measurably faster than restic on large counts of small files, which
describes `appdata/` well — though at 600 MB that is a tiebreaker, not an
argument.

### Why restic for service (2)

The offsite leg inverts the priorities. Metadata fidelity is equal, speed is
irrelevant at 600 MB daily, and the thing that actually matters is that the path
to Google Drive is *maintained*:

- Kopia's native Drive backend is experimental and requires a GCP service
  account, and its [rclone-backed repository
  commands](https://kopia.io/docs/reference/command-line/common/repository-create-rclone/)
  are marked "not maintained" in Kopia's own documentation.
- restic's rclone backend is first-class, widely deployed, and actively
  maintained. `RESTIC_REPOSITORY=rclone:gdrive:...` is the whole integration.

restic encrypts before anything leaves the house, so Google receives blobs and
never plaintext of `.env` or `docs/CREDENTIALS.md`. Do not additionally wrap it
in an rclone `crypt` remote — double encryption costs CPU and complicates restore
for no gain.

#### "restic + rclone" is one backup tool, not two

A common misreading, so stated plainly: **rclone is a storage driver, not a
backup tool.** restic does all the backup work — chunking, deduplication,
encryption, snapshots, retention. rclone only knows how to speak Google Drive's
API, which restic cannot do natively.

The mechanism matters, because it is the whole reason this path is trusted where
Kopia's is not. restic spawns

```
rclone serve restic --stdio gdrive:nas-backup/restic
```

as a **subprocess and pipes to its stdin/stdout**. No HTTP server, no localhost
port, no TLS certificate, no service listening on the NAS. Kopia's bridge spawns
`rclone serve webdav` and talks HTTPS to it over localhost instead — that extra
indirection is precisely where its [cert path
failures](https://github.com/kopia/kopia/issues/4429) and [startup
timeouts](https://github.com/kopia/kopia/issues/2573) come from.

**rclone is in this design only because the destination is Google Drive.**
Neither Kopia nor restic reaches Drive natively; both reach S3, B2 and R2
natively. Choosing option B in the table below removes rclone from the system
entirely:

| | Backup engine | Storage driver | Binaries needed to restore |
|---|---|---|---|
| Option A — restic → Drive | restic | rclone | restic + rclone |
| Option B — Kopia → B2/R2 | Kopia | *(none)* | kopia |

Counting binaries is not pedantry. Every one of them has to be installed,
version-matched, and working on whatever machine you are rebuilding from — at the
worst possible moment.

Separately, the "two tools, two restore procedures" cost weighed in [Why two
tools rather than one](#why-two-tools-rather-than-one) refers to **Kopia on
leg 1 versus restic on leg 2** — two backup engines across the two services. It
is not about restic and rclone within service (2).

### Why two tools rather than one

The obvious objection is that one tool means one restore procedure to learn and
test. That is a real cost and it is the strongest argument for using restic on
both legs, which remains a reasonable choice.

The counterargument: the dominant failure mode of deduplicating backup tools is
not disk failure, it is repository corruption or a format bug — and deduplication
means a single bad blob can poison many snapshots at once. Two copies in two
independent formats means a Kopia repository bug cannot take the offsite copy
with it. Since the two services already run on different hosts, on different
schedules, against different destinations, format diversity is close to free.

It is only free if **both** restore procedures are actually tested. Two untested
restore paths are strictly worse than one tested one.

### What would change this decision

- **`/mnt/ssd` moving to ZFS or btrfs.** A filesystem snapshot is atomic across
  the whole dataset, which solves the live-SQLite problem structurally — no dump
  step, no `VACUUM INTO`, no staleness guard. That is a better answer than
  anything above, and it is unavailable only because the volume is ext4.
- **Borg 2.0 reaching stable.** Its rclone backend plus Borg's track record would
  make it the strongest single-tool answer for both legs.
- **The dataset growing past ~10 GB.** Both free tiers — Google's 15 GB and
  B2/R2's 10 GB — stop being the sizing assumption, and the calculus becomes
  price per GB rather than which free tier fits. S3-class object storage stays
  cheaper than Drive at that point, and is natively supported by both tools with
  no rclone bridge at all.

## Service (1) — hourly NAS → PC

**Tool: Kopia** — see [Why Kopia for service (1)](#why-kopia-for-service-1).

```
/mnt/nas-ssd  ──kopia snapshot──>  /mnt/f/nas-backup/kopia
   (NFS, read-only)                 (9p → NTFS)
```

### Scheduling

`systemd=true` is already set in `/etc/wsl.conf` on this box, so a timer is the
clean answer:

```ini
[Timer]
OnBootSec=2min          # "when I open WSL" — WSL boot is the trigger
OnUnitActiveSec=1h      # then hourly
Persistent=true         # catch up one missed run if the PC was off
```

`Persistent=true` is the part cron cannot do. Kopia's own built-in scheduler is
the wrong choice here — it requires a long-running `kopia server` process, which
is more moving parts than a timer.

Note: `systemctl is-system-running` currently reports **degraded** on this WSL.
Something already fails at boot. Worth diagnosing before adding units, though it
should not block them.

### Details

- Repository: `/mnt/f/nas-backup/kopia`, filesystem backend.
- Cache: `~/.cache/kopia` on **ext4**, never on `/mnt/f` — a cache on 9p defeats
  the point.
- Source path `/mnt/nas-ssd` — mount must be up; the unit should check and fail
  loudly rather than snapshot an empty directory. An empty snapshot plus a
  retention policy is how you delete a backup by accident.
- Password from a root-owned file, not a compose `.env`.

## Service (2) — daily NAS → Google Drive

```
/mnt/ssd  ──backup tool──>  rclone  ──>  gdrive:nas-backup
 (local)                  (encrypted before leaving the house)
```

The offsite copy holds `.env` and `docs/CREDENTIALS.md`. It must be encrypted
client-side — Google must never receive plaintext. Both candidate tools encrypt
by default, so this is satisfied; do **not** additionally wrap it in an rclone
crypt remote, which would only double the CPU cost and complicate restore.

### Google Drive specifics

- **Create your own OAuth client_id.** rclone's default is shared across
  thousands of users and will throw HTTP 403 rate-limit errors. Google Cloud
  Console → new project → enable Drive API → OAuth credentials.
- The NAS is headless, so the OAuth dance needs `rclone authorize "drive"` run on
  a machine with a browser, then paste the token back. Budget for this step.
- 15 GB free tier vs ~600 MB of data — no storage purchase needed.
- Drive's 750 GB/day upload cap is irrelevant at this scale.

### Open decision: tool for service (2)

The reasoning is in [Why restic for service (2)](#why-restic-for-service-2) and
[Why two tools rather than one](#why-two-tools-rather-than-one). The choice
itself:

| Option | Destination | Pro | Con |
|---|---|---|---|
| **A. restic + rclone** | Google Drive | maintained rclone backend; format diversity against service (1) | second tool, second restore procedure to learn |
| **B. Kopia + native B2/R2** | Backblaze B2 or Cloudflare R2 | one tool end to end, native backend, no bridge | both copies share a repository format |
| C. Kopia + rclone | Google Drive | one tool | rides Kopia's unmaintained WebDAV bridge |
| D. Kopia + native gdrive | Google Drive | one tool, no rclone | **not viable on a personal account** — see below |
| E. restic for both | Google Drive | one tool | gives up Kopia's metadata/cache edge on leg 1 |

**A and B are both good; pick on what you value.** Choose **A** if the offsite
copy must live in Google Drive — an account you already have, already pay for,
and can browse in a web UI. Choose **B** if a single tool and a single restore
procedure matter more than which cloud holds the blobs; it is the cleaner system.

Everything else is a compromise. Decide at review — this changes only
[stage 4](IMPLEMENTATION.md).

### Why Kopia cannot reach Google Drive cleanly

Worth recording, because "just use Kopia for both" is the obvious first instinct
and it fails for non-obvious reasons.

**Native Drive backend (option D) requires a Google service account**, and
service accounts carry their own storage quota — effectively zero on a consumer
account. Uploads fail with `storage quota exceeded` while your own Drive sits
15 GB empty, because the service account, not you, owns the uploaded files
([kopia#2656](https://github.com/kopia/kopia/issues/2656)). The documented fix is
domain-wide delegation to impersonate a real user, which needs a Google Workspace
admin. A personal Gmail account has none. This is a hard blocker, not a rough
edge.

**The rclone bridge (option C)** works by Kopia spawning `rclone serve webdav` as
a subprocess and speaking WebDAV to it over localhost. That indirection is where
the failures live: [`timed out waiting for rclone to
start`](https://github.com/kopia/kopia/issues/2573), [missing WebDAV cert
paths](https://github.com/kopia/kopia/issues/4429), [`PutBlob() failed` against
Drive](https://github.com/kopia/kopia/issues/1698), and `sync-to` transferring
blobs one at a time over HTTP. It works for many people; it is also the code path
Kopia's own docs decline to maintain.

**Neither problem exists on B2, R2, or any S3-compatible target**, which Kopia
speaks natively — that is what makes option B the good version of "use Kopia for
everything". Backblaze B2's free tier is 10 GB and Cloudflare R2's is 10 GB, both
comfortably above this backup set, so option B costs nothing at current size.

The tradeoff option B accepts: both copies become Kopia repositories, so a Kopia
format bug could in principle affect both — the diversity argument in [Why two
tools rather than one](#why-two-tools-rather-than-one) cuts against it. Halving
the operational surface is a fair price for that, and a much better trade than
routing the one off-site copy through an unmaintained bridge.

## Retention

| | hourly | daily | weekly | monthly |
|---|---|---|---|---|
| Service (1) | 24 | 14 | 8 | 12 |
| Service (2) | — | 14 | 8 | 24 |

Service (2) keeps a longer tail because it is the copy that answers "this got
corrupted three months ago and nobody noticed."

## Verification and restore testing

A backup that has never been restored is not a backup — it is an untested
assumption with a cron entry.

- Weekly: `kopia snapshot verify` (service 1), `restic check` (service 2).
- Monthly: `restic check --read-data-subset=5%` — actually reads blob data rather
  than trusting the index.
- **Quarterly, manual:** restore a snapshot to a scratch directory and open the
  restored `radarr.db` with `sqlite3 ... "PRAGMA integrity_check"`. This is the
  only step that proves the SQLite dump logic works end to end. It is also the
  step most likely to get skipped — put it in the calendar, not just this doc.

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

1. `scripts/backup/dump-sqlite.sh` + hourly systemd timer on the NAS; add
   `appdata-dumps/` to `.gitignore`.
2. Verify a dumped `radarr.db` passes `PRAGMA integrity_check` while Radarr runs.
3. Exclusion list, shared by both services, kept in the repo as one file.
4. Service (1): install Kopia, create repo on `/mnt/f`, policy, service + timer.
5. Service (2): install restic + rclone, own OAuth client_id, headless authorize,
   create repo, service + timer.
6. `OnFailure=` handlers and Beszel alerting for both.
7. Restore drill for each, and write the result up as `RESTORE.md` alongside this
   file.

Steps 1–3 are shared groundwork and land first regardless of how the open
decision goes.
