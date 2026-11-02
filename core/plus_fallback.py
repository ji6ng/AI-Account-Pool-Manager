"""Plus 额度分层、账号专属路由，以及全部 Plus 耗尽后的显式备用通道。

内部号池为每个 Plus 凭据单独注册一个不进入客户端目录的别名。网关选择账号后
只把请求路由到该凭据的别名，因此普通 HTTP/网络错误不会让号池换其它账号；
只有新一轮额度快照明确显示当前优先账号耗尽，才选同组下一账号或低优先组。
"""
from __future__ import annotations

import asyncio
import copy
import math
import time
from typing import Any, Dict

from . import accounts, codex, keys, plus_priority, providers, proxycfg, settings

GEN_PATHS = {"v1/responses", "v1/chat/completions", "v1/completions", "v1/messages"}
DEFAULT = {"enabled": False, "check_interval": 30, "models": {}}


def sources(cfg: Dict[str, Any]) -> list:
    pool = (cfg.get("pools") or {}).get("plus") or {}
    if not pool.get("enabled", True):
        return []
    return [{"id": m["alias"], "display": m.get("display") or m["alias"]}
            for m in pool.get("models") or [] if m.get("alias")]


def targets(cfg: Dict[str, Any]) -> list:
    out = []
    for p in providers.enabled(cfg):
        if proxycfg._provider_kind(p) != "codex":
            continue
        for m in p.get("models") or []:
            mid = p["prefix"] + "/" + m["alias"]
            out.append({"id": mid, "display": m.get("display") or mid, "provider": p["label"]})
    return out


def config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("plus_fallback") or {}
    return {**DEFAULT, **raw} if isinstance(raw, dict) else copy.deepcopy(DEFAULT)


