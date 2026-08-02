import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "packaging" / "windows" / "build.ps1"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
PYINSTALLER_SPEC = PROJECT_ROOT / "packaging" / "windows" / "ble_calibration.spec"
DRIVER_DISCOVERY_FILES = (
    BUILD_SCRIPT,
    PROJECT_ROOT / "src" / "ble_calibration" / "app" / "main.py",
    PROJECT_ROOT / "packaging" / "windows" / "write_build_manifest.py",
)


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
        self.assertIn("exit code ${LASTEXITCODE}:", script)
        self.assertNotIn("exit code $LASTEXITCODE:", script)
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

    def test_windows_native_driver_and_numpy_are_collected(self) -> None:
        spec = PYINSTALLER_SPEC.read_text(encoding="utf-8")

        for path in DRIVER_DISCOVERY_FILES:
            source = path.read_text(encoding="utf-8")
            self.assertIn("zlgcan_driver", source, path)
            self.assertNotIn("clgcan_driver", source, path)

        self.assertIn('collect_all("numpy")', spec)
        self.assertIn('raise RuntimeError("numpy could not be collected")', spec)
        self.assertIn('module.endswith(".tests")', spec)

    def test_windows_build_runs_automatic_optimization_acceptance(self) -> None:
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        smoke = PROJECT_ROOT / "tools" / "optimization_ui_smoke.py"
        window = (
            PROJECT_ROOT / "src" / "ble_calibration" / "ui" / "main_window.py"
        )

        self.assertIn("tools\\optimization_ui_smoke.py", script)
        self.assertIn("optimization-workflow.json", script)
        self.assertIn("optimization.png", script)
        self.assertIn("GuiCallbackBridge(QObject)", smoke.read_text(encoding="utf-8"))
        self.assertIn("QueuedConnection", smoke.read_text(encoding="utf-8"))
        self.assertIn("Qt.ConnectionType.QueuedConnection", window.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
