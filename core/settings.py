"""设置文件：一切可调项都在这里，界面改完即落盘，用户不需要碰任何配置文件。"""
from __future__ import annotations

import copy
import json
import secrets
import threading
import time
from typing import Any, Dict

from . import paths

_lock = threading.RLock()
_cache: Dict[str, Any] | None = None

SETTINGS_FILE = "settings.json"

DEFAULTS: Dict[str, Any] = {
    "schema": 2,
    "first_run": True,
    # ---- 网络
    "listen_host": "0.0.0.0",      # 客户端需要从局域网连进来
    "ui_port": 8320,               # 管理界面 + 客户端 API 共用这一个端口
    "pool_port": 8318,             # 内部号池，默认只给本机网关用
    "proxy_url": "",               # 上游出网代理，例如 http://127.0.0.1:7894
    "internal_key": "",            # 网关访问号池用的 key，首次运行自动生成
    # ---- 管理界面
    "admin_password": "",          # 留空表示只允许本机访问管理界面
    # ---- 号池路由
    "routing": {
        "strategy": "fill-first",
        "session_affinity": True,
        "session_affinity_ttl": "1h",
        "request_retry": 5,
    },
    # 网关层处理，保存此项不需要重启内部号池。
    "plus_fallback": {"enabled": False, "check_interval": 30, "models": {}},
    "plus_account_order": [],
    "plus_priority": {"enabled": True, "groups": {}},
    # ---- 模型池：alias 是客户端看到的模型名，name 是上游真实模型名
    "pools": {
        "plus": {
            "label": "Plus",
            "enabled": True,
            "models": [
                {"alias": "plus-gpt-6.1-sol", "display": "Plus · GPT 6.1 Sol", "name": "gpt-6.1-sol"},
                {"alias": "plus-gpt-6-astra", "display": "Plus · GPT 6 Astra", "name": "gpt-6-astra"},
                {"alias": "plus-gpt-6-sol", "display": "Plus · GPT 6 Sol", "name": "gpt-6-sol"},
            ],
        },
        "pro": {
            "label": "Pro",
            "enabled": True,
            "models": [
                {"alias": "pro-gpt-6.1-sol", "display": "Pro · GPT 6.1 Sol", "name": "gpt-6.1-sol"},
                {"alias": "pro-gpt-6-astra", "display": "Pro · GPT 6 Astra", "name": "gpt-6-astra"},
                {"alias": "pro-gpt-6-sol", "display": "Pro · GPT 6 Sol", "name": "gpt-6-sol"},
            ],
        },
        # Gemini 号走 Google 的 Antigravity 通道。模型名先放两个常见的，
        # 登录成功后会用号池里的真实列表覆盖（见 accounts._sync_gemini_models）。
        "gemini": {
            "label": "Gemini",
            "enabled": True,
            "models": [
                # Pro 档号池代理不认识，由网关直连上游（core/gemini_direct.py）
                {"alias": "gemini-3-pro-preview", "display": "Gemini · 3 Pro", "name": "gemini-3.8-flash-tiered"},
                {"alias": "gemini-3-flash", "display": "Gemini · 3 Flash", "name": "gemini-3-flash"},
                # Antigravity 账号同时能用 Anthropic 的 Claude 模型（实测能出真 Claude）
                {"alias": "gemini-claude-sonnet-4-6", "display": "Gemini · Claude Sonnet 4.6", "name": "claude-sonnet-4-6"},
                {"alias": "gemini-claude-opus-4-6", "display": "Gemini · Claude Opus 4.6", "name": "claude-opus-4-6-thinking"},
            ],
        },
    },
    # ---- 上游不需要的旧档位，直接不暴露（模型目录有 1MB 上限，数量必须压住）
    "excluded_models": ["gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
    # ---- 第三方 AI 提供商（OpenAI 兼容接口），可加多个
    "providers": [],
    # ---- 其它
    "autostart": False,
    "log_keep": 500,
}

# 旧版本只有一条写死的 Kakou 通道，升到 schema 2 时搬到 providers 里
_LEGACY_PROVIDER = "kakou"


# 老配置的 gemini 池是登录时从号池同步来的，只有 flash。Pro 档需要单独补一条：
# 它的真实模型（gemini-3.8-flash-tiered）不在号池代理的注册表里，改由网关直连。
GEMINI_PRO = {
    "alias": "gemini-3-pro-preview",
    "display": "Gemini · 3 Pro",
    "name": "gemini-3.8-flash-tiered",
}

# 2026-10 补的「上游确实有、但默认没暴露」的模型。
# 只补一次（extras_v1 标记），用户删掉之后不会再被加回来。
# 注意：plus / pro 池不要再往里塞，Codex 那边只认 gpt-6.1-sol / gpt-6-sol / gpt-6-astra
# 三个上游名，gpt-6.1-terra、gpt-6-luna、gpt-5.6-* 写了也会被号池丢掉（unknown provider）。
EXTRA_MODELS: Dict[str, list] = {
    "gemini": [
        {"alias": "gemini-claude-sonnet-4-6", "display": "Gemini · Claude Sonnet 4.6", "name": "claude-sonnet-4-6"},
        {"alias": "gemini-claude-opus-4-6", "display": "Gemini · Claude Opus 4.6", "name": "claude-opus-4-6-thinking"},
    ],
}


def _ensure_extra_models(data: Dict[str, Any]) -> bool:
    if data.get("extras_v1"):
        return False
    pools = data.get("pools") or {}
    for key, extras in EXTRA_MODELS.items():
        pool = pools.get(key)
        if not isinstance(pool, dict):
            continue
        models = pool.get("models")
        if not isinstance(models, list):
            continue
        have = {str(m.get("alias") or "") for m in models if isinstance(m, dict)}
        for m in extras:
            if m["alias"] not in have:
                models.append(dict(m))
    data["extras_v1"] = True
    return True


def _ensure_gemini_pro(data: Dict[str, Any]) -> bool:
    pool = (data.get("pools") or {}).get("gemini")
    if not isinstance(pool, dict):
        return False
    models = pool.get("models")
    if not isinstance(models, list):
        return False
    for m in models:
        if not isinstance(m, dict):
            continue
        alias = str(m.get("alias") or "")
        if "pro" in alias and alias.startswith("gemini-"):
            return False
    models.insert(0, dict(GEMINI_PRO))
    return True


def _migrate(data: Dict[str, Any], prev_schema: int) -> bool:
    """按**原始文件**里的版本号判断，不能用合并默认值后的值。"""
    changed = False
    if prev_schema < 2:
        legacy = data.pop(_LEGACY_PROVIDER, None) or {}
        provs = data.get("providers")
        if not isinstance(provs, list):
            provs = []
        if legacy.get("api_key") and not provs:
            provs.append({
                "id": "kakou",
                "label": legacy.get("label") or "Kakou",
                "enabled": bool(legacy.get("enabled", True)),
                "base_url": legacy.get("base_url") or "",
                "api_key": legacy.get("api_key") or "",
                "prefix": legacy.get("prefix") or "kakou",
                "models": legacy.get("models") or [],
                "websockets": False,
                "note": "由旧版 Kakou 通道迁移",
            })
        data["providers"] = provs
        data["schema"] = 2
        changed = True
    if _LEGACY_PROVIDER in data:
        data.pop(_LEGACY_PROVIDER, None)
        changed = True
    return changed


def _merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def path():
    return paths.data_dir() / SETTINGS_FILE


def load() -> Dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache

        raw: Dict[str, Any] = {}
        p = path()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                raw = {}

        data = _merge(DEFAULTS, raw)
        changed = False
        try:
            prev = int(raw.get("schema") or 1)
        except Exception:
            prev = 1
        if _migrate(data, prev):
            changed = True
        if _ensure_gemini_pro(data):
            changed = True
        if _ensure_extra_models(data):
            changed = True
        if not data.get("internal_key"):
            data["internal_key"] = "sk-" + secrets.token_urlsafe(30)
            changed = True
        if raw != data or not p.exists():
            changed = True
        _cache = data
        if changed:
            _write(data)
        return data


def _write(data: Dict[str, Any]) -> None:
    p = path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def save(patch: Dict[str, Any], replace: bool = False) -> Dict[str, Any]:
    """合并保存；replace=True 时整份覆盖（用于导入）。"""
    global _cache
    with _lock:
        cur = load()
        new = copy.deepcopy(patch) if replace else _merge(cur, patch)
        new.pop(_LEGACY_PROVIDER, None)
        if not new.get("internal_key"):
            new["internal_key"] = cur.get("internal_key") or ("sk-" + secrets.token_urlsafe(30))
        _cache = new
        _write(new)
        return new


def mark_first_run_done() -> None:
    save({"first_run": False})


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def new_admin_password() -> str:
    return "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))


def now() -> float:
    return time.time()
