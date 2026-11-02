"""Gemini（Antigravity 通道）账号的额度查询与指定账号模型测试。

Codex 号的额度走 ChatGPT 的 /codex/usage，Gemini 号是另一套，而且上游把额度
分成了**两层**，必须两个接口合起来看：

  v1internal:retrieveUserQuotaSummary  → 按「模型组」给 5 小时 + 周 两个桶
      groups[{displayName, buckets[{bucketId, window, remainingFraction, resetTime}]}]
      bucketId 形如 gemini-weekly / gemini-5h / 3p-weekly / 3p-5h
  v1internal:retrieveUserQuota         → 单模型粒度的 5 小时窗口（拿来做明细）
      buckets[{modelId, remainingFraction, resetTime}]
  v1internal:loadCodeAssist            → 套餐名（free-tier / g1-pro-tier …）

两个坑：
  1. 官方客户端请求 summary 时 body 是 {"project": ""}，多传字段直接 400。
  2. **额度用尽时上游会把 remainingFraction 整个字段省掉**，不是给 0。
     早先按「没有就当跳过」处理，于是见底的模型凭空消失，最低值就成了 100%，
     这就是界面一直显示满额的原因。
"""

from __future__ import annotations

import datetime as _dt
import json
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from . import paths, settings

DEFAULT_BASE = "https://cloudcode-pa.googleapis.com"
UA = "antigravity"

# 上游的「内部模型」桶，用户在号池里根本选不到，列出来只会干扰
_INTERNAL_PREFIX = ("chat_", "tab_")

# window 字段的取值 → (中文短标签, 秒数)
_WINDOWS = {"5h": ("5 小时", 5 * 3600), "weekly": ("周", 7 * 86400)}
_WIN_ORDER = {"5h": 0, "weekly": 1}


def read_auth(file_name: str) -> Optional[Dict[str, Any]]:
    p = paths.auth_file(file_name)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _client(timeout: float = 25.0) -> httpx.Client:
    cfg = settings.load()
    proxy = (cfg.get("proxy_url") or "").strip() or None
    return httpx.Client(proxy=proxy, timeout=timeout, trust_env=False)


def _base(auth: Dict[str, Any]) -> str:
    hints = auth.get("antigravity_capability_hints") or {}
    return str(hints.get("endpoint") or DEFAULT_BASE).rstrip("/")


def _project(auth: Dict[str, Any]) -> str:
    hints = auth.get("antigravity_capability_hints") or {}
    return str(auth.get("project_id") or hints.get("project_id") or "")


def _post(c: httpx.Client, auth: Dict[str, Any], method: str, body: Dict[str, Any]) -> httpx.Response:
    return c.post(
        f"{_base(auth)}/v1internal:{method}",
        headers={
            "Authorization": f"Bearer {auth.get('access_token') or ''}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        },
        json=body,
    )


def chat(auth: Dict[str, Any], model: str, prompt: str, timeout: float = 240.0) -> Dict[str, Any]:
    """Test this Google account via its native endpoint, preserving account attribution."""
    if not auth.get("access_token"):
        return {"ok": False, "error": "凭据里没有 access_token"}
    started = time.time()
    payload = {
        "model": model, "project": _project(auth) or "aicode-consumers",
        "request": {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 2048}},
    }
    target = dict(auth)
    hints = dict(auth.get("antigravity_capability_hints") or {})
    hints.setdefault("endpoint", "https://daily-cloudcode-pa.googleapis.com")
    target["antigravity_capability_hints"] = hints
    try:
        with _client(timeout) as client:
            response = _post(client, target, "generateContent", payload)
        elapsed = round(time.time() - started, 1)
        if response.status_code != 200:
            return {"ok": False, "status": response.status_code, "error": _err(response), "elapsed": elapsed}
        data = response.json()
        data = data.get("response", data)
        candidate = (data.get("candidates") or [{}])[0]
        text = "".join(str(part.get("text") or "") for part in (candidate.get("content") or {}).get("parts", [])
                       if isinstance(part, dict) and not part.get("thought"))
        if candidate.get("finishReason") in ("MAX_TOKENS", "SAFETY", "RECITATION"):
            return {"ok": False, "error": "上游回答未完整结束：" + candidate["finishReason"], "elapsed": elapsed}
        return {"ok": bool(text.strip()), "text": text, "elapsed": elapsed,
                "error": "" if text.strip() else "上游返回 200 但没有模型正文"}
    except Exception as exc:
        return {"ok": False, "error": _exc(exc), "elapsed": round(time.time() - started, 1)}


