"""Offline Linux server regressions with isolated data and no system changes."""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main
from core import runmode, autostart, gateway, paths, settings


class LinuxServerTests(unittest.TestCase):
    def setUp(self):
        self.mode_patch = patch.object(runmode, "_mode", "server")
        self.mode_patch.start()
        self.addCleanup(self.mode_patch.stop)
        self.database_patch = patch.object(main.keys, "_db_path", None)
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="aipool-server-")
        self.home = Path(self.temp.name).resolve()
        self.saved_paths = paths._override, paths._resolved
        self.saved_host = settings._listen_host_override
        settings.set_listen_host_override("")
        paths.set_data_dir(self.home / "data")
        settings.reset_cache()
        settings.save({"ui_port": 18730, "pool_port": 18731})
        self.platform = patch.object(sys, "platform", "linux")
        self.platform.start()
        self.env = patch.dict(os.environ, {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "config"),
                                          "AIPOOL_MODE": "server", "DISPLAY": "", "WAYLAND_DISPLAY": ""})
        self.env.start()
        self.output = io.StringIO()
        self.stdout = contextlib.redirect_stdout(self.output)
        self.stdout.__enter__()

    def tearDown(self):
        self.stdout.__exit__(None, None, None)
        self.env.stop()
        self.platform.stop()
        settings.set_listen_host_override(self.saved_host)
        settings.reset_cache()
        paths._override, paths._resolved = self.saved_paths
        self.temp.cleanup()

    def request(self, method, url, *, local=False, **kwargs):
        async def run():
            transport = httpx.ASGITransport(app=gateway.create_app(), client=("127.0.0.1" if local else "198.51.100.10", 18739))
            async with httpx.AsyncClient(transport=transport, base_url="http://server") as client:
                return await client.request(method, url, **kwargs)
        return asyncio.run(run())

    def test_banner_and_password_warning(self):
        with patch("core.netinfo.local_ips", return_value=["192.168.1.22"]):
            main._print_server_banner(settings.load())
        text = self.output.getvalue()
        for expected in ("Linux 服务器模式", "http://127.0.0.1:18730/", "http://192.168.1.22:18730/",
                         str(paths.data_dir()), "号池端口：18731", "未设置管理密码：只能从本机访问管理页面；远程管理请先设置密码或使用 ssh -L 8320:127.0.0.1:8320 隧道",
                         "ssh -L 18730:127.0.0.1:18730"):
            self.assertIn(expected, text)
        self.output.seek(0)
        self.output.truncate()
        main._print_server_banner({**settings.load(), "admin_password": "secret", "listen_host": "127.0.0.1"})
        self.assertNotIn("未设置管理密码", self.output.getvalue())
        self.assertNotIn("LAN URL", self.output.getvalue())

    def test_frozen_linux_portable_and_xdg_data(self):
        paths._override = paths._resolved = None
        executable = self.home / "installed/AI号池管理器"
        with patch.object(paths, "is_frozen", return_value=True), patch.object(sys, "executable", str(executable)):
            self.assertEqual(paths.data_dir(), executable.parent / "data")
            paths._resolved = None
            with patch.object(paths, "_writable", return_value=False), patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home / "xdg-data")}):
                self.assertEqual(paths.data_dir(), self.home / "xdg-data/AIPool")

    def test_log_mirrors_only_linux(self):
        main._log("journal test")
        self.assertIn("journal test", self.output.getvalue())
        self.assertEqual(self.output.getvalue(), (paths.logs_dir() / "app.log").read_text())
        for platform in ("darwin", "win32"):
            self.output.seek(0)
            self.output.truncate()
            with patch.object(sys, "platform", platform):
                main._log("file only")
            self.assertEqual(self.output.getvalue(), "")

    def test_set_password_and_existing_remote_auth(self):
        self.assertEqual(self.request("POST", "/api/login", json={"password": ""}).status_code, 403)
        self.assertEqual(self.request("GET", "/api/settings").status_code, 401)
        with patch.dict(os.environ, {"AIPOOL_ADMIN_PASSWORD": "  server-test-password  "}), patch.object(main, "_take_lock") as take_lock:
            self.assertEqual(main.main(["--server", "--set-password"]), 0)
            take_lock.assert_not_called()
        settings.reset_cache()
        self.assertEqual(settings.load()["admin_password"], "server-test-password")
        self.assertNotIn("server-test-password", self.output.getvalue())
        self.assertEqual(self.request("POST", "/api/login", json={"password": "wrong"}).status_code, 401)
        response = self.request("POST", "/api/login", json={"password": "server-test-password"})
        self.assertEqual(response.status_code, 200)
        session = response.cookies["aipool_sid"]
        try:
            self.assertEqual(self.request("GET", "/api/settings", cookies={"aipool_sid": session}).status_code, 200)
        finally:
            gateway._sessions.pop(session, None)

    def test_blank_password_rejected_and_getpass_supported(self):
        settings.save({"admin_password": "original"})
        with patch.dict(os.environ, {"AIPOOL_ADMIN_PASSWORD": "  "}), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main.main(["--server", "--set-password"]), 1)
        self.assertEqual(settings.load()["admin_password"], "original")
        environment = dict(os.environ)
        environment.pop("AIPOOL_ADMIN_PASSWORD", None)
        with patch.dict(os.environ, environment, clear=True), patch.object(main.getpass, "getpass", return_value="prompt-password") as prompt:
            self.assertEqual(main.main(["--server", "--set-password"]), 0)
            prompt.assert_called_once()
        self.assertEqual(settings.load()["admin_password"], "prompt-password")

    def test_host_override_and_no_browser(self):
        saved_host = settings.load()["listen_host"]
        with patch.object(main, "_take_lock", return_value=None), patch.object(main, "_prepare_ports"), patch.object(main, "_seed_accounts"), patch.object(main.accounts, "sync_all"), patch.object(main, "_apply_autostart"), patch.object(main, "_boot_pool_async", return_value=Mock()), patch.object(main.pool, "stop"), patch.object(main.webbrowser, "open") as browser, patch("uvicorn.run") as run:
            self.assertEqual(main.main(["--host", "127.0.0.1"]), 0)
            self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
            browser.assert_not_called()
        self.assertEqual(settings.load()["listen_host"], "127.0.0.1")
        settings.save({"ui_port": 18732})
        settings.save({**settings.load(), "first_run": False}, replace=True)
        self.assertEqual(json.loads(settings.path().read_text())["listen_host"], saved_host)
        settings.set_listen_host_override("")
        settings.reset_cache()
        self.assertEqual(settings.load()["listen_host"], saved_host)

    def test_existing_and_stalled_instances_are_headless(self):
        with patch.object(main, "_take_lock", return_value=12345), patch.object(main, "_wait_existing", return_value="http://127.0.0.1:18730/"), patch.object(main.webbrowser, "open") as browser:
            self.assertEqual(main.main([]), 0)
            browser.assert_not_called()
        error = io.StringIO()
        with patch.object(main, "_take_lock", return_value=12345), patch.object(main, "_wait_existing", return_value=""), patch.object(main.subprocess, "Popen") as popen, contextlib.redirect_stderr(error):
            self.assertEqual(main.main([]), 1)
            popen.assert_not_called()
        self.assertIn("请执行 kill 12345 结束进程号 12345，然后重新启动本程序。", error.getvalue())

    def test_systemd_write_remove_and_quoting_without_systemctl(self):
        unit = self.home / "config/systemd/user/aipool-manager.service"
        executable = str(self.home / 'space dir/quoted"\\%$app')
        with patch.object(autostart.shutil, "which", return_value=None), patch.object(sys, "executable", executable), patch.object(paths, "is_frozen", return_value=False):
            main._apply_autostart(True)
            self.assertFalse(unit.exists())
            with patch.object(paths, "is_frozen", return_value=True):
                main._apply_autostart(True)
            text = unit.read_text()
            self.assertIn('ExecStart="' + executable.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '" --server', text)
            for expected in ("Restart=on-failure", "KillSignal=SIGTERM", "WantedBy=default.target"):
                self.assertIn(expected, text)
            main._apply_autostart(False)
            main._apply_autostart(False)
            self.assertFalse(unit.exists())
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}), patch.object(autostart.shutil, "which", return_value=None), patch.object(paths, "is_frozen", return_value=True):
            main._apply_autostart(True)
            self.assertTrue((self.home / ".config/systemd/user/aipool-manager.service").exists())

    def test_systemctl_failures_logged_and_toggle_applies(self):
        with patch.object(autostart.shutil, "which", return_value="/usr/bin/systemctl"), patch.object(autostart.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "no user bus")) as run, patch.object(paths, "is_frozen", return_value=True):
            main._apply_autostart(True)
            self.assertEqual([call.args[0][2] for call in run.call_args_list], ["daemon-reload", "enable"])
            self.assertIn("no user bus", self.output.getvalue())
            main._apply_autostart(False)
            self.assertEqual(run.call_args.args[0][2], "disable")
        with patch.dict(sys.modules, {"main": None}), patch.object(autostart, "apply") as apply:
            response = self.request("PUT", "/api/settings", local=True, json={"autostart": True})
            self.assertEqual(response.status_code, 200)
            apply.assert_called_once_with(True)

    def test_main_wrapper_supplies_log_callback(self):
        with patch.object(autostart, "apply") as apply:
            main._apply_autostart(True)
            apply.assert_called_once_with(True, log=main._log)

    def test_windows_registry_behavior(self):
        registry = Mock()
        executable = r'C:\Program Files\AIPool\manager.exe'
        with patch.object(sys, "platform", "win32"), patch.object(autostart.os, "name", "nt"), patch.object(sys, "executable", executable), patch.dict(sys.modules, {"winreg": registry}), patch.object(paths, "is_frozen", return_value=True):
            autostart.apply(True)
            registry.OpenKey.assert_called_with(registry.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, registry.KEY_SET_VALUE)
            registry.SetValueEx.assert_called_once_with(registry.OpenKey.return_value, "AIPoolManager", 0, registry.REG_SZ, f'"{executable}"')
            autostart.apply(False)
            registry.DeleteValue.assert_called_once_with(registry.OpenKey.return_value, "AIPoolManager")
            registry.DeleteValue.side_effect = FileNotFoundError
            with patch.object(paths, "is_frozen", return_value=False):
                autostart.apply(True)
            self.assertEqual(registry.SetValueEx.call_count, 1)
            self.assertEqual(registry.CloseKey.call_count, 3)

    def test_open_folder_never_launches_desktop(self):
        with patch.object(gateway.subprocess, "Popen") as popen:
            self.assertEqual(self.request("POST", "/api/system/open-folder").status_code, 401)
            response = self.request("POST", "/api/system/open-folder", local=True)
            self.assertEqual(response.json(), {"ok": True, "dir": str(paths.data_dir()), "opened": False})
            popen.assert_not_called()

    def test_linux_archive_preserves_symlinks_and_modes(self):
        spec = importlib.util.spec_from_file_location("linux_build_test", ROOT / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        self.assertEqual(build.DIST.name, "AI号池管理器-linux")
        folder = self.home / "dist/AI号池管理器-linux"
        folder.mkdir(parents=True)
        executable = folder / "executable"
        executable.write_text("test")
        executable.chmod(0o755)
        (folder / "link").symlink_to("executable")
        archive = self.home / "server.tar.gz"
        with patch.object(build, "DIST", folder):
            build._make_zip(archive)
        with tarfile.open(archive) as result:
            self.assertEqual(result.getmember(folder.name + "/executable").mode, 0o755)
            self.assertTrue(result.getmember(folder.name + "/link").issym())


if __name__ == "__main__":
    unittest.main(verbosity=2)
