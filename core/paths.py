"""路径解析：区分「程序文件」和「用户数据」。

打包后：
  - 程序资源（web 页面、内置的 cli-proxy-api）在 sys._MEIPASS 里，只读；
  - Windows 优先使用程序同级的 data，不可写时退回 %APPDATA%\\AIPool；
  - macOS 使用 ~/Library/Application Support/AIPool，不向 .app 包内写数据。
开发态优先放在工程根目录下，不可写时使用系统用户数据目录。
"""
from __future__ import annotations

import os
import pathlib
import secrets
import subprocess
import sys
import tempfile
import threading

APP_NAME = "AIPool"
PRODUCT_NAME = "AI 号池管理器"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> pathlib.Path:
    """程序所在目录（macOS 为 .app 的上级目录，开发态为工程根目录）。"""
    if is_frozen():
        executable = pathlib.Path(sys.executable).resolve()
        if sys.platform == "darwin":
            for parent in executable.parents:
                if parent.suffix == ".app":
                    return parent.parent
        return executable.parent
    return pathlib.Path(__file__).resolve().parent.parent


def res_dir() -> pathlib.Path:
    """只读资源目录（web/、vendor/）。"""
    if is_frozen():
        return pathlib.Path(getattr(sys, "_MEIPASS", app_dir()))
    return app_dir()


def _writable(d: pathlib.Path) -> bool:
    """能不能往这个目录写。

    探针文件名必须唯一：之前用 inode 命名，多个线程同时判断时
    会互相 unlink 对方的探针，失败的一方就此回退到 %APPDATA%，
    于是"数据目录突然换了"，读不到账号。
    """
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".w{os.getpid()}-{threading.get_ident()}-{secrets.token_hex(4)}.tmp"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


_override: pathlib.Path | None = None
_resolved: pathlib.Path | None = None
_resolve_lock = threading.Lock()


def set_data_dir(p: pathlib.Path) -> None:
    """测试/命令行用：强制指定数据目录（统一转绝对路径，子进程要靠它）。"""
    global _override, _resolved
    _override = pathlib.Path(p).expanduser().resolve()
    _override.mkdir(parents=True, exist_ok=True)
    _resolved = None


def data_dir() -> pathlib.Path:
    global _resolved
    if _override is not None:
        _override.mkdir(parents=True, exist_ok=True)
        return _override

    # 只判断一次：这个结论在一次运行里不会变，反复探测既慢又会互相干扰
    if _resolved is not None:
        return _resolved
    with _resolve_lock:
        if _resolved is not None:
            return _resolved
        portable = app_dir() / "data"
        if sys.platform == "darwin" and is_frozen():
            _resolved = pathlib.Path.home() / "Library" / "Application Support" / APP_NAME
            _resolved.mkdir(parents=True, exist_ok=True)
        elif _writable(portable):
            _resolved = portable
        else:
            if sys.platform == "win32":
                base = pathlib.Path(os.getenv("APPDATA") or tempfile.gettempdir())
            elif sys.platform == "darwin":
                base = pathlib.Path.home() / "Library" / "Application Support"
            else:
                base = pathlib.Path(os.getenv("XDG_DATA_HOME") or pathlib.Path.home() / ".local" / "share")
            roaming = base / APP_NAME
            roaming.mkdir(parents=True, exist_ok=True)
            _resolved = roaming
        return _resolved


def pool_dir() -> pathlib.Path:
    """cli-proxy-api 的工作目录：配置、账号、日志都在这里。"""
    d = data_dir() / "pool"
    d.mkdir(parents=True, exist_ok=True)
    return d


def auth_dir() -> pathlib.Path:
    d = pool_dir() / "auths"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> pathlib.Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def auth_file(name: str) -> pathlib.Path | None:
    """Accept a credential filename, never a path outside the credential directory."""
    if not name or any(ch in name for ch in ("/", "\\", ":", "\x00")) or not name.endswith(".json"):
        return None
    root = auth_dir().resolve()
    candidate = (root / name).resolve()
    return candidate if candidate.parent == root else None


def vendor_bin_name() -> str:
    """内置程序文件名：只有 Windows 带 .exe 后缀。"""
    return "cli-proxy-api.exe" if sys.platform == "win32" else "cli-proxy-api"


def vendor_bin() -> pathlib.Path:
    """当前平台的内置程序路径。"""
    return res_dir() / "vendor" / vendor_bin_name()


def prepare_vendor_bin(exe: pathlib.Path) -> None:
    """启动前补齐执行权限，并尽力清除 macOS 下载隔离标记。"""
    if os.name == "nt":
        return
    if not os.access(exe, os.X_OK):
        exe.chmod(exe.stat().st_mode | 0o111)
    if sys.platform == "darwin":
        try:
            subprocess.run(["xattr", "-d", "com.apple.quarantine", str(exe)],
                           capture_output=True, timeout=5, check=False)
        except Exception:
            pass


def web_dir() -> pathlib.Path:
    return res_dir() / "web"
