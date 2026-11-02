"""AI 提供商：OpenAI 兼容的第三方接口（中转站、自建网关、其它厂商）。

一个提供商 = 一组 base_url + api_key + 若干模型别名。
启用后会被写进号池的 config.yaml，客户端看到的名字是 `前缀/别名`，
这样第三方模型和订阅账号的模型可以同时出现在同一个号池里。
"""
from __future__ import annotations

import re
import secrets
import time
from urllib.parse import urlsplit
from typing import Any, Dict, List, Optional

import httpx

from . import settings

_FIELDS = ("id", "label", "enabled", "base_url", "api_key", "prefix",
           "models", "websockets", "kind", "note")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def _id_of(p: Dict[str, Any]) -> str:
    # 中文名粘不出 id，就退回用前缀，再不行给个通用名
    return (str(p.get("id") or "").strip()
            or _slug(p.get("prefix") or "")
            or _slug(p.get("label") or "")
            or "relay")


def _clean_models(raw: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for m in raw or []:
        if not isinstance(m, dict):
            continue
        alias = str(m.get("alias") or "").strip()
        if not alias:
            continue
        out.append({
            "alias": alias,
            "display": str(m.get("display") or "").strip(),
            "name": str(m.get("name") or alias).strip(),
        })
    return out


def normalize(p: Dict[str, Any], used: Optional[set] = None) -> Dict[str, Any]:
    if used is None:
        used = set()
    pid = _id_of(p)
    base = pid
    n = 2
    while pid in used:
        pid = f"{base}-{n}"
        n += 1
    used.add(pid)
    return {
        "id": pid,
        "label": str(p.get("label") or pid).strip() or pid,
        "enabled": bool(p.get("enabled", True)),
        "base_url": str(p.get("base_url") or "").strip().rstrip("/"),
        "api_key": str(p.get("api_key") or "").strip(),
        "prefix": str(p.get("prefix") or pid).strip().strip("/") or pid,
        "models": _clean_models(p.get("models")),
        "websockets": bool(p.get("websockets", False)),
        "kind": str(p.get("kind") or "").strip().lower(),
        "note": str(p.get("note") or "").strip(),
    }


def all_of(cfg: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    cfg = cfg or settings.load()
    used: set = set()
    out: List[Dict[str, Any]] = []
    for p in cfg.get("providers") or []:
        if isinstance(p, dict):
            out.append(normalize(p, used))
    return out


def enabled(cfg: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    return [p for p in all_of(cfg) if p["enabled"] and p["api_key"] and p["base_url"]]


def get(pid: str) -> Optional[Dict[str, Any]]:
    for p in all_of():
        if p["id"] == pid:
            return p
    return None


def _save(provs: List[Dict[str, Any]]) -> None:
    settings.save({"providers": provs})


def api_base(p: Dict[str, Any]) -> str:
    """Normalize a root or /v1 address once for tests and actual forwarding."""
    base = str(p.get("base_url") or "").strip().rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def _validate(p: Dict[str, Any]) -> str:
    try:
        url = urlsplit(p["base_url"])
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            return "请输入有效的 HTTP 或 HTTPS 接口地址，不含密钥、查询参数或片段"
        url.port  # Invalid/non-numeric ports must fail before saving.
    except ValueError:
        return "接口地址无效"
    if not p["api_key"]:
        return "API Key 不能为空"
    if p.get("kind") not in ("", "claude", "codex"):
        return "接口协议只能是自动、OpenAI Responses 或 Anthropic Messages"
    if not p["models"]:
        return "请至少添加一个模型"
    aliases = [m["alias"] for m in p["models"]]
    if len(set(aliases)) != len(aliases):
        return "模型别名不能重复"
    return ""


def add(data: Dict[str, Any]) -> Dict[str, Any]:
    provs = all_of()
    used = {p["id"] for p in provs}
    p = normalize(data, used)
    error = _validate(p)
    if error:
        return {"ok": False, "error": error}
    provs.append(p)
    _save(provs)
    return {"ok": True, "provider": p}


def update(pid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    provs = all_of()
    for i, p in enumerate(provs):
        if p["id"] != pid:
            continue
        merged = dict(p)
        for k in _FIELDS:
            if k in patch and k != "id":
                merged[k] = patch[k]
        merged = normalize(merged)
        error = _validate(merged)
        # Toggling a legacy entry should not require filling previously optional models.
        if error and set(patch) != {"enabled"}:
            return {"ok": False, "error": error}
        provs[i] = merged
        _save(provs)
        return {"ok": True, "provider": merged}
    return {"ok": False, "error": "提供商不存在"}


def delete(pid: str) -> Dict[str, Any]:
    provs = all_of()
    left = [p for p in provs if p["id"] != pid]
    if len(left) == len(provs):
        return {"ok": False, "error": "提供商不存在"}
    _save(left)
    return {"ok": True}


def new_id(label: str = "") -> str:
    return _slug(label) + "-" + secrets.token_hex(2)


# ---------------------------------------------------------------- 连通性测试

def _msg(resp: httpx.Response) -> str:
    try:
        d = resp.json()
        if isinstance(d, dict):
            err = d.get("error")
            if isinstance(err, dict):
                return str(err.get("message") or "")[:200]
            if err:
                return str(err)[:200]
            if d.get("detail"):
                return str(d["detail"])[:200]
    except Exception:
        pass
    return (resp.text or "")[:160].replace("\n", " ")


def test(p: Dict[str, Any]) -> Dict[str, Any]:
    """测地址和 Key 是否可用：先列模型，失败了再试一次最小对话。"""
    base = api_base(p)
    key = str(p.get("api_key") or "").strip()
    if not str(p.get("base_url") or "").strip() or not key:
        return {"ok": False, "message": "地址和 API Key 都要填"}
    cfg = settings.load()
    proxy = (cfg.get("proxy_url") or "").strip() or None
    headers = {"Authorization": f"Bearer {key}", "x-api-key": key, "anthropic-version": "2023-06-01", "Accept": "application/json"}
    started = time.time()
    try:
        with httpx.Client(proxy=proxy, timeout=20.0, trust_env=False,
                          follow_redirects=True) as c:
            r = c.get(f"{base}/models", headers=headers)
            ms = int((time.time() - started) * 1000)
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                items = data.get("data") if isinstance(data, dict) else None
                if isinstance(items, list):
                    ids = [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
                    return {"ok": True, "ms": ms, "models": ids[:40], "count": len(ids),
                            "message": f"模型列表可访问，共 {len(ids)} 个模型；实际调用请使用模型测试"}

            models_note = ("模型列表未返回有效 JSON 列表" if r.status_code == 200 else
                           f"列模型失败（HTTP {r.status_code}）：{_msg(r)}")

            # 有些中转不开放 /models，退一步发一次极小的对话
            probe = (p.get("models") or [{}])[0]
            model = str(probe.get("name") or probe.get("alias") or "").strip()
            if model:
                result = chat(p, model, "Reply: OK", timeout=20.0, max_tokens=32)
                ms2 = result.get("ms", 0)
                if result.get("ok"):
                    return {"ok": True, "ms": ms2, "count": 1, "models": [model],
                            "message": f"连接正常（{model} 可对话），但列表接口不可用"}
                return {"ok": False, "ms": ms2,
                        "message": f"{models_note}；对话也失败：{result.get('error', '未知错误')}"}
            return {"ok": False, "ms": ms, "message": models_note}
    except Exception as e:
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------- 直连对话
def _text_of(data: Dict[str, Any]) -> str:
    """从 OpenAI 兼容响应里抠出正文。

    中转站的实现五花八门：老接口给 choices[0].message.content，
    推理模型可能只给 reasoning_content，新接口给 responses 的 output 数组。
    """
    if not isinstance(data, dict):
        return ""
    # Anthropic Messages returns a top-level content array.
    content = data.get("content")
    if isinstance(content, list):
        text = "".join(str(x.get("text") or "") for x in content if isinstance(x, dict) and x.get("type") == "text")
        if text.strip():
            return text
    try:
        msg = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
        txt = msg.get("content")
        if isinstance(txt, list):
            txt = "".join(str(x.get("text") or "") for x in txt if isinstance(x, dict))
        txt = str(txt or "")
        if txt.strip():
            return txt
        alt = str(msg.get("reasoning_content") or msg.get("reasoning") or "")
        if alt.strip():
            return alt
    except Exception:
        pass
    try:
        parts: List[str] = []
        for item in data.get("output") or []:
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("text"):
                    parts.append(str(c["text"]))
        joined = "".join(parts)
        if joined.strip():
            return joined
    except Exception:
        pass
    try:
        return str((data.get("choices") or [{}])[0].get("text") or "")
    except Exception:
        return ""


def chat(p: Dict[str, Any], model: str, prompt: str, timeout: float = 240.0,
         max_tokens: int = 2048) -> Dict[str, Any]:
    """拿提供商的地址和 Key 直接发一条对话，返回模型原文。

    优先走 /chat/completions（几乎所有中转都支持），端点不存在时退到 /responses。
    不带 temperature：指纹要的是模型的自然分布，任何采样偏移都会污染结果。
    """
    base = api_base(p)
    key = str(p.get("api_key") or "").strip()
    model = str(model or "").strip()
    if not str(p.get("base_url") or "").strip() or not key:
        return {"ok": False, "error": "提供商的地址或 API Key 没填"}
    if not model:
        return {"ok": False, "error": "没指定模型"}

    cfg = settings.load()
    proxy = (cfg.get("proxy_url") or "").strip() or None
    from .proxycfg import _provider_kind
    native = _provider_kind(p) == "claude"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if native:
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    started = time.time()

    def _post(client: httpx.Client, path: str, payload: Dict[str, Any]):
        return client.post(f"{base}{path}", headers=headers, json=payload)

    try:
        with httpx.Client(proxy=proxy, trust_env=False, follow_redirects=True,
                          timeout=httpx.Timeout(timeout, connect=20.0)) as c:
            payload: Dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "max_tokens": max_tokens,
            }
            r = _post(c, "/messages" if native else "/chat/completions", payload)
            note = ""
            if not native and r.status_code == 400 and "max_tokens" in (r.text or ""):
                # 有些新模型只认 max_completion_tokens，干脆不传，让服务端用默认值
                payload.pop("max_tokens", None)
                r = _post(c, "/chat/completions", payload)
            if not native and r.status_code in (404, 405):
                note = f"/chat/completions 不可用（{r.status_code}），改试 /responses"
                r = _post(c, "/responses", {"model": model, "input": prompt,
                                            "stream": False,
                                            "max_output_tokens": max_tokens})
            if r.status_code != 200:
                return {"ok": False, "ms": int((time.time() - started) * 1000),
                        "status": int(r.status_code),
                        "error": (note + "；" if note else "") + f"HTTP {r.status_code}：{_msg(r)}"}
            try:
                data = r.json()
            except Exception:
                data = {}
            text = _text_of(data)
            ms = int((time.time() - started) * 1000)
            if not text.strip():
                return {"ok": False, "ms": ms, "status": 502, "error": "返回 200 但正文是空的"}
            return {"ok": True, "text": text, "ms": ms, "model": model}
    except Exception as e:
        return {"ok": False, "ms": int((time.time() - started) * 1000),
                "error": f"{type(e).__name__}: {e}"}
