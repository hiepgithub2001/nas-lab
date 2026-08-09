# Vietnamese dubbing worker

This service discovers Radarr movies tagged `vn-dub`, synthesizes a single
neutral Northern Vietnamese male narrator from the movie's Vietnamese SRT, and
publishes a selectable Jellyfin AAC sidecar. It never edits the source movie.

The default model is the pinned VieNeu v2 revision with preset ID `Tuyen`.
VoxCPM2 exists as a disabled cold-backup adapter and must not be enabled until a
consented neutral reference recording passes Phase 0.

## Safety defaults

- The Compose services are under the `vn-dubbing` profile and do not start with
  the normal NAS stack.
- Publication is blocked below 20 GB free on the media filesystem.
- The worker loads only one model and processes only one movie at a time.
- SQLite and all intermediate audio live on ext4 under `/state`.
- The final sidecar is written as `.partial`, verified, then atomically renamed.

## Development

```bash
cd services/vn-dubbing
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m vn_dubbing.cli --help
```

The host does not need FFmpeg for control-plane unit tests. FFmpeg and the TTS
runtime are installed in the worker image.

See
[`coding-and-container-patterns.md`](../../docs/arr-servers/vietnamese-ai-dubbing.md/coding-and-container-patterns.md)
for component boundaries, Docker image layers and Compose wiring.

## Operator flow

After configuring API keys and reclaiming media-disk space:

```bash
docker compose --profile vn-dubbing build vn-dub-worker
docker compose --profile vn-dubbing run --rm vn-dub-worker \
  smoke-test --text "Xin chào Việt Nam" --output /state/smoke.wav
docker compose --profile vn-dubbing up -d vn-dub-scheduler vn-dub-worker
docker compose --profile vn-dubbing exec vn-dub-scheduler vn-dub status
```

Do not enable the scheduler before the smoke test and Jellyfin-client sidecar
test are approved.
