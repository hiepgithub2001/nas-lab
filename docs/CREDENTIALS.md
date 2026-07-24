# Local credentials

**Not committed to git** (ignored via `.gitignore`). Local-only reference.

## Local services

All web UIs use the same login: **`admin` / `admin123`**

| Service      | URL                     | Username | Password   | Verified |
|--------------|-------------------------|----------|------------|----------|
| qBittorrent  | http://localhost:8080   | `admin`  | `admin123` | yes      |
| Radarr       | http://localhost:7878   | `admin`  | `admin123` | yes      |
| Sonarr       | http://localhost:8989   | `admin`  | `admin123` | yes      |
| Prowlarr     | http://localhost:9696   | `admin`  | `admin123` | yes      |
| Bazarr       | http://localhost:6767   | —        | —          | no auth set yet    |
| Jellyfin     | http://localhost:8096   | `admin`  | `admin123` | yes      |
| FlareSolverr | http://localhost:8191   | —        | —          | no auth (API only) |
| Recyclarr    | CLI only                | —        | —          | no UI    |

## External accounts

Third-party services on the public internet, used by Bazarr for subtitle downloads.

| Service              | URL                          | Username | Password   |
|----------------------|------------------------------|----------|------------|
| OpenSubtitles.com    | https://www.opensubtitles.com | `admin`  | `admin123` |

> **These are not like the local logins above.** The local apps are safe with a weak
> shared password only because they are bound to `localhost` on this machine. This
> account lives on the public internet, where the password is exposed to credential
> stuffing and automated attacks — the `localhost` justification does not apply.
> If `admin123` is reused on any account you would mind losing, change it here.
>
> Practical impact if this account is taken: subtitle downloads stop working and the
> account may be rate-limited or banned. Nothing in this stack is compromised, since
> Bazarr only ever *reads* from the provider.

Note: OpenSubtitles requires logging in on their website once before the API accepts
requests, and the free tier has a daily download quota.

## API keys

Used for app-to-app calls (Prowlarr → Radarr/Sonarr, Recyclarr → Radarr/Sonarr).
These are separate from the login password and act as full-access tokens.

| Service  | API key                            |
|----------|------------------------------------|
| Radarr   | `6cc098f0ad7d411d8919bdacd3c2a85a` |
| Sonarr   | `312c9d0190bc4813a4b2e273c73e4020` |
| Prowlarr | `94ddacac299c4d65b260f2110fa1b018` |
| Bazarr   | `d6e7bd6213692e78782aa0d634bac173` |
| Jellyfin | `3f9e151673bc446eab5fd83f752e9728` (created for Radarr/Sonarr library-update notifications) |

Re-read them from disk at any time:

```
grep -o '<ApiKey>[^<]*</ApiKey>' appdata/{radarr,sonarr,prowlarr}/config.xml
```

## Notes

- `admin123` is acceptable for the **local services** only because every port above is
  bound to `localhost` on this machine. Before exposing any of these to the internet
  (reverse proxy, port forward, VPN-less remote access), change to strong unique
  passwords — these apps have filesystem access and the API keys bypass the login
  entirely. The same reasoning does **not** cover the external accounts section.
- Bazarr currently has no authentication (`auth.type = None`); its web UI is open to
  anyone who can reach port 6767, though its API is still key-protected. Set it under
  **Settings → General → Security** before enabling remote access.
- qBittorrent's password is stored in its own config; if it ever reverts to a
  generated temporary one, recover it with:
  ```
  docker logs qbittorrent | grep -A2 "temporary password"
  ```
- Radarr/Sonarr/Prowlarr all use `Forms` authentication with
  `AuthenticationRequired: Enabled`.
