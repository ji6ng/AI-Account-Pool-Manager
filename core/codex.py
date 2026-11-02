"""直连单个账号的 Codex 上游客户端。

号池负责把流量摊给多个账号，但有两件事必须精确落到某一个号上：

  · 查这个号自己还剩多少额度（号池不提供这种视角）
  · 用这个号发一次探测请求，看它实际回的是哪个模型

所以这里不经过号池，直接拿凭据文件里的 access_token 调 OpenAI 的
backend-api。所有请求都走设置里的出网代理。
"""
from __future__ import annotations

import json
import math
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from . import paths, settings

BASE = "https://chatgpt.com/backend-api"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_DROP = {"authorization", "cookie"}


# ---------------------------------------------------------------- 凭据

def read_auth(file_name: str) -> Optional[Dict[str, Any]]:
    p = paths.auth_file(file_name)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _headers(auth: Dict[str, Any], accept: str = "application/json") -> Dict[str, str]:
    h = {
        "Authorization": f"Bearer {auth.get('access_token') or ''}",
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": UA,
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
    }
    acct = str(auth.get("account_id") or "").strip()
    if acct:
        h["chatgpt-account-id"] = acct
    return h


def _client(timeout: float = 30.0) -> httpx.Client:
    cfg = settings.load()
    proxy = (cfg.get("proxy_url") or "").strip() or None
    return httpx.Client(proxy=proxy, timeout=timeout, trust_env=False,
                        follow_redirects=False)


def _err(resp: httpx.Response) -> str:
    """把上游错误压成一句人能看懂的话。"""
    if resp.status_code == 401:
        return "凭据已失效（号池会自动刷新 token，稍后重试或重启号池）"
    if resp.status_code == 403:
        return "账号无权访问该接口（HTTP 403）"
    if resp.status_code == 429:
        return "触发了上游频率限制（HTTP 429），稍后再试"
    body = ""
    try:
        data = resp.json()
        if isinstance(data, dict):
            body = str(data.get("detail") or data.get("error") or "")[:200]
    except Exception:
        body = (resp.text or "")[:200].replace("\n", " ")
    return f"HTTP {resp.status_code}" + (f"：{body}" if body else "")


# ---------------------------------------------------------------- 额度

def _label(seconds: int) -> str:
    if not seconds:
        return "窗口"
    if seconds % 86400 == 0:
        d = seconds // 86400
        return f"{d} 天"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    return f"{round(seconds / 3600, 1)} 小时"


