from __future__ import annotations

import array
import hashlib
import json
import math
import mmap
import os
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any, Iterable

from .models import PermanentFailure, ProbeResult


def require_executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise PermanentFailure(f"required executable {name!r} was not found")
    return path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def ffprobe_json(path: Path) -> dict[str, Any]:
    executable = require_executable("ffprobe")
    result = run(
        [
            executable,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PermanentFailure(f"ffprobe returned invalid JSON for {path}") from exc


def probe_movie(path: Path) -> ProbeResult:
    data = ffprobe_json(path)
    streams = data.get("streams") or []
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not audio:
        raise PermanentFailure("movie contains no audio stream")
    default = [stream for stream in audio if (stream.get("disposition") or {}).get("default") == 1]
    selected = (default or audio)[0]
    duration = data.get("format", {}).get("duration") or selected.get("duration")
    if duration is None:
        raise PermanentFailure("movie duration is unavailable")
    return ProbeResult(
        duration_ms=round(float(duration) * 1000),
        audio_stream_index=int(selected["index"]),
        audio_codec=str(selected.get("codec_name") or "unknown"),
    )


def probe_audio_duration_ms(path: Path) -> int:
    data = ffprobe_json(path)
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise PermanentFailure(f"audio artifact contains no stream: {path}")
    duration = streams[0].get("duration") or data.get("format", {}).get("duration")
    if duration is None:
        raise PermanentFailure(f"audio duration unavailable: {path}")
    return round(float(duration) * 1000)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fit_cue(
    raw_path: Path,
    fitted_path: Path,
    window_ms: int,
    max_tempo: float,
    voice_lufs: float,
) -> dict[str, Any]:
    ffmpeg = require_executable("ffmpeg")
    normalized = fitted_path.with_suffix(".normalized.wav")
    common_filter = (
        "silenceremove=start_periods=1:start_silence=0.02:start_threshold=-50dB:"
        "stop_periods=1:stop_silence=0.02:stop_threshold=-50dB,"
        f"loudnorm=I={voice_lufs}:TP=-2:LRA=7,"
        "acompressor=threshold=0.125:ratio=2:attack=20:release=150,"
        "aresample=48000,aformat=sample_fmts=s16:channel_layouts=mono,"
        "afade=t=in:d=0.015,areverse,afade=t=in:d=0.020,areverse"
    )
    run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(raw_path),
            "-af",
            common_filter,
            "-c:a",
            "pcm_s16le",
            str(normalized),
        ]
    )
    raw_duration = probe_audio_duration_ms(raw_path)
    normalized_duration = probe_audio_duration_ms(normalized)
    required_tempo = max(1.0, normalized_duration / max(window_ms, 1))
    tempo = min(required_tempo, max_tempo)
    warning = "too_long" if required_tempo > max_tempo else None
    temporary = fitted_path.with_suffix(fitted_path.suffix + ".partial.wav")
    if tempo > 1.005:
        run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(normalized),
                "-af",
                f"atempo={tempo:.6f}",
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ]
        )
    else:
        shutil.copyfile(normalized, temporary)
    os.replace(temporary, fitted_path)
    normalized.unlink(missing_ok=True)
    return {
        "raw_duration_ms": raw_duration,
        "fitted_duration_ms": probe_audio_duration_ms(fitted_path),
        "tempo": tempo,
        "warning_code": warning,
        "artifact_sha256": sha256_file(fitted_path),
    }


def _write_sparse_wav(path: Path, duration_ms: int, sample_rate: int = 48_000) -> int:
    frames = math.ceil(duration_ms * sample_rate / 1000)
    data_size = frames * 2
    if data_size > 0xFFFFFFFF - 36:
        raise PermanentFailure("timeline exceeds classic WAV size limit")
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    with path.open("wb") as handle:
        handle.write(header)
        handle.truncate(44 + data_size)
    return frames


