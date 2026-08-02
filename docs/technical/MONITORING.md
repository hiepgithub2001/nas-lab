# Monitoring — health, logs and alerting

One tool: **Netdata**, running with host namespaces so it monitors the whole
machine rather than just the containers — which matters here, because two of the
services this stack depends on don't run in Docker at all.

| | Where it runs | Netdata sees it |
|---|---|---|
| Jellyfin, *arr apps, qBittorrent | Docker | yes, via cgroups |
| **`tailscaled`** — all remote access | systemd, on the host | yes |
| **`postgresql@16-main`** | systemd, on the host | yes, with a dedicated collector |
| `docker.service`, `containerd` | systemd, on the host | yes |
| RTX 4080 Super | host hardware | yes, via `nvidia-smi` |

A Docker-only tool (Portainer, Dozzle, lazydocker) monitors the first row and is
blind to the rest. If `tailscaled` dies, remote playback dies and nothing
container-scoped notices.

## How it runs

Netdata is a service in `docker-compose.yml`, but it is not a normal container —
it needs to see *past* its own namespace to monitor the host:

| Setting | Why |
|---|---|
| `pid: host`, `network_mode: host` | see host processes and sockets |
| `/proc`, `/sys`, `/` mounted at `/host/...` | host CPU, memory, disks |
| `/run/dbus:ro` | **systemd unit states** — `tailscaled`, `postgresql`, `docker` |
| `/var/run/docker.sock:ro` | container discovery |
| `/usr/lib/wsl:ro` + nvidia `utility` reservation | `nvidia-smi`, so the GPU appears |
| `cap_add: SYS_PTRACE, SYS_ADMIN` | per-process metrics |

That is a privileged deployment — roughly "read-only root on the host". It's the
documented Netdata layout and the price of monitoring a machine from inside it.

Open **http://localhost:19999**, or `100.69.57.57:19999` over Tailscale (see
[Remote Access](../REMOTE-ACCESS.md)). No account, no signup, no Netdata Cloud —
the agent collects and alerts standalone.

> **WSL note.** `/` on WSL is neither a shared nor a slave mount, so the usual
> `ro,rslave` propagation flag on the root bind is **rejected by the daemon** with
> `path / is mounted on / but it is not a shared or slave mount`. A plain `:ro`
> bind works and still yields correct disk metrics.

> **GPU note.** Environment variables alone are not enough — without the
> `deploy.resources.reservations.devices` block the container has no `nvidia-smi`
> and the GPU section silently never appears. Same lesson as
> [the Jellyfin outage](../incidents/2026-07-29-jellyfin-gpu-transcode-outage.md);
> `/usr/lib/wsl` is mounted live for the same reason as
> [the driver-libs incident](../incidents/2026-07-30-wsl-driver-libs-stale.md).

## What it actually sees here — verified

| Target | Chart | Status |
|---|---|---|
| Media drive | `disk_space./mnt/f` | monitored — 919.6 GB used / 11.9 GB free |
| Tailscale | `systemdunits_service-units.unit_tailscaled_service_state` | active |
| PostgreSQL | `unit_postgresql@16-main_service_state` | unit state yes, DB metrics need setup (below) |
| All 9 containers | `cgroup_<name>.cpu` etc. | discovered automatically |
| GPU | `nvidia_smi.gpu_*` incl. **encoder/decoder utilization** | working |

The NVENC/NVDEC charts are worth knowing about: they show transcode load directly,
replacing the manual `nvidia-smi` check in [Transcoding](TRANSCODING.md).

`/mnt/f` being a `v9fs` (9p) mount turned out **not** to be excluded by the
diskspace plugin's default filters — it is monitored without any configuration.

### PostgreSQL metrics need a database user

The collector finds the server but cannot authenticate, so you get unit state but no
query/connection metrics. To enable them, create a read-only user:

```sql
CREATE USER netdata PASSWORD '<pick-one>';
GRANT pg_monitor TO netdata;
```

then add it to `go.d/postgres.conf` via
`docker exec -it netdata /etc/netdata/edit-config go.d/postgres.conf`. Skip this if
you don't care about database internals — the unit-state alarm still fires if
PostgreSQL dies.

## Alerting

Hundreds of alarms ship enabled and pre-tuned — disk space with predicted
time-to-full, inode exhaustion, RAM and swap pressure, network errors, systemd
units entering a failed state, container states, and PostgreSQL specifics. You do
not write rules.

Notifications are dispatched by the agent itself (no cloud), configured in one file:

```bash
docker exec -it netdata /etc/netdata/edit-config health_alarm_notify.conf
docker restart netdata
```

