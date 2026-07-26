# Remapping "Other"-only indexers to Movies + TV

Some public indexers tag **every** result as category `Other` (8000) because the
source site gives no category metadata (e.g. generic DHT/magnet search sites like
FileMood, BTdirectory, MagnetDownload). Prowlarr then won't sync them to Radarr
(wants Movies, 2000) or Sonarr (wants TV, 5000) — no category overlap — and even a
manually-added Torznab feed gets its results filtered out by the apps.

This doc explains a **custom-definition override** that remaps those `Other` results to
**both** Movies *and* TV, so the indexer feeds Radarr and Sonarr. Each app's
**title-parsing** then does the real filtering (Radarr keeps things that parse as
movies, Sonarr keeps things that parse as episodes, both reject the rest).

## When to use it — and when NOT to

Only remap indexers that **genuinely carry movies/TV but mislabel them** — general
torrent search sites. Do **not** remap indexers that are truly software/OS-only
(LinuxTracker, Mac Torrents Download, CrackingPatching): they have no film/TV content,
and tagging their software cracks as "Movies" floods Radarr/Sonarr with junk.

Find candidates — indexers with `Other` but no TV and no Movies:

```bash
docker exec prowlarr curl -s -H "X-Api-Key: <prowlarr-key>" \
  http://localhost:9696/api/v1/indexer | python3 -c '
import json,sys
for i in json.load(sys.stdin):
    ids=[c.get("id") for c in (i.get("capabilities",{}) or {}).get("categories",[]) or []]
    tv=any(5000<=x<6000 for x in ids if isinstance(x,int))
    mv=any(2000<=x<3000 for x in ids if isinstance(x,int))
    other=any(8000<=x<9000 for x in ids if isinstance(x,int))
    if other and not tv and not mv: print(i["id"], i["name"])'
```

Then keep only the general-search ones from that list.

## How the remap works

A Prowlarr indexer is a Cardigann YAML definition in `/config/Definitions/`. The
`Other`-only ones map their single site category to Torznab `Other`:

```yaml
caps:
  categorymappings:              # (some use the map form: `categories:\n  Other: Other`)
    - {id: Other, cat: Other, desc: Other}
fields:
  category:
    text: Other                  # every result stamped "Other"
```

Cardigann's `categorymappings` is a **list**, and **duplicate `id` entries add
multiple Torznab categories** to each result. So mapping `Other` to *both* `TV` and
`Movies` tags every result with 5000 **and** 2000:

```yaml
caps:
  categorymappings:
    - {id: Other, cat: TV, desc: Other}
    - {id: Other, cat: Movies, desc: Other}
```

### The two rules that make it work

1. **Custom overrides must be unique.** Prowlarr rejects a custom definition that
   shares a filename / `id` / `name` with an official one
   (*"does not have unique file name or Indexer name"*). So the override is a
   **parallel** indexer: new filename, `id: <orig>multicat`, `name: <Orig> (Multi-Cat)`.
   It lives in `/config/Definitions/Custom/` (which survives Prowlarr updates; the
   official definitions in `/config/Definitions/` get overwritten).

2. **The category field must still emit the mapped id.** These definitions emit
   `category: text: Other`, which matches `id: Other` in the mappings — so no change to
   the field is needed.

## Applying it

Use the helper script (idempotent — safe to re-run):

```bash
scripts/remap-indexer-multicat.sh <definition-name> [<definition-name> ...]
# e.g.
scripts/remap-indexer-multicat.sh filemood damagnet magnetdownload btdirectory
```

For each name it copies the official definition to
`appdata/prowlarr/Definitions/Custom/<name>-multicat.yml`, gives it a unique id/name,
and remaps `Other` → `TV` + `Movies`. Then:

1. **Restart Prowlarr** so it loads the custom definitions:
   `docker compose restart prowlarr`
2. **Add each new "… (Multi-Cat)" indexer** in Prowlarr (Add Indexer → search its
   name), or via the API.
3. Prowlarr **syncs them to Radarr and Sonarr** automatically (they now declare
   Movies + TV).

Verify a feed now carries both categories:

```bash
docker exec prowlarr curl -s "http://localhost:9696/<newIndexerId>/api?apikey=<key>&t=tvsearch&q=test" \
  | grep -oE 'category" value="[0-9]+"' | sort -u
# expect 2000 (Movies) and 5000 (TV)
```

## Limitations & notes

- **Duplicate results:** the original `Other`-only indexer still exists alongside the
  Multi-Cat one, so a Prowlarr search shows both. Remove the original if you only want
  the Multi-Cat version.
- **Search noise:** because every result is tagged both Movies and TV, a movie search
  surfaces the indexer's TV content and vice-versa. Title-parsing rejects the
  mismatches, but you'll see them in interactive-search lists. Keep these indexers on
  **interactive search only** (not automatic/RSS) to avoid junk auto-grabs.
- **Updates:** custom overrides in `Definitions/Custom/` persist across Prowlarr
  updates, but if the upstream official definition changes structurally, re-run the
  script to regenerate the override from the new base.
- Unrelated but worth knowing: a strict indexer returning nothing for a *specific*
  series can also be an app-side bug (e.g. Sonarr searching the wrong season) — that is
  not a category problem and this remap won't fix it.
