"""Offline platform regressions: temporary homes and isolated process groups only."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import json
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main
from core import accounts, autostart, clients, paths, ports
from core.pool import Pool


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aipool-platform-")
        self.home = Path(self.temp.name).resolve()
        self.saved_paths = (paths._override, paths._resolved)
        paths._override = paths._resolved = None
        self.env = patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()

    def tearDown(self):
        paths._override, paths._resolved = self.saved_paths
        self.env.stop()
        self.temp.cleanup()

    def test_vendor_names_and_resource_root(self):
        for platform, name in (("win32", "cli-proxy-api.exe"), ("darwin", "cli-proxy-api"), ("linux", "cli-proxy-api")):
            with self.subTest(platform=platform), patch.object(sys, "platform", platform), patch.object(paths, "res_dir", return_value=self.home):
                self.assertEqual(paths.vendor_bin_name(), name)
                self.assertEqual(paths.vendor_bin(), self.home / "vendor" / name)

    def test_frozen_bundle_data_outside_app(self):
        exe = self.home / "Applications/AI号池管理器.app/Contents/MacOS/AI号池管理器"
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(exe)):
            self.assertEqual(paths.app_dir(), self.home / "Applications")
            self.assertEqual(paths.data_dir(), self.home / "Library/Application Support/AIPool")
            self.assertFalse((exe.parent / "data").exists())
            paths.set_data_dir(self.home / "override")
            self.assertEqual(paths.data_dir(), self.home / "override")

    def test_dev_data_and_platform_fallbacks(self):
        for platform in ("darwin", "linux", "win32"):
            with self.subTest(platform=platform), patch.object(sys, "platform", platform), patch.object(sys, "frozen", False, create=True), patch.object(paths, "app_dir", return_value=self.home / "repo"):
                paths._resolved = None
                self.assertEqual(paths.data_dir(), self.home / "repo/data")
        for platform, base in (("darwin", self.home / "Library/Application Support"), ("linux", self.home / "xdg"), ("win32", self.home / "roaming")):
            with self.subTest(fallback=platform), patch.object(sys, "platform", platform), patch.object(paths, "_writable", return_value=False), patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home / "xdg"), "APPDATA": str(self.home / "roaming")}):
                paths._resolved = None
                self.assertEqual(paths.data_dir(), base / "AIPool")
        with patch.object(sys, "platform", "linux"), patch.object(paths, "_writable", return_value=False), patch.dict(os.environ, {"XDG_DATA_HOME": ""}):
            paths._resolved = None
            self.assertEqual(paths.data_dir(), self.home / ".local/share/AIPool")

    def test_windows_frozen_portable(self):
        with patch.object(sys, "platform", "win32"), patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(self.home / "manager.exe")):
            self.assertEqual(paths.data_dir(), self.home / "data")

    def test_launchagent_write_remove(self):
        agent = self.home / "Library/LaunchAgents/com.aipool.manager.plist"
        exe = str(self.home / "AI号池管理器.app/Contents/MacOS/AI号池管理器")
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "executable", exe), patch.object(sys, "frozen", False, create=True):
            autostart.apply(True)
            self.assertFalse(agent.exists())
            with patch.object(sys, "frozen", True):
                autostart.apply(True)
                self.assertEqual(plistlib.loads(agent.read_bytes()), {"Label": "com.aipool.manager", "ProgramArguments": [exe], "RunAtLoad": True})
            autostart.apply(False)
            self.assertFalse(agent.exists())
            autostart.apply(False)

    def test_alert_escaping_and_failure(self):
        with patch.object(sys, "platform", "darwin"), patch.object(main.subprocess, "Popen") as spawn:
            main._alert('quote " and \\ slash\nnext')
            command = spawn.call_args.args[0]
            self.assertEqual(command[:2], ["osascript", "-e"])
            self.assertIn('quote \\" and \\\\ slash\\nnext', command[2])
            self.assertIn('buttons {"好"}', command[2])
            spawn.side_effect = OSError("unavailable")
            main._alert("ignored failure")

    @unittest.skipIf(os.name == "nt", "POSIX executable modes")
    def test_prepare_executable_and_quarantine_best_effort(self):
        binary = self.home / "cli-proxy-api"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o600)
        with patch.object(sys, "platform", "darwin"), patch.object(paths.subprocess, "run", side_effect=OSError("no xattr")) as run:
            paths.prepare_vendor_bin(binary)
            self.assertTrue(os.access(binary, os.X_OK))
            self.assertEqual(run.call_args.args[0], ["xattr", "-d", "com.apple.quarantine", str(binary)])

    def test_home_and_port_probes(self):
        with patch.dict(os.environ, {"USERPROFILE": "", "AIPOOL_CLIENT_HOME": ""}):
            self.assertEqual(clients.client_home(), self.home)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            self.assertFalse(ports.is_free(port, "127.0.0.1"))
            listener.listen()
            self.assertFalse(ports.is_free(port, "127.0.0.1"))
            self.assertTrue(ports.wait_open(port, timeout=1))

    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    def test_pool_stop_and_login_cancel_kill_grandchild(self):
        script = '''import subprocess, signal, sys, time
child = subprocess.Popen(["sleep", "60"])
def finish(*args):
    child.wait(timeout=3)
    sys.exit(0)
signal.signal(signal.SIGTERM, finish)
print(child.pid, flush=True)
while True: time.sleep(.1)
'''
        for mode in ("pool", "login"):
            with self.subTest(mode=mode):
                proc = subprocess.Popen([sys.executable, "-c", script], start_new_session=True, stdout=subprocess.PIPE, text=True)
                child_pid = int(proc.stdout.readline())
                try:
                    if mode == "pool":
                        pool = Pool()
                        pool.proc = proc
                        self.assertTrue(pool.stop(timeout=4)["ok"])
                        self.assertFalse(pool.running())
                    else:
                        accounts._sessions["platform-test"] = {"proc": proc}
                        self.assertTrue(accounts.cancel_login("platform-test")["ok"])
                    self.assertIsNotNone(proc.poll())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(child_pid, 0)
                finally:
                    accounts._sessions.pop("platform-test", None)
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=5)
                    proc.stdout.close()

    def test_cancel_delayed_pool_startup(self):
        stopping = main.threading.Event()
        stopping.set()
        with patch.object(main.pool, "start") as start:
            worker = main._boot_pool_async(stopping)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            start.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX signal shutdown")
    def test_main_signals_clean_up_async_pool_and_instance(self):
        for mode, signum in (("started", signal.SIGTERM), ("starting", signal.SIGTERM),
                             ("started", signal.SIGHUP)):
            with self.subTest(mode=mode, signal=signum), tempfile.TemporaryDirectory(dir=self.home) as temporary:
                root = Path(temporary)
                data = root / "data"
                auth = data / "pool/auths"
                auth.mkdir(parents=True)
                (auth / "offline.json").write_text("{}")
                (data / "settings.json").write_text('{"listen_host": "127.0.0.1"}')
                pid_file = root / "child.pid"
                marker = root / "starting"
                fake = root / "fake-vendor"
                fake.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport os,time\nPath({str(pid_file)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n")
                fake.chmod(0o700)
                launcher = (
                    "import sys,time; from pathlib import Path; import main; "
                    f"main.paths.vendor_bin=lambda: Path({str(fake)!r}); "
                )
                if mode == "starting":
                    launcher += (
                        "original=main.pool.start\n"
                        "def slow_start(*a, **kw):\n"
                        f"    Path({str(marker)!r}).touch()\n"
                        "    time.sleep(.6)\n"
                        "    result=original(*a, **kw)\n"
                        "    deadline=time.monotonic()+5\n"
                        f"    while not Path({str(pid_file)!r}).exists() and main.pool.running() and time.monotonic()<deadline: time.sleep(.02)\n"
                        "    return result\n"
                        "main.pool.start=slow_start\n"
                    )
                launcher += f"sys.exit(main.main(['--no-browser','--data-dir',{str(data)!r}]))"
                proc = subprocess.Popen([sys.executable, "-c", launcher], cwd=ROOT,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        self.assertIsNone(proc.poll(), "main exited before startup")
                        try:
                            instance = json.loads((data / ".instance").read_text())
                            with urllib.request.urlopen(f'http://127.0.0.1:{instance["ui_port"]}/api/whoami', timeout=.3) as response:
                                ready = response.status == 200
                            if ready and (marker if mode == "starting" else pid_file).exists():
                                break
                        except (OSError, ValueError, KeyError):
                            pass
                        time.sleep(.02)
                    else:
                        self.fail("main/pool did not start")
                    proc.send_signal(signum)
                    self.assertEqual(proc.wait(timeout=12), 0)
                    self.assertFalse((data / ".instance").exists())
                    self.assertTrue(pid_file.exists(), (data / "logs/app.log").read_text() + (data / "logs/pool.log").read_text())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(pid_file.read_text()), 0)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)
                    if pid_file.exists():
                        try:
                            os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    def test_stop_escalates_when_sigterm_ignored(self):
        proc = subprocess.Popen([sys.executable, "-c", 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready", flush=True); time.sleep(60)'], start_new_session=True, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            accounts._stop_process(proc, timeout=.1)
            self.assertEqual(proc.returncode, -signal.SIGKILL)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            proc.stdout.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
