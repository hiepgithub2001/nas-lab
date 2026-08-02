# Monitoring — health, logs and alerting

One tool: **Netdata**, installed natively on the WSL host. It covers the whole
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

## Install

Needs root, so run it yourself:

```bash
wget -O /tmp/netdata-kickstart.sh https://get.netdata.cloud/kickstart.sh \
  && sudo sh /tmp/netdata-kickstart.sh --stable-channel --disable-telemetry
```

Then open **http://localhost:19999**. It is also reachable over Tailscale at
`100.69.57.57:19999` (see [Remote Access](../REMOTE-ACCESS.md)). No account and no
Netdata Cloud signup is required — the agent runs and alerts entirely standalone.

Nothing needs configuring for discovery: it finds the containers, the systemd
units, PostgreSQL, the disks and the GPU on its own.

## Verify it sees what matters

Netdata's defaults are generous but not guaranteed to match this machine. Check
these three specifically, because each is a place where the useful signal could be
missing while the dashboard still looks healthy:

1. **`/mnt/f` appears under Disks.** This is the media drive — 932 GB, currently
   99% full — and it is a **`v9fs`** (9p) mount, not a normal block device. If it
   is absent from the disk-space charts, add its filesystem type back in:

   ```bash
   sudo /etc/netdata/edit-config netdata.conf
   # under [plugin:proc:diskspace], check the two `exclude space metrics on ...`
   # patterns and make sure neither matches /mnt/f or v9fs
   ```

   Getting this wrong produces the worst possible outcome: confident green alerting
   on the 1 TB root filesystem (6% used) and silence on the drive that is actually
   full.

2. **The GPU section exists.** Requires `nvidia-smi`, which works under WSL. See
   [GPU passthrough](GPU-WSL-PASSTHROUGH.md) if it's missing.

3. **`tailscaled` and `postgresql` appear as systemd units**, so a failed state
   raises an alarm.

## Alerting

Hundreds of alarms ship enabled and pre-tuned — disk space with predicted
time-to-full, inode exhaustion, RAM and swap pressure, network errors, systemd
units entering a failed state, container states, and PostgreSQL specifics. You do
not write rules.

Notifications are dispatched by the agent itself (no cloud), configured in one file:

```bash
sudo /etc/netdata/edit-config health_alarm_notify.conf
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

Disable what isn't real, early:

```bash
sudo /etc/netdata/edit-config health.d/<alarm-family>.conf   # set: to: silent
sudo systemctl restart netdata
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
