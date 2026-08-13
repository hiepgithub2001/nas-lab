# Personal cloud — Immich and Nextcloud

Two services replacing the commercial equivalents: **Immich** for photos and
video (Google Photos), **Nextcloud** for files, calendar and contacts (Drive,
iCloud). Both run on the NAS from `docker-compose.cloud.yml`, reachable over the
tailnet.

| | Immich | Nextcloud |
|---|---|---|
| URL (LAN + tailnet) | `http://ubuntu-2404:2283` | `https://ubuntu-2404:8081` (self-signed) |
| Containers | server, machine-learning, valkey, postgres | app, postgres, redis |
| User data | `/mnt/hdd/cloud/immich` | `/mnt/hdd/cloud/nextcloud` |
| Database | `appdata/immich/postgres` (SSD) | `appdata/nextcloud/postgres` (SSD) |
| Mobile app | Immich (iOS/Android) | Nextcloud + DAVx5 |

```bash
docker compose -f docker-compose.cloud.yml up -d      # start
docker compose -f docker-compose.cloud.yml ps         # status
docker compose -f docker-compose.cloud.yml logs -f immich-server
```

## A separate Compose project, and why that matters

This stack declares `name: nas-cloud`; the media stack declares `name: nas-lab`.
Both compose files sit in the same directory, and Compose otherwise derives the
project name *from the directory* — so without those two lines both files would
claim the project `nas-lab`, each would consider the other's seven containers
orphans, and a single `docker compose --remove-orphans` on either would delete
the other stack. The names are load-bearing. Don't remove them.

The practical benefit is independent lifecycles: `docker compose down` on the
media stack to restart Radarr does not take your photo library offline.

## Storage layout

The two disks do different jobs, and these services are split across both.

```
/mnt/ssd  (815 GB)   appdata/immich/postgres      Immich database
                     appdata/nextcloud/postgres   Nextcloud database
                     appdata/nextcloud/config     Nextcloud app + config

/mnt/hdd  (9.1 TB)   cloud/immich/                photo originals, thumbs, video
                     cloud/nextcloud/             user files
```

Databases go on the SSD because they are random-I/O and small. Bulk user data
goes on the HDD because it grows without bound and is read sequentially.

**Immich's `/data` is one tree and cannot be split.** It holds `upload/`,
`library/`, `thumbs/`, `encoded-video/` and `profile/`, and Immich moves files
between `upload/` and `library/` with `rename()`, which is not atomic across
filesystems. So the thumbnails live on the HDD next to the originals even though
the SSD would serve them faster. Mounting subdirectories separately is not
supported and will corrupt the library.

## Backups — read this before trusting the setup

The existing backup system quiesces SQLite into `appdata-dumps/` and snapshots
the result with Kopia. Postgres needed its own path, for a reason worth stating
plainly: **a Kopia snapshot of these two services' live database directories
would have failed the backup outright.**

The data directories are `0700` owned by uid 999, the container's `postgres`
user. Service (2) runs as 1001 on the NAS, and the NFS export squashes every
client UID — root included — to 1001 for service (1). So neither leg can read
them, and Kopia treats an unreadable entry as a fatal error: the unit would have
failed on every single run. Even with permission, a file-level copy of a running
cluster captures torn pages and a WAL from a different moment; it is not a
backup, and Immich's documentation says as much.

So:

- Both live directories are excluded in `scripts/backup/excludes.txt`.
- `scripts/backup/dump-postgres.sh` runs hourly at `:45` and writes
  `pg_dump --clean --if-exists | gzip` into
  `appdata-dumps/postgres/current/`, published atomically, with a `.stamp`.
- `scripts/backup/guard-source.sh` refuses to snapshot if that dump set is
  missing or older than two hours — the same rule already applied to SQLite.
- Kopia then backs up the dumps, on both legs, like everything else.

Install the timer once, on the NAS:

```bash
sudo cp /mnt/ssd/nas-lab/scripts/backup/systemd/nas-dump-postgres.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nas-dump-postgres.timer
sudo systemctl start nas-dump-postgres.service    # prime the first dump set
```

Verify:

```bash
ls -la /mnt/ssd/nas-lab/appdata-dumps/postgres/current/
systemctl list-timers nas-dump-postgres.timer
```

### Restoring a database

```bash
# Immich
docker compose -f docker-compose.cloud.yml up -d immich-postgres
zcat /mnt/ssd/nas-lab/appdata-dumps/postgres/current/immich.sql.gz \
  | docker exec -i immich-postgres psql --username=immich --dbname=immich
docker compose -f docker-compose.cloud.yml up -d

# Nextcloud — same shape
zcat /mnt/ssd/nas-lab/appdata-dumps/postgres/current/nextcloud.sql.gz \
  | docker exec -i nextcloud-postgres psql --username=nextcloud --dbname=nextcloud
```