def _window(w: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(w, dict):
        return None
    used = float(w.get("used_percent") or 0)
    span = int(w.get("limit_window_seconds") or 0)
    return {
        "used": round(used, 1),
        "left": round(max(0.0, 100.0 - used), 1),
        "window_seconds": span,
        "label": _label(span),
        "reset_after": int(w.get("reset_after_seconds") or 0),
        "reset_at": int(w.get("reset_at") or 0),
    }


def usage(auth: Dict[str, Any]) -> Dict[str, Any]:
    """查这个号当前的额度窗口。

    /codex/usage 比 /wham/usage 稳（后者偶尔被 Cloudflare 掐掉连接），
    两者字段基本一致。
    """
    if not auth.get("access_token"):
        return {"ok": False, "error": "凭据里没有 access_token"}
    try:
        with _client(25.0) as c:
            r = c.get(f"{BASE}/codex/usage", headers=_headers(auth))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if r.status_code != 200:
        return {"ok": False, "error": _err(r), "status": r.status_code}
    try:
        d = r.json()
    except Exception:
        return {"ok": False, "error": "上游返回的不是 JSON"}

    rl = d.get("rate_limit") or {}
    cr = d.get("credits") or {}
    rc = d.get("rate_limit_reset_credits") or {}
    return {
        "ok": True,
        "email": d.get("email") or auth.get("email") or "",
        "plan": d.get("plan_type") or auth.get("plan_type") or "",
        "account_id": d.get("account_id") or auth.get("account_id") or "",
        "allowed": bool(rl.get("allowed", True)),
        "limit_reached": bool(rl.get("limit_reached")),
        "primary": _window(rl.get("primary_window")),
        "secondary": _window(rl.get("secondary_window")),
        "credits": {
            "has": bool(cr.get("has_credits")),
            "unlimited": bool(cr.get("unlimited")),
            "balance": cr.get("balance") or "0",
            "overage_limit_reached": bool(cr.get("overage_limit_reached")),
        },
        "reset": {
            "available": int(rc.get("available_count") or 0),
            "applicable": int(rc.get("applicable_available_count") or 0),
        },
        "fetched_at": int(time.time()),
    }


def credits(auth: Dict[str, Any]) -> Dict[str, Any]:
    """列出这个号攒着的「额度重置」机会，带到期时间。"""
    try:
        with _client(25.0) as c:
            r = c.get(f"{BASE}/wham/rate-limit-reset-credits", headers=_headers(auth))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "credits": []}
    if r.status_code != 200:
        return {"ok": False, "error": _err(r), "credits": []}
    try:
        d = r.json()
    except Exception:
        return {"ok": False, "error": "上游返回的不是 JSON", "credits": []}
    out: List[Dict[str, Any]] = []
    for c in d.get("credits") or []:
        if not isinstance(c, dict):
            continue
        out.append({
            "id": str(c.get("id") or ""),
            "status": str(c.get("status") or ""),
            "title": str(c.get("title") or "额度重置"),
            "description": str(c.get("description") or ""),
            "granted_at": str(c.get("granted_at") or ""),
            "expires_at": str(c.get("expires_at") or ""),
            "supported": bool(c.get("is_supported_by_plan", True)),
        })
    out.sort(key=lambda x: x.get("expires_at") or "")
    return {"ok": True, "credits": out, "available": int(d.get("available_count") or 0)}


CONSUME_MSG = {
    "reset": "已重置，额度窗口已恢复",
    "nothing_to_reset": "当前没有需要重置的额度窗口",
    "no_credit": "没有可用的重置机会",
    "already_redeemed": "这次重置已经生效过了",
}


def consume(auth: Dict[str, Any], credit_id: str = "") -> Dict[str, Any]:
    """用掉一次额度重置机会。

    这个操作不可撤销，调用方必须先让用户确认。
    redeem_request_id 是幂等键：同一份请求重试不会重复扣次数。
    """
    body: Dict[str, Any] = {"redeem_request_id": str(uuid.uuid4())}
    if credit_id:
        body["credit_id"] = credit_id
    try:
        with _client(40.0) as c:
            r = c.post(f"{BASE}/wham/rate-limit-reset-credits/consume",
                       headers={**_headers(auth), "Content-Type": "application/json"},
                       json=body)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if r.status_code != 200:
        return {"ok": False, "error": _err(r)}
    try:
        d = r.json()
    except Exception:
        return {"ok": False, "error": "上游返回的不是 JSON"}
    code = str(d.get("code") or "")
    return {
        "ok": code == "reset",
        "code": code,
        "message": CONSUME_MSG.get(code, code or "未知结果"),
        "windows_reset": int(d.get("windows_reset") or 0),
        "credit": d.get("credit") or {},
    }


# ---------------------------------------------------------------- 对话

def _extract_delta(evt: Dict[str, Any]) -> str:
    """responses 流里文本增量可能出现在两个位置。"""
    if evt.get("type") in ("response.output_text.delta", "response.text.delta"):
        return str(evt.get("delta") or "")
    return ""


