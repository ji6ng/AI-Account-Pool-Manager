"""号池运行时数据：从 CLIProxyAPI 的管理接口读「每个账号的实时状态」。

为什么需要它：客户端请求经过号池后会轮转账号，网关自己并不知道这一次
落到了哪个号。号池提供了两条线索，这里把它们接起来——

1. 响应头 `X-Cpa-Trace-Id` 形如 `20261024134532-81a729393fa3f3ac-01a16c71-...`，
   第二段就是 `auth_index`，能唯一确定账号；
2. 管理接口 `/v0/management/auth-files` 给出 `auth_index → 邮箱` 的映射，
   以及每个账号最近 20 个 10 分钟桶的成功/失败数。

管理接口需要密钥，且**空密钥时整段返回 404**，所以 config.yaml 里必须写
`management.secret-key`（由 proxycfg 生成）。号池启动时会把它换成 bcrypt 哈希再写回。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from . import settings

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"at": 0.0, "files": [], "ok": False, "error": ""}
_TTL = 8.0


def base_url(cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = cfg or settings.load()
    return f"http://127.0.0.1:{int(cfg.get('pool_port') or 8319)}"


def _get(path: str, timeout: float = 6.0) -> Dict[str, Any]:
    cfg = settings.load()
    key = str(cfg.get("internal_key") or "")
    try:
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as c:
            r = c.get(base_url(cfg) + path,
                      headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
            if r.status_code == 404:
                return {"ok": False, "error": "号池管理接口未开启（管理密钥没写进配置）"}
            if r.status_code != 200:
                return {"ok": False, "error": f"管理接口 HTTP {r.status_code}"}
            data = r.json()
            return data if isinstance(data, dict) else {"ok": False, "error": "返回格式异常"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def auth_files(force: bool = False, ttl: float = _TTL) -> Dict[str, Any]:
    """账号运行时列表（带缓存）。返回 {ok, files:[...], error}。"""
    now = time.time()
    with _LOCK:
        # 失败结果也缓存，否则管理接口没开时每个请求都会多打一次号池
        if not force and _CACHE["at"] and now - _CACHE["at"] < ttl:
            return {"ok": _CACHE["ok"], "files": _CACHE["files"], "error": _CACHE["error"]}
    data = _get("/v0/management/auth-files")
    files = data.get("files") if isinstance(data.get("files"), list) else []
    ok = bool(files) or not data.get("error")
    with _LOCK:
        _CACHE.update({"at": now, "files": files, "ok": ok,
                       "error": "" if ok else str(data.get("error") or "")})
    return {"ok": ok, "files": files, "error": "" if ok else str(data.get("error") or "")}


def index(force: bool = False) -> Dict[str, str]:
    """auth_index → 邮箱。用于把响应头里的账号身份翻译成人能看的名字。"""
    out: Dict[str, str] = {}
    for f in auth_files(force).get("files") or []:
        ai = str(f.get("auth_index") or "")
        if ai:
            out[ai] = str(f.get("email") or f.get("account") or f.get("id") or "")
    return out


def account_of_trace(trace_id: str) -> str:
    """从 X-Cpa-Trace-Id 里取 auth_index，再查邮箱。查不到就返回空串。"""
    parts = str(trace_id or "").split("-")
    if len(parts) < 2:
        return ""
    ai = parts[1].strip()
    if not ai:
        return ""
    return index().get(ai, "")


def runtime() -> Dict[str, Any]:
    """给界面用的账号运行时快照：状态、近 3 小时的 10 分钟分桶、累计成功失败。"""
    got = auth_files()
    files = got.get("files") or []
    rows: List[Dict[str, Any]] = []
    for f in files:
        buckets = []
        for b in f.get("recent_requests") or []:
            if not isinstance(b, dict):
                continue
            buckets.append({"time": str(b.get("time") or ""),
                            "ok": int(b.get("success") or 0),
                            "fail": int(b.get("failed") or 0)})
        ok = sum(b["ok"] for b in buckets)
        fail = sum(b["fail"] for b in buckets)
        rows.append({
            "file": str(f.get("id") or ""),
            "email": str(f.get("email") or f.get("account") or ""),
            "status": str(f.get("status") or ""),
            "status_message": str(f.get("status_message") or ""),
            "disabled": bool(f.get("disabled")),
            "unavailable": bool(f.get("unavailable")),
            "cooldowns": f.get("cooldowns") or [],
            "auth_index": str(f.get("auth_index") or ""),
            "buckets": buckets,
            "recent_ok": ok,
            "recent_fail": fail,
            "recent_total": ok + fail,
            "lifetime_ok": int(f.get("success") or 0),
            "lifetime_fail": int(f.get("failed") or 0),
        })
    return {"ok": got.get("ok", False), "error": got.get("error", ""),
            "window_minutes": len(rows[0]["buckets"]) * 10 if rows else 0,
            "accounts": rows}


def drop_cache() -> None:
    with _LOCK:
        _CACHE.update({"at": 0.0, "files": [], "ok": False, "error": ""})
