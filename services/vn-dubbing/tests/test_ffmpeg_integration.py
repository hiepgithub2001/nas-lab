from __future__ import annotations

import array
import math
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from vn_dubbing.audio import (
    activity_intervals,
    assemble_timeline,
    create_gain_envelope,
    fit_cue,
    mix_voice_over,
    probe_movie,
)
from vn_dubbing.verification import verify_output


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is unavailable")
class FfmpegIntegrationTests(unittest.TestCase):
    def test_fit_preserves_internal_speech_pause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "speech-with-pause.wav"
            tone = [
                round(5000 * math.sin(2 * math.pi * 440 * index / 24000))
                for index in range(24000)
            ]
            samples = array.array("h", tone + [0] * 12000 + tone)
            with wave.open(str(raw), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(24000)
                target.writeframes(samples.tobytes())

            fitted = root / "fitted.wav"
            result = fit_cue(raw, fitted, 3000, 1.35, -18)

        self.assertGreater(result["fitted_duration_ms"], 2400)
        self.assertLess(result["fitted_duration_ms"], 2600)
        self.assertIsNone(result["warning_code"])

    def test_short_voice_over_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "fixture.mkv"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=320x180:r=24:d=3",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=220:sample_rate=48000:duration=3",
                    "-c:v",
                    "ffv1",
                    "-c:a",
                    "pcm_s16le",
                    "-shortest",
                    str(movie),
                ],
                check=True,
            )
            raw = root / "raw.wav"
            samples = array.array(
                "h",
                [round(5000 * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(24000)],
            )
            with wave.open(str(raw), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(24000)
                target.writeframes(samples.tobytes())
            fitted = root / "fitted.wav"
            fit = fit_cue(raw, fitted, 1200, 1.35, -18)
            row = {
                "state": "completed",
                "normalized_text": "Xin chào",
                "start_ms": 500,
                "end_ms": 1700,
                "fitted_audio_path": str(fitted),
                "fitted_duration_ms": fit["fitted_duration_ms"],
            }
            probe = probe_movie(movie)
            timeline = root / "timeline.wav"
            envelope = root / "envelope.wav"
            output = root / "voice-over.aac"
            assemble_timeline([row], timeline, probe.duration_ms)
            mix = {
                "activity_padding_before_ms": 120,
                "activity_padding_after_ms": 250,
                "merge_activity_gap_ms": 300,
                "duck_db": -14,
                "attack_ms": 80,
                "release_ms": 300,
                "true_peak_limit": 0.891251,
            }
            intervals = activity_intervals([row], mix, probe.duration_ms)
            create_gain_envelope(envelope, probe.duration_ms, intervals, -14, 80, 300)
            output_profile = {"codec": "aac", "sample_rate": 48000, "bitrate": "160k", "channels": 2}
            mix_voice_over(
                movie,
                probe.audio_stream_index,
                timeline,
                envelope,
                output,
                probe.duration_ms,
                output_profile,
                mix,
            )
            result = verify_output(output, probe.duration_ms, output_profile)
        self.assertEqual(result["codec"], "aac")
        self.assertEqual(result["sample_rate"], 48000)
        self.assertEqual(result["channels"], 2)


if __name__ == "__main__":
    unittest.main()
