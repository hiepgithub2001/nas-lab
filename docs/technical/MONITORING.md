# Monitoring — health checks and live logs

Two tools cover "is it up?" and "why did it do that?" without changing how the
stack runs — one container, one binary on the host.

| Tool | Where | Answers |
|---|---|---|
| **Dozzle** | http://localhost:8081 | live logs from every container, in a browser |
| **lazydocker** | terminal — `lazydocker` | logs + CPU/RAM/restart controls in one TUI |

Dozzle is also reachable over Tailscale at `100.69.57.57:8081` — so you can check
the stack from a phone (see [Remote Access](../REMOTE-ACCESS.md)).

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

For eight containers on one node with one user, lazydocker and Dozzle deliver the
whole benefit at zero architectural cost.

[K9s]: https://k9scli.io/

## Dozzle — live logs in a browser

Streams stdout/stderr from every container, with search, filtering, and multi-container
split view. No configuration; it reads the Docker socket and discovers everything.

Useful because the alternative — `docker compose logs -f radarr` — can't be read from
a phone and doesn't survive closing the terminal.

> **Security note.** Dozzle mounts `/var/run/docker.sock` read-only. Read-only
> prevents writes through that socket, but socket access is still effectively root
> on the host. This is acceptable *only* because nothing here is published beyond
> the LAN and the tailnet. Do not expose port 8081 publicly.

### Container logs vs application logs

Two different streams, and the one you want depends on the question:

- **Container logs** (Dozzle, `docker logs`) — startup, crashes, permission errors,
  anything the process wrote to stdout. First stop when a container won't stay up.
- **Application logs** (`appdata/<app>/logs/*.txt`) — the *arr apps write their own
  rotated logs, e.g. `appdata/radarr/logs/radarr.0.txt`. These carry the detail
  Dozzle won't show: indexer queries, import decisions, API errors. Often easier to
  `grep` than the container stream.

Jellyfin is the same: `appdata/jellyfin/log/` holds the ffmpeg transcode logs, which
is where transcode failures actually explain themselves (see [FFmpeg](FFMPEG.md)).

## Why there is no uptime monitor

Uptime Kuma was set up here and then deliberately removed. It is the obvious next
addition, so the reasoning is worth recording:

- **Everything is up or down together.** One compose project on one machine — seven
  HTTP monitors report roughly one bit of information.
- **The expected state after a reboot is all-red.** This stack
  [starts manually by design](../QUICKSTART.md), so an uptime monitor alerts every
  time the PC restarts. Alerts that fire on normal behaviour get ignored.
- **It watches the thing that doesn't break and misses the thing that does.** The
  real failure mode here is the media drive filling up: downloads and imports start
  failing while every service happily keeps answering HTTP 200.

If alerting is ever wanted, the version worth building is a **disk-space** alarm —
a cron job pushing a heartbeat only while free space is above a threshold, with the
notification going to Telegram — not a set of HTTP checks.

## lazydocker — the terminal view

```
lazydocker
```

Installed to `~/.local/bin/lazydocker`. Panes for containers, images, volumes;
arrow keys to select, and the right pane shows live logs, CPU/memory graphs, and the
config. `r` restarts a container, `d` removes, `[`/`]` switch tabs.

This is the fastest way to answer "which container is eating RAM" or "restart Bazarr"
without remembering compose syntax.

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
~300 MB worst case for the whole stack. Verify it applied:

```bash
docker inspect jellyfin --format '{{.HostConfig.LogConfig.Config}}'
# -> map[max-file:3 max-size:10m]
```

Note this only takes effect when a container is **recreated**, not restarted.

## Quick health check by hand

When you just want a yes/no across the board without opening anything:

```bash
for p in 8096 7878 8989 9696 6767 8080 8191 8081; do
  printf "%s -> %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://localhost:$p/)"
done
```

`200` or `302` is healthy (`302` is a redirect to a login page). `000` means nothing
is listening — the container is down or still starting. The same check, plus GPU and
Tailscale verification, is what the `start-media-stack` skill runs.

## What this does not cover

Nothing here watches **disk space**, which is currently the stack's real constraint —
the media drive runs near full, and a full disk breaks downloads and imports long
before it breaks a health check. Until that has headroom, check it directly:

```bash
df -h /mnt/f /
```
