# Local credentials

> ## ⚠️ THIS FILE IS PUBLIC
>
> It is **tracked in git and pushed to a public GitHub repository**, and has been
> since commit `aca5c07` on **2026-07-25**. Anyone can read it, unauthenticated,
> at `raw.githubusercontent.com/hiepgithub2001/nas-lab/main/docs/arr-servers/CREDENTIALS.md`.
>
> This header previously claimed the file was ignored via `.gitignore`. That was
> wrong — the entry in `.gitignore` is commented out (`# CREDENTIALS.md`), so it
> never took effect. Corrected 2026-08-13.
>
> **Every value below should be treated as compromised and rotated**, in this
> order of urgency:
> 1. **OpenSubtitles.com** — a real account on the public internet, facing
>    credential stuffing. If `admin123` is reused on email or anything else,
>    change those first.
> 2. **The five API keys** — full-access tokens that bypass the login entirely.
>    Regenerate from each app's Settings → General.
> 3. The local service passwords.
>
> Untracking the file (`git rm --cached` plus uncommenting the `.gitignore`
> line) stops future commits, but the file remains readable in git history and
> in any clone or cache already made. Rotation is the only real fix.

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

## Personal cloud (docker-compose.cloud.yml)

Added 2026-08-13. Unlike the media stack above, these bind `0.0.0.0` rather than
`localhost`, so they are reachable from the LAN and the tailnet — the
"localhost-only" justification for `admin123` in the Notes section does **not**
apply to these two.

| Service   | URL                          | Username             | Password   | Verified |
|-----------|------------------------------|----------------------|------------|----------|
| Immich    | http://ubuntu-2404:2283      | `admin@immich.local` | `admin123` | yes      |
| Nextcloud | https://ubuntu-2404:8081     | `admin`              | `admin123` | yes      |

- **Immich logs in by email, not username** — there is no username field, so the
  login is `admin@immich.local`. The address is local-only and sends no mail.
- Immich sets `shouldChangePassword` on the admin account at signup, so it will
  prompt on first login. Dismissible.
- Nextcloud rejected `admin123` under its default password policy (under 10
  characters, in the top-1,000,000 list, and present in breach databases). To
  apply it, three checks were relaxed **for all Nextcloud accounts**:

  ```
  occ config:app:set password_policy minLength --value=6
  occ config:app:set password_policy enforceNonCommonPassword --value=0
  occ config:app:set password_policy enforceHaveIBeenPwned --value=0
  ```

  Restore with `10`, `1`, `1` respectively.
- Nextcloud's own installer created a separate database role, `oc_admin`, with a
  generated password stored in
  `appdata/nextcloud/config/www/nextcloud/config/config.php`. That file is **not**
  tracked (`appdata/` is ignored) and the password appears nowhere in this repo.
- Database passwords for both services live in `.env` as `IMMICH_DB_PASSWORD` and
  `NEXTCLOUD_DB_PASSWORD`. `.env` is genuinely ignored — verified, unlike this
  file's former claim.

## External accounts

Third-party services on the public internet, used by Bazarr for subtitle downloads.

| Service              | URL                          | Username | Password   |
|----------------------|------------------------------|----------|------------|
| OpenSubtitles.com    | https://www.opensubtitles.com | `admin`  | `admin123` |
| VietMediaF           | (private tracker)             | `hiep622032001` | `hiep@vF1002` |
| NetHD (VietTorrent)  | https://nethd.org             | `lehiep2203`    | `hiep@nD1002` |

VietMediaF login email: `hiep622032001@gmail.com`. Private Vietnamese-language
torrent tracker for movies/TV, invite-only — this account was not self-registered
via a public signup form. Not yet wired into Prowlarr as an indexer.

NetHD login email: `hiep622032001@gmail.com`. Semi-private Vietnamese-language
tracker for HD movies/TV. **Added to Prowlarr as indexer id 64 on 2026-08-19** —
connection tested and a live search returned results, so the credentials are
confirmed working. This is the stack's only Vietnamese-content indexer.

- Set **Torrents per page: 100** on the NetHD account profile; the Prowlarr
  definition expects it for complete result pages.
- NetHD deletes accounts after **2 years of inactivity**.

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

- **The premise of this section no longer holds.** It was written when the file was
  believed private and the services were localhost-only. Neither is true now: the
  file is public (see the header), and the cloud stack binds `0.0.0.0`. Treat the
  reasoning below as historical.
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
