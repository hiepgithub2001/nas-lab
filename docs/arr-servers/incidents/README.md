# Incidents

Things that broke, why, and what stopped them breaking again.

This folder is deliberately separate from `docs/technical/`. That folder explains how
the system is **meant** to work; this one records how it **actually** failed. Both are
useful, but mixing them buries the guides under a growing pile of incident reports.

## Postmortems

Resolved incidents, newest first.

| Date | Incident | Impact | Root cause |
|---|---|---|---|
| 2026-07-30 | [WSL driver libraries stale after a Windows GPU update](2026-07-30-wsl-driver-libs-stale.md) 🔴 **open** | All transcoding down; Jellyfin then failed to start entirely | WSL imports driver libraries only at VM boot, so a mid-run Windows driver update left it 8 days behind |
| 2026-07-29 | [Jellyfin GPU transcode outage](2026-07-29-jellyfin-gpu-transcode-outage.md) | All transcoding down ~2 days; direct-play unaffected | Container had no driver libraries while the host was healthy |

The two are the **same shape at different layers** — see the
[comparison table](2026-07-30-wsl-driver-libs-stale.md#comparison-of-the-two-incidents).
One command tells them apart: if `nvidia-smi` works on the WSL host it is the 07-29
shape; if it crashes it is the 07-30 shape.

## Known issues

Open or recurring problems that have no fix yet, or a workaround instead of one.

| Issue | Status | Workaround |
|---|---|---|
| OpenSubtitles.com login fails (`AuthenticationError`) | Open | Other providers (`subdl`, `subsource`) cover most needs. Note `.com` and `.org` are separate accounts |
| Sonarr rejects releases whose filenames omit the year (`Unknown Series`) | Open | Manual Import with the series set explicitly, or grab a release whose name carries the year |
| `merge-subs.py` exists twice — tracked copy and the gitignored one Bazarr runs | Open | Copy `scripts/merge-subs.py` into `appdata/bazarr/scripts/` after every edit, or bind-mount it |

## When to write a postmortem

Write one when the answer to *"why did that happen?"* took real digging, or when the
same thing could plausibly recur. A one-line fix with an obvious cause does not need a
document — a note in **Known issues** above is enough.

Good triggers:

- Something was broken for hours or days before anyone noticed
- The first hypothesis was wrong and cost time
- The fix is non-obvious, or the failure looked like something it wasn't
- It will silently come back unless prevented

## How to add one

1. Copy [`TEMPLATE.md`](TEMPLATE.md) to `YYYY-MM-DD-short-slug.md`.
2. Date-prefix the filename — the folder then sorts chronologically on its own.
3. Fill it in while the details are fresh. Paste **real log lines and command output**,
   not paraphrases; the exact wording of an error is often the thing that cracks the
   next case.
4. Add a row to the table above.

## Conventions

- **Blameless.** Record what the system did, not who typed what.
- **Keep the wrong turns in.** A report that only shows the correct path teaches nothing
  about how to avoid the incorrect one.
- **Show the evidence.** Real output beats description.
- **Diagrams help.** GitHub renders ` ```mermaid ` blocks inline — a sequence diagram of
  the failure chain, or a before/after of the fix, often explains faster than prose.
- **Separate cause from trigger.** The title that failed to play is usually the
  messenger, not the reason.
