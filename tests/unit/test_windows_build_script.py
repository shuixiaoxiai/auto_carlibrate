import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "packaging" / "windows" / "build.ps1"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


class WindowsBuildScriptTests(unittest.TestCase):
    def test_active_environment_is_preferred_and_can_be_explicit(self) -> None:
        script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('[string]$PythonExecutable = ""', script)
        self.assertIn("$env:CONDA_PREFIX", script)
        self.assertIn("$env:VIRTUAL_ENV", script)
        self.assertLess(
            script.index("$env:CONDA_PREFIX"),
            script.index("Get-Command python.exe"),
        )
        self.assertIn("Build interpreter: $PythonExecutable", script)

    def test_python_failures_stop_and_python_313_has_compatible_qt(self) -> None:
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        project = PYPROJECT.read_text(encoding="utf-8")

        self.assertIn("if ($LASTEXITCODE -ne 0)", script)
        self.assertNotRegex(
            script,
            re.compile(r"^python(?:\.exe)?\s", re.MULTILINE | re.IGNORECASE),
        )
        self.assertIn(
            '"PySide6==6.7.3; python_version < \'3.13\'"',
            project,
        )
        self.assertIn(
            '"PySide6==6.8.3; python_version >= \'3.13\'"',
            project,
        )


if __name__ == "__main__":
    unittest.main()
