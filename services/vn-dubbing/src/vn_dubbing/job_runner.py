from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .audio import (
    activity_intervals,
    assemble_timeline,
    create_gain_envelope,
    fit_cue,
    mix_voice_over,
    probe_movie,
)
from .clients import JellyfinClient, RadarrClient
from .config import Profile, Settings
from .db import Database
from .discovery import movie_video_path, sha256_file
from .models import (
    AdmissionDeferred,
    JobState,
    NeedsReview,
    PermanentFailure,
)
from .subtitles import format_srt, parse_srt
from .tts import create_backend
from .verification import publish_sidecar, sidecar_path, verify_output, write_json_atomic


LOGGER = logging.getLogger(__name__)


def deterministic_seed(job_id: str, cue_index: int) -> int:
    digest = hashlib.sha256(f"{job_id}:{cue_index}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def _copy_atomic(source: Path, destination: Path) -> None:
    temporary = destination.with_name(destination.name + ".partial")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def synthesize_and_fit_with_retries(
    *,
    backend: Any,
    job_id: str,
    cue: Any,
    cue_dir: Path,
    max_attempts: int,
    max_tempo: float,
    voice_lufs: float,
) -> dict[str, Any]:
    """Regenerate a stochastic outlier and keep the shortest valid take."""
    base = f"{cue['cue_index']:06d}"
    candidates: list[tuple[dict[str, Any], Path, Path]] = []
    for attempt in range(1, max_attempts + 1):
        candidate_raw = cue_dir / f"{base}.attempt-{attempt:02d}.raw.wav"
        candidate_fitted = cue_dir / f"{base}.attempt-{attempt:02d}.fitted.wav"
        if not candidate_raw.is_file():
            backend.synthesize(
                cue["normalized_text"],
                candidate_raw,
                deterministic_seed(job_id, cue["cue_index"] + attempt - 1),
            )
        fit = fit_cue(
            candidate_raw,
            candidate_fitted,
            cue["end_ms"] - cue["start_ms"],
            max_tempo,
            voice_lufs,
        )
        candidates.append((fit, candidate_raw, candidate_fitted))
        if fit["warning_code"] is None:
            break

    selected = min(candidates, key=lambda item: item[0]["fitted_duration_ms"])
    fit, selected_raw, selected_fitted = selected
    canonical_raw = cue_dir / f"{base}.raw.wav"
    canonical_fitted = cue_dir / f"{base}.fitted.wav"
    _copy_atomic(selected_raw, canonical_raw)
    _copy_atomic(selected_fitted, canonical_fitted)
    fit["artifact_sha256"] = sha256_file(canonical_fitted)
    fit["synthesis_attempts"] = len(candidates)
    fit["raw_audio_path"] = str(canonical_raw)
    fit["fitted_audio_path"] = str(canonical_fitted)
    for _, raw, fitted in candidates:
        raw.unlink(missing_ok=True)
        fitted.unlink(missing_ok=True)
    return fit


def _free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1024**3


def _free_vram_mb() -> int | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    values = [int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()]
    return max(values) if values else None


def preflight(settings: Settings, profile: Profile, job: Any) -> tuple[Path, Path, Any]:
    video_path = Path(job["video_path"])
    subtitle_path = Path(job["subtitle_path"])
    if not video_path.is_file():
        raise PermanentFailure(f"movie file is missing: {video_path}")
    if not subtitle_path.is_file():
        raise PermanentFailure(f"subtitle file is missing: {subtitle_path}")
    if sha256_file(subtitle_path) != job["subtitle_sha256"]:
        raise PermanentFailure("subtitle changed after this job was discovered")
    if profile.sha256 != job["profile_sha256"]:
        raise PermanentFailure("profile changed after this job was discovered")
    if (
        settings.publish_mode == "copy"
        and _free_gb(video_path.parent) < settings.min_media_free_gb
    ):
        raise AdmissionDeferred(
            f"media filesystem has {_free_gb(video_path.parent):.1f} GB free; "
            f"requires {settings.min_media_free_gb} GB"
        )
    if _free_gb(settings.state_dir) < settings.min_work_free_gb:
        raise AdmissionDeferred(
            f"work filesystem has {_free_gb(settings.state_dir):.1f} GB free; "
            f"requires {settings.min_work_free_gb} GB"
        )
    free_vram = _free_vram_mb()
    if settings.require_gpu and free_vram is None:
        raise AdmissionDeferred("GPU is required but nvidia-smi is unavailable")
    if free_vram is not None and free_vram < settings.min_free_vram_mb:
        raise AdmissionDeferred(
            f"GPU has {free_vram} MB free; requires {settings.min_free_vram_mb} MB"
        )
    movie_probe = probe_movie(video_path)
    return video_path, subtitle_path, movie_probe


def _validate_radarr_identity(settings: Settings, job: Any) -> None:
    settings.require_radarr()
    movie = RadarrClient(settings.radarr_url, settings.radarr_api_key).movie(job["radarr_movie_id"])
    if int((movie.get("movieFile") or {}).get("id") or 0) != job["radarr_movie_file_id"]:
        raise PermanentFailure("Radarr movie file changed after this job was discovered")
    current_path = movie_video_path(movie)
    if current_path is None or current_path != Path(job["video_path"]).resolve():
        raise PermanentFailure("Radarr movie path changed after this job was discovered")


def _manifest(
    settings: Settings, profile: Profile, job: Any, movie_probe: Any
) -> dict[str, Any]:
    model = profile.model
    return {
        "schema_version": 1,
        "job_id": job["id"],
        "identity_hash": job["identity_hash"],
        "movie": {
            "radarr_movie_id": job["radarr_movie_id"],
            "radarr_movie_file_id": job["radarr_movie_file_id"],
            "video_path": job["video_path"],
            "duration_ms": movie_probe.duration_ms,
            "audio_stream_index": movie_probe.audio_stream_index,
        },
        "subtitle": {
            "path": job["subtitle_path"],
            "sha256": job["subtitle_sha256"],
        },
        "profile": {"path": str(profile.path), "sha256": profile.sha256, "data": profile.data},
        "engine": job["engine"],
        "publication": {
            "mode": settings.publish_mode,
            "published_dir": str(settings.published_dir),
            "published_link_root": str(settings.published_link_root),
        },
        "voice_notice": {
            "voice_id": model["voice_id"],
            "license": model.get("voice_license"),
            "attribution": model.get("voice_attribution"),
        },
        "created_at": int(time.time()),
    }


def run_job(settings: Settings, profile: Profile, database: Database, job_id: str, owner: str) -> Path:
    job = database.get_job(job_id)
    if job is None:
        raise PermanentFailure(f"job {job_id} does not exist")
    if job["state"] != JobState.RUNNING or job["lease_owner"] != owner:
        raise PermanentFailure(f"job {job_id} is not leased by {owner}")

    job_dir = settings.jobs_dir / job_id
    cue_dir = job_dir / "cues"
    cue_dir.mkdir(parents=True, exist_ok=True)
    backend = None
    try:
        video_path, subtitle_path, movie_probe = preflight(settings, profile, job)
        _validate_radarr_identity(settings, job)
        original_stat = video_path.stat()
        write_json_atomic(
            job_dir / "manifest.json", _manifest(settings, profile, job, movie_probe)
        )

        cues = parse_srt(
            subtitle_path,
            allow_emotion_tags=bool(profile.speech.get("allow_emotion_tags", False)),
        )
        database.upsert_cues(job_id, cues)
        (job_dir / "normalized.srt").write_text(format_srt(cues), encoding="utf-8")
        rows = database.cues_for_job(job_id)
        backend = create_backend(settings, profile, job["engine"])
        max_tempo = float(profile.speech["max_tempo"])
        max_synthesis_attempts = int(profile.speech.get("max_synthesis_attempts", 4))
        voice_lufs = float(profile.speech["voice_lufs"])

        for row in rows:
            current = database.get_job(job_id)
            if current is None or current["state"] == JobState.CANCEL_REQUESTED:
                database.set_job_state(job_id, JobState.CANCELLED)
                raise PermanentFailure("job cancelled after checkpoint")
            if database.cue_artifact_valid(row):
                database.heartbeat(job_id, owner, settings.lease_seconds)
                continue
            if not row["normalized_text"]:
                silent = cue_dir / f"{row['cue_index']:06d}.fitted.wav"
                # A short valid silence artifact keeps resume semantics uniform.
                import wave

                with wave.open(str(silent), "wb") as target:
                    target.setnchannels(1)
                    target.setsampwidth(2)
                    target.setframerate(48_000)
                    target.writeframes(b"\0\0" * 480)
                artifact_hash = sha256_file(silent)
                database.complete_cue(
                    job_id,
                    row["cue_index"],
                    raw_audio_path=str(silent),
                    fitted_audio_path=str(silent),
                    raw_duration_ms=10,
                    fitted_duration_ms=10,
                    tempo=1.0,
                    synthesis_attempts=0,
                    warning_code="non_speech",
                    artifact_sha256=artifact_hash,
                )
                continue

            fit = synthesize_and_fit_with_retries(
                backend=backend,
                job_id=job_id,
                cue=row,
                cue_dir=cue_dir,
                max_attempts=max_synthesis_attempts,
                max_tempo=max_tempo,
                voice_lufs=voice_lufs,
            )
            database.complete_cue(
                job_id,
                row["cue_index"],
                **fit,
            )
            if not database.heartbeat(job_id, owner, settings.lease_seconds):
                raise PermanentFailure("job lease was lost")

        rows = database.cues_for_job(job_id)
        spoken = [row for row in rows if row["normalized_text"]]
        too_long = [row for row in spoken if row["warning_code"] == "too_long"]
        if spoken and len(too_long) / len(spoken) > 0.01:
            raise NeedsReview(
                f"{len(too_long)}/{len(spoken)} spoken cues exceed the {max_tempo:.2f}x tempo limit"
            )

        timeline = job_dir / "timeline.wav"
        envelope = job_dir / "duck-envelope.wav"
        work_output = job_dir / "voice-over.aac"
        assemble_timeline(rows, timeline, movie_probe.duration_ms)
        intervals = activity_intervals(rows, profile.mix, movie_probe.duration_ms)
        create_gain_envelope(
            envelope,
            movie_probe.duration_ms,
            intervals,
            float(profile.mix["duck_db"]),
            int(profile.mix["attack_ms"]),
            int(profile.mix["release_ms"]),
        )
        mix_voice_over(
            video_path,
            movie_probe.audio_stream_index,
            timeline,
            envelope,
            work_output,
            movie_probe.duration_ms,
            profile.output,
            profile.mix,
        )
        verification = verify_output(work_output, movie_probe.duration_ms, profile.output)
        write_json_atomic(job_dir / "verification.json", verification)
        destination = sidecar_path(video_path)
        published_artifact = publish_sidecar(
            work_output,
            destination,
            mode=settings.publish_mode,
            identity_hash=job["identity_hash"],
            published_dir=settings.published_dir,
            published_link_root=settings.published_link_root,
            stop_publish_free_gb=settings.stop_publish_free_gb,
        )
        if settings.publish_mode == "symlink" and not destination.is_symlink():
            raise PermanentFailure("symlink publication did not create an adjacent link")
        if sha256_file(destination) != verification["sha256"]:
            raise PermanentFailure("published sidecar checksum differs from verified output")
        if video_path.stat().st_size != original_stat.st_size or video_path.stat().st_mtime_ns != original_stat.st_mtime_ns:
            raise PermanentFailure("source movie metadata changed during publication")
        if settings.jellyfin_refresh:
            if not settings.jellyfin_api_key:
                LOGGER.warning("JELLYFIN_API_KEY is empty; sidecar published but refresh skipped")
            else:
                JellyfinClient(settings.jellyfin_url, settings.jellyfin_api_key).refresh_library()
        database.set_job_state(job_id, JobState.COMPLETED, output_path=str(destination))
        LOGGER.info(
            "published %s sidecar %s backed by %s",
            settings.publish_mode,
            destination,
            published_artifact,
        )
        return destination
    finally:
        if backend is not None:
            backend.close()


def execute_job(settings: Settings, profile: Profile, database: Database, job_id: str, owner: str) -> int:
    try:
        destination = run_job(settings, profile, database, job_id, owner)
        LOGGER.info("job %s published %s", job_id, destination)
        return 0
    except AdmissionDeferred as exc:
        LOGGER.warning("job %s deferred: %s", job_id, exc)
        database.defer_job(job_id, exc.code, str(exc), retry_after=300)
        return exc.exit_code
    except NeedsReview as exc:
        LOGGER.error("job %s needs review: %s", job_id, exc)
        database.set_job_state(
            job_id, JobState.NEEDS_REVIEW, error_code=exc.code, error=str(exc)
        )
        return exc.exit_code
    except PermanentFailure as exc:
        LOGGER.error("job %s failed permanently: %s", job_id, exc)
        current = database.get_job(job_id)
        if current is not None and current["state"] != JobState.CANCELLED:
            database.set_job_state(
                job_id, JobState.PERMANENT_FAILED, error_code=exc.code, error=str(exc)
            )
        return exc.exit_code
    except Exception as exc:
        LOGGER.exception("job %s failed", job_id)
        database.set_job_state(
            job_id,
            JobState.RETRYABLE_FAILED,
            error_code=type(exc).__name__,
            error=str(exc)[:2000],
            retry_after=300,
        )
        return 10
