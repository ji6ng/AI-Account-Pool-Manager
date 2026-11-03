"""Unified Linux mode resolution and cross-mode startup regressions."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main
from core import autostart, paths, runmode


class LinuxModeTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch.object(sys, "platform", "linux")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.state = patch.object(runmode, "_mode", "")
        self.state.start()
        self.addCleanup(self.state.stop)
        self.environment = patch.dict(os.environ, {"AIPOOL_MODE": "", "DISPLAY": "", "WAYLAND_DISPLAY": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_resolution_priority_and_source(self):
        for flag in (None, "desktop", "server"):
            for environment in ("", "desktop", "server", "INVALID"):
                for display in ("", ":1"):
                    with self.subTest(flag=flag, environment=environment, display=display), patch.dict(
                            os.environ, {"AIPOOL_MODE": environment, "DISPLAY": display}):
                        messages = []
                        expected = flag or (environment if environment in ("desktop", "server") else
                                            ("desktop" if display else "server"))
                        source = "flag" if flag else ("env" if environment in ("desktop", "server") else "auto")
                        self.assertEqual(runmode.set_mode(flag, log=messages.append), expected)
                        self.assertEqual(runmode.mode(), expected)
                        self.assertEqual(runmode.is_server(), expected == "server")
                        self.assertEqual(runmode.is_desktop(), expected == "desktop")
                        self.assertIn(f"source={source}", messages[-1])
                        self.assertEqual(any("警告" in message for message in messages), environment == "INVALID")
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
            self.assertEqual(runmode.set_mode(log=lambda message: None), "desktop")

    def test_native_platforms_ignore_linux_options(self):
        for platform in ("darwin", "win32"):
            with patch.object(sys, "platform", platform):
                self.assertEqual(runmode.set_mode("server"), "native")
                self.assertEqual(runmode.mode(), "native")
                self.assertFalse(runmode.is_server())
                self.assertFalse(runmode.is_desktop())

    def test_cli_flags_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main.main(["--server", "--desktop"])
        self.assertEqual(raised.exception.code, 2)

    def test_resolved_mode_survives_display_disappearing(self):
        with patch.dict(os.environ, {"DISPLAY": ":1"}):
            runmode.set_mode(log=lambda message: None)
        self.assertTrue(runmode.is_desktop())
        with patch.object(main.webbrowser, "open") as browser, patch("builtins.print") as output, \
                patch.object(main, "_log") as log:
            main._open_browser("http://127.0.0.1:18780/")
            browser.assert_not_called()
            output.assert_called_once()
            log.assert_called_once()

    def test_desktop_log_does_not_mirror_stdout(self):
        with patch.object(runmode, "_mode", "desktop"), patch.object(main.paths, "logs_dir"), \
                patch("builtins.open"), contextlib.redirect_stdout(io.StringIO()) as output:
            main._log("file only")
        self.assertEqual(output.getvalue(), "")

    def test_switching_removes_other_artifact_and_disable_removes_both(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}), \
                patch.object(paths, "is_frozen", return_value=True), \
                patch.object(autostart.shutil, "which", return_value="/usr/bin/systemctl"), \
                patch.object(autostart.subprocess, "run") as systemctl:
            systemctl.return_value.returncode = 0
            entry = Path(directory) / "autostart/aipool-manager.desktop"
            unit = Path(directory) / "systemd/user/aipool-manager.service"
            for selected in ("server", "desktop", "server", "desktop"):
                runmode.set_mode(selected, log=lambda message: None)
                autostart.apply(True)
                self.assertEqual(unit.exists(), selected == "server")
                self.assertEqual(entry.exists(), selected == "desktop")
                self.assertIn(" --" + selected, (unit if selected == "server" else entry).read_text())
                self.assertEqual(systemctl.call_args.args[0][2], "enable" if selected == "server" else "disable")
            unit.write_text("stale")
            autostart.apply(False)
            self.assertFalse(unit.exists())
            self.assertFalse(entry.exists())

    def test_default_launcher_and_system_service(self):
        launcher = (ROOT / "packaging/linux/aipool-manager.desktop").read_text()
        self.assertIn("Exec=@EXEC@\n", launcher)
        self.assertNotIn("--server", launcher)
        service = (ROOT / "packaging/linux/aipool-manager.service").read_text()
        self.assertIn(" --server ", service)
        self.assertNotIn("--no-browser", service)


if __name__ == "__main__":
    unittest.main(verbosity=2)
