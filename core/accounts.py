"""账号管理：列出、启用/停用、删除、归池、以及内置的设备码登录。

昵称/别名一律由设置里的「池 → 模型」定义推导，所以用户改一次模型名，
所有账号的别名会一起同步，不需要逐个改文件。
"""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from . import paths, plus_priority, ports, settings

_lock = threading.RLock()
_sessions: Dict[str, Dict[str, Any]] = {}

DEVICE_URL = "https://auth.openai.com/codex/device"
_CODE_RE = re.compile(r"device code:\s*([A-Za-z0-9\-]{4,})", re.I)
_URL_RE = re.compile(r"(https://\S+)")


def _jwt_claims(token: str) -> Dict[str, Any]:
    """只解 payload，不验签 —— 这里只需要读里面的订阅日期。"""
    try:
        part = str(token or "").split(".")[1]
        part += "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(part))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def subscription(data: Dict[str, Any]) -> Dict[str, str]:
    """账号的「订阅到期」信息。

    凭据文件里的 expired 是**刷新令牌**的有效期（登录时给 10 天，每次续期
    都会往后滚），拿它当"账号到期"会让人以为号要过期了。真正的订阅到期
    在 id_token 的 chatgpt_subscription_active_until 里，解出来给前端用。
    """
    auth = _jwt_claims(data.get("id_token") or "").get("https://api.openai.com/auth") or {}
    return {
        "until": str(auth.get("chatgpt_subscription_active_until") or ""),
        "start": str(auth.get("chatgpt_subscription_active_start") or ""),
        "checked": str(auth.get("chatgpt_subscription_last_checked") or ""),
        "credential": str(data.get("expired") or ""),
        "refreshed": str(data.get("last_refresh") or ""),
    }



# ---------------------------------------------------------------- 读取

def _files() -> List[os.PathLike]:
    d = paths.auth_dir()
    return sorted(p for p in d.glob("*.json") if p.is_file())


def _read(p) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(p, data: Dict[str, Any]) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def pool_of(data: Dict[str, Any]) -> str:
    """按别名前缀判断账号属于哪个池。"""
    for a in data.get("model_aliases") or []:
        alias = str(a.get("alias") or "")
        if "-" in alias:
            head = alias.split("-", 1)[0]
            if head:
                return head
    return ""


def list_accounts() -> List[Dict[str, Any]]:
    cfg = settings.load()
    out: List[Dict[str, Any]] = []
    for p in _files():
        data = _read(p)
        if not data:
            continue
        aliases = data.get("model_aliases") or []
        out.append({
            "file": p.name,
            "email": data.get("email") or "",
            "provider": provider_of(data),
            "plan": data.get("plan_type") or data.get("plan") or "",
            "pool": pool_of(data),
            "disabled": bool(data.get("disabled")),
            "expired": data.get("expired") or "",
            "sub": subscription(data),
            "last_refresh": data.get("last_refresh") or "",
            "aliases": [a.get("alias") for a in aliases],
            "mtime": int(p.stat().st_mtime),
        })
    label = {k: v.get("label", k) for k, v in (cfg.get("pools") or {}).items()}
    for a in out:
        a["pool_label"] = label.get(a["pool"], a["pool"] or "未归类")
    out.sort(key=lambda x: (x["pool"], x["email"]))
    return out


# ---------------------------------------------------------------- 归池 / 别名

def aliases_for(pool_key: str, file_name: str = "") -> List[Dict[str, str]]:
    cfg = settings.load()
    pool = (cfg.get("pools") or {}).get(pool_key) or {}
    result = [
        {"alias": m["alias"], "display-name": m.get("display") or m["alias"], "name": m.get("name") or m["alias"]}
        for m in (pool.get("models") or [])
        if m.get("alias")
    ]
    if pool_key == "plus" and file_name:
        result += [{"alias": plus_priority.private_alias(file_name, m["alias"]),
                    "name": m["name"], "display-name": m["display-name"]}
                   for m in list(result)]
    return result