def chat(auth: Dict[str, Any], model: str, prompt: str,
         timeout: float = 240.0, instructions: str = "") -> Dict[str, Any]:
    """用指定账号、指定模型发一次对话，返回完整文本。

    走的是 Codex CLI 同一个端口（/codex/responses，SSE 流）。
    """
    if not auth.get("access_token"):
        return {"ok": False, "error": "凭据里没有 access_token"}
    body = {
        "model": model,
        "instructions": instructions,
        "input": [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": prompt}],
        }],
        "stream": True,
        "store": False,
        "prompt_cache_key": str(uuid.uuid4()),
    }
    headers = {
        **_headers(auth, accept="text/event-stream"),
        "Content-Type": "application/json",
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
        "Session_id": str(uuid.uuid4()),
    }
    parts: List[str] = []
    completed = False
    started = time.time()
    try:
        with _client(timeout) as c:
            with c.stream("POST", f"{BASE}/codex/responses", headers=headers, json=body) as r:
                if r.status_code != 200:
                    r.read()
                    return {"ok": False, "error": _err(r), "status": r.status_code,
                            "elapsed": round(time.time() - started, 1)}
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        evt = json.loads(raw)
                    except Exception:
                        continue
                    t = evt.get("type") or ""
                    if t in ("response.output_text.delta", "response.text.delta"):
                        parts.append(str(evt.get("delta") or ""))
                    elif t == "response.completed":
                        completed = True
                        # 有些版本不给 delta，只在 completed 里给完整 output
                        if not "".join(parts):
                            resp = evt.get("response") or {}
                            for item in resp.get("output") or []:
                                for ct in item.get("content") or []:
                                    if ct.get("type") in ("output_text", "text"):
                                        parts.append(str(ct.get("text") or ""))
                    elif t in ("response.failed", "response.incomplete", "error"):
                        err_obj = evt.get("error")
                        err_dict = err_obj if isinstance(err_obj, dict) else {}
                        resp_err = ((evt.get("response") or {}).get("error") or {})
                        msg = resp_err.get("message") \
                            or err_dict.get("message") \
                            or (err_obj if isinstance(err_obj, str) else None) \
                            or evt.get("message") \
                            or ("上游回答未完整结束" if t == "response.incomplete" else "上游返回失败")
                        code = err_dict.get("code") or resp_err.get("code")
                        if code and str(code) not in str(msg):
                            msg = f"{msg} ({code})"
                        return {"ok": False, "error": str(msg)[:300],
                                "elapsed": round(time.time() - started, 1)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "elapsed": round(time.time() - started, 1)}

    if not completed:
        return {"ok": False, "status": 502, "error": "上游响应流提前结束，未收到完整回答",
                "elapsed": round(time.time() - started, 1)}
    text = "".join(parts)
    return {"ok": bool(text), "text": text,
            "error": "" if text else "上游没有返回任何文本",
            "elapsed": round(time.time() - started, 1)}


def refresh_hint(err: str) -> str:
    """错误信息里带 401 时给一句运维提示。"""
    return ("号池每 10 分钟会自动续期一次，若一直 401，"
            "在「概览 → 重启」让号池刷新一次凭据即可。"
            if "401" in str(err) or "失效" in str(err) else "")


def human_seconds(s: int) -> str:
    if not s or s < 0:
        return "—"
    if s >= 86400:
        return f"{s // 86400} 天 {s % 86400 // 3600} 小时"
    if s >= 3600:
        return f"{s // 3600} 小时 {s % 3600 // 60} 分"
    return f"{max(1, s // 60)} 分钟"


def pct(v: float) -> str:
    return f"{math.floor(v + 0.5)}%"


# ---------------------------------------------------------------- 额度缓存

_CACHE: Dict[str, tuple] = {}
_CACHE_LOCK = threading.Lock()


def cached_usage(file_name: str, ttl: float = 60.0, force: bool = False) -> Dict[str, Any]:
    """额度查询有点慢（要出网），同一个号 60 秒内复用上一次结果。"""
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(file_name)
        if hit and not force and now - hit[0] < ttl:
            return dict(hit[1], cached=True)
    auth = read_auth(file_name)
    data = usage(auth) if auth else {"ok": False, "error": "凭据文件读不出来"}
    data["file"] = file_name
    with _CACHE_LOCK:
        _CACHE[file_name] = (now, data)
    return dict(data, cached=False)


def drop_cache(file_name: str = "") -> None:
    with _CACHE_LOCK:
        if file_name:
            _CACHE.pop(file_name, None)
        else:
            _CACHE.clear()