The dumps carry `--clean --if-exists`, so they drop and recreate what they own
and can be restored over a non-empty database.

**A database restore alone is not a restore.** Immich's database holds metadata
that points at files under `/mnt/hdd/cloud/immich`; Nextcloud's holds the file
index for `/mnt/hdd/cloud/nextcloud`. Both trees are on the HDD, which the Kopia
snapshots of `/mnt/ssd` **do not cover** — the backup roots are the SSD on the
NAS and the NFS mount on the PC. Restoring the database against missing files
gives you a working app with a library of broken thumbnails.

> **Open item:** bulk user data on `/mnt/hdd` is not currently in either backup
> leg. Photos are the most irreplaceable data on this machine, and right now
> only their metadata is protected. Adding `/mnt/hdd/cloud` as a second Kopia
> snapshot source is the fix; it was left out of this change because 8.2 TB of
> headroom on the Drive destination is a sizing question worth deciding
> deliberately rather than by default.

## Remote access — direct ports over the tailnet

Both containers bind `0.0.0.0`, and Tailscale runs on the host, so both are
reachable from any device on the tailnet with no proxy in between:

```
Immich      http://ubuntu-2404:2283
Nextcloud   https://ubuntu-2404:8081     (self-signed — expect a warning)
```

Nothing is published to the internet. There is no port forwarding on the router,
and Tailscale's `funnel` — the feature that *would* publish a service publicly —
is not enabled.

### Why there is no `tailscale serve` here

It was set up on 2026-08-13 with real Let's Encrypt certificates on `:443` and
`:8443`, verified working, and **turned off the same day** by preference for
fewer moving parts. `tailscale serve status` reports no config. Reinstating it:

```bash
sudo tailscale serve --bg --https=443 http://localhost:2283
sudo tailscale serve --bg --https=8443 https+insecure://localhost:8081
```

Two things are worth knowing before deciding either way.

**HTTPS here was never about encryption.** Tailscale carries everything over
WireGuard, so tailnet traffic is already encrypted end to end before it reaches
the network — TLS on top is encryption inside encryption. What the certificate
bought was **client software behavior**: Nextcloud's desktop and mobile sync
clients refuse an untrusted certificate without a manually pinned exception on
every device, and browsers gate "secure context" features (service workers, PWA
install, clipboard, Web Crypto) on trusted HTTPS regardless of the tunnel
underneath, which costs Immich its installable web app. **Turning serve off does
not make Nextcloud plain HTTP** — the LinuxServer image only listens on 443 and
keeps serving its own self-signed certificate, so the practical effect is a
browser warning to click through and an exception to pin per sync client.

**`serve` and `funnel` are one word apart.** `serve` is tailnet-only; `funnel`
takes identical arguments and publishes the same endpoint to the public
internet — no login, no tailnet membership, anyone with the URL. Funnel needs
both an ACL grant and an explicit command, so it cannot happen by accident, but
if outside access is ever wanted, the answer is a reverse proxy with
authentication decided on purpose, never `funnel` on a photo library.

