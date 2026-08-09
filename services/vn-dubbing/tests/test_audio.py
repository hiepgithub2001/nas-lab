from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from vn_dubbing.audio import (
    activity_intervals,
    assemble_timeline,
    create_gain_envelope,
)


class Row(dict):
    pass


def make_wav(path: Path, frames: int, value: int = 1000) -> None:
    import array

    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(48_000)
        target.writeframes(array.array("h", [value]) * frames)


class AudioAssemblyTests(unittest.TestCase):
    def test_sparse_timeline_places_and_mixes_cues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.wav"
            second = root / "second.wav"
            make_wav(first, 480, 1000)
            make_wav(second, 480, 2000)
            rows = [
                Row(fitted_audio_path=str(first), start_ms=10),
                Row(fitted_audio_path=str(second), start_ms=15),
            ]
            timeline = root / "timeline.wav"
            assemble_timeline(rows, timeline, 100)
            with wave.open(str(timeline), "rb") as source:
                source.setpos(15 * 48)
                sample = int.from_bytes(source.readframes(1), "little", signed=True)
        self.assertEqual(sample, 3000)

    def test_activity_intervals_merge_small_gaps(self) -> None:
        mix = {
            "activity_padding_before_ms": 0,
            "activity_padding_after_ms": 0,
            "merge_activity_gap_ms": 300,
        }
        rows = [
            Row(
                state="completed",
                normalized_text="Một",
                start_ms=1000,
                fitted_duration_ms=500,
            ),
            Row(
                state="completed",
                normalized_text="Hai",
                start_ms=1700,
                fitted_duration_ms=300,
            ),
        ]
        self.assertEqual(activity_intervals(rows, mix, 3000), [(1000, 2000)])

    def test_gain_envelope_reaches_configured_duck(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "envelope.wav"
            create_gain_envelope(path, 1000, [(100, 900)], -14, 80, 100)
            with wave.open(str(path), "rb") as source:
                source.setpos(400)
                sample = int.from_bytes(source.readframes(1), "little", signed=True)
                source.setpos(0)
                unity = int.from_bytes(source.readframes(1), "little", signed=True)
        self.assertEqual(unity, 32767)
        expected = round(32767 * 10 ** (-14 / 20))
        self.assertLessEqual(abs(sample - expected), 1)


if __name__ == "__main__":
    unittest.main()