27 integrations are supported. **Telegram** is the natural choice here since a bot
already exists for this setup — set `SEND_TELEGRAM="YES"`, then `TELEGRAM_BOT_TOKEN`
and `DEFAULT_RECIPIENT_TELEGRAM` (your chat ID).

Expect a **critical disk alarm immediately** once `/mnt/f` is monitored. At 99% that
is correct, not a misconfiguration.

### Prune the noise in week one

WSL exposes no thermal sensors and presents virtual network and disk devices that
look abnormal to alarms written for bare metal. Left alone, Netdata will cry wolf,
you will mute the channel, and the disk alert you actually needed will arrive
somewhere you have stopped reading.

Already observed in this install's logs, all harmless but all noise:

- `python.d` modules failing to load (`haproxy`, `pandas`, `traefik`) — missing
  Python deps for collectors nothing here uses
- `libreswan` / `opensips` check failures — services that don't exist
- the `maxscale` collector false-matching **Sonarr on :8989** and failing to parse
  its HTML

Disable what isn't real, early:

```bash
docker exec -it netdata /etc/netdata/edit-config health.d/<alarm-family>.conf
# set:  to: silent
docker restart netdata
```

Keep: disk space, memory, systemd unit state, container state, PostgreSQL.

### What Netdata cannot alert on: itself

Netdata runs *on* the machine it watches. This stack
[starts manually by design](../QUICKSTART.md) and WSL stops when Windows sleeps or
shuts down — so the agent is down at precisely the moments you would most want to
hear from it. **It can never tell you the server is offline.**

That is also why an uptime monitor *on this host* (Uptime Kuma and similar) is
pointless, and it was tried and removed here for that reason.

Machine-level downtime requires a watcher **off the box** — a dead-man's-switch such
as [Healthchecks.io](https://healthchecks.io/): a cron job pings out on a schedule,
and the external service alerts you when the pings stop. Roughly ten minutes to set
up, and the only architecture that catches "the whole machine is gone".

## Container logs

Netdata reads journald — so host services (`tailscaled`, `postgresql`, `docker`) are
covered — but it does **not** read Docker container logs. That gap is filled at zero
cost:

```bash
docker compose logs -f radarr              # one service, live
docker compose logs -f radarr qbittorrent  # interleaved, for correlating
```

Application logs are often better still: the *arr apps write their own rotated logs
to `appdata/<app>/logs/*.txt` with the detail the container stream omits — indexer
queries, import decisions, API errors. Jellyfin's ffmpeg transcode logs are in
`appdata/jellyfin/log/` (see [FFmpeg](FFMPEG.md)).

**Dozzle and lazydocker were used here and then removed.** Both are good, but both
are Docker-only, and keeping a second and third tool to cover a subset of what
Netdata already shows was not worth the maintenance — including, in Dozzle's case, a
root-equivalent Docker socket mount.

## Why not Kubernetes

The obvious-looking path to "nicer logs" is k3s + [K9s], since K9s is the TUI most
people have seen. It is the wrong trade here:

- K9s is a **Kubernetes client**. It cannot talk to Docker, so using it means
  running a cluster.
- The GPU passthrough would have to be rebuilt on the NVIDIA device plugin, and the
  `/usr/lib/wsl` mount rederived as a hostPath — the most fragile part of this
  stack, and the subject of two [incidents](../incidents/).
- Tailscale access works precisely because containers publish to `0.0.0.0` in the
  host namespace. Under Kubernetes that becomes NodePort or an Ingress controller,
  and the "every service comes along for free" property is lost.
- `kubectl logs` **loses history on pod restart**, so log aggregation would still
  have to be added afterwards — Kubernetes doesn't solve the original problem.

[K9s]: https://k9scli.io/

## Log rotation

Docker's `json-file` driver keeps logs **forever** by default. A crash-looping
container can quietly fill the WSL ext4 root. `docker-compose.yml` therefore sets a
shared cap via a YAML anchor:

```yaml
x-logging: &default-logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

Each service references it with `logging: *default-logging` — 30 MB per container,
~240 MB worst case for the whole stack. Verify it applied:

```bash
docker inspect jellyfin --format '{{.HostConfig.LogConfig.Config}}'
# -> map[max-file:3 max-size:10m]
```

This only takes effect when a container is **recreated**, not restarted.

## Quick health check by hand

Without opening anything:

```bash
for p in 8096 7878 8989 9696 6767 8080 8191; do
  printf "%s -> %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://localhost:$p/)"
done
df -h /mnt/f /
systemctl is-active tailscaled docker
```

`200` or `302` is healthy (`302` is a redirect to a login page). `000` means nothing
is listening — the container is down or still starting.
