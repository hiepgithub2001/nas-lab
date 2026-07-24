# Remote Access — watching away from home, via Tailscale

By default this stack is reachable only on your own network. This guide adds remote
access using [Tailscale](https://tailscale.com/), so you can watch from anywhere
without exposing anything to the public internet.

For local usage see the [User Guide](USER-GUIDE.md).

## Why Tailscale rather than port forwarding

The obvious approach — forward port 8096 on your router — puts your Jellyfin login
page on the public internet, where it will be found and brute-forced by automated
scanners within days. Given the passwords currently in use (see below), that is not an
acceptable option.

Tailscale instead builds a private encrypted mesh network ("tailnet") between *your
own devices*. Each device gets a stable `100.x.y.z` address that only other devices
signed into your tailnet can reach. Nothing is published publicly, no router
configuration is needed, and it works from behind CGNAT/mobile networks.

> ### Read this before you enable remote access
>
> Every app in this stack currently uses `admin` / `admin123`. That was defensible
> while everything was bound to `localhost` on one machine. Once your server is
> reachable from other devices, the only thing standing between your library and
> anyone who gets onto your tailnet — a shared device, a stolen laptop, a phone you
> lent out — is that password.
>
> **Change the Jellyfin password to something strong before finishing this guide.**
> Jellyfin → **Dashboard → Users → admin → Password**. Then update
> `CREDENTIALS.md`.

## How it will work

```
Your phone (Tailscale on)  ──encrypted tailnet──▶  Home machine  ──▶  Jellyfin :8096
```

Your server joins the tailnet once. Every device you want to watch on also joins.
They then talk directly and privately, wherever they are.

## Which machine runs Tailscale

This is the one real decision, and it matters because of how this particular
environment is set up.

Jellyfin runs in Docker inside WSL2, and this WSL instance uses
**`networkingMode=mirrored`** (confirmed in `/etc/wsl.conf`). In mirrored mode WSL
shares the Windows host's network interfaces rather than sitting behind a private
NAT — the machine's LAN address serves Jellyfin directly, with no `netsh portproxy`
forwarding needed.

That makes **installing Tailscale on Windows** the recommended route:

| | Tailscale on Windows *(recommended)* | Tailscale in a Docker container |
|---|---|---|
| Setup | Installer + browser login | Compose service + auth key |
| Runs when WSL is stopped | Yes | No |
| Survives WSL/Docker restarts | Yes | Needs the stack up |
| Secrets in the repo | None | An auth key to keep out of git |
| Tailnet identity | The whole machine | Jellyfin alone |

Windows wins on being always-on: a media server you have to remember to start is a
media server that is down whenever you actually want it. Take the Docker route only
if you specifically want Jellyfin to be its own tailnet node, or you plan to move this
stack to a Linux host later.

---

## Option A — Tailscale on Windows (recommended)

### 1. Install and sign in on the server

1. Download the Windows client: https://tailscale.com/download/windows
2. Run the installer, then sign in (Google/Microsoft/GitHub account, or email).
   The account you choose *is* your tailnet — use the same one on every device.
3. Once connected, find this machine's tailnet address from the tray icon, or:
   ```
   tailscale ip -4
   ```
   It will look like `100.x.y.z`.

Because WSL is in mirrored networking mode, Jellyfin — already listening on
`0.0.0.0:8096` — is reachable at that address with no further configuration.

### 2. Enable MagicDNS (worth doing)

In the [Tailscale admin console](https://login.tailscale.com/admin/dns), turn on
**MagicDNS**. Your machines then get names instead of numbers, so you can use
`http://<machine-name>:8096` and never care about the IP again.

### 3. Add your other devices

Install Tailscale on each device you want to watch from and sign in with **the same
account**:

- Android — Play Store
- iPhone / iPad / Apple TV — App Store
- Windows / macOS / Linux — https://tailscale.com/download

### 4. Point Jellyfin clients at the server

In the Jellyfin app on a remote device, add the server as:

```
http://<machine-name>:8096      (with MagicDNS)
http://100.x.y.z:8096           (without)
```

Do **not** use `localhost` — on a phone that means the phone itself. Do not use
`192.168.x.x` either; that only works on your home network.

### 5. Verify it actually works remotely

Test from genuinely outside your network — turn Wi-Fi off on your phone and use mobile
data, with Tailscale connected. Testing while still on your home Wi-Fi proves nothing:
it may be succeeding over the LAN rather than the tailnet.

```
tailscale status      # are both devices online and connected?
tailscale ping <machine-name>
```

---

## Option B — Tailscale as a Docker container

Use this if you want Jellyfin to appear on the tailnet as its own node, independent of
the Windows host.

### 1. Create an auth key

In the admin console → **Settings → Keys → Generate auth key**. Enable **Reusable**
and **Ephemeral: off**. Copy it — it is shown once.

### 2. Add it to `.env` (already gitignored)

```
TS_AUTHKEY=tskey-auth-xxxxxxxxxxxx
```

Never commit this. It can add machines to your tailnet.

### 3. Add the service to `docker-compose.yml`

```yaml
  tailscale:
    image: tailscale/tailscale:latest
    container_name: tailscale
    hostname: jellyfin          # becomes the MagicDNS name
    restart: unless-stopped
    environment:
      - TS_AUTHKEY=${TS_AUTHKEY}
      - TS_STATE_DIR=/var/lib/tailscale
      - TS_USERSPACE=false
    volumes:
      - ${CONFIG_ROOT}/tailscale:/var/lib/tailscale
    devices:
      - /dev/net/tun:/dev/net/tun
    cap_add:
      - NET_ADMIN
      - SYS_MODULE
    ports:
      - 8096:8096               # moved here from the jellyfin service
```

Then make Jellyfin share that container's network stack — remove its own `ports:`
block and add:

```yaml
    network_mode: service:tailscale
```

`/dev/net/tun` is present in this WSL kernel (verified), so `TS_USERSPACE=false` works
and you get proper kernel networking rather than the slower userspace fallback.

### 4. Bring it up

```
docker compose up -d tailscale jellyfin
docker compose exec tailscale tailscale status
```

Trade-off to be aware of: with `network_mode: service:tailscale`, Jellyfin no longer
has its own network identity, so if the tailscale container is down Jellyfin is
unreachable even locally.

---

## Streaming quality when away from home

Remote playback is limited by your **home upload** bandwidth, which on most consumer
connections is far smaller than download. A 4K remux at 60+ Mbps will not stream to a
phone on mobile data.

Two ways to deal with it:

- **Lower the quality in the client.** In the Jellyfin app, set a bitrate cap for
  remote playback (e.g. 4–8 Mbps for 1080p). This makes the server transcode down.
- **Prefer smaller sources.** A 1080p WEB-DL streams comfortably where a 2160p remux
  will not — worth remembering when picking releases.

Note that transcoding is **CPU-only** in this setup: no GPU device is passed into the
Jellyfin container, so every transcode is software. One remote 4K→1080p transcode can
saturate a CPU. If remote watching becomes routine, consider passing through a GPU
(QuickSync/NVENC) — a separate piece of work not covered here.

Check your actual upload speed before blaming the setup:

```
speedtest-cli --simple      # or use fast.com from the server's browser
```

## Reaching the other apps remotely

Everything else in the stack is on the same machine, so once Tailscale is up they all
follow — no extra setup:

| App | Remote URL |
|---|---|
| Jellyfin | `http://<machine-name>:8096` |
| Radarr | `http://<machine-name>:7878` |
| Sonarr | `http://<machine-name>:8989` |
| Prowlarr | `http://<machine-name>:9696` |
| qBittorrent | `http://<machine-name>:8080` |

This is genuinely useful — you can queue a film from your phone while out and have it
waiting when you get home. It is also exactly why the weak shared password matters:
these apps can write to your filesystem, and their API keys bypass the login entirely.
Strengthen the passwords.

## What not to do

- **Tailscale Funnel** publishes a service to the *entire public internet*. It exists
  for things you intend to be public. Do not put Jellyfin behind it.
- **Router port forwarding** — the thing Tailscale replaces. Once Tailscale works,
  leave port 8096 closed on your router.
- **Sharing your tailnet login.** To give someone else access, use Tailscale's
  device-sharing feature and a separate Jellyfin user account, not your credentials.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Can't reach `100.x.y.z:8096` | Tailscale disconnected on one end. `tailscale status` on both. |
| Works at home, fails away | You were testing over LAN. Re-test on mobile data with Wi-Fi off. |
| MagicDNS name won't resolve | MagicDNS not enabled, or device needs reconnect. Try the raw `100.x` IP. |
| Connects, playback stalls | Upload bandwidth or CPU transcode limit — cap the client bitrate. |
| Jellyfin says "not allowed" | Check **Dashboard → Networking**: remote access must stay enabled and the IP filter empty (currently `EnableRemoteAccess=True`, filter empty). |

```
tailscale status            # peer list and connection type
tailscale netcheck          # NAT / relay diagnosis
docker compose logs -f jellyfin
```

A connection showing as `relay` rather than `direct` still works, just with more
latency — Tailscale is routing via a DERP relay because neither end could hole-punch.
