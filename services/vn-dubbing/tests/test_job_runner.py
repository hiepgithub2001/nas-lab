from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vn_dubbing.job_runner import synthesize_and_fit_with_retries


class FakeBackend:
    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, text: str, output_path: Path, seed: int) -> None:
        del text, seed
        self.calls += 1
        output_path.write_bytes(str(self.calls).encode())


def fake_fit(raw: Path, fitted: Path, window_ms: int, max_tempo: float, voice_lufs: float):
    del window_ms, max_tempo, voice_lufs
    attempt = int(raw.read_text())
    shutil.copyfile(raw, fitted)
    durations = {1: 5000, 2: 900}
    return {
        "raw_duration_ms": durations[attempt],
        "fitted_duration_ms": durations[attempt],
        "tempo": 1.0,
        "warning_code": "too_long" if attempt == 1 else None,
        "artifact_sha256": "replaced-after-selection",
    }


class CueRetryTests(unittest.TestCase):
    @patch("vn_dubbing.job_runner.fit_cue", side_effect=fake_fit)
    def test_regenerates_too_long_take_and_keeps_valid_candidate(self, _fit) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeBackend()
            result = synthesize_and_fit_with_retries(
                backend=backend,
                job_id="job",
                cue={
                    "cue_index": 7,
                    "normalized_text": "Xin chào",
                    "start_ms": 0,
                    "end_ms": 1000,
                },
                cue_dir=root,
                max_attempts=4,
                max_tempo=1.35,
                voice_lufs=-18,
            )

            self.assertEqual(backend.calls, 2)
            self.assertEqual(result["synthesis_attempts"], 2)
            self.assertIsNone(result["warning_code"])
            self.assertEqual(Path(result["raw_audio_path"]).read_bytes(), b"2")
            self.assertEqual(Path(result["fitted_audio_path"]).read_bytes(), b"2")
            self.assertFalse(list(root.glob("*.attempt-*.wav")))


if __name__ == "__main__":
    unittest.main()
