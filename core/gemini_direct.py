"""Gemini「Pro」直连通道。

为什么要有这个文件：cli-proxy-api 8.0.40 的 Antigravity 目录里**没有** Pro 档模型。
它在 Antigravity 客户端里选「Pro」时实际发的模型是 `gemini-3.8-flash-tiered`
（见账号凭据里的 `tieredModelIds.pro`），但这个 id 不在号池二进制的注册表里，
写成 `model_aliases` 也会被静默丢掉 —— 实测三种写法都收不到。

所以 Pro 请求不再过号池代理，直接拿账号的 OAuth 令牌打上游 `v1internal`：
请求/响应形状和 Gemini 原生接口一一对应，只是外面多包一层 `{"model","project","request"}`，
返回多包一层 `{"response": ...}`，这里负责拆掉。

只用凭据文件里的 access_token（号池代理每小时会刷新并写回），失败就换下一个号。
"""
from __future__ import annotations

import asyncio
import itertools
import json
import pathlib
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import paths, settings

# Gemini CLI 内置的模型名 → 上游真实模型。
# 前 6 个是「Pro」，号池代理根本路由不了，只能直连；
# 后面的是 Flash，代理认得，但仍然改写成池里的真实名，免得客户端报 404。
PRO_MODELS = {
    "gemini-3-pro-preview": "gemini-3.8-flash-tiered",
    "gemini-3.1-pro-preview": "gemini-3.8-flash-tiered",
    "gemini-3.1-pro-preview-customtools": "gemini-3.8-flash-tiered",
    "gemini-3-pro": "gemini-3.8-flash-tiered",
    "gemini-3.1-pro": "gemini-3.8-flash-tiered",
    "gemini-2.5-pro": "gemini-3.8-flash-tiered",
}

FLASH_MODELS = {
    "gemini-3-flash-preview": "gemini-3-flash",
    "gemini-3.8-flash": "gemini-3.8-flash-high",
    "gemini-3.5-flash": "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite",
    "gemini-2.5-flash": "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
}

# 展示名（给 /v1beta/models 用）
DISPLAY = {
    "gemini-3-pro-preview": "Gemini 3 Pro（直连 tiered）",
    "gemini-3-flash-preview": "Gemini 3 Flash（Preview 名）",
    "gemini-3.8-flash": "Gemini 3.8 Flash",
    "gemini-3.5-flash": "Gemini 3.5 Flash",
}

_rotation = itertools.count()
_client: Optional[httpx.AsyncClient] = None
_client_proxy: Optional[str] = None


def alias_of(model: str) -> Optional[str]:
    """客户端发来的名字要不要改写。"""
    if model in PRO_MODELS:
        return PRO_MODELS[model]
    if model in FLASH_MODELS:
        return FLASH_MODELS[model]
    return None


def is_direct(model: str) -> bool:
    return model in PRO_MODELS


def is_direct_target(name: str) -> bool:
    """上游模型名是不是只能直连的那种（号池注册表里没有）。"""
    return name in PRO_MODELS.values() or name.endswith("-tiered")


def _client_for(proxy: str) -> httpx.AsyncClient:
    global _client, _client_proxy
    if _client is not None and _client_proxy == proxy and not _client.is_closed:
        return _client
    _client = httpx.AsyncClient(
        proxy=proxy or None,
        timeout=httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=15.0),
        trust_env=False,
    )
    _client_proxy = proxy
    return _client


