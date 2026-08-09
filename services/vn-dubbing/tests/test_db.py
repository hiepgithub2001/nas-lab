from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vn_dubbing.db import Database
from vn_dubbing.models import JobState, SubtitleCue


def job_values(identity: str = "identity") -> dict[str, object]:
    return {
        "identity_hash": identity,
        "radarr_movie_id": 10,
        "radarr_movie_file_id": 20,
        "title": "A Film",
        "video_path": "/data/media/A.mkv",
        "subtitle_path": "/data/media/A.vi.srt",
        "subtitle_sha256": "subtitle",
        "profile_sha256": "profile",
        "engine": "vieneu-v2",
    }


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "dubbing.sqlite3")
        self.database.migrate()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_wal_and_idempotent_identity(self) -> None:
        first_id, created = self.database.insert_job(job_values())
        second_id, created_again = self.database.insert_job(job_values())
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first_id, second_id)
        with self.database.connect() as connection:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode, "wal")

    def test_one_worker_lease(self) -> None:
        job_id, _ = self.database.insert_job(job_values())
        leased = self.database.lease_next("worker-a", 180, 3)
        self.assertEqual(leased["id"], job_id)
        self.assertIsNone(self.database.lease_next("worker-b", 180, 3))
        self.assertTrue(self.database.heartbeat(job_id, "worker-a", 180))
        self.assertFalse(self.database.heartbeat(job_id, "worker-b", 180))

    def test_cue_checkpoint_progress(self) -> None:
        job_id, _ = self.database.insert_job(job_values())
        cues = [SubtitleCue(1, 0, 1000, "Xin chào", "Xin chào")]
        self.database.upsert_cues(job_id, cues)
        artifact = Path(self.temporary.name) / "cue.wav"
        artifact.write_bytes(b"audio")
        import hashlib

        self.database.complete_cue(
            job_id,
            1,
            raw_audio_path=str(artifact),
            fitted_audio_path=str(artifact),
            raw_duration_ms=500,
            fitted_duration_ms=500,
            tempo=1.0,
            artifact_sha256=hashlib.sha256(b"audio").hexdigest(),
        )
        job = self.database.get_job(job_id)
        row = self.database.cues_for_job(job_id)[0]
        self.assertEqual(job["completed_cues"], 1)
        self.assertTrue(self.database.cue_artifact_valid(row))

    def test_retry_resets_failure(self) -> None:
        job_id, _ = self.database.insert_job(job_values())
        self.database.set_job_state(job_id, JobState.PERMANENT_FAILED, error="bad")
        self.assertTrue(self.database.retry(job_id))
        self.assertEqual(self.database.get_job(job_id)["state"], JobState.PENDING)


if __name__ == "__main__":
    unittest.main()
