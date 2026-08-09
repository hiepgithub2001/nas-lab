from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from .clients import RadarrClient
from .config import Profile, Settings
from .db import Database
from .models import PermanentFailure
from .subtitles import select_vietnamese_subtitle


LOGGER = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def job_identity(
    movie_id: int,
    movie_file_id: int,
    video_path: Path,
    subtitle_sha256: str,
    profile_sha256: str,
    engine: str,
) -> str:
    value = "\0".join(
        (str(movie_id), str(movie_file_id), str(video_path), subtitle_sha256, profile_sha256, engine)
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def movie_video_path(movie: dict[str, Any]) -> Path | None:
    movie_file = movie.get("movieFile") or {}
    direct_path = movie_file.get("path")
    if direct_path:
        return Path(direct_path).resolve()
    relative = movie_file.get("relativePath")
    root = movie.get("path")
    if relative and root:
        return (Path(root) / relative).resolve()
    return None


def ensure_within(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PermanentFailure(f"path escapes media root: {path}") from exc


def discover_once(
    settings: Settings,
    profile: Profile,
    database: Database,
    client: RadarrClient | None = None,
) -> dict[str, int]:
    settings.require_radarr()
    profile.validate_engine(settings.tts_engine)
    client = client or RadarrClient(settings.radarr_url, settings.radarr_api_key)
    tags = client.tags()
    matching = [tag for tag in tags if tag.get("label") == settings.tag]
    if len(matching) > 1:
        raise PermanentFailure(f"Radarr contains duplicate exact tag {settings.tag!r}")
    if not matching:
        LOGGER.info("Radarr tag %s does not exist", settings.tag)
        database.cancel_untagged_pending(set())
        return {"tagged": 0, "created": 0, "existing": 0, "waiting": 0}

    tag_id = int(matching[0]["id"])
    tagged_movies = [movie for movie in client.movies() if tag_id in movie.get("tags", [])]
    tagged_ids = {int(movie["id"]) for movie in tagged_movies}
    stats = {"tagged": len(tagged_movies), "created": 0, "existing": 0, "waiting": 0}

    for movie in tagged_movies:
        movie_id = int(movie["id"])
        title = str(movie.get("title") or movie_id)
        movie_file = movie.get("movieFile") or {}
        movie_file_id = int(movie_file.get("id") or 0)
        video_path = movie_video_path(movie)
        if not movie.get("hasFile") or not movie_file_id or video_path is None:
            database.upsert_discovery(movie_id, title, "waiting_movie_file")
            stats["waiting"] += 1
            continue
        ensure_within(video_path, settings.media_root)
        if not video_path.is_file():
            database.upsert_discovery(movie_id, title, "missing_movie_file", str(video_path))
            stats["waiting"] += 1
            continue
        subtitle_path, reason = select_vietnamese_subtitle(video_path)
        if subtitle_path is None:
            database.upsert_discovery(movie_id, title, reason or "waiting_subtitle")
            stats["waiting"] += 1
            continue
        ensure_within(subtitle_path, settings.media_root)
        subtitle_hash = sha256_file(subtitle_path)
        identity = job_identity(
            movie_id,
            movie_file_id,
            video_path,
            subtitle_hash,
            profile.sha256,
            settings.tts_engine,
        )
        database.mark_superseded_jobs(movie_id, movie_file_id)
        blocking = database.blocking_job_for_movie_file(movie_id, movie_file_id)
        if blocking is not None and blocking["identity_hash"] != identity:
            state = str(blocking["state"])
            database.upsert_discovery(
                movie_id,
                title,
                f"already_{state}",
                f"job={blocking['id']}",
            )
            stats["existing"] += 1
            continue
        _, created = database.insert_job(
            {
                "identity_hash": identity,
                "radarr_movie_id": movie_id,
                "radarr_movie_file_id": movie_file_id,
                "title": title,
                "video_path": str(video_path),
                "subtitle_path": str(subtitle_path),
                "subtitle_sha256": subtitle_hash,
                "profile_sha256": profile.sha256,
                "engine": settings.tts_engine,
            }
        )
        database.upsert_discovery(movie_id, title, "queued" if created else "unchanged")
        stats["created" if created else "existing"] += 1

    database.cancel_untagged_pending(tagged_ids)
    return stats
