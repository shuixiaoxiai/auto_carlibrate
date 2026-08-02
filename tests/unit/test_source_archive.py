import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.create_source_archive import EXCLUDED_PATHS, create_archive


class SourceArchiveTests(unittest.TestCase):
    def test_archive_is_revision_stamped_and_excludes_historical_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = create_archive(Path(temp_dir))
            with zipfile.ZipFile(archive_path) as archive:
                names = tuple(archive.namelist())
                revision = archive.read("SOURCE_REVISION.txt").decode().strip()

        self.assertEqual(len(revision), 40)
        self.assertIn("src/ble_calibration/optimization/search.py", names)
        for excluded in EXCLUDED_PATHS:
            self.assertFalse(
                any(
                    name == excluded or name.startswith(f"{excluded}/")
                    for name in names
                ),
                excluded,
            )


if __name__ == "__main__":
    unittest.main()
