from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .audio import ffprobe_json, sha256_file
from .models import PermanentFailure


def sidecar_path(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.stem}.Vietnamese AI Voice-over.vi.aac")


def verify_output(
    output_path: Path,
    expected_duration_ms: int,
    profile_output: dict[str, Any],
) -> dict[str, Any]:
    data = ffprobe_json(output_path)
    audio_streams = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"]
    if len(audio_streams) != 1:
        raise PermanentFailure(f"expected one output audio stream, found {len(audio_streams)}")
    stream = audio_streams[0]
    if stream.get("codec_name") != profile_output["codec"]:
        raise PermanentFailure(
            f"expected codec {profile_output['codec']}, found {stream.get('codec_name')}"
        )
    if int(stream.get("sample_rate") or 0) != int(profile_output["sample_rate"]):
        raise PermanentFailure("output sample rate does not match profile")
    if int(stream.get("channels") or 0) != int(profile_output["channels"]):
        raise PermanentFailure("output channel count does not match profile")
    duration = stream.get("duration") or data.get("format", {}).get("duration")
    if duration is None:
        raise PermanentFailure("output duration is unavailable")
    duration_ms = round(float(duration) * 1000)
    if abs(duration_ms - expected_duration_ms) > 250:
        raise PermanentFailure(
            f"output duration differs from movie by {duration_ms - expected_duration_ms} ms"
        )
    return {
        "codec": stream["codec_name"],
        "sample_rate": int(stream["sample_rate"]),
        "channels": int(stream["channels"]),
        "duration_ms": duration_ms,
        "bytes": output_path.stat().st_size,
        "sha256": sha256_file(output_path),
    }


def publish_atomic(source: Path, destination: Path, stop_publish_free_gb: int) -> None:
    free_bytes = shutil.disk_usage(destination.parent).free
    required = stop_publish_free_gb * 1024**3 + source.stat().st_size
    if free_bytes < required:
        raise PermanentFailure(
            f"publication blocked: {free_bytes / 1024**3:.1f} GB free, "
            f"requires {required / 1024**3:.1f} GB"
        )
    partial = destination.with_name(destination.name + ".partial")
    try:
        with source.open("rb") as reader, partial.open("wb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(partial, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        partial.unlink(missing_ok=True)


def publish_sidecar(
    source: Path,
    destination: Path,
    *,
    mode: str,
    identity_hash: str,
    published_dir: Path,
    published_link_root: Path,
    stop_publish_free_gb: int,
) -> Path:
    """Publish a regular sidecar or an adjacent link to an ext4 artifact."""
    if mode == "copy":
        publish_atomic(source, destination, stop_publish_free_gb)
        return destination
    if mode != "symlink":
        raise PermanentFailure(f"unknown publication mode {mode!r}")

    published_dir.mkdir(parents=True, exist_ok=True)
    artifact = published_dir / f"{identity_hash}.aac"
    publish_atomic(source, artifact, stop_publish_free_gb)
    link_target = published_link_root / artifact.name
    if not link_target.is_file():
        raise PermanentFailure(
            f"published link target is not visible inside the worker: {link_target}"
        )
    if not os.path.samefile(artifact, link_target):
        raise PermanentFailure(
            "VN_DUB_PUBLISHED_DIR and VN_DUB_PUBLISHED_LINK_ROOT do not expose "
            "the same storage"
        )

    partial = destination.with_name(destination.name + ".partial-link")
    try:
        partial.unlink(missing_ok=True)
        os.symlink(link_target, partial)
        if not partial.is_file():
            raise PermanentFailure(f"publication symlink cannot resolve: {partial}")
        os.replace(partial, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        partial.unlink(missing_ok=True)
    return artifact


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
