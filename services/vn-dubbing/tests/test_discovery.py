from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vn_dubbing.config import Profile, Settings
from vn_dubbing.db import Database
from vn_dubbing.discovery import discover_once


PROFILE = """
profile_version: 1
model:
  engine: vieneu-v2
  repository: example/model
  revision: abc
  voice_id: Tuyen
speech: {}
mix: {}
output: {}
"""


class FakeRadarr:
    def __init__(self, movie: dict[str, object]):
        self._movie = movie

    def tags(self):
        return [{"id": 7, "label": "vn-dub"}]

    def movies(self):
        return [self._movie]


class DiscoveryTests(unittest.TestCase):
    def test_repeated_discovery_creates_one_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            media = root / "media"
            media.mkdir()
            video = media / "Film.mkv"
            subtitle = media / "Film.vi.srt"
            video.write_bytes(b"not-a-real-video")
            subtitle.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", encoding="utf-8"
            )
            profile_path = root / "profile.yaml"
            profile_path.write_text(PROFILE, encoding="utf-8")
            profile = Profile.load(profile_path)
            settings = Settings(
                state_dir=state,
                media_root=media,
                profile_path=profile_path,
                db_path=state / "db.sqlite3",
                model_cache=state / "cache",
                jobs_dir=state / "jobs",
                health_dir=state / "health",
                published_dir=state / "published",
                published_link_root=state / "published",
                radarr_url="http://radarr",
                radarr_api_key="key",
                jellyfin_url="http://jellyfin",
                jellyfin_api_key="",
                tag="vn-dub",
                tts_engine="vieneu-v2",
                scan_interval=3600,
                supervisor_poll_interval=30,
                lease_seconds=180,
                min_media_free_gb=0,
                stop_publish_free_gb=0,
                min_work_free_gb=0,
                min_free_vram_mb=0,
                require_gpu=False,
                jellyfin_refresh=False,
                max_attempts=3,
                publish_mode="copy",
            )
            database = Database(settings.db_path)
            database.migrate()
            movie = {
                "id": 1,
                "title": "Film",
                "hasFile": True,
                "tags": [7],
                "path": str(media),
                "movieFile": {"id": 2, "path": str(video)},
            }
            first = discover_once(settings, profile, database, FakeRadarr(movie))
            second = discover_once(settings, profile, database, FakeRadarr(movie))
            jobs = database.list_jobs()
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["existing"], 1)
        self.assertEqual(len(jobs), 1)


if __name__ == "__main__":
    unittest.main()