def assemble_timeline(cues: Iterable[Any], output_path: Path, duration_ms: int) -> None:
    sample_rate = 48_000
    total_frames = _write_sparse_wav(output_path, duration_ms, sample_rate)
    with output_path.open("r+b") as handle, mmap.mmap(handle.fileno(), 0) as mapped:
        for cue in cues:
            fitted = Path(cue["fitted_audio_path"])
            with wave.open(str(fitted), "rb") as source:
                if (
                    source.getnchannels() != 1
                    or source.getsampwidth() != 2
                    or source.getframerate() != sample_rate
                ):
                    raise PermanentFailure(f"unexpected fitted WAV format: {fitted}")
                start_frame = round(cue["start_ms"] * sample_rate / 1000)
                remaining = max(0, total_frames - start_frame)
                frame_offset = 0
                while remaining:
                    payload = source.readframes(min(8192, remaining))
                    if not payload:
                        break
                    incoming = array.array("h")
                    incoming.frombytes(payload)
                    begin = 44 + (start_frame + frame_offset) * 2
                    end = begin + len(payload)
                    existing = array.array("h")
                    existing.frombytes(mapped[begin:end])
                    for index, value in enumerate(incoming):
                        mixed = existing[index] + value
                        existing[index] = max(-32768, min(32767, mixed))
                    mapped[begin:end] = existing.tobytes()
                    frame_offset += len(incoming)
                    remaining -= len(incoming)
        mapped.flush()


def activity_intervals(cues: Iterable[Any], mix: dict[str, Any], duration_ms: int) -> list[tuple[int, int]]:
    before = int(mix["activity_padding_before_ms"])
    after = int(mix["activity_padding_after_ms"])
    merge_gap = int(mix["merge_activity_gap_ms"])
    intervals = []
    for cue in cues:
        if not cue["normalized_text"] or cue["state"] != "completed":
            continue
        start = max(0, int(cue["start_ms"]) - before)
        spoken_end = int(cue["start_ms"]) + int(cue["fitted_duration_ms"] or 0)
        end = min(duration_ms, spoken_end + after)
        if end > start:
            intervals.append((start, end))
    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if merged and start - merged[-1][1] <= merge_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def create_gain_envelope(
    output_path: Path,
    duration_ms: int,
    intervals: list[tuple[int, int]],
    duck_db: float,
    attack_ms: int,
    release_ms: int,
) -> None:
    # One sample per millisecond is enough for a volume envelope and keeps a
    # two-hour control file around 14 MB. FFmpeg resamples it to 48 kHz.
    sample_rate = 1000
    count = max(1, duration_ms)
    unity = 32767
    ducked = round(unity * (10 ** (duck_db / 20)))
    values = array.array("h", [unity]) * count
    for start, end in intervals:
        attack_end = min(end, start + max(attack_ms, 1))
        release_start = max(attack_end, end - max(release_ms, 1))
        for position in range(start, min(attack_end, count)):
            fraction = (position - start) / max(attack_end - start, 1)
            values[position] = round(unity + (ducked - unity) * fraction)
        for position in range(attack_end, min(release_start, count)):
            values[position] = ducked
        for position in range(release_start, min(end, count)):
            fraction = (position - release_start) / max(end - release_start, 1)
            values[position] = round(ducked + (unity - ducked) * fraction)
    with wave.open(str(output_path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(values.tobytes())


def mix_voice_over(
    video_path: Path,
    audio_stream_index: int,
    timeline_path: Path,
    envelope_path: Path,
    output_path: Path,
    duration_ms: int,
    profile_output: dict[str, Any],
    profile_mix: dict[str, Any],
) -> None:
    ffmpeg = require_executable("ffmpeg")
    duration = duration_ms / 1000
    limiter = float(profile_mix["true_peak_limit"])
    filter_graph = (
        f"[0:{audio_stream_index}]aresample=48000,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_dur={duration:.3f},"
        f"atrim=duration={duration:.3f}[bed];"
        "[2:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,"
        "pan=stereo|c0=c0|c1=c0[env];"
        "[bed][env]amultiply[ducked];"
        "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,"
        "pan=stereo|c0=c0|c1=c0[voice];"
        f"[ducked][voice]amix=inputs=2:duration=first:normalize=0,alimiter=limit={limiter}[out]"
    )
    temporary = output_path.with_name(output_path.stem + ".partial.aac")
    run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(timeline_path),
            "-i",
            str(envelope_path),
            "-filter_complex",
            filter_graph,
            "-map",
            "[out]",
            "-c:a",
            profile_output["codec"],
            "-b:a",
            str(profile_output["bitrate"]),
            "-ar",
            str(profile_output["sample_rate"]),
            "-ac",
            str(profile_output["channels"]),
            "-t",
            f"{duration:.3f}",
            str(temporary),
        ]
    )
    os.replace(temporary, output_path)