> **One thing that cannot be undone:** the certificates issued on 2026-08-13 are
> permanently recorded in public
> [Certificate Transparency](https://certificate.transparency.dev/) logs, so
> `ubuntu-2404.tail9dbb76.ts.net` is publicly searchable as a string. That
> reveals a hostname, not an address or any access — the name resolves only
> inside the tailnet. Switching *Enable HTTPS* back off at
> [login.tailscale.com/admin/dns](https://login.tailscale.com/admin/dns) stops
> future issuance; it cannot retract what is already logged.

## How Nextcloud was installed

**Not through the web wizard.** The wizard defaults to SQLite, and accepting
that default would have quietly stranded the Postgres container — the app would
work, and none of the Postgres backup machinery above would be protecting
anything. It was installed from the CLI against Postgres instead:

```bash
docker exec nextcloud occ maintenance:install \
  --database=pgsql --database-host=nextcloud-postgres \
  --database-name=nextcloud --database-user=nextcloud \
  --database-pass="$NEXTCLOUD_DB_PASSWORD" \
  --admin-user=admin --admin-pass='<generated>' --data-dir=/data
```

Nextcloud's installer then created **its own database role, `oc_admin`**, with a
password it generated and stored in `config.php` — it does not use the
`nextcloud` role for day-to-day queries. That is normal, and it does not affect
the backup: `dump-postgres.sh` connects as `nextcloud`, which owns the cluster
and can dump objects owned by `oc_admin`. It does mean `config.php` holds a
credential that exists nowhere else, so a restore needs both the SQL dump *and*
`appdata/nextcloud/config/` — both are in the snapshot.

### Configuration applied

```bash
docker exec nextcloud occ config:system:set memcache.local --value='\\OC\\Memcache\\Redis'
docker exec nextcloud occ config:system:set memcache.locking --value='\\OC\\Memcache\\Redis'
docker exec nextcloud occ config:system:set memcache.distributed --value='\\OC\\Memcache\\Redis'
docker exec nextcloud occ config:system:set trusted_domains 1 --value=ubuntu-2404
docker exec nextcloud occ config:system:set trusted_domains 2 --value=ubuntu-2404.tail9dbb76.ts.net
docker exec nextcloud occ config:system:set trusted_domains 3 --value=100.67.117.59
docker exec nextcloud occ config:system:set trusted_proxies 0 --value=172.16.0.0/12
docker exec nextcloud occ config:system:set overwrite.cli.url --value=https://ubuntu-2404:8081
```

> **Double the backslashes.** `--value='\OC\Memcache\Redis'` loses one level of
> escaping on the way through `docker exec` and lands in `config.php` as the
> string `OCMemcacheRedis`. Nextcloud then throws
> `Memcache OCMemcacheRedis not available for local cache` on *every* `occ`
> invocation, including the one that would undo it — leaving you editing
> `appdata/nextcloud/config/www/nextcloud/config/config.php` by hand to recover.

The tailnet IP is a `trusted_domain` alongside the names because Nextcloud
matches on the `Host` header and answers **HTTP 400** to anything unlisted — a
client that reaches the NAS by IP rather than by MagicDNS name gets a bare
"Access through untrusted domain" page otherwise.

`trusted_proxies` makes Nextcloud read the forwarded client IP instead of
logging every request as coming from the Docker bridge; without it a few failed
logins from one device rate-limit *everyone* through brute-force protection.

There is deliberately **no `overwritehost` or `overwriteprotocol`**. Both would
pin every generated URL to the tailnet name and break LAN access on
`https://ubuntu-2404:8081`. They are unnecessary here because the LinuxServer
image serves HTTPS itself, so the connection is HTTPS end to end and Nextcloud
already sees the right scheme. `overwrite.cli.url` is set because CLI-generated
links (emails, notifications) have no request to infer a host from.

## Credentials

Database passwords live in `.env` (untracked) as `IMMICH_DB_PASSWORD` and
`NEXTCLOUD_DB_PASSWORD`. They are read only at container start.

**Changing a password in `.env` after first run does not change it in the
database.** Postgres sets the password from the environment during `initdb`, on
the first start against an empty data directory, and ignores it forever after.
Editing `.env` afterwards only breaks the app's ability to log in. To rotate,
change it in both places:

```bash
docker exec -it immich-postgres psql -U immich -c "ALTER USER immich PASSWORD 'new';"
# then update IMMICH_DB_PASSWORD in .env and recreate the stack
```

Nextcloud's admin account was created during the CLI install above (user
`admin`); Immich's is created on first visit to its web UI. Record both in
`docs/arr-servers/CREDENTIALS.md` alongside the rest, and change the generated
Nextcloud admin password from Settings → Personal → Security.

## Upgrades

Immich's server and database images are **version-locked** — the Postgres image
ships the VectorChord extension at a version the server expects — so they move
together. Take both lines from the upstream release file rather than bumping one:

```bash
curl -sL https://github.com/immich-app/immich/releases/latest/download/docker-compose.yml \
  | grep -E 'immich-server:|immich-machine-learning:|postgres:|valkey:'
```

`IMMICH_VERSION` in `.env` is pinned to a major track (`v3`) rather than
`release`, so a routine `docker compose pull` cannot walk across a breaking
upgrade unattended. Read Immich's release notes before moving it.

Nextcloud upgrades **one major at a time** and will refuse to skip. The
`postgres:18-alpine` pin is likewise deliberate: Postgres will not start against
a data directory written by a different major version, so moving majors means a
planned `pg_upgrade`, not a `docker compose pull`.

### The two Postgres services mount different paths

This looks like a bug in `docker-compose.cloud.yml` and is not:

```yaml
immich-postgres:    ${CONFIG_ROOT}/immich/postgres:/var/lib/postgresql/data
nextcloud-postgres: ${CONFIG_ROOT}/nextcloud/postgres:/var/lib/postgresql
```

The official image changed the convention in **Postgres 18**: the mount moves up
one level and the cluster lands in a version-named subdirectory
(`18/docker/`), so a later `pg_upgrade --link` can see both majors inside one
mount point. Immich's database image is built on Postgres 14 and still expects
the old path. Mounting an 18 the old way makes it refuse to start with
*"PostgreSQL data in /var/lib/postgresql/data (unused mount/volume)"* — which is
exactly how this was found during the first bring-up.