def _accounts() -> List[Tuple[pathlib.Path, Dict[str, Any]]]:
    """可用的 Google 号：没被停用、有 token、没标过期。"""
    out: List[Tuple[pathlib.Path, Dict[str, Any]]] = []
    now = time.time()
    for p in sorted(paths.auth_dir().glob("antigravity-*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("disabled") or not d.get("access_token"):
            continue
        exp = d.get("expired") or ""
        if isinstance(exp, str) and exp:
            try:
                from datetime import datetime

                if datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp() < now - 60:
                    continue
            except Exception:
                pass
        out.append((p, d))
    return out


def _body(model: str, payload: Dict[str, Any], auth: Dict[str, Any]) -> Dict[str, Any]:
    hints = auth.get("antigravity_capability_hints") or {}
    project = hints.get("project_id") or auth.get("project_id") or "aicode-consumers"
    return {"model": model, "project": project, "request": payload}


async def call(model: str, action: str, payload: Dict[str, Any], stream: bool) -> Tuple[int, str, Any]:
    """直连上游。返回 (状态码, content-type, body 或异步迭代器)。"""
    accs = _accounts()
    if not accs:
        return 503, "application/json", json.dumps(
            {"error": {"code": 503, "message": "号池里没有可用的 Gemini 号", "status": "UNAVAILABLE"}},
            ensure_ascii=False).encode()

    start = next(_rotation) % len(accs)
    ordered = accs[start:] + accs[:start]
    proxy = (settings.load().get("proxy_url") or "").strip()
    client = _client_for(proxy)

    last: Tuple[int, str, bytes] = (502, "application/json", b'{"error":{"message":"no account tried"}}')
    for _, auth in ordered:
        hints = auth.get("antigravity_capability_hints") or {}
        base = (hints.get("endpoint") or "https://daily-cloudcode-pa.googleapis.com").rstrip("/")
        url = f"{base}/v1internal:{action}"
        if stream:
            url += "?alt=sse"
        headers = {
            "Authorization": "Bearer " + str(auth.get("access_token")),
            "Content-Type": "application/json",
            "User-Agent": "antigravity",
        }
        try:
            if stream:
                req = client.build_request("POST", url, headers=headers,
                                           json=_body(model, payload, auth))
                resp = await client.send(req, stream=True)
                ctype = resp.headers.get("content-type", "text/event-stream")
                if resp.status_code != 200:
                    raw = await resp.aread()
                    await resp.aclose()
                    last = (resp.status_code, ctype, raw)
                    if resp.status_code in (401, 429, 500, 503):
                        continue
                    return last
                return resp.status_code, ctype, resp

            r = await client.post(url, headers=headers, json=_body(model, payload, auth))
            ctype = r.headers.get("content-type", "application/json")
            if r.status_code == 200:
                return 200, ctype, _unwrap(r.content)
            last = (r.status_code, ctype, r.content)
            if r.status_code in (401, 429, 500, 503):
                continue
            return last
        except Exception as e:  # 网络抖动换个号再试
            last = (502, "application/json",
                    json.dumps({"error": {"code": 502, "message": f"直连上游失败：{e}"}},
                               ensure_ascii=False).encode())
            continue
    return last


def _unwrap(raw: bytes) -> bytes:
    """上游回 {"response": {...}}，Gemini 客户端只认里面那层。"""
    try:
        obj = json.loads(raw)
    except Exception:
        return raw
    if isinstance(obj, dict) and isinstance(obj.get("response"), dict):
        return json.dumps(obj["response"], ensure_ascii=False).encode()
    return raw


async def stream_bytes(resp: httpx.Response):
    """把上游 SSE 每行 `data: {"response":{...}}` 拆成裸的 Gemini 事件。"""
    try:
        async for line in resp.aiter_lines():
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                obj = json.loads(chunk)
            except Exception:
                continue
            inner = obj.get("response") if isinstance(obj, dict) else None
            if inner is None:
                continue
            yield b"data: " + json.dumps(inner, ensure_ascii=False).encode() + b"\n\n"
    finally:
        await resp.aclose()


def extra_models() -> List[Dict[str, Any]]:
    """/v1beta/models 里补上这些别名，Gemini CLI 才能看见。"""
    out: List[Dict[str, Any]] = []
    for name in list(PRO_MODELS) + list(FLASH_MODELS):
        out.append({
            "name": "models/" + name,
            "displayName": DISPLAY.get(name, name),
            "description": DISPLAY.get(name, name),
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
            "supportedInputModalities": ["text", "image"],
            "supportedOutputModalities": ["text"],
            "inputTokenLimit": 1048576,
            "outputTokenLimit": 65536,
        })
    return out
