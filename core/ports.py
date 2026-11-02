"""端口工具：起服务前先确保端口可用，被占就自动往后找。"""
from __future__ import annotations

import socket
import time


def _connectable(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, int(port))) == 0


def is_free(port: int, host: str = "0.0.0.0") -> bool:
    """端口是否可用。

    先做连接探测再做 bind 探测，两者缺一不可：
      - Windows 上 SO_REUSEADDR 会让 bind 抢到别人正在 LISTEN 的端口
        （cli-proxy-api 就是这种程序），只看 bind 会误判为空闲；
      - 反过来，某些被系统保留的端口 connect 不通但 bind 也会失败。
    """
    if _connectable(port):
        return False

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick(
    preferred: int,
    host: str = "0.0.0.0",
    tries: int = 30,
    exclude: set[int] | list[int] | None = None,
) -> int:
    """从 preferred 往后找一个可用端口。

    exclude 里的端口一定跳过：号池端口刚被选中但进程还没起来，这时它仍然是
    "连接不通 + bind 成功" 的空闲状态，不排除就会把同一个端口再分给界面。
    """
    skip = {int(x) for x in (exclude or ())}
    for p in range(int(preferred), int(preferred) + tries):
        if p in skip:
            continue
        if is_free(p, host):
            return p
    raise RuntimeError(f"{preferred}~{preferred + tries} 之间没有可用端口")


def wait_open(port: int, timeout: float = 25.0, host: str = "127.0.0.1") -> bool:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            if s.connect_ex((host, int(port))) == 0:
                return True
        time.sleep(0.2)
    return False
