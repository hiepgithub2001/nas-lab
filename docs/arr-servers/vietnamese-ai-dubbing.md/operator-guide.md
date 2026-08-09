# Vietnamese AI voice-over — operator guide

The implementation is staged behind the Compose profile `vn-dubbing`. It will
not start with the normal NAS stack and must remain stopped until this guide's
activation checks pass.

## Current pinned profile

```text
engine       VieNeu v2 standard/PyTorch
model        pnnbao-ump/VieNeu-TTS-v2
revision     b62b1cbddec67cb1d26ac602965d39f0a7faddf2
voice        Tuyen — Northern Vietnamese male
emotion      natural
sampling     temperature 1.0, top_k 50; up to 4 takes for a too-long cue
mix          original bed -14 dB while Vietnamese speech is active
output       AAC-LC, 48 kHz stereo, 160 kbit/s
```

The current short reference candidate is:

```text
appdata/vn-dubbing/smoke-vieneu-v2-tuyen-default-sampling.wav
```

It is 24 kHz mono PCM and 5.08 seconds long. It is a candidate, not approval:
listen to it and repeat the smoke test before enabling any movie job. A separate
sampling run produced an abnormally long take, which is why the movie worker now
regenerates a too-long cue up to four times and selects the shortest result. The
preset voice asset declares CC BY-NC 4.0 and attribution to `pnnbao-ump`; this
pipeline records that notice in every job manifest.

## Activation gates

1. In `auto` mode, verify both regular and ext4-backed sidecars in every
   important Jellyfin client. Forced `copy` requires at least 20 GB free on
   `${DATA_ROOT}/media`.
2. Listen to and approve the smoke sample.
3. Add a dedicated Radarr API key and Jellyfin API key to the untracked `.env`.
4. Verify one manually generated external AAC sidecar on each important
   Jellyfin client.
5. Accept that Phase 1 is an AI voice-over: the whole original mix is lowered
   under speech, not only the actors' dialogue.

Check disk space:

```bash
df -h /mnt/f
```

Choose the publication mechanism in the untracked `.env`:

```dotenv
# Default: prefer a regular media-side AAC, then fall back to ext4 at quota
VN_DUB_PUBLISH_MODE=auto

# Force either mechanism when required
VN_DUB_PUBLISH_MODE=copy
VN_DUB_PUBLISH_MODE=symlink
```

After flipping the value, recreate the worker and Jellyfin containers so both
receive the matching `/vn-dub-published` mount. Existing completed outputs are
not deleted automatically. A new `copy` publication replaces an old link with a
regular file; a new `symlink` publication replaces an old regular sidecar with a
link after the ext4 artifact is verified. In `auto`, this decision is made at
final publication rather than job admission because available space can change
during a long synthesis.

## Build and smoke test

```bash
docker compose --profile vn-dubbing build vn-dub-worker

docker compose --profile vn-dubbing run --rm vn-dub-worker \
  smoke-test \
  --text "Xin chào Việt Nam. Đây là giọng nam miền Bắc, trung tính và ổn định." \
  --output /state/smoke-vieneu-v2-tuyen-neutral.wav
```

The model cache and output are under `appdata/vn-dubbing`, on ext4. The command
loads one model, writes one file and exits; it does not start a server.

For code organization, image construction and service/volume diagrams, see
[coding-and-container-patterns.md](coding-and-container-patterns.md).

## Opt in a movie

1. Add the exact Radarr tag `vn-dub` to the movie.
2. Ensure the imported video has exactly one preferred normal Vietnamese SRT:
   `<video-stem>.vi.srt` or `<video-stem>.vie.srt`.
3. Avoid `forced`, `foreign`, `sdh`, `cc`, and `hi` tracks; they are excluded.
4. Start the services only after the activation gates pass:

```bash
docker compose --profile vn-dubbing up -d vn-dub-scheduler vn-dub-worker
docker compose --profile vn-dubbing exec vn-dub-scheduler vn-dub status
```

Discovery runs hourly. To reconcile immediately:

```bash
docker compose --profile vn-dubbing run --rm vn-dub-scheduler discover --once
```

The output is published beside the movie as:

```text
<video-stem>.Vietnamese AI Voice-over.vi.aac
```

In `copy` mode—or `auto` while above quota—this path is a regular AAC. In
`symlink` mode—or `auto` after media reaches quota—it points to the verified AAC
under `appdata/vn-dubbing/published`. The source movie is never opened for
writing.

## Recovery commands

```bash
# Detailed machine-readable status
docker compose --profile vn-dubbing exec vn-dub-scheduler vn-dub status --json

# Retry after fixing the reported cause
docker compose --profile vn-dubbing exec vn-dub-scheduler vn-dub retry --job <job-id>

# Stop safely after the current cue checkpoint
docker compose --profile vn-dubbing exec vn-dub-scheduler vn-dub cancel --job <job-id>

# Explicitly authorize a new job for the same movie file, then reconcile
docker compose --profile vn-dubbing exec vn-dub-scheduler \
  vn-dub mark-stale --movie-id <radarr-movie-id>
docker compose --profile vn-dubbing run --rm vn-dub-scheduler discover --once

# Stop automation; checkpointed job files remain
docker compose --profile vn-dubbing stop vn-dub-scheduler vn-dub-worker
```

SQLite is stored at `appdata/vn-dubbing/dubbing.sqlite3` in WAL mode. Do not
copy only the main database file while services are writing; stop both services
or use SQLite's online backup mechanism.

## VoxCPM2 cold backup

VoxCPM2 is implemented but disabled. Do not enable it until a consented neutral
Northern male reference WAV, its exact transcript, and SHA-256 are recorded in
the profile. Then set `VN_DUB_INSTALL_VOXCPM=true` and
`VN_DUB_ENGINE=voxcpm2` in `.env`, rebuild, and run the VoxCPM2 smoke test. A
fallback creates a new whole-movie job; VieNeu and VoxCPM2 clips are never mixed
in one track.