def _err(resp: httpx.Response) -> str:
    if resp.status_code == 401:
        return "凭据已过期（号池会自动刷新 token，稍后重试）"
    if resp.status_code == 403:
        return "这个 Google 号无权访问 Antigravity（HTTP 403）"
    if resp.status_code == 429:
        return "触发了上游频率限制（HTTP 429），稍后再试"
    body = ""
    try:
        data = resp.json()
        if isinstance(data, dict):
            e = data.get("error")
            body = str((e.get("message") if isinstance(e, dict) else e) or data.get("detail") or "")[:160]
    except Exception:
        body = (resp.text or "")[:160].replace("\n", " ")
    return f"HTTP {resp.status_code}" + (f"：{body}" if body else "")


def _exc(e: Exception) -> str:
    """出网异常说人话。用户看到 ConnectError 那串英文只会以为账号坏了。"""
    name, msg = type(e).__name__, str(e)
    if "UNEXPECTED_EOF" in msg or "handshake" in msg.lower():
        return "连不上上游（代理节点把 SSL 断开了，换个节点再试）"
    if name in ("ConnectError", "ProxyError", "ConnectTimeout", "RemoteProtocolError"):
        return "连不上上游（先看「设置」里的代理是否可用）"
    if "Timeout" in name:
        return "上游响应超时"
    return f"{name}: {msg[:120]}"


