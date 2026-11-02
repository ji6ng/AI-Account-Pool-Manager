"""Plus 先耗尽一个账号再用下一个：5 小时组优先，普通错误不参与切换。

每个账号的内部模型别名只注册在自己的凭据上，网关以此固定账号，避免核心的
会话亲和或失败重试改变选择。这里只读额度事实；不写 disabled、token 或冷却。
"""
from __future__ import annotations

import hashlib
import math
import time
from typing import Any, Dict

PREFIX = "aipool-private-"


def private_alias(file: str, model: str) -> str:
    digest = hashlib.sha256((file + "\0" + model).encode()).hexdigest()[:32]
    return PREFIX + digest


def enabled(cfg: Dict[str, Any]) -> bool:
    return (cfg.get("plus_priority") or {}).get("enabled", True) is True


def validate(raw: Any, order: Any, rows: list) -> tuple:
    if not isinstance(raw, dict) or not isinstance(raw.get("enabled", True), bool):
        raise ValueError("账号优先级开关必须是布尔值")
    groups = raw.get("groups", {})
    if not isinstance(groups, dict) or any(not isinstance(k, str) or v not in ("auto", "five_hour", "reserve")
                                           for k, v in groups.items()):
        raise ValueError("账号分组只能是自动、有 5 小时窗口或无 5 小时窗口")
    if not isinstance(order, list) or any(not isinstance(x, str) for x in order) or len(set(order)) != len(order):
        raise ValueError("账号顺序必须是不重复的账号文件名列表")
    known = {a["file"] for a in rows if a.get("pool") == "plus" and a.get("provider", "codex") == "codex"}
    if any(x not in known for x in [*order, *groups]):
        raise ValueError("配置包含不存在的 Plus 账号，请刷新列表")
    return {"enabled": raw.get("enabled", True), "groups": groups}, order


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
    for k in ("primary", "secondary"):
        w = usage.get(k)
        if (isinstance(w, dict) and isinstance(w.get("used"), (int, float))
                and w["used"] >= 100 and usage.get("allowed") is False):
            return "exhausted"
    return "unknown"


def group_of(usage: Dict[str, Any], override: str, previous: str = "unknown") -> str:
    if override in ("five_hour", "reserve"):
        return override
    if usage.get("ok"):
        windows = [w for w in (usage.get("primary"), usage.get("secondary")) if isinstance(w, dict)]
        if any(w.get("window_seconds") == 18000 for w in windows):
            return "five_hour"
        if windows and all(isinstance(w.get("window_seconds"), (int, float))
                           and w["window_seconds"] > 0 for w in windows):
            return "reserve"
    return previous


def status(rows: list, usages: dict, cfg: dict, previous: dict | None = None) -> dict:
    order = cfg.get("plus_account_order") or []
    overrides = (cfg.get("plus_priority") or {}).get("groups") or {}
    rank = {file: i for i, file in enumerate(order)}
    group_rank = {"five_hour": 0, "unknown": 1, "reserve": 2}
    items = []
    for a in rows:
        if a.get("pool") != "plus" or a.get("provider", "codex") != "codex":
            continue
        file = a["file"]
        u = usages.get(file) or {}
        override = overrides.get(file, "auto")
        group = group_of(u, override, (previous or {}).get(file, "unknown"))
        items.append({"file": file, "email": a.get("email") or file,
                      "disabled": bool(a.get("disabled")), "group": group,
                      "override": override, "quota": quota_state(u),
                      "checked_at": u.get("fetched_at") or 0,
                      "aliases": list(a.get("aliases") or [])})
    items.sort(key=lambda a: (group_rank[a["group"]], rank.get(a["file"], len(rank)), a["file"]))
    active = next((a for a in items if not a["disabled"] and a["quota"] != "exhausted"), None)
    eligible = [a for a in items if not a["disabled"]]
    return {"enabled": enabled(cfg), "accounts": items,
            "selected": active["file"] if active else "",
            "selected_label": active["email"] if active else "",
            "active_group": active["group"] if active else "empty",
            "all_exhausted": bool(eligible) and all(a["quota"] == "exhausted" for a in eligible),
            "order": order, "groups": overrides}