def validate(raw: Any, cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Plus 备用路由必须是一个配置对象")
    enabled, interval, mapping = raw.get("enabled", False), raw.get("check_interval", 30), raw.get("models", {})
    if not isinstance(enabled, bool):
        raise ValueError("Plus 备用路由开关必须是布尔值")
    if isinstance(interval, bool) or not isinstance(interval, int) or not 15 <= interval <= 300:
        raise ValueError("额度检查间隔必须是 15–300 秒的整数")
    if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
        raise ValueError("备用路径必须是 Plus 模型到代理商模型的映射")
    mapping = {k: v for k, v in mapping.items() if v}
    if enabled:
        if not mapping:
            raise ValueError("请至少选择一条 Plus 备用路径")
        srcs, dsts = {m["id"] for m in sources(cfg)}, {m["id"] for m in targets(cfg)}
        for src, dst in mapping.items():
            if src not in srcs:
                raise ValueError(f"Plus 模型不存在或已停用：{src}")
            if dst not in dsts:
                raise ValueError(f"备用模型不存在、已停用或不支持 Responses：{dst}")
    return {"enabled": enabled, "check_interval": interval, "models": mapping}


def quota_state(usage: Dict[str, Any], now: float | None = None) -> str:
    now = time.time() if now is None else now
    fetched = usage.get("fetched_at")
    if (not usage.get("ok") or not isinstance(fetched, (int, float)) or not math.isfinite(fetched)
            or not -5 <= now - fetched <= 310):
        return "unknown"
    if usage.get("limit_reached") is True:
        return "exhausted"
    if usage.get("allowed") is True:
        return "available"
    for name in ("primary", "secondary"):
        w = usage.get(name)
        if isinstance(w, dict) and isinstance(w.get("used"), (int, float)) and w["used"] >= 100:
            return "exhausted"
    return "unknown"


def _account_snapshot(rows: list, usage: dict, cfg: dict, previous: dict | None = None) -> dict:
    priority_cfg = {"plus_account_order": cfg.get("plus_account_order") or [],
                    "plus_priority": cfg.get("plus_priority") or {"enabled": True, "groups": {}}}
    return plus_priority.status(rows, usage, priority_cfg, previous)


class Monitor:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.at = 0.0
        self.signature = None
        self.value: Dict[str, Any] = {}
        self.known_groups: Dict[str, str] = {}

    async def check(self, cfg: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
        opts = config(cfg)
        requested_at = time.monotonic()
        async with self.lock:
            try:
                rows = await asyncio.to_thread(accounts.list_accounts)
                rows = [a for a in rows if a.get("pool") == "plus" and (a.get("provider") or "codex") == "codex"]
            except Exception:
                return {"state": "unknown", "reason": "账号列表读取失败，继续用 Plus", "checked_at": 0,
                        "total": 0, "available": 0, "exhausted": 0, "unknown": 0,
                        "accounts": [], "usage": {}}
            signature = (tuple((a["file"], a.get("mtime"), a.get("disabled")) for a in rows), repr(opts),
                         repr(cfg.get("plus_priority")), repr(cfg.get("plus_account_order")), cfg.get("proxy_url"))
            if (self.signature == signature and self.value
                    and (self.at >= requested_at or (not force and requested_at - self.at < opts["check_interval"]))):
                return dict(self.value)

            async def one(a):
                if a.get("disabled"):
                    return {"ok": False, "fetched_at": time.time()}
                try:
                    return await asyncio.to_thread(codex.cached_usage, a["file"], 0.0, True)
                except Exception:
                    return {"ok": False}

            results = await asyncio.gather(*(one(a) for a in rows))
            usage = {a["file"]: u for a, u in zip(rows, results)}
            ordering = _account_snapshot(rows, usage, cfg, self.known_groups)
            self.known_groups = {a["file"]: a["group"] for a in ordering["accounts"]}
            active = [a for a in ordering["accounts"] if not a["disabled"]]
            counts = {"available": 0, "exhausted": 0, "unknown": 0}
            for a in active:
                counts[a["quota"]] += 1
            selected = next((a for a in active if a["quota"] != "exhausted"), None)
            state, reason = "primary", "优先使用有 5 小时窗口的 Plus 账号"
            if not active:
                state, reason = "empty", "没有启用的 Plus 账号"
            elif not selected:
                state, reason = "fallback", "所有启用的 Plus 账号都已确认限额"
            elif selected["quota"] == "unknown":
                state, reason = "unknown", "优先账号额度未知，保留该账号，不自动跳到后备组"
            elif selected["group"] == "reserve":
                state, reason = "reserve", "有 5 小时窗口的账号全部限额，使用后备 Plus 账号"
            self.value = {"state": state, "reason": reason, "checked_at": int(time.time()),
                          "total": len(active), **counts, "accounts": ordering["accounts"], "usage": usage}
            self.signature, self.at = signature, time.monotonic()
            return dict(self.value)

    async def decide(self, cfg: Dict[str, Any], row: Dict[str, Any], model: str,
                     path: str, force: bool = False) -> Dict[str, str]:
        out = {"requested": model, "actual": model, "wire_model": model, "reason": "",
               "mode": "primary", "account": "", "file": ""}
        if path.rstrip("/") not in GEN_PATHS or model not in {m["id"] for m in sources(cfg)}:
            return out
        account_switch_on = (cfg.get("plus_priority") or {}).get("enabled", True)
        opts = config(cfg)
        dst = opts["models"].get(model, "")
        if not account_switch_on and (not opts["enabled"] or not dst):
            return out
        snap = await self.check(cfg, force)
        if not account_switch_on:
            if snap["state"] == "fallback":
                if dst not in {m["id"] for m in targets(cfg)}:
                    return {**out, "mode": "blocked", "reason": "备用模型已删除、停用或协议不兼容"}
                if not keys.allowed(row, dst):
                    return {**out, "mode": "blocked", "reason": f"当前 Key 未授权备用模型 {dst}"}
                return {**out, "actual": dst, "wire_model": dst, "mode": "fallback", "reason": snap["reason"]}
            return out
        rows = snap.get("accounts") or []
        source_accounts = []
        for a in rows:
            if a["disabled"]:
                continue
            private = plus_priority.private_alias(a["file"], model)
            source_accounts.append(dict(a, private=private))
        if account_switch_on and source_accounts:
            # 五小时组严格优先；额度未知的高优先级账号会阻挡后备组，避免误耗后备额度。
            ordered = sorted(source_accounts, key=lambda a: (
                {"five_hour": 0, "unknown": 1, "reserve": 2}.get(a["group"], 1),
                (cfg.get("plus_account_order") or []).index(a["file"])
                if a["file"] in (cfg.get("plus_account_order") or []) else 10**9,
                a["file"]))
            selected = next((a for a in ordered if a["quota"] != "exhausted"), None)
            if selected:
                if selected["private"] not in selected["aliases"]:
                    out.update(mode="blocked", reason="账号专属路由尚未同步，请使用新版程序重新启动号池；未退回自动轮换")
                    return out
                out.update(wire_model=selected["private"], mode="account", file=selected["file"],
                           reason=f"Plus 账号优先级：{selected['group']}；账号额度状态：{selected['quota']}",
                           account=selected["email"])
                return out
        if not opts["enabled"] or not dst:
            if account_switch_on:
                out.update(mode="blocked", reason="所有 Plus 账号已限额或没有可用账号，且此模型未设置备用路径")
            return out
        if snap["state"] != "fallback":
            out.update(mode="blocked" if snap["state"] == "empty" else "primary", reason=snap["reason"])
            if snap["state"] == "empty":
                out["reason"] = "没有启用的 Plus 账号，不自动使用代理商备用模型"
            return out
        if dst not in {m["id"] for m in targets(cfg)}:
            out.update(mode="blocked", reason="备用模型已删除、停用或协议不兼容")
        elif not keys.allowed(row, dst):
            out.update(mode="blocked", reason=f"当前 Key 未授权备用模型 {dst}，未进行切换")
        else:
            out.update(actual=dst, wire_model=dst, mode="fallback", reason=snap["reason"])
        return out

    async def status(self, cfg: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
        snap = await self.check(cfg, force)
        opts, routes = config(cfg), []
        available_targets = {m["id"] for m in targets(cfg)}
        for src in sources(cfg):
            dst = opts["models"].get(src["id"], "")
            active = next((a for a in snap.get("accounts", [])
                           if not a["disabled"] and a["quota"] != "exhausted"), None)
            fallback = snap["state"] == "fallback" and bool(dst)
            mode = "fallback" if fallback and dst in available_targets else ("blocked" if fallback else "account" if active else "primary")
            actual = dst if mode == "fallback" else src["id"]
            routes.append({"source": src["id"], "target": dst, "mode": mode, "actual": actual,
                           "account": active["email"] if active else ""})
        snapshot = {k: v for k, v in snap.items() if k not in ("accounts", "usage")}
        return {"snapshot": snapshot, "routes": routes, "sources": sources(cfg),
                "targets": targets(cfg), "config": opts,
                "account_priority": {
                    "enabled": (cfg.get("plus_priority") or {}).get("enabled", True),
                    "accounts": snap.get("accounts") or [],
                    "selected": next((a["file"] for a in snap.get("accounts") or []
                                      if not a["disabled"] and a["quota"] != "exhausted"), ""),
                    "order": cfg.get("plus_account_order") or [],
                    "groups": (cfg.get("plus_priority") or {}).get("groups") or {},
                }}

    async def run(self) -> None:
        while True:
            self.wake.clear()
            interval = 30
            try:
                cfg = settings.load()
                interval = config(cfg)["check_interval"]
                if config(cfg)["enabled"] or (cfg.get("plus_priority") or {}).get("enabled", True):
                    await self.check(cfg)
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
