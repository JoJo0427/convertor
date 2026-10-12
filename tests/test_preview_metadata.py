"""Run with desktop/runtime/bin/python3 tests/test_preview_metadata.py."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from app.jobs import OutputFile


class PreviewMetadataTests(unittest.TestCase):
    def test_players_match_output_formats(self):
        for format_id, kind in (("mp3", "audio"), ("mp4", "video"),
                                ("png", "image"), ("pdf", "document")):
            with self.subTest(format=format_id):
                output = OutputFile(f"test.{format_id}", Path(f"/tmp/test.{format_id}"), 1, format_id)
                data = output.to_dict()
                self.assertTrue(data["previewable"])
                self.assertEqual(data["previewKind"], kind)

    def test_archive_is_not_offered_as_a_preview(self):
        output = OutputFile("test.zip", Path("/tmp/test.zip"), 1, "zip")
        self.assertFalse(output.to_dict()["previewable"])


if __name__ == "__main__":
    unittest.main()