def assign_pool(file_name: str, pool_key: str) -> Dict[str, Any]:
    p = paths.auth_file(file_name)
    data = _read(p)
    if not data:
        return {"ok": False, "error": "凭据文件读不出来"}
    if (provider_of(data) == "gemini") != (pool_key == "gemini"):
        return {"ok": False, "error": "Google 账号只能加入 Gemini 池，ChatGPT 账号只能加入 ChatGPT 池"}
    aliases = aliases_for(pool_key, file_name)
    if not aliases:
        return {"ok": False, "error": f"{pool_key} 池没有配置模型"}
    with _lock:
        data["model_aliases"] = aliases
        data.pop("fork", None)
        _write(p, data)
    return {"ok": True, "aliases": [a["alias"] for a in aliases]}


def sync_all() -> Dict[str, Any]:
    """设置里改了模型名/显示名后，把所有已归类账号的别名刷新一遍。"""
    changed = 0
    for p in _files():
        data = _read(p)
        if not data:
            continue
        key = pool_of(data)
        if not key:
            continue
        want = aliases_for(key, p.name)
        if not want:
            continue
        if data.get("model_aliases") != want:
            with _lock:
                data["model_aliases"] = want
                _write(p, data)
            changed += 1
    return {"ok": True, "changed": changed}


def set_disabled(file_name: str, disabled: bool) -> Dict[str, Any]:
    p = paths.auth_file(file_name)
    data = _read(p)
    if not data:
        return {"ok": False, "error": "凭据文件读不出来"}
    with _lock:
        data["disabled"] = bool(disabled)
        _write(p, data)
    return {"ok": True}


def delete(file_name: str) -> Dict[str, Any]:
    p = paths.auth_file(file_name)
    if p is None or not p.exists():
        return {"ok": False, "error": "文件不存在"}
    with _lock:
        p.unlink()
        lockf = p.with_name(p.name + ".lock")
        if lockf.exists():
            try:
                lockf.unlink()
            except Exception:
                pass
    return {"ok": True}


# ---------------------------------------------------------------- 设备码登录

def _reader(proc: subprocess.Popen, q: "queue.Queue[str]") -> None:
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            q.put(line.rstrip("\r\n"))
    except Exception:
        pass
    finally:
        q.put("__EOF__")


# ---------------------------------------------------------------- 登录

# 两家账号的登录方式不一样：
#   codex  —— OpenAI 设备码，断开网也能看到码，轮询等它变成已授权；
#   gemini —— Google OAuth（Antigravity 通道），必须开浏览器走授权页，
#             授权完浏览器会打本地回调端口；万一回调打不回来，可以把
#             地址栏那条 URL 粘回来，进程支持从标准输入读。
_LOGIN = {
    "codex": {
        "flag": "-codex-device-login",
        "url": DEVICE_URL,
        "match": lambda u: "auth.openai.com" in u,
        "name": "OpenAI 设备码",
    },
    "gemini": {
        "flag": "-antigravity-login",
        "url": "",
        "match": lambda u: "accounts.google.com" in u,
        "name": "Google 授权页",
    },
}


def provider_of(data: Dict[str, Any]) -> str:
    """凭据文件属于哪一家。Gemini 走的是 Google 的 Antigravity 通道。"""
    t = str(data.get("type") or "").strip().lower()
    if t in ("antigravity", "gemini", "gemini-cli"):
        return "gemini"
    if t in ("codex", "openai"):
        return "codex"
    return t or "codex"


