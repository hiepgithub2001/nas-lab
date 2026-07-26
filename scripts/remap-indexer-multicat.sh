#!/usr/bin/env bash
# Remap "Other"-only Prowlarr indexers to Movies + TV via a custom definition override.
#
# For each definition name given, creates a parallel "<Name> (Multi-Cat)" indexer in
# Prowlarr's Custom definitions folder whose results are tagged BOTH Movies (2000) and
# TV (5000), so it feeds Radarr and Sonarr. The original is left untouched.
#
# See docs/technical/indexer-category-remap.md for the full explanation.
#
# Usage:
#   scripts/remap-indexer-multicat.sh filemood damagnet magnetdownload btdirectory ...
#
# After running: restart Prowlarr, then add each "… (Multi-Cat)" indexer.
# Idempotent — re-running regenerates the override from the current official definition.

set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <definition-name> [<definition-name> ...]" >&2
  echo "  (definition name = the .yml basename, e.g. 'filemood')" >&2
  exit 2
fi

# docker access: plain 'docker' or 'sg docker -c'
if docker ps >/dev/null 2>&1; then
  d() { docker "$@"; }
else
  d() { sg docker -c "docker $(printf '%q ' "$@")"; }
fi

for NAME in "$@"; do
  echo "== $NAME =="
  d exec prowlarr sh -c '
    set -e
    NAME="'"$NAME"'"
    SRC="/config/Definitions/$NAME.yml"
    DST="/config/Definitions/Custom/${NAME}-multicat.yml"
    [ -f "$SRC" ] || { echo "  ! no definition /config/Definitions/$NAME.yml — skipping"; exit 0; }
    mkdir -p /config/Definitions/Custom
    cp "$SRC" "$DST"

    # unique id + name (required for a custom override)
    sed -i "s|^id: .*|&multicat|" "$DST"
    sed -i "s|^\(name: .*\)|\1 (Multi-Cat)|" "$DST"

    # remap Other -> TV + Movies, handling both definition forms:
    #   map form:   `  categories:` / `    Other: Other`
    #   list form:  `    - {id: Other, cat: Other, desc: Other}`
    sed -i "s|^  categories:|  categorymappings:|" "$DST"
    sed -i "s|^    Other: Other[[:space:]]*$|    - {id: Other, cat: TV, desc: Other}\n    - {id: Other, cat: Movies, desc: Other}|" "$DST"
    sed -i "s|^    - {id: Other, cat: Other, desc: Other}[[:space:]]*$|    - {id: Other, cat: TV, desc: Other}\n    - {id: Other, cat: Movies, desc: Other}|" "$DST"

    if grep -q "cat: TV" "$DST" && grep -q "cat: Movies" "$DST"; then
      echo "  -> wrote $DST (Other remapped to TV + Movies)"
    else
      echo "  ! remap anchor not found in $NAME — its category block has a non-standard shape."
      echo "    Inspect $SRC and edit the custom copy by hand."
    fi
  '
done

echo
echo "Next:"
echo "  1. docker compose restart prowlarr"
echo "  2. Add each '<Name> (Multi-Cat)' indexer in Prowlarr (it will sync to Radarr/Sonarr)."
