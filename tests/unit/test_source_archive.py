import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.create_source_archive import EXCLUDED_PATHS, create_archive


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def is_git_checkout() -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


class SourceArchiveTests(unittest.TestCase):
    def test_archive_is_revision_stamped_and_excludes_historical_artifacts(self):
        if not is_git_checkout():
            self.skipTest("source archive generation requires a Git checkout")
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

    def test_exported_source_tree_has_revision_and_no_historical_artifacts(self):
        if is_git_checkout():
            self.skipTest("exported-tree check only applies outside a Git checkout")

        revision = (PROJECT_ROOT / "SOURCE_REVISION.txt").read_text(
            encoding="utf-8"
        ).strip()
        self.assertRegex(revision, r"^[0-9a-f]{40}$")
        self.assertTrue(
            (PROJECT_ROOT / "src/ble_calibration/optimization/search.py").is_file()
        )
        for excluded in EXCLUDED_PATHS:
            self.assertFalse((PROJECT_ROOT / excluded).exists(), excluded)


if __name__ == "__main__":
    unittest.main()
