# Monitoring — health and alerting

One tool: **Beszel** — a hub (web UI) plus an agent (collector), both small.
It covers host and container health with history and alerting, and is
deliberately minimal: a handful of legible charts rather than a chart tree.

**http://localhost:8091**, or `100.69.57.57:8091` over Tailscale (see
[Remote Access](../REMOTE-ACCESS.md)).

> Beszel's default port is 8090, but on this machine a **Windows** process
> already holds it. WSL forwards `localhost` to Windows, so the port answers
> (HTTP 501) while `ss` shows nothing listening inside WSL — and Docker's bind
> fails with `address already in use`. The hub is published on **8091** instead.

## What it monitors

| Target | How |
|---|---|
| Host CPU, memory, network, load | agent with `network_mode: host` |
| Root disk | `sdd` (the WSL ext4 VHDX) |
| **Media drive** `/mnt/f` | bind-mounted to `/extra-filesystems/mnt-f:ro` |
| All containers, individually | `/var/run/docker.sock:ro` |
| GPU | `henrygd/beszel-agent-nvidia` + nvidia `utility` reservation, `/usr/lib/wsl` |

Extra disks are **not** auto-discovered in Docker — they are monitored by
bind-mounting them under `/extra-filesystems/`. Without that line the media
drive, the one that actually fills up, is invisible while the dashboard stays
green about the healthy root filesystem.

> The agent logs `WARN Device not found in diskstats name=F:\` on startup. That
> is I/O throughput stats being unavailable for a 9p mount — **space usage is
> still collected correctly**, which is the part that matters here.

## First-run setup

The hub needs an account and the agent needs registering. Both are one-time, in
the web UI:

1. Open http://localhost:8091 and create the admin account. Record it in
   `CREDENTIALS.md`.
2. **Add System** → give it a name, set **Host** to `/beszel_socket/beszel.sock`
   and leave the port empty. The hub and agent share that socket through
   `appdata/beszel/socket`, so nothing is exposed on the network.
3. Green in the systems table means connected.

The agent authenticates the hub with the hub's own SSH key, carried in `.env` as
`BESZEL_KEY`. It is generated on the hub's first run at
`appdata/beszel/data/id_ed25519`; the public half is derived with
`ssh-keygen -y -f id_ed25519`. Only the public key goes in `.env`.

## Alerting

Configured per-system in the UI, with thresholds on CPU, memory, disk, bandwidth,
temperature, load average and status. Notifications go out by email or webhook.

**Set the disk alert first.** `/mnt/f` sits at 99%, and it is the failure mode
that actually breaks this stack — downloads and imports fail while every service
keeps answering normally.

### What Beszel cannot alert on

- **The machine being down.** The agent runs on the machine it watches, and this
  stack [starts manually by design](../QUICKSTART.md). Machine-level downtime
  needs a watcher off the box — a dead-man's-switch such as
  [Healthchecks.io](https://healthchecks.io/), where a cron pings out and the
  external service alerts when pings stop.
- **`tailscaled` failing.** See below.

## What was given up, and why

Netdata was installed here first and then swapped out. It monitored strictly
more — but 1,881 charts, config through `docker exec`, collector noise needing
pruning and a privileged container proved to be more instrument than one idle
machine needs. Beszel answers the same day-to-day questions in a fraction of the
interface.

The losses are smaller than expected:

| Concern | Reality |
|---|---|
| systemd units | **Not lost.** 37 units are tracked including `tailscaled`, `postgresql@16-main` and `docker` — but only because the agent mounts the dbus and systemd sockets. Without those it silently reports none. |
| Per-application HTTP checks | Genuinely lost. A container can be "running" while the app inside is hung; only container state and resource usage are tracked. |
| Per-process metrics, journald log viewing | Use `htop` and `journalctl` directly. |

Verified reporting on this machine: CPU, memory, load average, root disk, GPU,
per-container CPU/memory/network for all ten containers, `F:\` at 98.7% via the
extra-filesystems mount, and 37 systemd services.

## Container logs

Beszel does not read logs. That gap is filled at zero cost:

```bash
docker compose logs -f radarr              # one service, live
docker compose logs -f radarr qbittorrent  # interleaved, for correlating
```

Application logs are often better still: the *arr apps write their own rotated
logs to `appdata/<app>/logs/*.txt` with detail the container stream omits —
indexer queries, import decisions, API errors. Jellyfin's ffmpeg transcode logs
are in `appdata/jellyfin/log/` (see [FFmpeg](FFMPEG.md)).

**Dozzle and lazydocker were used here and then removed.** Both were good at
logs specifically, but keeping extra tools to cover a subset of what one tool
shows was not worth the maintenance — including, in Dozzle's case, a
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
