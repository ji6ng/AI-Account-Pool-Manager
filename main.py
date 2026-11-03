"""程序入口：检查单实例 → 准备数据 → 起号池 → 起 Web 服务 → 打开浏览器。"""
from __future__ import annotations

import argparse
import os
import pathlib
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import threading
import tempfile
import time
import traceback
import webbrowser
from typing import Optional

from core import accounts, keys, paths, ports, proxycfg, settings
from core.pool import pool
from core.linux_desktop import desktop_exec, has_display

UI_TITLE = paths.PRODUCT_NAME


# ---------------------------------------------------------------- 小工具

def _log(msg: str) -> None:
    try:
        p = paths.logs_dir() / "app.log"
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _alert(msg: str) -> None:
    """无控制台时也能让用户看到提示。"""
    try:
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, msg, UI_TITLE, 0x40)
        elif sys.platform.startswith("linux"):
            _log(msg)
            if not has_display():
                return
            for tool, arguments in (
                ("zenity", ["--info", "--title", UI_TITLE, "--text", msg]),
                ("kdialog", ["--title", UI_TITLE, "--msgbox", msg]),
                ("notify-send", [UI_TITLE, msg]),
            ):
                executable = shutil.which(tool)
                if executable:
                    try:
                        subprocess.Popen([executable, *arguments], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                        return
                    except OSError:
                        continue
        elif sys.platform == "darwin":
            def quote(text: str) -> str:
                return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n') + '"'

            script = f'display dialog {quote(msg)} buttons {{"好"}} with title {quote(UI_TITLE)}'
            subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _open_browser(url: str) -> None:
    if sys.platform.startswith("linux") and not has_display():
        print(f"管理地址：{url}", flush=True)
        _log(f"管理地址：{url}")
        return
    webbrowser.open(url)


def _already_running(port: int) -> bool:
    """端口上是不是本程序的另一个实例。"""
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/whoami", timeout=2.5, trust_env=False)
        if r.status_code == 200 and "password_required" in (r.json() or {}):
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- 单实例锁

def _lock_path():
    return paths.data_dir() / ".instance"


def _pid_alive(pid: int) -> bool:
    """进程是否还活着。

    注意：Windows 上千万别用 os.kill(pid, 0) —— 那里没有信号 0，任何非
    CTRL_C_EVENT 的值都会直接 TerminateProcess，等于把别人的进程杀掉。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(h)

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_lock() -> dict:
    try:
        import json

        data = json.loads(_lock_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _take_lock() -> Optional[int]:
    """抢单实例锁。

    返回 None 表示抢到了；返回已有实例的 pid 表示别人在跑，本次应退出。
    """
    info = _read_lock()
    pid = int(info.get("pid") or 0)
    if pid and pid != os.getpid() and _pid_alive(pid):
        return pid
    try:
        import json

        _lock_path().parent.mkdir(parents=True, exist_ok=True)
        _lock_path().write_text(
            json.dumps({"pid": os.getpid(), "ui_port": 0}), encoding="utf-8"
        )
    except Exception:
        pass
    return None


def _update_lock(ui_port: int) -> None:
    try:
        import json

        _lock_path().write_text(
            json.dumps({"pid": os.getpid(), "ui_port": int(ui_port)}), encoding="utf-8"
        )
    except Exception:
        pass


def _release_lock() -> None:
    try:
        if int(_read_lock().get("pid") or 0) == os.getpid():
            _lock_path().unlink()
    except Exception:
        pass


def _wait_existing(deadline: float = 8.0) -> str:
    """等已在运行的实例把界面起起来，返回它的地址；等不到返回空串。"""
    end = time.time() + deadline
    while time.time() < end:
        port = int(_read_lock().get("ui_port") or 0)
        if not port:
            try:
                port = int(settings.load().get("ui_port") or 0)
            except Exception:
                port = 0
        if port and _already_running(port):
            return f"http://127.0.0.1:{port}/"
        time.sleep(0.4)
    return ""


def _seed_settings() -> None:
    """首次运行：把随包附带的初始设置放进去（端口、代理、可选通道）。"""
    p = paths.data_dir() / "settings.json"
    if p.exists():
        return
    seed = paths.res_dir() / "seed" / "settings.json"
    if seed.exists():
        shutil.copy2(seed, p)
        _log("已应用预置设置")


def _seed_accounts() -> None:
    """首次运行时，把随包附带的账号凭据放进数据目录。"""
    if any(paths.auth_dir().glob("*.json")):
        return
    seed = paths.res_dir() / "seed" / "auths"
    if not seed.exists():
        return
    n = 0
    for f in seed.glob("*.json"):
        shutil.copy2(f, paths.auth_dir() / f.name)
        n += 1
    if n:
        _log(f"已导入预置账号 {n} 个")


def _apply_autostart(enabled: bool) -> None:
    if sys.platform.startswith("linux"):
        temporary = None
        try:
            config = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
            entry = config / "autostart" / "aipool-manager.desktop"
            if not enabled:
                entry.unlink(missing_ok=True)
            elif paths.is_frozen():
                entry.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=entry.parent,
                                                 prefix=".aipool-", delete=False) as stream:
                    temporary = pathlib.Path(stream.name)
                    stream.write("[Desktop Entry]\nType=Application\nName=AI 号池管理器\n"
                                 f"Exec={desktop_exec(sys.executable)}\nTerminal=false\n")
                temporary.replace(entry)
        except Exception as exc:
            _log(f"设置开机自启失败：{exc}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
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
        except Exception as e:
            _log(f"设置开机自启失败：{e}")
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
    except Exception as e:
        _log(f"设置开机自启失败：{e}")


def _prepare_ports() -> None:
    """确定号池端口和界面端口。

    号池此时还没启动、不占端口，所以界面端口必须显式避开刚选中的号池端口，
    否则两者会撞到同一个端口号上，两个进程抢着监听。
    """
    cfg = settings.load()
    host = cfg.get("listen_host") or "0.0.0.0"

    pool_port = int(cfg.get("pool_port") or 8318)
    if not ports.is_free(pool_port, host):
        new = ports.pick(pool_port + 1, host)
        _log(f"号池端口 {pool_port} 被占用，改用 {new}")
        pool_port = new
        settings.save({"pool_port": pool_port})

    ui_port = int(cfg.get("ui_port") or 8320)
    if ui_port == pool_port or not ports.is_free(ui_port, host):
        new = ports.pick(ui_port + 1, host, exclude={pool_port})
        _log(f"界面端口 {ui_port} 被占用，改用 {new}")
        ui_port = new
        settings.save({"ui_port": ui_port})


def _boot_pool_async(stopping: threading.Event) -> threading.Thread:
    def _run():
        if stopping.wait(0.4):
            return
        try:
            # POSIX 只等待进程创建，关闭时才能及时 join；端口健康由管理界面检查。
            r = pool.start(wait=os.name == "nt")
            _log(f"号池启动：{r}")
        except Exception:
            _log("号池启动异常：\n" + traceback.format_exc())

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    return worker


# ---------------------------------------------------------------- 主流程

def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=UI_TITLE)
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    ap.add_argument("--port", type=int, default=0, help="临时指定界面端口")
    ap.add_argument("--data-dir", default="", help="指定数据目录（排查问题用）")
    ap.add_argument("--console", action="store_true", help="保留控制台输出")
    args = ap.parse_args(argv)

    if args.data_dir:
        paths.set_data_dir(args.data_dir)

    _log("=" * 60)
    _log(f"启动 {UI_TITLE}｜frozen={paths.is_frozen()}｜data={paths.data_dir()}")

    _seed_settings()
    cfg = settings.load()
    if args.port:
        settings.save({"ui_port": int(args.port)})
        cfg = settings.load()

    # 已经在跑：直接把界面拉起来，不重复起服务
    holder = _take_lock()
    if holder and not args.port:
        url = _wait_existing()
        if url:
            _log(f"已有实例在运行（pid {holder}），打开 {url}")
            if not args.no_browser:
                _open_browser(url)
            return 0
        # 进程在、界面不通：这时候要是在同一个数据目录再起一个号池，
        # 两个进程会抢同一批凭据文件去刷新 token，比让用户手动结束更危险。
        monitor = "活动监视器" if sys.platform == "darwin" else "任务管理器"
        msg = (
            f"检测到程序已在运行（进程号 {holder}），但管理界面没有响应。\n\n"
            f"请打开{monitor}，结束进程号 {holder}，然后重新打开本程序。"
        )
        if sys.platform.startswith("linux"):
            msg = (f"检测到程序已在运行（进程号 {holder}），但管理界面没有响应。\n\n"
                   f"请用「系统监视器」或命令 kill {holder} 结束进程号 {holder}，然后重新打开本程序。")
        _log(msg.replace("\n", " "))
        _alert(msg)
        return 1

    _seed_accounts()
    _prepare_ports()

    cfg = settings.load()
    proxycfg.write(cfg)
    keys.init()
    # 设置里的模型列表是「真相」，凭据文件里的 model_aliases 是副本。
    # 版本升级可能往池里补了模型（settings 的迁移函数），这里先刷一遍，
    # 否则号池启动时读到的还是旧别名，新模型不会出现在客户端列表里。
    try:
        accounts.sync_all()
    except Exception:
        _log("同步账号别名失败：\n" + traceback.format_exc())
    # A diagnostic instance with separate data must not alter the installed app's startup entry.
    if not args.data_dir:
        _apply_autostart(bool(cfg.get("autostart")))
    _update_lock(int(cfg["ui_port"]))

    port = int(cfg["ui_port"])
    url = f"http://127.0.0.1:{port}/"

    def _open_later():
        if args.no_browser:
            return
        for _ in range(80):
            if not ports.is_free(port):
                break
            time.sleep(0.2)
        try:
            _open_browser(url)
        except Exception:
            pass

    threading.Thread(target=_open_later, daemon=True).start()

    stopping = threading.Event()
    pool_worker = None
    previous_handlers = {}

    def _exit_on_signal(signum, frame):
        # uvicorn 关闭后会恢复并重新触发原信号；SystemExit 保证执行下面的 finally。
        raise SystemExit(0)

    try:
        if os.name != "nt":
            for signum in (signal.SIGTERM, signal.SIGHUP):
                previous_handlers[signum] = signal.signal(signum, _exit_on_signal)
        pool_worker = _boot_pool_async(stopping)
        import uvicorn

        from core.gateway import app as webapp

        uvicorn.run(webapp, host=cfg.get("listen_host") or "0.0.0.0", port=port,
                    log_level="info" if args.console else "warning",
                    access_log=False, log_config=None)
    except Exception:
        _log("Web 服务异常：\n" + traceback.format_exc())
        raise
    finally:
        stopping.set()
        try:
            # 等待启动线程越过检查/创建进程的竞态，再统一停止，避免关完后又启动。
            if pool_worker is not None and os.name != "nt":
                pool_worker.join()
            pool.stop()
        except Exception:
            pass
        _release_lock()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        _log("致命错误：\n" + traceback.format_exc())
        raise
