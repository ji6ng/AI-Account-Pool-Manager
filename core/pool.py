"""托管 cli-proxy-api 子进程：启动、停止、重启、健康检查、日志尾巴。"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from . import accounts, paths, ports, proxycfg, settings

CREATE_NO_WINDOW = 0x08000000
_lock = threading.RLock()


def _startupinfo():
    if os.name != "nt":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


class Pool:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.started_at: float = 0.0
        self.last_error: str = ""
        self._log_fh = None

    # ---------------------------------------------------------------- 状态
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def pid(self) -> Optional[int]:
        return self.proc.pid if self.running() else None

    def uptime(self) -> int:
        return int(time.time() - self.started_at) if self.running() else 0

    def health(self) -> Dict[str, Any]:
        cfg = settings.load()
        port = int(cfg["pool_port"])
        info: Dict[str, Any] = {
            "running": self.running(),
            "pid": self.pid(),
            "port": port,
            "uptime": self.uptime(),
            "reachable": False,
            "models": 0,
            "error": self.last_error,
        }
        if not info["running"]:
            return info
        try:
            r = httpx.get(
                f"http://127.0.0.1:{port}/v1/models",
                headers={"Authorization": f"Bearer {cfg['internal_key']}"},
                timeout=6.0,
            )
            info["reachable"] = r.status_code == 200
            if r.status_code == 401:
                info["error"] = "端口被其它实例占用（用我们的 key 访问返回 401）"
            if r.status_code == 200:
                data = r.json()
                info["models"] = len(data.get("data") or [])
        except Exception as e:
            info["error"] = f"{type(e).__name__}: {e}"
        return info

    # ---------------------------------------------------------------- 生命周期
    def start(self, wait: bool = True) -> Dict[str, Any]:
        with _lock:
            if self.running():
                return {"ok": True, "already": True, "pid": self.pid()}

            exe = paths.vendor_bin()
            if not exe.exists():
                self.last_error = f"缺少内置程序：{exe}"
                return {"ok": False, "error": self.last_error}

            cfg = settings.load()
            accounts.sync_all()
            proxycfg.write(cfg)

            log_file = paths.logs_dir() / "pool.log"
            self._log_fh = open(log_file, "ab", buffering=0)
            cmd = [str(exe.resolve()), "-config", str(proxycfg.config_path().resolve())]
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    cwd=str(paths.pool_dir().resolve()),
                    stdout=self._log_fh,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
                    startupinfo=_startupinfo(),
                )
            except Exception as e:
                self.last_error = f"启动失败：{e}"
                return {"ok": False, "error": self.last_error}

            self.started_at = time.time()
            self.last_error = ""

        if wait:
            ok = ports.wait_open(int(cfg["pool_port"]), timeout=30.0)
            # 端口通了不代表是我们的进程：也可能是别人的服务占着同一个端口
            if ok and self.proc is not None and self.proc.poll() is not None:
                code = self.proc.returncode
                tail = self.tail(8)
                self.proc = None
                self.last_error = f"号池进程启动后立即退出（退出码 {code}），端口可能被别的程序占用"
                return {"ok": False, "error": self.last_error, "log": tail}
            if not ok:
                tail = self.tail(6)
                self.last_error = "号池 30 秒内没有监听端口"
                return {"ok": False, "error": self.last_error, "log": tail}
        return {"ok": True, "pid": self.pid(), "port": int(settings.load()["pool_port"])}

    def stop(self, timeout: float = 8.0) -> Dict[str, Any]:
        with _lock:
            if not self.running():
                self._close_log()
                return {"ok": True, "already": True}
            proc = self.proc
            assert proc is not None
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=10)
                else:
                    proc.terminate()
                proc.wait(timeout=timeout)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            self.proc = None
            self.started_at = 0.0
            self._close_log()
            return {"ok": True}

    def restart(self) -> Dict[str, Any]:
        self.stop()
        time.sleep(0.6)
        return self.start()

    def _close_log(self) -> None:
        try:
            if self._log_fh:
                self._log_fh.close()
        except Exception:
            pass
        self._log_fh = None

    # ---------------------------------------------------------------- 日志
    def tail(self, lines: int = 60) -> List[str]:
        for f in (paths.logs_dir() / "pool.log", paths.auth_dir() / "logs" / "main.log"):
            if f.exists():
                try:
                    data = f.read_text(encoding="utf-8", errors="replace").splitlines()
                    # 每次重启都会追加一遍启动横幅，历史堆在一起没法看；
                    # 只保留最后一次启动之后的内容
                    for i in range(len(data) - 1, -1, -1):
                        if data[i].startswith("CLIProxyAPI Version"):
                            data = data[i:]
                            break
                    return data[-lines:]
                except Exception:
                    continue
        return []


pool = Pool()
