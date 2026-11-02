"""本机网络信息：给界面显示「局域网 Base URL」用。

「客户端接入」卡片原来只用浏览器地址栏的 host 拼 Base URL，
如果你在本机用 127.0.0.1:8321 打开管理界面，卡片里就只有回环地址，
手机 / 别的电脑照着填是连不上的。这里枚举本机 IPv4，
让界面能同时给出一条同网段可用的地址。
"""
from __future__ import annotations

import socket
import threading
import time
from typing import List

_LOCK = threading.Lock()
_CACHE = {"at": 0.0, "ips": []}          # type: dict
_TTL = 30.0                               # 秒；DHCP 换地址最多 30 秒后反映出来

# 出网探测目标：只做 UDP connect，不会真的发包
_PROBES = ("223.5.5.5", "8.8.8.8", "1.1.1.1")


def _private(ip: str) -> bool:
    """是不是一个能给别人用的局域网地址（排除回环和 APIPA 自分配地址）。"""
    if not ip or ip.startswith("127.") or ip.startswith("169.254."):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if a == 10:                    # 10.0.0.0/8
        return True
    if a == 192 and b == 168:       # 192.168.0.0/16
        return True
    if a == 172 and 16 <= b <= 31:  # 172.16.0.0/12
        return True
    return False


def _route_ip() -> str:
    """问内核「真要出网会走哪块网卡」。UDP connect 不产生流量。"""
    for probe in _PROBES:
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.2)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
    return ""


def _enumerate() -> List[str]:
    """枚举主机名绑定的所有 IPv4（多网卡 / 有线无线同时连时会返回多个）。"""
    out: List[str] = []
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            out.append(ip)
    except Exception:
        pass
    if not out:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                out.append(info[4][0])
        except Exception:
            pass
    return out


def _virtual_host(ip: str) -> bool:
    """Hyper-V / WSL / Docker 这类虚拟交换机的宿主地址（172.16/12 段、末位 .1）。

    它们只有本机能访问，列在界面里纯属噪音，所以只从「其他网卡」里剔掉，
    不动默认路由那一个。
    """
    parts = ip.split(".")
    if len(parts) != 4 or parts[3] != "1":
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return a == 172 and 16 <= b <= 31


def local_ips(force: bool = False) -> List[str]:
    """本机可用的局域网 IPv4，默认路由那块网卡排最前。"""
    now = time.time()
    with _LOCK:
        if not force and _CACHE["ips"] and now - _CACHE["at"] < _TTL:
            return list(_CACHE["ips"])

    primary = _route_ip()
    found: List[str] = []
    if _private(primary):
        found.append(primary)
    for ip in _enumerate():
        if not _private(ip) or ip in found:
            continue
        if _virtual_host(ip) and ip != primary:
            continue
        found.append(ip)
    # 没有内网地址时（比如直连公网），至少给出默认路由那个，别留空
    if not found and primary and not primary.startswith("127."):
        found.append(primary)

    with _LOCK:
        _CACHE["at"] = now
        _CACHE["ips"] = list(found)
    return found
