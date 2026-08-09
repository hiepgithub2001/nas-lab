from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vn_dubbing.models import PermanentFailure
from vn_dubbing.subtitles import parse_srt, select_vietnamese_subtitle
from vn_dubbing.text_normalization import normalize_text


class TextNormalizationTests(unittest.TestCase):
    def test_removes_untrusted_emotion_and_sdh_tags(self) -> None:
        self.assertEqual(
            normalize_text("NAM: [cười] — Xin chào!!! (thở dài)"),
            "Xin chào.",
        )

    def test_preserves_vietnamese_unicode_and_joins_lines(self) -> None:
        self.assertEqual(normalize_text("Tôi là người\nViệt Nam."), "Tôi là người Việt Nam.")

    def test_non_speech_cue_becomes_empty(self) -> None:
        self.assertEqual(normalize_text("♪ [MUSIC]"), "")


class SubtitleTests(unittest.TestCase):
    def test_parses_bom_multiline_and_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "movie.vi.srt"
            path.write_text(
                "\ufeff1\n00:00:01,000 --> 00:00:03,000\nXin chào\nViệt Nam.\n\n"
                "2\n00:00:02,500 --> 00:00:04,000\n[MUSIC]\n",
                encoding="utf-8",
            )
            cues = parse_srt(path)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].normalized_text, "Xin chào Việt Nam.")
        self.assertEqual(cues[1].normalized_text, "")

    def test_rejects_non_monotonic_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.srt"
            path.write_text(
                "1\n00:00:02,000 --> 00:00:03,000\nHai\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\nMột\n",
                encoding="utf-8",
            )
            with self.assertRaises(PermanentFailure):
                parse_srt(path)

    def test_subtitle_precedence_and_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "Film.mkv"
            video.touch()
            (root / "Film.AI.vi.srt").touch()
            normal = root / "Film.vi.srt"
            normal.touch()
            (root / "Film.forced.vi.srt").touch()
            selected, reason = select_vietnamese_subtitle(video)
        self.assertEqual(selected, normal.resolve())
        self.assertIsNone(reason)

    def test_equal_priority_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "Film.mkv"
            video.touch()
            (root / "Film.vi.srt").touch()
            (root / "Film.vie.srt").touch()
            selected, reason = select_vietnamese_subtitle(video)
        self.assertIsNone(selected)
        self.assertEqual(reason, "ambiguous_subtitle")


if __name__ == "__main__":
    unittest.main()
