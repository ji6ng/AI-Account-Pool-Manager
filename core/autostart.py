"""Platform startup registration, shared by the entrypoint and settings API."""
from __future__ import annotations

import logging
import os
import pathlib
import plistlib
import shutil
import subprocess
import sys
from typing import Callable

from . import paths


def apply(enabled: bool, log: Callable[[str], None] = logging.getLogger(__name__).warning) -> None:
    if sys.platform.startswith("linux"):
        if enabled and not paths.is_frozen():
            return
        try:
            config = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
            unit = config / "systemd/user/aipool-manager.service"
            if enabled:
                executable = sys.executable.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
                executable = executable.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
                unit.parent.mkdir(parents=True, exist_ok=True)
                temporary = unit.with_suffix(".service.tmp")
                temporary.write_text(
                    '[Unit]\nDescription=AI Pool Manager (headless)\n\n[Service]\n'
                    f'ExecStart="{executable}" --no-browser\n'
                    'Restart=on-failure\nKillSignal=SIGTERM\n\n[Install]\nWantedBy=default.target\n',
                    encoding="utf-8",
                )
                temporary.replace(unit)
            else:
                unit.unlink(missing_ok=True)
            systemctl = shutil.which("systemctl")
            if systemctl:
                for command in ([systemctl, "--user", "daemon-reload"],
                                [systemctl, "--user", "enable" if enabled else "disable", "aipool-manager.service"]):
                    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
                    if result.returncode:
                        log(f"设置开机自启失败：{' '.join(command)}: {result.stderr.strip()}")
        except Exception as exc:
            log(f"设置开机自启失败：{exc}")
        return
    if sys.platform == "darwin":
        try:
            agent = pathlib.Path.home() / "Library" / "LaunchAgents" / "com.aipool.manager.plist"
            if not enabled:
                agent.unlink(missing_ok=True)
            elif paths.is_frozen():
                agent.parent.mkdir(parents=True, exist_ok=True)
                payload = {"Label": "com.aipool.manager",
                           "ProgramArguments": [sys.executable], "RunAtLoad": True}
                temporary = agent.with_suffix(".plist.tmp")
                temporary.write_bytes(plistlib.dumps(payload))
                temporary.replace(agent)
        except Exception as exc:
            log(f"设置开机自启失败：{exc}")
        return
    if os.name != "nt":
        return
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                             winreg.KEY_SET_VALUE)
        name = "AIPoolManager"
        if enabled and paths.is_frozen():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as exc:
        log(f"设置开机自启失败：{exc}")
