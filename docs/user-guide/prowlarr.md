# Prowlarr — indexer manager

http://localhost:9696 · login in [`CREDENTIALS.md`](../CREDENTIALS.md)

Prowlarr is the search layer. You add indexers (trackers) here **once**, and it pushes
them to Radarr and Sonarr automatically — you never add indexers in those apps
directly. When Radarr searches for a film, it's really asking Prowlarr, which queries
every indexer.

## Adding an indexer

**Indexers → Add Indexer** → pick one → fill any required fields → **Test** → **Save**.

- **Public** indexers (The Pirate Bay, LimeTorrents, Knaben, 1337x) need no account but
  have variable seed quality.
- **Private** trackers need an account and API key/passkey, but have far healthier
  seeders — the real fix for slow downloads.

Once saved, Prowlarr syncs it into Radarr/Sonarr on its own (this connection is set up
once under **Settings → Apps** — see [QUICKSTART](../QUICKSTART.md)).

## FlareSolverr (Cloudflare-protected indexers)

Some sites (1337x, EZTV, Nyaa) sit behind Cloudflare, which rejects Prowlarr's plain
HTTP client and produces an *"SSL connection could not be established / connection
reset"* error. **FlareSolverr** (already running in the stack) is a headless browser
that solves the challenge.

Wire it up once:

1. **Settings → Indexer Proxies → +  → FlareSolverr**
   - **Tags:** `flaresolverr`
   - **Host:** `http://flaresolverr:8191`
   - **Test → Save**
2. Open the Cloudflare-protected indexer → add the **same tag** (`flaresolverr`) →
   **Test → Save**.

The tag is the link: only tagged indexers route through FlareSolverr. Tag just the ones
that need it.

## Connection errors

Prowlarr's error message is generic, but the **last line** tells you the real cause and
therefore the fix:

| Error contains | Cause | Fix |
|---|---|---|
| *"connection reset" / "SSL could not be established"* | Cloudflare | route through [FlareSolverr](#flaresolverr-cloudflare-protected-indexers) |
| *"Name does not resolve"* | domain is dead, or DNS-blocked | different mirror; DNS change only if it resolves on public DNS |
| *"...DNS/SSL issues... IPv6"* | broken IPv6 route | already fixed stack-wide (`sysctls: disable_ipv6` in compose) |
| a `.i2p` address | anonymous I2P network needs an I2P router | skip it — not usable without I2P, and not needed |

### Telling "dead domain" from "DNS-blocked"

Both say *"Name does not resolve"*, but the fix differs. Test the domain against public
DNS:

```
docker exec prowlarr getent hosts <domain>          # your resolver
curl -s "https://1.1.1.1/dns-query?name=<domain>&type=A" -H "accept: application/dns-json"
```

- **NXDOMAIN on public DNS too** → the domain is genuinely dead → use another indexer.
- **Resolves on public DNS but not yours** → your DNS is blocking it → change DNS.

### Choosing a Base URL / mirror

Many indexers offer several mirror domains in a **Base URL** dropdown. Prefer the
**official** domain (e.g. `1337x.to`, not third-party `.cc`/`.ws`/`unblock*` clones —
those come and go and can serve tampered results). Pick from the dropdown rather than
free-typing a mirror.

Note for some regions (e.g. Vietnam): ISPs DNS-block many torrent domains. The "best"
mirror is whichever your ISP hasn't blocked — or switch the containers to Cloudflare
DNS (`1.1.1.1`) to sidestep the blocks entirely.

## Usenet vs torrents

Prowlarr handles both, but Usenet indexers (NZBGeek, etc.) only do the *searching*.
Usenet also needs a paid **provider** (the servers with the data) and a **Usenet
download client** (SABnzbd/NZBGet) — neither is in this stack by default. An indexer
alone downloads nothing. Usenet is faster and dodges the seed lottery, but it's a paid
bundle (~$10–15/mo). Torrents need none of that.