def _stop_process(proc: subprocess.Popen, timeout: float = 3.0) -> None:
    """结束登录/号池的整棵进程树，避免回调服务和孙进程残留。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        else:
            # 子进程通过 start_new_session 创建，组号固定为它自己的 PID。
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=timeout)
    except Exception:
        pass
    finally:
        try:
            if os.name == "nt":
                if proc.poll() is None:
                    proc.kill()
            else:
                # 主进程先退出时，组里仍可能有忽略 SIGTERM 的孙进程。
                os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=timeout)
        except Exception:
            pass


def start_login(pool_key: str, provider: str = "codex") -> Dict[str, Any]:
    provider = (provider or "codex").strip().lower()
    spec = _LOGIN.get(provider)
    if not spec:
        return {"ok": False, "error": f"不支持的账号类型：{provider}"}

    exe = paths.vendor_bin()
    if not exe.exists():
        return {"ok": False, "error": f"缺少内置程序 {paths.vendor_bin_name()}"}
    if not aliases_for(pool_key):
        return {"ok": False, "error": f"{pool_key} 池还没有配置模型"}

    sid = uuid.uuid4().hex[:12]
    before = {p.name for p in _files()}

    flags = 0x08000000 if os.name == "nt" else 0
    si = None
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0

    args = [str(exe.resolve()), "-config", str((paths.pool_dir() / "config.yaml").resolve()), spec["flag"]]
    callback_port = 0
    if provider == "gemini":
        # 端口被占会导致授权回调失败，先挑一个空的
        callback_port = ports.pick(18450, host="127.0.0.1")
        args += ["-no-browser", "-oauth-callback-port", str(callback_port)]

    paths.prepare_vendor_bin(exe)
    proc = subprocess.Popen(
        args,
        cwd=str(paths.pool_dir().resolve()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # gemini 要留一条往进程里写「回调 URL」的路，所以不能用 DEVNULL
        stdin=subprocess.PIPE if provider == "gemini" else subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=flags,
        startupinfo=si,
        start_new_session=os.name != "nt",
    )
    q: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=_reader, args=(proc, q), daemon=True).start()

    ses = {
        "id": sid,
        "pool": pool_key,
        "provider": provider,
        "proc": proc,
        "queue": q,
        "code": "",
        "url": spec["url"],
        "match": spec["match"],
        "callback_port": callback_port,
        "status": "waiting",     # waiting | done | failed | timeout
        "error": "",
        "account": "",
        "before": before,
        "started": time.time(),
        "log": [],
    }
    _sessions[sid] = ses
    threading.Thread(target=_watch, args=(ses,), daemon=True).start()
    return {"ok": True, "session": sid, "url": spec["url"], "provider": provider,
            "callback_port": callback_port}


def submit_callback(sid: str, url: str) -> Dict[str, Any]:
    """把浏览器地址栏那条回调 URL 交给登录进程（回调端口打不回来时用）。"""
    ses = _sessions.get(sid)
    if not ses:
        return {"ok": False, "error": "会话不存在或已结束"}
    if ses.get("provider") != "gemini":
        return {"ok": False, "error": "这个会话不需要粘贴回调地址"}
    if ses.get("status") != "waiting":
        return {"ok": False, "error": "登录已经结束"}
    text = (url or "").strip()
    if not text:
        return {"ok": False, "error": "请贴入完整的回调地址"}
    if "code=" not in text and not text.startswith("4/"):
        return {"ok": False, "error": "这看起来不是授权回调地址（应包含 code=…）"}
    proc: subprocess.Popen = ses["proc"]
    if proc.poll() is not None or proc.stdin is None:
        return {"ok": False, "error": "登录进程已退出，请重新发起"}
    try:
        proc.stdin.write(text + "\n")
        proc.stdin.flush()
    except Exception as e:
        return {"ok": False, "error": f"写入失败：{e}"}
    ses["log"].append("<< 已提交回调地址")
    return {"ok": True}


def _watch(ses: Dict[str, Any]) -> None:
    proc: subprocess.Popen = ses["proc"]
    q: "queue.Queue[str]" = ses["queue"]
    file_seen = ""
    while True:
        try:
            line = q.get(timeout=1.0)
        except queue.Empty:
            line = ""

        if line:
            if line == "__EOF__":
                break
            ses["log"].append(line)
            if len(ses["log"]) > 40:
                ses["log"] = ses["log"][-40:]
            if not ses["code"]:
                m = _CODE_RE.search(line)
                if m:
                    ses["code"] = m.group(1).upper()
            if not ses["url"] or ses["url"] == DEVICE_URL:
                m = _URL_RE.search(line)
                if m:
                    u = m.group(1).rstrip(".,)")
                    match = ses.get("match")
                    if (match and match(u)) or (not match and "auth.openai.com" in u):
                        ses["url"] = u

        # 不管有没有输出，都看看有没有新凭据文件落地
        new = [p.name for p in _files() if p.name not in ses["before"]]
        if new:
            file_seen = new[0]
            break

        if proc.poll() is not None:
            break
        if time.time() - ses["started"] > 900:
            ses["status"] = "timeout"
            ses["error"] = "15 分钟内没有完成授权，请重试"
            _stop_process(proc)
            return

    try:
        proc.wait(timeout=3)
    except Exception:
        _stop_process(proc)
    else:
        if os.name != "nt":
            _stop_process(proc)

    if file_seen:
        res = assign_pool(file_seen, ses["pool"])
        ses["account"] = file_seen
        if res.get("ok"):
            ses["status"] = "done"
            if ses.get("provider") == "gemini":
                # 新号落地后号池会自己在几秒内加载，趁这时把真实模型名补进池里，
                # 免得用户拿着预置的模型名去调、结果上游回 404。
                try:
                    _sync_gemini_models()
                except Exception:
                    pass
        else:
            ses["status"] = "failed"
            ses["error"] = res.get("error", "")
    elif ses["status"] == "waiting":
        ses["status"] = "failed"
        tail = [l for l in ses["log"][-6:] if l.strip()]
        ses["error"] = ses.get("error") or ("授权未完成：" + (" / ".join(tail) if tail else "进程已退出"))


def get_login(sid: str) -> Dict[str, Any]:
    ses = _sessions.get(sid)
    if not ses:
        return {"ok": False, "error": "会话不存在或已结束"}
    return {
        "ok": True,
        "id": sid,
        "status": ses["status"],
        "code": ses["code"],
        "url": ses["url"],
        "provider": ses.get("provider") or "codex",
        "callback_port": ses.get("callback_port") or 0,
        "account": ses["account"],
        "error": ses["error"],
        "pool": ses["pool"],
        "log": ses["log"][-8:],
    }


def cancel_login(sid: str) -> Dict[str, Any]:
    ses = _sessions.get(sid)
    if not ses:
        return {"ok": True}
    proc: subprocess.Popen = ses["proc"]
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass
    _stop_process(proc)
    ses["status"] = "failed"
    ses["error"] = "已取消"
    return {"ok": True}


def cleanup_sessions(max_age: float = 1800) -> None:
    now = time.time()
    for sid in list(_sessions):
        if now - _sessions[sid]["started"] > max_age:
            cancel_login(sid)
            _sessions.pop(sid, None)


# ---------------------------------------------------------------- Gemini 模型同步

# 池里先放两个最常见的名字，登录成功后再用上游真实列表覆盖。
DEFAULT_GEMINI_MODELS = ["gemini-3-pro", "gemini-3-flash"]


def _pool_model_ids() -> List[str]:
    """号池当前实际暴露的模型名。"""
    import httpx

    cfg = settings.load()
    try:
        r = httpx.get(
            f"http://127.0.0.1:{int(cfg['pool_port'])}/v1/models",
            headers={"Authorization": f"Bearer {cfg['internal_key']}"},
            timeout=6.0,
        )
        if r.status_code != 200:
            return []
        return [str(m.get("id") or "") for m in (r.json().get("data") or []) if m.get("id")]
    except Exception:
        return []


def _sync_gemini_models() -> None:
    """把号池里真实的 gemini-* 模型名写进 gemini 池。

    只有在池里还是预置值时才覆盖，用户自己改过就尊重用户的配置。
    号池加载新凭据有几秒延迟，所以这里重试几次。
    """
    cfg = settings.load()
    pool = (cfg.get("pools") or {}).get("gemini") or {}
    cur = [str(m.get("alias") or "") for m in (pool.get("models") or []) if m.get("alias")]
    if cur and cur != DEFAULT_GEMINI_MODELS:
        return

    ids: List[str] = []
    for _ in range(4):
        time.sleep(2.0)
        ids = [i for i in _pool_model_ids() if i.lower().startswith("gemini-")]
        if ids:
            break
    if not ids:
        return

    def pretty(name: str) -> str:
        tail = name[len("gemini-"):] if name.lower().startswith("gemini-") else name
        return "Gemini · " + tail.replace("-", " ").title()

    models = [{"alias": i, "display": pretty(i), "name": i} for i in dict.fromkeys(ids)]
    settings.save({"pools": {"gemini": {"models": models}}})
    try:
        sync_all()
    except Exception:
        pass
