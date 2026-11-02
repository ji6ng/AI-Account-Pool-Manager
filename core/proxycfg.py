"""把设置渲染成 cli-proxy-api 的 config.yaml。

用户永远不需要手写这个文件：界面上的开关和输入框改动后，这里整份重新生成。
"""
from __future__ import annotations

import pathlib
import re
from typing import Any, Dict, List

from . import paths, providers, settings


def _q(v: str) -> str:
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _b(v: bool) -> str:
    return "true" if v else "false"


def config_path() -> pathlib.Path:
    return paths.pool_dir() / "config.yaml"


def _provider_kind(p: Dict[str, Any]) -> str:
    """这个通道该注册成 codex（Responses）还是 claude（Messages）。

    显式写了 kind 就听设置；没写就看模型名 —— 全是 claude-* 的中转必然是
    Anthropic 协议的（Kakou_claude、SudoCode 这种），混着别的就还是走 codex。
    """
    kind = str(p.get("kind") or "").strip().lower()
    if kind in ("claude", "codex"):
        return kind
    names = [str(m.get("name") or m.get("alias") or "").lower()
             for m in (p.get("models") or []) if m.get("alias")]
    if names and all(n.startswith(("claude", "anthropic")) for n in names):
        return "claude"
    return "codex"


def render(cfg: Dict[str, Any] | None = None) -> str:
    cfg = cfg or settings.load()
    L: List[str] = []
    a = L.append

    a("config-version: 8")
    a("# 本文件由 AI 号池管理器自动生成，界面里改设置会整份覆盖，不要手改")
    a("server:")
    a(f"    host: {_q(cfg['listen_host'])}")
    a(f"    port: {int(cfg['pool_port'])}")
    a("management:")
    a("    allow-remote: false")
    # 管理接口要一把钥匙，否则 /v0/management 整段 404。
    # 号池启动时会把这个明文换成 bcrypt 哈希再写回，下一轮 render 又写回明文，无所谓。
    a(f"    secret-key: {_q(cfg['internal_key'])}")
    a("    disable-control-panel: true")
    a("access:")
    a("    api-keys:")
    a(f"        - {_q(cfg['internal_key'])}")

    r = cfg.get("routing") or {}
    a("routing:")
    a(f"    strategy: {_q(r.get('strategy', 'fill-first'))}")
    a("    force-model-prefix: true")
    a(f"    session-affinity: {_b(bool(r.get('session_affinity', True)))}")
    a(f"    session-affinity-ttl: {_q(r.get('session_affinity_ttl', '1h'))}")
    a("    retry:")
    a(f"        request-retry: {int(r.get('request_retry', 5))}")
    a("    cooldown:")
    a("        transient-error-cooldown-seconds: -1")

    proxy = (cfg.get("proxy_url") or "").strip()
    if proxy:
        a("requests:")
        a(f"    proxy-url: {_q(proxy)}")

    a("oauth:")
    a('    auth-dir: "./auths"')
    excluded = [m for m in (cfg.get("excluded_models") or []) if m]
    if excluded:
        a("    excluded-models:")
        a("        codex:")
        for m in excluded:
            a(f"            - {_q(m)}")

    provs = providers.enabled(cfg)
    if provs:
        # 号池按协议分家：api-keys.codex 只走 /v1/responses（OpenAI Responses），
        # api-keys.claude 才走 /v1/messages（Anthropic 原生）。把 Claude 系中转
        # 塞进 codex 组，号池会把 Messages 请求翻成 Responses 协议再发上游，
        # 中转那边根本没有 /responses 上的 claude 模型 —— 表现就是 5 次重试后 503。
        groups: Dict[str, List[Dict[str, Any]]] = {"codex": [], "claude": []}
        for p in provs:
            groups[_provider_kind(p)].append(p)
        a("api-keys:")
        for kind in ("codex", "claude"):
            items = groups[kind]
            if not items:
                continue
            a(f"    {kind}:")
            for i, p in enumerate(items, 1):
                base = providers.api_base(p)
                if kind == "claude":
                    # Messages 执行器自己接 /v1/messages，base-url 里带 /v1 会拼成 /v1/v1/messages
                    base = re.sub(r"/v1$", "", base)
                a(f"        - base-url: {_q(base)}")
                a(f"          prefix: {_q(p['prefix'])}")
                a("          keys:")
                a(f"            - api-key: {_q(p['api_key'])}")
                models = [m for m in (p.get("models") or []) if m.get("alias")]
                if models:
                    a("              models:")
                    for m in models:
                        a(f"                - alias: {_q(m['alias'])}")
                        a(f"                  name: {_q(m.get('name') or m['alias'])}")
                        if m.get("display"):
                            a(f"                  display-name: {_q(m['display'])}")
                if kind == "codex":
                    a(f"              websockets: {_b(p.get('websockets'))}")
                a(f"          name: {_q(kind + '-' + str(i))}")

    a("observability:")
    a("    logs:")
    a("        request-log: true")
    a("        logging-to-file: true")
    a("        logs-max-total-size-mb: 50")
    a("")
    return "\n".join(L)


def write(cfg: Dict[str, Any] | None = None) -> pathlib.Path:
    p = config_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(render(cfg), encoding="utf-8")
    tmp.replace(p)
    return p


def model_names(cfg: Dict[str, Any] | None = None) -> List[str]:
    """号池对客户端暴露的全部模型名（含第三方提供商的 前缀/别名）。"""
    cfg = cfg or settings.load()
    out: List[str] = []
    for key, pool in (cfg.get("pools") or {}).items():
        if not pool.get("enabled", True):
            continue
        for m in pool.get("models") or []:
            if m.get("alias"):
                out.append(m["alias"])
    for p in providers.enabled(cfg):
        prefix = str(p.get("prefix") or "").strip("/")
        for m in p.get("models") or []:
            if m.get("alias"):
                out.append(f"{prefix}/{m['alias']}" if prefix else m["alias"])
    return out
