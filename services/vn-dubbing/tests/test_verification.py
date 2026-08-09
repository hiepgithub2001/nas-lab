from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vn_dubbing.verification import publish_sidecar


class PublicationTests(unittest.TestCase):
    def test_symlink_mode_keeps_bytes_in_published_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "work.aac"
            source.write_bytes(b"audio-on-ext4")
            media = root / "media"
            media.mkdir()
            destination = media / "Film.Vietnamese AI Voice-over.vi.aac"
            published = root / "published"

            artifact = publish_sidecar(
                source,
                destination,
                mode="symlink",
                identity_hash="abc123",
                published_dir=published,
                published_link_root=published,
                stop_publish_free_gb=0,
            )

            self.assertTrue(destination.is_symlink())
            self.assertEqual(destination.read_bytes(), b"audio-on-ext4")
            self.assertEqual(artifact, published / "abc123.aac")
            self.assertEqual(artifact.read_bytes(), b"audio-on-ext4")

    def test_copy_mode_replaces_an_existing_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "work.aac"
            source.write_bytes(b"copied-audio")
            old = root / "old.aac"
            old.write_bytes(b"old")
            destination = root / "Film.vi.aac"
            destination.symlink_to(old)

            artifact = publish_sidecar(
                source,
                destination,
                mode="copy",
                identity_hash="unused",
                published_dir=root / "published",
                published_link_root=root / "published",
                stop_publish_free_gb=0,
            )

            self.assertFalse(destination.is_symlink())
            self.assertEqual(destination.read_bytes(), b"copied-audio")
            self.assertEqual(artifact, destination)


if __name__ == "__main__":
    unittest.main()