def _epoch(iso: str) -> int:
    """上游给的是 2026-10-27T18:26:47Z 这种，转成 unix 秒。"""
    if not iso:
        return 0
    try:
        return int(_dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def _pct(bucket: Dict[str, Any]) -> Optional[float]:
    """取剩余百分比。上游用尽时省掉 remainingFraction —— 那是 0，不是「没数据」。

    只有 remainingAmount（没有百分比）的桶返回 None，调用方跳过。
    """
    frac = bucket.get("remainingFraction")
    if frac is None:
        amt = bucket.get("remainingAmount")
        if amt is not None:
            try:
                # 只报了绝对量的桶没有百分比可算；报 0 就是彻底用尽
                return 0.0 if float(amt) <= 0 else None
            except Exception:
                return None
        # 没有 resetTime 的多半是内部占位桶，别当成 0 吓人
        if not bucket.get("resetTime"):
            return None
        return 0.0
    try:
        return round(max(0.0, min(1.0, float(frac))) * 100, 1)
    except Exception:
        return None


def _window_of(bucket: Dict[str, Any]) -> str:
    win = str(bucket.get("window") or "").lower()
    if win in _WINDOWS:
        return win
    bid = str(bucket.get("bucketId") or "")
    if bid.endswith("weekly"):
        return "weekly"
    if bid.endswith("5h"):
        return "5h"
    return ""


def _group_name(raw: str, bucket_ids: List[str]) -> str:
    raw = (raw or "").strip()
    ids = " ".join(bucket_ids)
    if "gemini" in ids or "gemini" in raw.lower():
        return "Gemini"
    if "3p" in ids or "claude" in raw.lower() or "gpt" in raw.lower():
        return "Claude / GPT"
    return raw or "模型组"


def _summary(c: httpx.Client, auth: Dict[str, Any], now: int,
             errs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """周限额只有这里才有。失败就返回空，不影响 5 小时窗口的展示。"""
    try:
        # 官方客户端就是传空 project，多传字段会被上游 400
        r = _post(c, auth, "retrieveUserQuotaSummary", {"project": ""})
        if r.status_code != 200:
            if errs is not None:
                errs.append("周限额接口 " + _err(r))
            return []
        d = r.json()
    except Exception as e:
        if errs is not None:
            errs.append("周限额接口 " + _exc(e))
        return []

    out: List[Dict[str, Any]] = []
    for g in d.get("groups") or []:
        if not isinstance(g, dict):
            continue
        buckets: List[Dict[str, Any]] = []
        for b in g.get("buckets") or []:
            if not isinstance(b, dict):
                continue
            win = _window_of(b)
            if not win:
                continue
            left = _pct(b)
            if left is None:
                continue
            reset_at = _epoch(b.get("resetTime") or "")
            label, span = _WINDOWS[win]
            buckets.append({
                "window": win,
                "label": label,
                "left": left,
                "used": round(100.0 - left, 1),
                "reset_at": reset_at,
                "reset_after": max(0, reset_at - now) if reset_at else 0,
            })
        if not buckets:
            continue
        buckets.sort(key=lambda x: _WIN_ORDER.get(x["window"], 9))
        raw_name = str(g.get("displayName") or "")
        out.append({
            "name": _group_name(raw_name, [str(b.get("bucketId") or "") for b in g.get("buckets") or []]),
            "raw_name": raw_name,
            "desc": str(g.get("description") or ""),
            "buckets": buckets,
        })
    return out


def _tier(c: httpx.Client, auth: Dict[str, Any]) -> Dict[str, str]:
    """套餐名，失败不影响额度查询本身。"""
    try:
        r = _post(c, auth, "loadCodeAssist", {"metadata": {"pluginType": "GEMINI"}})
        if r.status_code != 200:
            return {}
        d = r.json()
        t = d.get("currentTier") or {}
        paid = d.get("paidTier") or {}
        out = {
            "id": str(t.get("id") or ""),
            "name": str(t.get("name") or ""),
            "paid": str(paid.get("name") or ""),
        }
        bad = d.get("ineligibleTiers") or []
        if bad and isinstance(bad[0], dict):
            out["ineligible"] = str(bad[0].get("reasonCode") or "")
        return out
    except Exception:
        return {}


def _models(c: httpx.Client, auth: Dict[str, Any], now: int,
            errs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """单模型粒度的 5 小时窗口，只用来做明细（悬停）与兜底。"""
    try:
        r = _post(c, auth, "retrieveUserQuota", {"project": _project(auth)})
        if r.status_code != 200:
            if errs is not None:
                errs.append("模型额度接口 " + _err(r))
            return []
        d = r.json()
    except Exception as e:
        if errs is not None:
            errs.append("模型额度接口 " + _exc(e))
        return []

    models: List[Dict[str, Any]] = []
    for b in d.get("buckets") or []:
        if not isinstance(b, dict):
            continue
        mid = str(b.get("modelId") or "")
        if not mid or mid.startswith(_INTERNAL_PREFIX):
            continue
        left = _pct(b)
        if left is None:
            continue
        reset_at = _epoch(b.get("resetTime") or "")
        models.append({
            "model": mid,
            "left": left,
            "reset_at": reset_at,
            "reset_after": max(0, reset_at - now) if reset_at else 0,
        })
    models.sort(key=lambda m: (m["left"], m["model"]))
    return models


def usage(auth: Dict[str, Any]) -> Dict[str, Any]:
    """查这个 Gemini 号还剩多少：按模型组给 5 小时 + 周两个窗口。"""
    if not auth.get("access_token"):
        return {"ok": False, "provider": "gemini", "error": "凭据里没有 access_token"}
    now = int(time.time())
    errs: List[str] = []
    try:
        with _client() as c:
            groups = _summary(c, auth, now, errs)
            models = _models(c, auth, now, errs)
            tier = _tier(c, auth)
    except Exception as e:
        return {"ok": False, "provider": "gemini", "error": _exc(e)}

    # 老号 / 接口抽风时 summary 可能给空，退化成「全模型共用一个 5 小时窗口」
    if not groups and models:
        low = models[0]
        groups = [{
            "name": "Gemini",
            "raw_name": "Gemini models",
            "desc": "上游没返回周限额（该套餐下 Gemini 模型不受周上限约束）",
            "buckets": [{
                "window": "5h",
                "label": _WINDOWS["5h"][0],
                "left": low["left"],
                "used": round(100.0 - low["left"], 1),
                "reset_at": low["reset_at"],
                "reset_after": low["reset_after"],
            }],
        }]

    # AI Pro / Ultra 下 Gemini 模型只受 5 小时窗口约束，上游不会下发 gemini-* 分组，
    # 只剩 Claude/GPT 一组。这时用单模型额度补一个 Gemini 组，否则界面上根本看不到
    # Gemini 的余量（用户最关心的恰恰是这个）。
    if models and not any(g["name"] == "Gemini" for g in groups):
        low = models[0]
        groups.insert(0, {
            "name": "Gemini",
            "raw_name": "Gemini models",
            "desc": "上游没有下发 Gemini 组的周限额（该套餐下 Gemini 模型通常不受周上限约束）",
            "buckets": [{
                "window": "5h",
                "label": _WINDOWS["5h"][0],
                "left": low["left"],
                "used": round(100.0 - low["left"], 1),
                "reset_at": low["reset_at"],
                "reset_after": low["reset_after"],
            }],
        })

    if not groups:
        # 接口本身失败（代理挂了 / token 过期）和「号没开通」要分开说，否则用户去查错方向
        if errs:
            return {"ok": False, "provider": "gemini",
                    "error": "；".join(errs[:2]), "tier": tier}
        return {"ok": False, "provider": "gemini",
                "error": "上游没有返回任何额度（号可能没开通 Code Assist）",
                "tier": tier}

    # 5 小时窗口取全组最低，周窗口同理 —— 谁先见底谁就是瓶颈
    def worst(win: str) -> Optional[Dict[str, Any]]:
        cand = [b for g in groups for b in g["buckets"] if b["window"] == win]
        if not cand:
            return None
        return min(cand, key=lambda b: b["left"])

    w5, ww = worst("5h"), worst("weekly")
    reached = any(b["left"] <= 0 for g in groups for b in g["buckets"])

    plan = tier.get("paid") or tier.get("name") or "Antigravity"
    return {
        "ok": True,
        "provider": "gemini",
        "email": auth.get("email") or "",
        "plan": plan,
        "tier_id": tier.get("id") or "",
        "plan_name": tier.get("name") or "",
        "ineligible": tier.get("ineligible") or "",
        "groups": groups,
        "models": models,
        "model_count": len(models),
        "has_weekly": bool(ww),
        # 复用 Codex 那套窗口结构，别处（重置弹窗等）照旧能读
        "primary": {
            "used": w5["used"], "left": w5["left"],
            "window_seconds": 5 * 3600, "label": "5 小时",
            "reset_after": w5["reset_after"], "reset_at": w5["reset_at"],
        } if w5 else None,
        "secondary": {
            "used": ww["used"], "left": ww["left"],
            "window_seconds": 7 * 86400, "label": "周",
            "reset_after": ww["reset_after"], "reset_at": ww["reset_at"],
        } if ww else None,
        "limit_reached": reached,
        "fetched_at": now,
    }


_CACHE: Dict[str, tuple] = {}
_CACHE_LOCK = threading.Lock()


def cached_usage(file_name: str, ttl: float = 60.0, force: bool = False) -> Dict[str, Any]:
    """和 codex 那边一样：出网查询，同一个号 60 秒内复用。"""
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(file_name)
        if hit and not force and now - hit[0] < ttl:
            return dict(hit[1], cached=True)
    auth = read_auth(file_name)
    data = usage(auth) if auth else {"ok": False, "provider": "gemini", "error": "凭据文件读不出来"}
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
