"""本机客户端的接入配置：检测、合并写入、备份与回滚。

借鉴 magpie 的 Agents 页思路：不只是生成一段配置让用户自己粘贴，而是
直接读本机客户端的配置文件、判断它当前指向哪个地址，并且**只改我们
负责的那几个键**，其余内容（注释、顺序、其它段落）原样保留。

为什么值得单独一个模块：
  · 用户改过端口或换过 Key 之后，客户端配置会静默失效——文件还在、
    格式没错，只是地址指向一个已经不存在的端口。这种"看起来配好了"
    的状态最容易浪费半小时。
  · 直接写文件比复制粘贴少一次人为出错，但风险也更高，所以这里所有
    写操作都有备份，并且写完立刻回读校验，失败自动还原。

三个客户端三种格式：
  Claude Code  ~/.claude/settings.json   JSON，改 env 里的 ANTHROPIC_* 与模型菜单
  Codex CLI    ~/.codex/config.toml      TOML，改根键与 [model_providers.cliproxy]
  Gemini CLI   ~/.gemini/.env            行式 KEY=VALUE，保留注释

测试时用环境变量 AIPOOL_CLIENT_HOME 指向一个临时目录，就不会碰到真实配置。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import time
import tomllib
from typing import Any, Dict, List, Optional, Tuple

from . import keys, paths, proxycfg, settings

# 写到客户端里的提供方名字，改它等于换一个 provider 段
CODEX_PROVIDER = "cliproxy"

CLAUDE_ROLES: Tuple[Tuple[str, str], ...] = (
    ("SONNET", "ANTHROPIC_DEFAULT_SONNET_MODEL"),
    ("OPUS", "ANTHROPIC_DEFAULT_OPUS_MODEL"),
    ("HAIKU", "ANTHROPIC_DEFAULT_HAIKU_MODEL"),
    ("FABLE", "ANTHROPIC_DEFAULT_FABLE_MODEL"),
    ("SUBAGENT", "CLAUDE_CODE_SUBAGENT_MODEL"),
)


# ---------------------------------------------------------------- 路径

def client_home() -> pathlib.Path:
    """客户端配置所在的用户目录。测试时可用环境变量指走。"""
    override = os.environ.get("AIPOOL_CLIENT_HOME", "").strip()
    if override:
        return pathlib.Path(override)
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    return pathlib.Path(home) if home else pathlib.Path.home()


def _specs() -> List[Dict[str, Any]]:
    h = client_home()
    return [
        {"id": "claude-code", "label": "Claude Code", "kind": "json",
         "path": h / ".claude" / "settings.json",
         "note": "写入用户级 settings.json；重新启动 Claude Code 生效"},
        {"id": "codex", "label": "Codex CLI", "kind": "toml",
         "path": h / ".codex" / "config.toml",
         "note": "合并到 config.toml；重启 Codex，VS Code 插件执行 Reload Window"},
        {"id": "gemini-cli", "label": "Gemini CLI", "kind": "env",
         "path": h / ".gemini" / ".env",
         "note": "写入 .env；重新打开终端生效"},
    ]


def _spec(cid: str) -> Optional[Dict[str, Any]]:
    for s in _specs():
        if s["id"] == cid:
            return s
    return None


def _backup_dir() -> pathlib.Path:
    d = paths.data_dir() / "backups" / "clients"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _backup(p: pathlib.Path, cid: str) -> str:
    if not p.exists():
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = p.suffix or (".env" if p.name.startswith(".env") else "")
    dst = _backup_dir() / f"{cid}-{stamp}{suffix}"
    n = 1
    while dst.exists():
        dst = _backup_dir() / f"{cid}-{stamp}-{n}{suffix}"
        n += 1
    shutil.copy2(p, dst)
    return str(dst)


def _atomic_write(p: pathlib.Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".aipool.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- 读当前状态

def _norm_base(url: str, *, strip_v1: bool = False) -> str:
    u = (url or "").strip().rstrip("/")
    if strip_v1:
        u = re.sub(r"/v1$", "", u, flags=re.I)
    return u


def _read_json(p: pathlib.Path) -> Dict[str, Any]:
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _read_env(p: pathlib.Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _read_toml(p: pathlib.Path) -> Dict[str, Any]:
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _current(spec: Dict[str, Any]) -> Dict[str, Any]:
    """从某个客户端配置里读出「地址 / 模型 / Key」三件事。"""
    p: pathlib.Path = spec["path"]
    base = model = token = ""
    roles: Dict[str, str] = {}
    try:
        if spec["kind"] == "json":
            env = (_read_json(p).get("env") or {})
            base = str(env.get("ANTHROPIC_BASE_URL") or "")
            model = str(env.get("ANTHROPIC_MODEL") or env.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or "")
            token = str(env.get("ANTHROPIC_AUTH_TOKEN") or "")
            roles = {role: str(env.get(field) or "") for role, field in CLAUDE_ROLES}
        elif spec["kind"] == "toml":
            obj = _read_toml(p)
            prov = (obj.get("model_providers") or {}).get(CODEX_PROVIDER) or {}
            base = str(prov.get("base_url") or "")
            model = str(obj.get("model") or "")
            token = str(prov.get("experimental_bearer_token") or "")
        else:
            env = _read_env(p)
            base = str(env.get("GOOGLE_GEMINI_BASE_URL") or "")
            model = str(env.get("GEMINI_MODEL") or "")
            token = str(env.get("GEMINI_API_KEY") or "")
    except Exception:
        pass
    return {"base_url": base, "model": model, "token": token, "roles": roles}


def _stored_base(kind: str, base: str) -> str:
    """同一个网关地址，落到不同客户端里应该长什么样。

    Codex 自己在 base_url 后面拼 `/responses`，所以必须带 `/v1`；
    Claude Code 拼 `/v1/messages`、Gemini CLI 拼 `/v1beta/...`，都不能带。
    多一个或少一个 `/v1` 都会让客户端打到不存在的路径——这正是要检测的 drift。
    """
    u = _norm_base(base)
    if kind == "toml":
        return u if u.lower().endswith("/v1") else u + "/v1"
    return re.sub(r"/v1$", "", u, flags=re.I)


def _same_base(kind: str, a: str, b: str) -> bool:
    if not a or not b:
        return False
    return _norm_base(a).lower() == _stored_base(kind, b).lower()


def detect(base_url: str) -> List[Dict[str, Any]]:
    """列出三个客户端的安装状态、当前指向，以及是否已经失效。"""
    known = {k["secret"]: k for k in keys.list_keys()}
    out: List[Dict[str, Any]] = []
    for spec in _specs():
        p: pathlib.Path = spec["path"]
        installed = p.exists()
        cur = _current(spec) if installed else {"base_url": "", "model": "", "token": "", "roles": {}}
        key = known.get(cur["token"]) if cur["token"] else None
        drift = bool(installed and cur["base_url"] and not _same_base(spec["kind"], cur["base_url"], base_url))
        item: Dict[str, Any] = {
            "id": spec["id"], "label": spec["label"], "path": str(p),
            "installed": installed, "note": spec["note"],
            "base_url": cur["base_url"], "model": cur["model"],
            "roles": cur.get("roles") or {},
            "key_id": key["id"] if key else 0,
            "key_name": key["name"] if key else "",
            "key_known": bool(key),
            "drift": drift,
            "status": "missing" if not installed else ("drift" if drift else "ok"),
        }
        if installed:
            try:
                if spec["kind"] == "toml":
                    tomllib.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                item["status"] = "broken"
                item["error"] = f"config.toml 解析失败：{e}"
        out.append(item)
    return out


# ---------------------------------------------------------------- TOML 行级编辑

_SECTION_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")


def _section_spans(lines: List[str]) -> List[Tuple[str, int, int]]:
    """返回 [(表名, 起始行, 结束行)]，结束行是下一节表头的位置。"""
    heads: List[Tuple[str, int]] = []
    for i, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m:
            heads.append((m.group(1).strip(), i))
    spans: List[Tuple[str, int, int]] = []
    for idx, (name, start) in enumerate(heads):
        end = heads[idx + 1][1] if idx + 1 < len(heads) else len(lines)
        spans.append((name, start, end))
    return spans


def _toml_set(text: str, table: Optional[str], key: str, literal: str) -> str:
    """在指定的表（None 表示根）里设置一个键，保留其它所有行。

    只做行级替换：命中就整行换掉，没命中就在该表末尾插入。
    不重排、不删注释，避免把用户手写的 config.toml 弄乱。
    """
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n"
    spans = _section_spans(lines)
    new_line = f"{key} = {literal}\n"

    if table is None:
        first_head = spans[0][1] if spans else len(lines)
        pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
        for i in range(first_head):
            if pat.match(lines[i]):
                lines[i] = new_line
                return "".join(lines)
        lines.insert(first_head, new_line)
        return "".join(lines)

    span = next((s for s in spans if s[0] == table), None)
    if span is None:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.append(f"[{table}]\n")
        lines.append(new_line)
        return "".join(lines)

    _, start, end = span
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i in range(start + 1, end):
        if pat.match(lines[i]):
            lines[i] = new_line
            return "".join(lines)
    lines.insert(end, new_line)
    return "".join(lines)


def _toml_lit(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(str(value), ensure_ascii=False)


# ---------------------------------------------------------------- .env 行级编辑

def _env_set(text: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n"
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = f"{key}={value}\n"
            return "".join(lines)
    lines.append(f"{key}={value}\n")
    return "".join(lines)


# ---------------------------------------------------------------- 写入

def _allowed_models(key: Dict[str, Any]) -> Tuple[bool, set]:
    listed = key.get("model_list") or []
    if not listed or "*" in listed:
        return True, set()
    return False, set(listed)


def apply(cid: str, base_url: str, model: str, key_id: int,
          roles: Optional[Dict[str, str]] = None,
          catalog: Optional[List[str]] = None) -> Dict[str, Any]:
    """把某个客户端的配置改成本网关 + 指定 Key + 指定模型。

    返回 {"ok": True, "path", "backup", "changed": [...]}；
    失败时抛出 ValueError，已写的内容会回滚。
    """
    spec = _spec(cid)
    if not spec:
        raise ValueError(f"不认识的客户端：{cid}")

    key = next((k for k in keys.list_keys() if int(k["id"]) == int(key_id)), None)
    if not key:
        raise ValueError("选中的 Key 不存在，请刷新后重试")

    model = (model or "").strip()
    if not model:
        raise ValueError("请选择要接入的模型")
    is_all, allowed = _allowed_models(key)
    if not is_all and model not in allowed:
        raise ValueError(f"这把 Key 没有授权 {model}")
    for role, value in (roles or {}).items():
        value = (value or "").strip()
        if value and not is_all and value not in allowed:
            raise ValueError(f"角色 {role} 用了这把 Key 未授权的模型 {value}")

    base = _norm_base(base_url)
    if not re.match(r"^https?://", base):
        raise ValueError("Base URL 必须是 http(s) 地址")

    p: pathlib.Path = spec["path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(p, cid)
    before = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

    changed: List[str] = []
    try:
        if spec["kind"] == "json":
            text = _write_json(p, key, base, model, roles or {}, catalog or [])
        elif spec["kind"] == "toml":
            text = _write_toml(p, key, base, model)
        else:
            text = _write_env(p, key, base, model)
        _atomic_write(p, text)
        _verify(spec, base, model, key["secret"])
        changed = ["base_url", "api_key", "model"]
    except Exception as e:
        try:
            if backup:
                shutil.copy2(backup, p)
            elif not before:
                p.unlink(missing_ok=True)
            else:
                _atomic_write(p, before)
        except Exception:
            pass
        raise ValueError(f"{spec['label']} 写入失败，已还原：{e}") from e

    return {"ok": True, "path": str(p), "backup": backup, "changed": changed,
            "base_url": base if spec["kind"] != "json" else base,
            "model": model}


def _write_json(p: pathlib.Path, key: Dict[str, Any], base: str, model: str,
                roles: Dict[str, str], catalog: List[str]) -> str:
    obj = _read_json(p)
    env = obj.get("env")
    if not isinstance(env, dict):
        env = {}
        obj["env"] = env
    # Claude Code 的 base URL 不带 /v1
    env["ANTHROPIC_BASE_URL"] = _norm_base(base, strip_v1=True)
    env["ANTHROPIC_AUTH_TOKEN"] = key["secret"]
    env["ANTHROPIC_MODEL"] = model
    for role, field in CLAUDE_ROLES:
        value = (roles.get(role) or "").strip()
        if value:
            env[field] = value
    is_all, allowed = _allowed_models(key)
    ids: List[str] = []
    for m in (catalog or []):
        m = (m or "").strip()
        if m and (is_all or m in allowed) and m not in ids:
            ids.append(m)
    if ids:
        obj["availableModels"] = ids
        obj["modelPicker"] = {
            "replaceBuiltInOptions": True,
            "options": [{"model": m, "label": m} for m in ids],
        }
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def _write_toml(p: pathlib.Path, key: Dict[str, Any], base: str, model: str) -> str:
    text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    if not text.strip():
        text = ""
    effort = "high"
    try:
        cur = tomllib.loads(text) if text.strip() else {}
        effort = str(cur.get("model_reasoning_effort") or "high")
    except Exception:
        pass

    base_v1 = base if base.lower().endswith("/v1") else base + "/v1"
    table = f"model_providers.{CODEX_PROVIDER}"
    text = _toml_set(text, None, "model", _toml_lit(model))
    text = _toml_set(text, None, "model_provider", _toml_lit(CODEX_PROVIDER))
    text = _toml_set(text, None, "model_reasoning_effort", _toml_lit(effort))
    text = _toml_set(text, table, "name", _toml_lit("AI 号池"))
    text = _toml_set(text, table, "base_url", _toml_lit(base_v1))
    text = _toml_set(text, table, "wire_api", _toml_lit("responses"))
    text = _toml_set(text, table, "supports_websockets", "false")
    text = _toml_set(text, table, "requires_openai_auth", "false")
    text = _toml_set(text, table, "model_catalog_url", _toml_lit(base_v1 + "/models"))
    text = _toml_set(text, table, "experimental_bearer_token", _toml_lit(key["secret"]))
    text = _toml_set(text, "features", "api_key_model_discovery", "true")
    tomllib.loads(text)          # 语法不过就直接抛，交给上层回滚
    return text


def _write_env(p: pathlib.Path, key: Dict[str, Any], base: str, model: str) -> str:
    text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    text = _env_set(text, "GOOGLE_GEMINI_BASE_URL", _norm_base(base, strip_v1=True))
    text = _env_set(text, "GEMINI_API_KEY", key["secret"])
    text = _env_set(text, "GEMINI_MODEL", model)
    return text


def _verify(spec: Dict[str, Any], base: str, model: str, secret: str) -> None:
    cur = _current(spec)
    if not _same_base(spec["kind"], cur["base_url"], base):
        raise ValueError(f"回读地址不一致：{cur['base_url'] or '空'}")
    if model and cur["model"] != model:
        raise ValueError(f"回读模型不一致：{cur['model'] or '空'}")
    if secret and cur["token"] != secret:
        raise ValueError("回读 Key 不一致")


# ---------------------------------------------------------------- 建议

def suggestions(cfg: Dict[str, Any], key: Dict[str, Any]) -> List[str]:
    """给某个 Key 推荐的模型清单：全局对外模型 ∩ Key 授权，保持原有顺序。"""
    is_all, allowed = _allowed_models(key)
    out: List[str] = []
    for name in proxycfg.model_names(cfg):
        if is_all or name in allowed:
            out.append(name)
    return out


def gateway_base(cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = cfg or settings.load()
    return f"http://127.0.0.1:{int(cfg['ui_port'])}"
