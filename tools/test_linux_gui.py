"""Offline Linux desktop integration tests, runnable on macOS and Linux."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main
import build as builder
from core import gateway, paths
from core.linux_desktop import desktop_exec
from fastapi.testclient import TestClient


class LinuxGuiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aipool-linux-gui-")
        self.root = Path(self.temporary.name).resolve()
        self.saved_paths = paths._override, paths._resolved
        paths.set_data_dir(self.root / "data")
        self.platform = patch.object(sys, "platform", "linux")
        self.platform.start()
        self.environment = patch.dict(os.environ, {"HOME": str(self.root),
            "XDG_CONFIG_HOME": str(self.root / "config"), "DISPLAY": "", "WAYLAND_DISPLAY": ""})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.platform.stop()
        paths._override, paths._resolved = self.saved_paths
        self.temporary.cleanup()

    def test_alert_selection_and_headless(self):
        with patch.object(main.subprocess, "Popen") as launch, patch.object(main.shutil, "which") as which:
            main._alert("test")
            launch.assert_not_called()
            which.assert_not_called()
            for display in ("DISPLAY", "WAYLAND_DISPLAY"):
                for selected in ("zenity", "kdialog", "notify-send", None):
                    with self.subTest(display=display, tool=selected), patch.dict(os.environ, {display: ":1"}):
                        launch.reset_mock()
                        which.side_effect = lambda tool: f"/usr/bin/{tool}" if tool == selected else None
                        main._alert("hello")
                        if selected:
                            self.assertEqual(launch.call_args.args[0][0], f"/usr/bin/{selected}")
                            self.assertNotIn("shell", launch.call_args.kwargs)
                        else:
                            launch.assert_not_called()

    def test_alert_launch_failure_falls_back(self):
        with patch.dict(os.environ, {"DISPLAY": ":1"}), patch.object(main.shutil, "which", side_effect=lambda tool: tool), patch.object(main.subprocess, "Popen", side_effect=[OSError(), object()]) as launch:
            main._alert("hello")
            self.assertEqual(launch.call_args.args[0][0], "kdialog")

    def test_browser_headless_and_desktop(self):
        with patch.object(main.webbrowser, "open") as browser, patch("builtins.print") as output, patch.object(main, "_log") as log:
            main._open_browser("http://127.0.0.1:18710/")
            browser.assert_not_called()
            output.assert_called_once()
            log.assert_called_once()
            with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
                main._open_browser("http://127.0.0.1:18710/")
            browser.assert_called_once()

    def test_exec_escaping(self):
        self.assertEqual(desktop_exec('/tmp/a b"$`\\%/app'),
                         '"/tmp/a b' + '\\\\"' + '\\\\$' + '\\\\`' + '\\\\\\\\' + '%%/app"')

    def test_frozen_data_directory(self):
        paths._override = paths._resolved = None
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(self.root / "app/manager")):
            self.assertEqual(paths.data_dir(), self.root / "app/data")
            paths._resolved = None
            with patch.object(paths, "_writable", return_value=False), patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "xdg")}):
                self.assertEqual(paths.data_dir(), self.root / "xdg/AIPool")

    def test_autostart_write_remove(self):
        entry = self.root / "config/autostart/aipool-manager.desktop"
        executable = '/tmp/a b"$`\\%/app'
        with patch.object(paths, "is_frozen", return_value=False):
            main._apply_autostart(True)
            self.assertFalse(entry.exists())
        with patch.object(paths, "is_frozen", return_value=True), patch.object(sys, "executable", executable):
            main._apply_autostart(True)
            self.assertIn(f"Exec={desktop_exec(executable)}\n", entry.read_text())
            self.assertEqual(list(entry.parent.iterdir()), [entry])
        with patch.object(paths, "is_frozen", return_value=False):
            main._apply_autostart(False)
            main._apply_autostart(False)
        self.assertFalse(entry.exists())
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}), patch.object(paths, "is_frozen", return_value=True):
            main._apply_autostart(True)
            self.assertTrue((self.root / ".config/autostart/aipool-manager.desktop").exists())
            main._apply_autostart(False)

    def test_open_folder(self):
        with TestClient(gateway.create_app(), client=("127.0.0.1", 18710)) as client, patch.object(gateway.subprocess, "Popen") as launch:
            response = client.post("/api/system/open-folder")
            self.assertEqual(response.json(), {"ok": True, "dir": str(paths.data_dir()), "opened": False})
            launch.assert_not_called()
            with patch.dict(os.environ, {"DISPLAY": ":1"}), patch.object(gateway.shutil, "which", return_value=None):
                self.assertFalse(client.post("/api/system/open-folder").json()["opened"])
            with patch.dict(os.environ, {"DISPLAY": ":1"}), patch.object(gateway.shutil, "which", return_value="/usr/bin/xdg-open"):
                self.assertTrue(client.post("/api/system/open-folder").json()["opened"])
                launch.assert_called_once_with(["/usr/bin/xdg-open", str(paths.data_dir())])

    def test_launcher_install_uninstall(self):
        package = self.root / 'package space "$`\\%'
        package.mkdir()
        for filename in ("install-desktop.sh", "aipool-manager.desktop"):
            shutil.copy2(ROOT / "packaging/linux" / filename, package / filename)
        shutil.copy2(ROOT / "web/logo.png", package / "aipool-manager.png")
        executable = package / "AI号池管理器/AI号池管理器"
        executable.parent.mkdir()
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        environment = dict(os.environ, XDG_DATA_HOME="")
        launcher = self.root / ".local/share/applications/aipool-manager.desktop"
        icon = self.root / ".local/share/icons/hicolor/128x128/apps/aipool-manager.png"
        for unused in range(2):
            subprocess.run(["bash", str(package / "install-desktop.sh")], env=environment, check=True, capture_output=True)
            self.assertIn(f"Exec={desktop_exec(str(executable))}\n", launcher.read_text())
            self.assertEqual(icon.read_bytes(), (ROOT / "web/logo.png").read_bytes())
        subprocess.run(["bash", str(package / "install-desktop.sh"), "--uninstall"], env=environment, check=True, capture_output=True)
        self.assertFalse(launcher.exists())
        self.assertFalse(icon.exists())
        self.assertTrue(executable.exists())

    def test_linux_archive_preserves_modes_and_links(self):
        distribution = self.root / "dist/AI号池管理器-linux"
        application = distribution / "AI号池管理器"
        application.mkdir(parents=True)
        executable = application / "AI号池管理器"
        executable.write_text("executable")
        executable.chmod(0o755)
        (application / "link").symlink_to(executable.name)
        archive = self.root / "gui.tar.gz"
        with patch.object(builder, "IS_LINUX", True), patch.object(builder, "DIST", distribution):
            builder._make_zip(archive)
        with tarfile.open(archive) as stream:
            member = stream.getmember(f"{distribution.name}/{application.name}/{executable.name}")
            self.assertEqual(member.mode & 0o777, 0o755)
            self.assertTrue(stream.getmember(f"{distribution.name}/{application.name}/link").issym())

    def test_linux_retirement_leaves_other_platforms(self):
        output = self.root / "dist"
        output.mkdir()
        distribution = output / "AI号池管理器-linux"
        distribution.mkdir()
        archive = output / f"AI号池管理器-linux-gui-{builder.platform.machine()}.tar.gz"
        archive.touch()
        other_outputs = [output / name for name in ("AI号池管理器", "AI号池管理器-macOS", "server.tar.gz")]
        for other in other_outputs:
            other.touch()
        with patch.object(builder, "IS_LINUX", True), patch.object(builder, "IS_MAC", False), patch.object(builder, "ROOT", self.root), patch.object(builder, "DIST_ROOT", output), patch.object(builder, "DIST", distribution):
            retired = builder._retire_old_dist()
        self.assertEqual(retired.parent, self.root / ".build-trash")
        self.assertFalse(distribution.exists())
        self.assertTrue((retired / distribution.name).exists())
        self.assertTrue((retired / archive.name).exists())
        self.assertTrue(all(other.exists() for other in other_outputs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
