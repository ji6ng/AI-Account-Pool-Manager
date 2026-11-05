"""Web 服务：管理界面 + 对客户端暴露的 OpenAI 兼容入口。

同一个端口干两件事：
  /            管理界面（默认只允许本机访问，设了管理口令才允许远程）
  /v1/...      客户端入口，按 Key 校验来源、按白名单裁剪模型，其余原样透传给内部号池
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import (__version__, activation, accounts, autostart, clients, codex, codexcat, gemini, gemini_direct, keys,
               modeltrace, netinfo, paths, plus_fallback, plus_priority, poolapi, providers, proxycfg, settings)
from .pool import pool

LOCKED = {"locked": True}
DROP_REQ = {
    "host", "content-length", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade",
    "authorization", "x-api-key", "accept-encoding", "cookie",
}
DROP_RESP = {"content-length", "content-encoding", "transfer-encoding", "connection", "keep-alive"}
GEN_HINTS = ("/responses", "/chat/completions", "/completions", "/messages", "/embeddings")

_sessions: Dict[str, float] = {}
_client: Optional[httpx.AsyncClient] = None
BOOT = time.time()


def http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(None, connect=20.0),
            limits=httpx.Limits(max_connections=128, max_keepalive_connections=64),
            follow_redirects=False,
            trust_env=False,
        )
    return _client


# ---------------------------------------------------------------- 鉴权

def _is_local(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


def _has_session(request: Request) -> bool:
    sid = request.cookies.get("aipool_sid") or ""
    exp = _sessions.get(sid)
    if exp and exp > time.time():
        return True
    return False


def require_admin(request: Request) -> None:
    if _is_local(request) or _has_session(request):
        return
    raise HTTPException(status_code=401, detail="需要先登录管理界面")


def new_session() -> str:
    sid = secrets.token_urlsafe(24)
    _sessions[sid] = time.time() + 86400
    return sid


async def _restart_saved(result: Dict[str, Any]) -> Dict[str, Any]:
    """A saved configuration is not a successful restart until the child is ready."""
    if not result.get("ok"):
        return result
    restart = await asyncio.to_thread(pool.restart)
    if not restart.get("ok"):
        return {**result, "ok": False, "saved": True, "restarted": False,
                "error": "配置已保存，但号池启动失败：" + str(restart.get("error") or "请查看运行日志")}
    return {**result, "restarted": True}


# ---------------------------------------------------------------- 应用

def create_app() -> FastAPI:
    app = FastAPI(title=paths.PRODUCT_NAME, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.plus_fallback = plus_fallback.Monitor()
    app.state.activation_scheduler = activation.Scheduler()

    @app.on_event("startup")
    async def _boot() -> None:
        keys.init()
        app.state.fallback_task = asyncio.create_task(app.state.plus_fallback.run())
        app.state.activation_task = asyncio.create_task(app.state.activation_scheduler.run())

    @app.on_event("shutdown")
    async def _stop_fallback() -> None:
        activation_task = getattr(app.state, "activation_task", None)
        if activation_task:
            activation_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await activation_task
        task = getattr(app.state, "fallback_task", None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # ------------------------------------------------------------ 登录
    @app.post("/api/login")
    async def api_login(request: Request):
        cfg = settings.load()
        password = (cfg.get("admin_password") or "").strip()
        body = await request.json()
        if not password:
            if not _is_local(request):
                raise HTTPException(status_code=403, detail="未设置管理口令，仅允许本机管理")
            return {"ok": True, "token": ""}
        if str(body.get("password") or "") != password:
            raise HTTPException(status_code=401, detail="口令不正确")
        sid = new_session()
        resp = JSONResponse({"ok": True})
        resp.set_cookie("aipool_sid", sid, httponly=True, samesite="lax", max_age=86400)
        return resp

    @app.get("/api/whoami")
    async def api_whoami(request: Request):
        cfg = settings.load()
        need = bool((cfg.get("admin_password") or "").strip())
        return {"local": _is_local(request), "password_required": need,
                "logged_in": _has_session(request), "allowed": _is_local(request) or _has_session(request)}

    # ------------------------------------------------------------ 概览
    @app.get("/api/overview")
    async def api_overview(request: Request):
        require_admin(request)
        cfg = settings.load()
        h = await asyncio.to_thread(pool.health)
        accs = accounts.list_accounts()
        by_pool: Dict[str, int] = {}
        for a in accs:
            by_pool[a["pool_label"]] = by_pool.get(a["pool_label"], 0) + 1
        return {
            "product": paths.PRODUCT_NAME,
            "version": __version__,
            "uptime": int(time.time() - BOOT),
            "pool": h,
            "accounts": {"total": len(accs), "active": sum(1 for a in accs if not a["disabled"]), "by_pool": by_pool},
            "keys": len(keys.list_keys()),
            "today": keys.today_count(),
            "stats": keys.req_stats(30, [
                {"prefix": p.get("prefix"), "label": p.get("label")}
                for p in providers.enabled(cfg)
            ]),
            "exposed": len(proxycfg.model_names(cfg)),
            "ports": {"ui": int(cfg["ui_port"]), "pool": int(cfg["pool_port"])},
            # 「客户端接入」卡片要给出局域网地址：浏览器地址栏只能反映你自己怎么打开的，
            # 在 127.0.0.1 打开时就看不到同网段可用的那条。
            "lan_ips": netinfo.local_ips(),
            "lan_enabled": str(cfg.get("listen_host") or "") not in ("127.0.0.1", "localhost", "::1"),
            "proxy_url": cfg.get("proxy_url") or "",
            "data_dir": str(paths.data_dir()),
        }

    @app.post("/api/pool/{action}")
    async def api_pool(action: str, request: Request):
        require_admin(request)
        if action == "start":
            r = await asyncio.to_thread(pool.start)
        elif action == "stop":
            r = await asyncio.to_thread(pool.stop)
        elif action == "restart":
            r = await asyncio.to_thread(pool.restart)
        else:
            raise HTTPException(status_code=400, detail="未知操作")
        return r

    # ------------------------------------------------------------ 账号
    @app.get("/api/accounts")
    async def api_accounts(request: Request):
        require_admin(request)
        return {"accounts": accounts.list_accounts()}

    @app.get("/api/runtime/accounts")
    async def api_runtime_accounts(request: Request):
        """号池里每个账号的实时状态：近 3 小时的 10 分钟分桶、累计成功失败、冷却。"""
        require_admin(request)
        snap = await asyncio.to_thread(poolapi.runtime)
        by_file = {a["file"]: a for a in accounts.list_accounts()}
        for row in snap.get("accounts") or []:
            meta = by_file.get(row["file"]) or {}
            row["plan"] = meta.get("plan") or ""
            row["sub"] = meta.get("sub") or {}
        return snap

    @app.post("/api/accounts/login")
    async def api_login_start(request: Request):
        require_admin(request)
        body = await request.json()
        return accounts.start_login(
            str(body.get("pool") or "plus"),
            str(body.get("provider") or "codex"),
        )

    @app.post("/api/accounts/login/{sid}/callback")
    async def api_login_callback(sid: str, request: Request):
        """Google 授权回调打不回本机时，前端把地址栏那条 URL 交回来。"""
        require_admin(request)
        body = await request.json()
        return accounts.submit_callback(sid, str(body.get("url") or ""))

    @app.get("/api/accounts/login/{sid}")
    async def api_login_state(sid: str, request: Request):
        require_admin(request)
        return accounts.get_login(sid)

    @app.delete("/api/accounts/login/{sid}")
    async def api_login_cancel(sid: str, request: Request):
        require_admin(request)
        return accounts.cancel_login(sid)

    @app.post("/api/accounts/{file}/pool")
    async def api_account_pool(file: str, request: Request):
        require_admin(request)
        body = await request.json()
        return accounts.assign_pool(file, str(body.get("pool") or ""))

    @app.post("/api/accounts/{file}/disabled")
    async def api_account_disabled(file: str, request: Request):
        require_admin(request)
        body = await request.json()
        return accounts.set_disabled(file, bool(body.get("disabled")))

    @app.delete("/api/accounts/{file}")
    async def api_account_delete(file: str, request: Request):
        require_admin(request)
        codex.drop_cache(file)
        gemini.drop_cache(file)
        return accounts.delete(file)

    # ------------------------------------------------------------ 额度
    @app.get("/api/usage")
    async def api_usage(request: Request, refresh: int = 0):
        """所有账号的额度一起查（并发，单号失败不影响其它号）。

        Codex 号走 ChatGPT 的用量接口，Gemini 号走 Antigravity 的配额接口，
        两边字段不同，各查各的，前端按 provider 分开渲染。
        """
        require_admin(request)
        accs = accounts.list_accounts()
        rows = await asyncio.gather(*[
            asyncio.to_thread(
                (gemini if (a.get("provider") or "codex") == "gemini" else codex).cached_usage,
                a["file"], 60.0, bool(refresh))
            for a in accs
        ]) if accs else []
        return {"usage": {a["file"]: r for a, r in zip(accs, rows)}}

    @app.get("/api/accounts/{file}/usage")
    async def api_account_usage(file: str, request: Request, refresh: int = 1):
        require_admin(request)
        auth = codex.read_auth(file) or {}
        prov = "gemini" if str(auth.get("type") or "") == "antigravity" else "codex"
        mod = gemini if prov == "gemini" else codex
        return await asyncio.to_thread(mod.cached_usage, file, 60.0, bool(refresh))

    @app.get("/api/accounts/{file}/credits")
    async def api_account_credits(file: str, request: Request):
        require_admin(request)
        auth = codex.read_auth(file)
        if not auth:
            raise HTTPException(status_code=404, detail="凭据文件读不出来")
        # Google 号没有「额度重置次数」这个概念，别拿 ChatGPT 的接口去问
        if str(auth.get("type") or "") == "antigravity":
            return {"ok": False, "credits": [], "error": "Google 号不支持额度重置"}
        return await asyncio.to_thread(codex.credits, auth)

    @app.post("/api/accounts/{file}/reset")
    async def api_account_reset(file: str, request: Request):
        """用掉一次额度重置机会。不可撤销，前端必须已经确认过。"""
        require_admin(request)
        auth = codex.read_auth(file)
        if not auth:
            raise HTTPException(status_code=404, detail="凭据文件读不出来")
        body = await request.json() if await request.body() else {}
        r = await asyncio.to_thread(codex.consume, auth, str(body.get("credit_id") or ""))
        codex.drop_cache(file)
        r["usage"] = await asyncio.to_thread(codex.cached_usage, file, 60.0, True)
        return r

    # ------------------------------------------------------------ 模型
    @app.get("/api/models")
    async def api_models(request: Request):
        require_admin(request)
        cfg = settings.load()
        try:
            r = await http().get(
                f"http://127.0.0.1:{int(cfg['pool_port'])}/v1/models",
                headers={"Authorization": f"Bearer {cfg['internal_key']}"}, timeout=10.0)
            data = r.json()
            ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        except Exception:
            ids = []
        pools = cfg.get("pools") or {}
        return {"models": ids, "exposed": proxycfg.model_names(cfg), "pools": pools,
                "suggest": {k: _pool_suggest(cfg, k) for k in pools}}

    @app.post("/api/pools/{key}/models")
    async def api_pool_model_add(key: str, request: Request):
        """给某个订阅池加一个模型（官方账号用）。

        只改设置不够：号池把「别名 → 上游模型」写在每个凭据文件的 model_aliases 里，
        所以保存后还要调用 accounts.sync_all() 把别名刷进所有账号。
        """
        require_admin(request)
        body = await request.json()
        cfg = settings.load()
        pools = cfg.get("pools") or {}
        if key not in pools:
            return {"ok": False, "error": f"没有「{key}」这个订阅池"}
        name = str(body.get("name") or "").strip()
        alias = str(body.get("alias") or "").strip()
        display = str(body.get("display") or "").strip()
        if not name and not alias:
            return {"ok": False, "error": "上游模型名不能为空"}
        if not name:
            name = alias
        if not alias:
            # plus / pro 池的别名必须带前缀，否则 pool_of() 认不出账号属于哪个池
            alias = f"{key}-{name}" if key in ("plus", "pro") else name
        if not display:
            display = alias
        if alias in set(proxycfg.model_names(cfg)):
            return {"ok": False, "error": f"「{alias}」已经存在了"}
        models = [dict(m) for m in (pools[key].get("models") or []) if isinstance(m, dict)]
        models.append({"alias": alias, "display": display, "name": name})
        settings.save({"pools": {key: {"models": models}}})
        synced = accounts.sync_all().get("changed", 0)
        # 号池只在启动时读 model_aliases，改完必须重启才对外生效（约 3~5 秒）
        await asyncio.to_thread(pool.restart)
        # Pro 档别名走网关直连，号池本来就不列，跳过确认
        verified = await _pool_settle(cfg, alias, True) if not gemini_direct.is_direct_target(alias) else True
        hint = ""
        if key == "gemini" and gemini_direct.is_direct_target(name):
            hint = "Pro 档：网关直连上游，只在 Gemini 原生协议（/v1beta）下可用。"
        elif not verified:
            hint = ("号池没列出这个模型：上游名「%s」它不认识，发请求会 404。"
                    "换一个上游名再试（界面上「上游名」输入框有候选）。" % name)
        return {"ok": True, "alias": alias, "models": models, "verified": verified,
                "accounts_synced": synced, "restarted": True, "hint": hint}

    @app.delete("/api/pools/{key}/models/{alias}")
    async def api_pool_model_del(key: str, alias: str, request: Request):
        require_admin(request)
        cfg = settings.load()
        pools = cfg.get("pools") or {}
        if key not in pools:
            return {"ok": False, "error": f"没有「{key}」这个订阅池"}
        cur = [dict(m) for m in (pools[key].get("models") or []) if isinstance(m, dict)]
        models = [m for m in cur if str(m.get("alias") or "") != alias]
        if len(models) == len(cur):
            return {"ok": False, "error": f"没找到模型「{alias}」"}
        if not models:
            return {"ok": False, "error": "池里至少要留一个模型"}
        settings.save({"pools": {key: {"models": models}}})
        synced = accounts.sync_all().get("changed", 0)
        await asyncio.to_thread(pool.restart)
        verified = await _pool_settle(cfg, alias, False)
        hint = "" if verified else "号池重启后没能确认这个模型已消失，等几秒再看模型列表。"
        return {"ok": True, "models": models, "verified": verified, "hint": hint,
                "accounts_synced": synced, "restarted": True}

    # ------------------------------------------------------------ AI 提供商
    @app.get("/api/providers")
    async def api_providers(request: Request):
        require_admin(request)
        return {"providers": providers.all_of()}

    @app.post("/api/providers")
    async def api_provider_add(request: Request):
        require_admin(request)
        r = providers.add(await request.json())
        return await _restart_saved(r)

    @app.patch("/api/providers/{pid}")
    async def api_provider_update(pid: str, request: Request):
        require_admin(request)
        r = providers.update(pid, await request.json())
        return await _restart_saved(r)

    @app.delete("/api/providers/{pid}")
    async def api_provider_delete(pid: str, request: Request):
        require_admin(request)
        r = providers.delete(pid)
        return await _restart_saved(r)

    @app.post("/api/providers/test")
    async def api_provider_test(request: Request):
        """测试连通性。可以传已保存的 id，也可以传对话框里还没保存的内容。"""
        require_admin(request)
        body = await request.json()
        target = body if body.get("base_url") else (providers.get(str(body.get("id") or "")) or {})
        return await asyncio.to_thread(providers.test, target)

    # ------------------------------------------------------------ 模型测试
    @app.get("/api/modeltrace/bank")
    async def api_mt_bank(request: Request):
        require_admin(request)
        return await asyncio.to_thread(modeltrace.bank_info)

    @app.post("/api/modeltrace/bank/update")
    async def api_mt_bank_update(request: Request):
        require_admin(request)
        return await asyncio.to_thread(modeltrace.update_bank)

    @app.get("/api/modeltrace/targets")
    async def api_mt_targets(request: Request):
        """可测的目标：订阅池的「账号×模型」，以及提供商的模型（不经过账号）。"""
        require_admin(request)
        cfg = settings.load()
        models: List[Dict[str, Any]] = []
        for key, p in (cfg.get("pools") or {}).items():
            if not p.get("enabled", True):
                continue
            for m in p.get("models") or []:
                if m.get("alias"):
                    up = m.get("name") or m["alias"]
                    models.append({"key": "p:" + m["alias"],
                                   "alias": m["alias"], "upstream": up,
                                   "in_bank": modeltrace.is_known(up),
                                   "pool": key, "pool_label": p.get("label") or key})
        prov_models: List[Dict[str, Any]] = []
        for p in providers.all_of(cfg):
            prefix = str(p.get("prefix") or "").strip()
            for m in p.get("models") or []:
                name = m.get("name") or m.get("alias")
                if not name:
                    continue
                prov_models.append({
                    "key": f"r:{p['id']}|{name}",
                    "provider": p["id"],
                    "provider_label": p.get("label") or p["id"],
                    "alias": m.get("alias") or name,
                    "upstream": name,
                    "exposed": f"{prefix}/{m.get('alias') or name}" if prefix else (m.get("alias") or name),
                    "in_bank": modeltrace.is_known(name),
                    "enabled": bool(p.get("enabled", True)),
                    "ready": bool(p.get("base_url") and p.get("api_key")),
                })
        return {"models": models, "provider_models": prov_models,
                "accounts": accounts.list_accounts()}

    @app.post("/api/modeltrace/run")
    async def api_mt_run(request: Request):
        require_admin(request)
        body = await request.json()
        files = [str(x) for x in (body.get("accounts") or [])]
        models = [str(x) for x in (body.get("models") or [])]
        relays = body.get("relays") or []
        rounds = int(body.get("rounds") or 1)
        return modeltrace.start(files, models, rounds,
                                relays if isinstance(relays, list) else [])

    @app.get("/api/modeltrace/job/{jid}")
    async def api_mt_job(jid: str, request: Request):
        require_admin(request)
        return modeltrace.get(jid)

    @app.post("/api/modeltrace/job/{jid}/cancel")
    async def api_mt_cancel(jid: str, request: Request):
        require_admin(request)
        return modeltrace.cancel(jid)

    # ------------------------------------------------------------ 设置
    @app.get("/api/settings")
    async def api_get_settings(request: Request):
        require_admin(request)
        return settings.load()

    @app.get("/api/activation-tasks")
    async def api_activation_tasks(request: Request):
        require_admin(request)
        return app.state.activation_scheduler.snapshot()

    @app.post("/api/activation-tasks")
    async def api_activation_task_create(request: Request):
        require_admin(request)
        try:
            task = app.state.activation_scheduler.create(await request.json())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"ok": True, "task": task}

    @app.put("/api/activation-tasks/{task_id}")
    async def api_activation_task_update(task_id: str, request: Request):
        require_admin(request)
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            raise HTTPException(status_code=400, detail="启用状态必须是布尔值")
        try:
            return app.state.activation_scheduler.set_enabled(task_id, body["enabled"])
        except KeyError as e:
            raise HTTPException(status_code=404, detail="定时任务不存在") from e
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.delete("/api/activation-tasks/{task_id}")
    async def api_activation_task_delete(task_id: str, request: Request):
        require_admin(request)
        try:
            return app.state.activation_scheduler.delete(task_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="定时任务不存在") from e
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.post("/api/activation-tasks/{task_id}/run")
    async def api_activation_task_run(task_id: str, request: Request):
        require_admin(request)
        try:
            return app.state.activation_scheduler.run_now(task_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="定时任务不存在") from e
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get("/api/plus-fallback")
    async def api_plus_fallback(request: Request, refresh: int = 0):
        require_admin(request)
        return await app.state.plus_fallback.status(settings.load(), bool(refresh))

    @app.get("/api/plus-priority")
    async def api_plus_priority(request: Request, refresh: int = 0):
        require_admin(request)
        cfg = settings.load()
        accs = accounts.list_accounts()
        rows = await asyncio.gather(*[
            asyncio.to_thread(codex.cached_usage, a["file"], 60.0, bool(refresh))
            for a in accs if a.get("provider", "codex") == "codex" and a.get("pool") == "plus"
        ])
        usage = {}
        i = 0
        for a in accs:
            if a.get("provider", "codex") == "codex" and a.get("pool") == "plus":
                usage[a["file"]] = rows[i]; i += 1
        return plus_priority.status(accs, usage, cfg)

    @app.put("/api/plus-priority")
    async def api_put_plus_priority(request: Request):
        require_admin(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="请求格式无效")
        rows = accounts.list_accounts()
        order = body.get("order")
        if order is None:
            order = settings.load().get("plus_account_order") or []
        try:
            groups, order = plus_priority.validate(body.get("plus_priority") or settings.load().get("plus_priority") or {}, order, rows)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        settings.save({"plus_account_order": order, "plus_priority": groups})
        return {"ok": True, "order": order, "plus_priority": groups}

    @app.put("/api/settings")
    async def api_put_settings(request: Request):
        require_admin(request)
        body = await request.json()
        old = settings.load()
        ports = {}
        for name in ("ui_port", "pool_port"):
            value = body.get(name, old[name])
            if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
                raise HTTPException(status_code=400, detail="端口必须是 1024–65535 之间的整数")
            ports[name] = value
        if ports["ui_port"] == ports["pool_port"]:
            raise HTTPException(status_code=400, detail="界面端口和内部号池端口不能相同")
        if "plus_fallback" in body:
            try:
                body["plus_fallback"] = plus_fallback.validate(body["plus_fallback"], {**old, **body})
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            # 模型映射是替换操作，不能深合并后保留用户已清空的旧路径。
            body = {**old, **body}
            new = settings.save(body, replace=True)
        else:
            new = settings.save(body)
        app.state.plus_fallback.wake.set()
        if sys.platform.startswith("linux") and old.get("autostart") != new.get("autostart"):
            await asyncio.to_thread(autostart.apply, bool(new.get("autostart")))
        need_restart = (
            int(old.get("pool_port", 0)) != int(new.get("pool_port", 0))
            or (old.get("proxy_url") or "") != (new.get("proxy_url") or "")
            or old.get("routing") != new.get("routing")
            or old.get("excluded_models") != new.get("excluded_models")
            or old.get("providers") != new.get("providers")
            or old.get("pools") != new.get("pools")
            or old.get("listen_host") != new.get("listen_host")
        )
        synced = 0
        if old.get("pools") != new.get("pools"):
            synced = accounts.sync_all().get("changed", 0)
        if need_restart:
            # 重启要 3~5 秒，别把整个事件循环卡住（否则页面转圈、其他请求排队）
            restarted = await _restart_saved({"ok": True})
            if not restarted["ok"]:
                return restarted
        return {"ok": True, "restarted": need_restart, "accounts_synced": synced,
                "restart_ui": int(old.get("ui_port", 0)) != int(new.get("ui_port", 0))}

    @app.post("/api/settings/proxy-test")
    async def api_proxy_test(request: Request):
        require_admin(request)
        body = await request.json()
        url = str(body.get("proxy_url") or "").strip()
        if not url:
            return {"ok": True, "msg": "未设置代理，将直连"}
        try:
            async with httpx.AsyncClient(proxy=url, timeout=15.0, trust_env=False) as c:
                r = await c.get("https://api.openai.com/v1/models")
            return {"ok": True, "msg": f"代理可用，HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "msg": f"{type(e).__name__}: {e}"}

    # ------------------------------------------------------------ 客户端 Key
    @app.get("/api/keys")
    async def api_keys(request: Request):
        require_admin(request)
        return {"keys": keys.list_keys()}

    @app.post("/api/keys")
    async def api_key_create(request: Request):
        require_admin(request)
        body = await request.json()
        return keys.create_key(str(body.get("name") or ""), str(body.get("models", "*")),
                               str(body.get("secret") or ""))

    @app.patch("/api/keys/{key_id}")
    async def api_key_update(key_id: int, request: Request):
        require_admin(request)
        return keys.update_key(key_id, await request.json())

    @app.delete("/api/keys/{key_id}")
    async def api_key_delete(key_id: int, request: Request):
        require_admin(request)
        return keys.delete_key(key_id)

    # ------------------------------------------------------------ 本机客户端
    @app.get("/api/clients")
    async def api_clients(request: Request):
        require_admin(request)
        cfg = settings.load()
        base = clients.gateway_base(cfg)
        return {
            "base": base,
            "clients": clients.detect(base),
            "models": proxycfg.model_names(cfg),
            "keys": [{"id": k["id"], "name": k["name"], "models": k["models"],
                      "model_list": k["model_list"], "enabled": k["enabled"]}
                     for k in keys.list_keys()],
        }

    @app.post("/api/clients/{cid}/apply")
    async def api_client_apply(cid: str, request: Request):
        require_admin(request)
        body = await request.json()
        cfg = settings.load()
        base = str(body.get("base_url") or clients.gateway_base(cfg))
        try:
            res = clients.apply(
                cid, base, str(body.get("model") or ""), int(body.get("key_id") or 0),
                roles=body.get("roles") or {}, catalog=body.get("catalog") or [])
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {**res, "base": clients.gateway_base(cfg),
                "clients": clients.detect(clients.gateway_base(cfg))}

    # ------------------------------------------------------------ 日志
    @app.get("/api/logs")
    async def api_logs(request: Request, limit: int = 60):
        require_admin(request)
        return {"logs": keys.list_logs(min(limit, 300)), "pool": pool.tail(40)}

    @app.delete("/api/logs")
    async def api_logs_clear(request: Request):
        require_admin(request)
        keys.clear_logs()
        return {"ok": True}

    # ------------------------------------------------------------ 系统
    @app.post("/api/system/open-folder")
    async def api_open_folder(request: Request):
        require_admin(request)
        d = str(paths.data_dir())
        if sys.platform.startswith("linux"):
            from core import runmode
            from core.linux_desktop import has_display
            opener = shutil.which("xdg-open") if runmode.is_desktop() and has_display() else None
            if opener:
                try:
                    subprocess.Popen([opener, d])
                except OSError:
                    return {"ok": True, "dir": d, "opened": False}
            return {"ok": True, "dir": d, "opened": bool(opener)}
        if os.name == "nt":
            subprocess.Popen(["explorer", d])
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", d])
        return {"ok": True, "dir": d}

    @app.post("/api/system/quit")
    async def api_quit(request: Request):
        require_admin(request)
        pool.stop()
        threading_exit()
        return {"ok": True}

    # ------------------------------------------------------------ 静态界面
    web = paths.web_dir()
    if (web / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=str(web)), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(str(web / "index.html"))
    else:
        @app.get("/")
        async def no_ui():
            return JSONResponse({"error": "缺少界面文件", "web": str(web)}, status_code=500)

    # ------------------------------------------------------------ 客户端数据面
    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def forward(path: str, request: Request):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        return await _forward(path, request)

    return app


def threading_exit() -> None:
    import threading

    # os._exit 不会走 finally，单实例锁文件得在这里先删掉，
    # 否则下次启动会看到一条指向已死进程的记录。
    try:
        lock = paths.data_dir() / ".instance"
        if lock.exists():
            import json

            if int(json.loads(lock.read_text(encoding="utf-8")).get("pid") or 0) == os.getpid():
                lock.unlink()
    except Exception:
        pass

    def _die():
        time.sleep(0.6)
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()


# ---------------------------------------------------------------- 转发实现

def _bearer(request: Request) -> str:
    """从请求里取密钥。

    除了 OpenAI 那套 `Authorization: Bearer`，也要认 Gemini 客户端的
    `x-goog-api-key` —— Gemini CLI 只会这么带 key，少一条就接不进来。
    """
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    for name in ("x-api-key", "x-goog-api-key"):
        v = (request.headers.get(name) or "").strip()
        if v:
            return v
    return ""


def _err(msg: str, status: int, code: str = "invalid_api_key") -> JSONResponse:
    return JSONResponse({"error": {"message": msg, "type": "invalid_request_error", "code": code}}, status_code=status)


def _relay_err(msg: str, status: int, anthropic: bool = False) -> JSONResponse:
    """第三方通道自己的错误。

    Anthropic 协议那边必须包成 {"type":"error","error":{...}}，否则 Claude
    客户端解析不出来，界面上只剩一句 unknown；其余客户端按 OpenAI 形状给。
    """
    if anthropic:
        return JSONResponse({"type": "error",
                             "error": {"type": "api_error", "message": msg}},
                            status_code=status)
    return JSONResponse({"error": {"message": msg, "type": "server_error", "code": "provider_error"}},
                        status_code=status)


def _shape_err(content: bytes, status: int) -> bytes:
    """把非 Anthropic 形状的错误体包成 Anthropic 形状。

    new-api 系中转（SudoCode 这种）回的是
    {"error":{"code":"","message":"Invalid token"},"type":"new_api_error"}，
    少了外层 "type":"error" 与 error.type，Claude 客户端只会显示 unknown。
    """
    try:
        obj = json.loads(content)
    except Exception:
        return content
    if not isinstance(obj, dict):
        return content
    if obj.get("type") == "error" and isinstance(obj.get("error"), dict):
        return content
    msg = ""
    e = obj.get("error")
    if isinstance(e, dict):
        msg = str(e.get("message") or e.get("code") or "")
    elif isinstance(e, str):
        msg = e
    if not msg:
        msg = str(obj.get("message") or obj.get("detail") or "")
    if not msg:
        return content
    return json.dumps({"type": "error", "error": {
        "type": "api_error" if status >= 500 else "invalid_request_error",
        "message": msg}}, ensure_ascii=False).encode()


def _is_gen(p: str) -> bool:
    return any(h in p for h in GEN_HINTS)


def _filter_models(payload: Dict[str, Any], allow: List[str]) -> Dict[str, Any]:
    """只留下号池定义的模型名。

    两种返回结构都要处理：
      标准接口   {"data":[{"id": "plus-gpt-6.1-sol"}, ...]}
      Codex 目录 {"models":[{"slug": "plus-gpt-6.1-sol", ...}, ...]}
    上游还会掺进图片模型、codex-auto-review 之类，客户端看到只会困惑，
    而且每个模型带着几十 KB 的说明，攒够 1MB 会让整份目录加载失败。
    """
    if not allow:
        return payload
    keep = set(allow)
    # Pro 档别名只在 Gemini 原生接口（/v1beta）可用，网关直连上游；
    # 列进 OpenAI 的 /v1/models 会让别的客户端点了就报错，这里过滤掉。
    native_only = set(gemini_direct.PRO_MODELS)
    if isinstance(payload.get("data"), list):
        payload["data"] = [m for m in payload["data"] if isinstance(m, dict) and m.get("id") in keep
                           and m.get("id") not in native_only]
    if isinstance(payload.get("models"), list):
        payload["models"] = [
            m for m in payload["models"]
            if isinstance(m, dict) and (m.get("slug") in keep or m.get("id") in keep or m.get("name") in keep)
            and (m.get("slug") or m.get("id") or m.get("name")) not in native_only
        ]
    return payload


def _allow_list(row: Dict[str, Any], cfg: Dict[str, Any]) -> List[str]:
    """某个 key 最终能看到的模型：号池对外名单 ∩ 该 key 的白名单。"""
    exposed = proxycfg.model_names(cfg)
    if not exposed:
        return []
    listed = row.get("model_list") or []
    if not listed or "*" in listed:
        return exposed
    return [m for m in exposed if m in set(listed)]


async def _pool_model_ids(cfg: Dict[str, Any]) -> set:
    """号池当前对外列出的模型名。"""
    try:
        r = await http().get(
            f"http://127.0.0.1:{int(cfg['pool_port'])}/v1/models",
            headers={"Authorization": f"Bearer {cfg['internal_key']}"}, timeout=8.0)
        data = r.json()
        return {m.get("id") for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")}
    except Exception:
        return set()


async def _pool_settle(cfg: Dict[str, Any], alias: str, want: bool, timeout: float = 15.0) -> bool:
    """等号池把新的模型表读进来。

    重启后端口虽然已经通了，但账号/别名表还要一两秒才读完，
    这段时间 /v1/models 返回的仍是旧列表 —— 立刻回前端，用户会以为「加了没生效」。
    """
    end = time.time() + timeout
    while time.time() < end:
        ids = await _pool_model_ids(cfg)
        # 列表为空说明号池还没起来或查询失败，这时「不在列表里」不能当结论
        if ids and ((alias in ids) == want):
            return True
        await asyncio.sleep(0.4)
    return False


def _is_codex(request: Request) -> bool:
    """Codex 拉目录时会带 originator: codex_cli_rs（实测 0.164）。"""
    o = (request.headers.get("originator") or "").lower()
    ua = (request.headers.get("user-agent") or "").lower()
    return o.startswith("codex") or "codex_cli_rs" in ua


def _model_labels(cfg: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for pool in (cfg.get("pools") or {}).values():
        for m in pool.get("models") or []:
            if m.get("alias"):
                out[m["alias"]] = m.get("display") or m["alias"]
    for p in providers.enabled(cfg):
        prefix = str(p.get("prefix") or "").strip("/")
        for m in p.get("models") or []:
            if not m.get("alias"):
                continue
            name = f"{prefix}/{m['alias']}" if prefix else m["alias"]
            out[name] = m.get("display") or name
    return out


def _pool_suggest(cfg: Dict[str, Any], key: str) -> List[str]:
    """「添加模型」时给上游模型名做候选，省得用户凭记忆敲错。"""
    names = set()
    pool = (cfg.get("pools") or {}).get(key) or {}
    for m in pool.get("models") or []:
        if isinstance(m, dict) and m.get("name"):
            names.add(str(m["name"]))
    if key in ("plus", "pro"):
        # 只管列已经跑通的那几个。excluded_models 里的名字（gpt-6-luna、gpt-5.6-*）
        # 号池压根不认，当候选列出来等于给人挖坑：选了必报 unknown provider。
        names |= {"gpt-6.1-sol", "gpt-6-sol", "gpt-6-astra"}
    if key == "gemini":
        names |= set(gemini_direct.PRO_MODELS.values())
        names |= {"gemini-3-flash", "gemini-3.8-flash-high",
                  "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"}
    return sorted(names)


def _gemini_alias(cfg: Dict[str, Any], model: str) -> Optional[str]:
    """Gemini 原生路径的模型改名：先看设置里配的别名，再回落到内置表。

    这样用户在「模型」页手工加的 Pro 档（alias → gemini-3.8-flash-tiered）
    也能走直连，不用改代码。
    """
    for m in (((cfg.get("pools") or {}).get("gemini") or {}).get("models") or []):
        if isinstance(m, dict) and str(m.get("alias") or "") == model:
            name = str(m.get("name") or "")
            return name if name and name != model else None
    return gemini_direct.alias_of(model)


def _chat_entries(row: Dict[str, Any], cfg: Dict[str, Any]) -> List[tuple]:
    """能给对话类客户端用的模型（Codex 的 Responses、Claude 的 Messages 都算）。

    Gemini 能用的只有 Flash 系：号池 8.0.40 的 Antigravity 注册表里
    只有 gemini-3-flash / -3.8-flash-high / -3.5-flash-lite / -3.1-flash-lite
    这几条（实测 Responses 和 Messages 接口带工具调用都正常）。Pro 档的真实模型
    gemini-3.8-flash-tiered 不在注册表里，只能由网关直连原生接口（/v1beta），
    塞给它们只会多出一条点了就报错；图片模型同理。
    """
    native_only = set(gemini_direct.PRO_MODELS)      # 只有 /v1beta 直连认得这些名字
    label = _model_labels(cfg)
    out: List[tuple] = []
    for name in _allow_list(row, cfg):
        if not name:
            continue
        if name.startswith("gpt-image") or name in ("codex-auto-review",):
            continue
        if name in native_only or name.endswith("-image"):
            continue
        disp = label.get(name, name)
        # 号池同步出来的显示名是「Gemini · 3.8 Flash High」，菜单里后缀 High
        # 只让人困惑（它本来就是这一档唯一的 3.8 Flash），去掉。
        if name.startswith("gemini-") and disp.endswith(" High"):
            disp = disp[: -len(" High")]
        out.append((name, disp))
    return out


# Codex 对模型目录有硬上限：实测给 23 条它只保留前 20 条（后面的悄悄丢掉），
# 再多就会整份丢弃、菜单空白。每条固定 23KB（21KB 系统提示词），所以按条数卡死。
CODEX_MAX_MODELS = 20


def _codex_entries(row: Dict[str, Any], cfg: Dict[str, Any]) -> List[tuple]:
    """Codex 目录要 (slug, 显示名, 说明) 三元组。

    顺序是「订阅池 → 第三方提供商」，超上限时先丢提供商，
    免得菜单里出现一堆点不动的名字。
    """
    items = _chat_entries(row, cfg)
    if len(items) > CODEX_MAX_MODELS:
        items = items[:CODEX_MAX_MODELS]
    return [(slug, disp, disp) for slug, disp in items]


# Claude Desktop 的模型校验（app.asar 里那两个函数，实测 3.6556）：
#
#   To      = ["sonnet","opus","haiku","fable","mythos"]
#   FCe     = ^(sonnet|opus|haiku|fable|mythos)(-[\d.]+)?$
#   ICe     = ["claude", ...To, "anthropic"]
#   LCe     = /ark-code|astron|command-r|deepseek|doubao|gemini|gemma|glm|gpt|grok|
#              hermes|hy3|kimi|lfm|ling|llama|longcat|mimo|minimax|mistral|mixtral|
#              moonshot|nemotron|openai|phi-|qianfan|qwen|tc-code|unic|yi-|stepfun|
#              step-3|seed-|bytedance|hunyuan|granite|amazon\.nova|nova-|devstral|
#              ministral|ernie|codex|arcee|trinity|abab|phi\d|k2\.|m2\.|jamba|
#              arctic|solar|mercury|zamba|kat-coder|ds-|dpsk/
#   Do(id)  = !LCe.test(id) && (FCe.test(id) || /claude|anthropic|档位词/.test(id))
#
# 关键在发现模型那段：`!Do(id) && !tier ? 丢掉 : 保留`。
# 也就是说 **只要带上 anthropic_family_tier，id 里有没有 gpt/gemini 都不管**。
# 所以这里不再套 claude-* 的壳，Claude 菜单里显示的就是 Codex 那份真实模型表。
ANTHROPIC_TIERS: Tuple[str, ...] = ("sonnet", "opus", "haiku", "fable", "mythos")

# 老的 claude-* 名字继续认（Claude Code 里有人写死过、后台小任务也会发），
# 但不再出现在菜单里；进来时照样翻译成真实模型（见 _anthropic_target）。
ANTHROPIC_ROUTES: Tuple[Tuple[str, str, str, str], ...] = (
    # 每条的目标必须是「号池真的认得」的别名，否则客户端选中就 404。
    # Codex 侧只有 gpt-6.1-sol / gpt-6-sol / gpt-6-astra 三个上游可用，
    # gpt-6.1-terra、gpt-6-luna、gpt-5.6-* 号池一律 unknown provider。
    # sonnet 档 —— Plus 号池（日常主力，Claude Code 默认走这一档）
    ("claude-sonnet-4-5", "Plus · GPT 6.1 Sol", "sonnet", "plus-gpt-6.1-sol"),
    ("claude-sonnet-4-5-20250929", "Plus · GPT 6 Astra", "sonnet", "plus-gpt-6-astra"),
    ("claude-3-7-sonnet-20250219", "Plus · GPT 6 Sol", "sonnet", "plus-gpt-6-sol"),
    # sonnet 档 —— Antigravity 账号原生服务真 Claude
    ("claude-sonnet-4-6", "Claude · Sonnet 4.6", "sonnet", "gemini-claude-sonnet-4-6"),
    # opus 档 —— Pro 号池（推理更重）
    ("claude-opus-4-5", "Pro · GPT 6.1 Sol", "opus", "pro-gpt-6.1-sol"),
    ("claude-opus-4-1", "Pro · GPT 6 Astra", "opus", "pro-gpt-6-astra"),
    ("claude-opus-4-20250514", "Pro · GPT 6 Sol", "opus", "pro-gpt-6-sol"),
    ("claude-opus-4-6", "Claude · Opus 4.6", "opus", "gemini-claude-opus-4-6"),
    # haiku 档 —— Gemini Flash 系（快、适合后台小任务）
    ("claude-haiku-4-5", "Gemini · 3 Flash", "haiku", "gemini-3-flash"),
    ("claude-3-5-haiku-20241022", "Gemini · 3.5 Flash Lite", "haiku", "gemini-3.5-flash-lite"),
    ("claude-3-haiku-20240307", "Gemini · 3.1 Flash Lite", "haiku", "gemini-3.1-flash-lite"),
    # fable 档 —— Gemini 3.8 高配
    ("claude-fable-5", "Gemini · 3.8 Flash", "fable", "gemini-3.8-flash-high"),
)


def _anthropic_tier_of(name: str) -> str:
    """给真实模型挑一个 Claude 认识的档位。

    档位只影响选择器里的分组和「按档回退」时选哪条，不影响真实调用；
    但每一行都必须带，否则带 gpt/gemini 的 id 会被客户端丢掉。
    """
    n = (name or "").lower()
    if "opus" in n or n.startswith("pro-") or "/pro-" in n:
        return "opus"
    if "high" in n:
        return "fable"
    if "lite" in n or "flash" in n or "haiku" in n:
        return "haiku"
    return "sonnet"


# Claude Desktop 除了发现阶段那道闸，选模型时还会再过一次 Do(id)：
# resolvedModels → ajn → Oo("gateway", id) → Do(id)，而 Do 的黑名单里
# gpt / gemini / deepseek / codex 这些词一个都不放过。所以 id 里带厂商名的
# 真模型名是绝对进不了 picker 的 —— 这跟配不配 anthropic_family_tier 无关。
#
# 能进去的 id 只有两种：纯档位名（sonnet-4.5），或者含 claude/anthropic/档位词
# 且不含黑名单词的名字。
#
# 光进去还不够 —— 「思考程度」菜单是另一套逻辑（Hon）：
#   kon[canonical(id)] ?? (/^(?:claude-)?(?:fable|mythos)(?:-|$)/.test(id) ? Oon : void 0)
# 只有内置表里那些 claude-* 名字、或 fable-/mythos- 开头的 id 才会拿到 effort_options；
# 拿不到就「no effort menu for this model」，界面上根本没有档位可选。
# max_effort_level 只决定「最高能调到哪」（缺省当 low，所以以前只能停在 Low）。
#
# 所以统一挂到 fable- 命名空间：
#   plus-gpt-6.1-sol     →  fable-plus-6.1-sol
#   gemini-3-flash       →  fable-3-flash
#   kakou/gpt-6.1-sol    →  fable-kakou-6.1-sol
#   Claude 真身（claude-sonnet-4-6 / claude-opus-4-6）原样保留，它自己有档位表。
# 菜单里显示的是真实名字，id 只是给客户端看的路由名，进来再翻回去。
CLAUDE_BLOCK = re.compile(
    r"ark-code|astron|command-r|deepseek|doubao|gemini|gemma|glm|gpt|grok|hermes|hy3|"
    r"kimi|lfm|\bling\b|llama|longcat|mimo|minimax|mistral|mixtral|moonshot|nemotron|"
    r"openai|phi-|qianfan|qwen|tc-code|\bunic\b|yi-|stepfun|step-3|seed-|bytedance|"
    r"hunyuan|granite|amazon\.nova|nova-|devstral|ministral|ernie|codex|arcee|trinity|"
    r"abab|phi\d|\bk2\.|\bm2\.|jamba|arctic|solar|mercury|zamba|kat-coder|\bds-|dpsk",
    re.I,
)
CLAUDE_TIER_WORDS = ("claude", "anthropic") + ANTHROPIC_TIERS

# 这些 id 在客户端内置表（kon）里有完整的思考档位，别再套壳。
CLAUDE_KNOWN_IDS = frozenset({
    "claude-haiku-4-5", "claude-sonnet-4-5", "claude-sonnet-4-6", "claude-sonnet-5",
    "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5",
    "claude-fable-5", "claude-mythos-5",
})


def _claude_clean(slug: str) -> str:
    """去掉黑名单词与多余连字符，剩下的部分当 id 主体。"""
    n = CLAUDE_BLOCK.sub("", slug)
    n = re.sub(r"[-/]{2,}", lambda m: m.group(0)[0], n)   # plus--6.1 → plus-6.1
    n = re.sub(r"(^|/)-+", r"\1", n)                     # /-v4.1 → /v4.1
    return re.sub(r"-+(?=/|$)", "", n)


def _claude_route(slug: str, tier: str) -> str:
    """真实模型名 → Claude 客户端能收下、且带思考档位的 id。

    先去黑名单词（gpt / gemini / deepseek 等）；已经落在客户端已知模型表里的
    claude-* 名字原样保留，其余的挂到 fable- 命名空间 —— 只有这个名字空间
    会让客户端生成 effort_options（低/中/高/Extra/Max）。
    """
    n = _claude_clean(slug)
    low = n.lower()
    if low in CLAUDE_KNOWN_IDS or low.startswith(("fable-", "mythos-")):
        return n
    return "fable-" + n.replace("/", "-")


def _anthropic_entries(row: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    """Claude 侧菜单：和 Codex 同一份模型表，显示的就是真名。

    只列 key 真正有权限的模型；顺序即默认（第一条是默认模型）。
    每个档位的第一条标 is_family_default，客户端按档位回退时就会选中它。
    """
    out: List[Dict[str, str]] = []
    seen_tier: set = set()
    seen_id: set = set()
    for slug, disp in _chat_entries(row, cfg):
        tier = _anthropic_tier_of(slug)
        rid = _claude_route(slug, tier)
        if rid in seen_id:
            continue
        seen_id.add(rid)
        item: Dict[str, str] = {"type": "model", "id": rid, "display_name": disp,
                                "created_at": "2026-01-01T00:00:00Z",
                                "anthropic_family_tier": tier,
                                # 不写这个字段，客户端会把「最高档」当 low（Ssn 的兜底），
                                # 思考程度就只能停在 Low、调不上去。Eo 的合法值是
                                # low/medium/high/xhigh/max，"max" 表示不设上限。
                                "max_effort_level": "max"}
        if tier not in seen_tier:
            item["is_family_default"] = True
            seen_tier.add(tier)
        out.append(item)
    return out


def _anthropic_target(model: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """把 Claude 那边的模型名翻译成号池里的真实模型。

    三种写法都要认：
      1. 真名（Claude Code 里手敲 `/model plus-gpt-6.1-sol`）—— 原样透传；
      2. 菜单里的路由名（`fable-plus-6.1-sol`）—— 翻回 plus-gpt-6.1-sol；
         旧版的 `sonnet/plus-6.1-sol` 也认，换命名空间前的会话还在用；
      3. 老的 claude-* 名字（历史配置、后台小任务会发）—— 按表翻或按档兜底。
    认不出返回空串，调用方就按原样发，让号池自己报错。
    """
    m = (model or "").strip()
    if not m:
        return ""
    names = list(proxycfg.model_names(cfg)) if cfg is not None else []
    # 1) 真名直通
    if m in set(names):
        return m
    # 2) 菜单路由名
    for slug in names:
        if m == _claude_route(slug, _anthropic_tier_of(slug)):
            return slug
    # 2b) 旧版「档位/名字」路由（1.2.0 的写法）
    ml = m.lower()
    for pre in ANTHROPIC_TIERS:
        if ml.startswith(pre + "/"):
            rest = ml[len(pre) + 1:]
            for slug in names:
                if _claude_clean(slug).lower() == rest:
                    return slug
            break
    # 3) 老 claude-* 名字
    for public, _disp, _tier, target in ANTHROPIC_ROUTES:
        if ml == public:
            return target
    if not ml.startswith("claude-"):
        return ""
    if "haiku" in ml:
        return "gemini-3-flash"
    if "opus" in ml:
        return "pro-gpt-6.1-sol"
    if "fable" in ml or "mythos" in ml:
        return "gemini-3.8-flash-high"
    return "plus-gpt-6.1-sol"


def _provider_of(model: str, cfg: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """这个模型是不是第三方通道的（`kakou/gpt-6.1-sol`、`sudo/claude-...`）。

    号池自带模型返回 None。用来区分「我们自己的密钥不对」和「中转方的 Key 被拒」，
    两者的 HTTP 状态码不能混着回给客户端。
    """
    m = (model or "").strip()
    if "/" not in m:
        return None
    low = m.lower()
    provs = providers.all_of(cfg)
    # 先按原样大小写精确匹配（Kakou 与 kakou 是两条不同通道，label 要指对人）
    for p in provs:
        pre = str(p.get("prefix") or "").strip()
        if pre and m.startswith(pre + "/"):
            return {"prefix": pre, "label": str(p.get("label") or pre)}
    for p in provs:
        pre = str(p.get("prefix") or "").strip()
        if pre and low.startswith(pre.lower() + "/"):
            return {"prefix": pre, "label": str(p.get("label") or pre)}
    return None


async def _gemini_direct(row: Dict[str, Any], model: str, real: str, action: str,
                         raw: bytes, full: str, started: float, note_of) -> Response:
    """Pro 档走直连（号池代理没有这个模型，见 gemini_direct 顶部说明）。"""
    try:
        payload = json.loads(raw or b"{}")
    except Exception:
        payload = {}
    stream = action == "streamGenerateContent"
    status, ctype, body = await gemini_direct.call(real, action, payload, stream)

    if stream and status == 200 and not isinstance(body, bytes):
        async def gen():
            try:
                async for chunk in gemini_direct.stream_bytes(body):
                    yield chunk
            finally:
                keys.log(row["id"], row["name"], model, full, status, int((time.time() - started) * 1000),
                         note_of(f"直连 {real}"))
        return StreamingResponse(gen(), status_code=status,
                                 media_type="text/event-stream; charset=utf-8")

    if isinstance(body, bytes):
        content = body
    else:
        content = json.dumps(body, ensure_ascii=False).encode()
    keys.log(row["id"], row["name"], model, full, status, int((time.time() - started) * 1000),
             note_of(f"直连 {real}"))
    return Response(content=content, status_code=status,
                    media_type=ctype or "application/json")


async def _public_model_chunks(chunks, internal: str, public: str):
    """流中内部模型名可能横跨网络分片；最多保留一段模型名长度的尾部。"""
    if internal == public:
        async for chunk in chunks:
            yield chunk
        return
    needle, replacement, pending = internal.encode(), public.encode(), b""
    async for chunk in chunks:
        pending += chunk
        while len(pending) >= len(needle):
            index = pending.find(needle)
            if index >= 0:
                yield pending[:index] + replacement
                pending = pending[index + len(needle):]
            else:
                end = len(pending) - len(needle) + 1
                yield pending[:end]
                pending = pending[end:]
                break
    if pending:
        yield pending.replace(needle, replacement)


async def _forward(path: str, request: Request):
    started = time.time()
    full = "/" + path
    cfg = settings.load()
    raw = await request.body()

    model = ""
    effort = ""
    if raw and "json" in (request.headers.get("content-type") or "").lower():
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                model = str(obj.get("model") or "")
                # 推理档位到底是客户端发来的值，还是上游默认？记下来才说得清。
                # 传了 `reasoning.effort` 就照抄；有的客户端走 `reasoning_effort` 顶层字段。
                rsn = obj.get("reasoning")
                oc = obj.get("output_config")
                th = obj.get("thinking")
                if isinstance(rsn, dict) and rsn.get("effort"):
                    effort = str(rsn.get("effort"))
                elif obj.get("reasoning_effort"):
                    effort = str(obj.get("reasoning_effort"))
                # Claude 侧调「思考程度」走的是 output_config.effort，
                # 新版也可能塞在 thinking.effort 里；两个都记，日志才对得上。
                elif isinstance(oc, dict) and oc.get("effort"):
                    effort = str(oc.get("effort"))
                elif isinstance(th, dict) and th.get("effort"):
                    effort = str(th.get("effort"))
        except Exception:
            pass

    if not model:
        # Gemini 客户端把模型名放在 URL 里（/v1beta/models/<model>:generateContent），
        # body 中没有 model 字段。不补这一步，按 key 限定模型就形同虚设，
        # 请求日志的「模型」列也会是空的。
        mm = re.search(r"/models/([^/:?]+)", request.url.path)
        if mm:
            model = mm.group(1)

    # Claude 客户端发来的是菜单里的路由名（sonnet/plus-6.1-sol）或老的 claude-*，
    # 转发前换成号池里的真实模型。日志仍记客户端原本要的那个名字。
    asked = model
    if path.startswith("v1/messages") and model:
        target = _anthropic_target(model, cfg)
        if target and target != model:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    obj["model"] = target
                    raw = json.dumps(obj, ensure_ascii=False).encode()
                    model = target
            except Exception:
                pass

    route_note = ""

    def note_of(base: str = "") -> str:
        """日志的「说明」列：原来只有失败原因，现在附带请求的推理档位。"""
        tag = f"effort={effort}" if effort else ""
        route = f"{asked} → {model}" if model and asked != model else ""
        return " · ".join(x for x in (base, route, route_note, tag) if x)

    secret = _bearer(request)
    if not secret:
        return _err("缺少 API key，请在 Authorization 里带 Bearer <key>", 401)
    row = keys.find_by_secret(secret)
    if row is None:
        keys.log(0, "", model, full, 401, int((time.time() - started) * 1000), note_of("未知密钥"))
        return _err("API key 无效", 401)
    if model.startswith(plus_priority.PREFIX):
        return _err("内部账号路由不可直接调用，请选择公开的 Plus 模型", 403, "private_route")
    # Gemini 客户端用的是自己那套模型名（gemini-3-pro-preview 之类），
    # 和号池里的真实名对不上，这里先改名；Pro 档还要绕开号池代理直连上游。
    alias = _gemini_alias(cfg, model) if path.startswith("v1beta/models/") else None
    action = ""
    if alias:
        mm = re.search(r":([A-Za-z]+)$", request.url.path)
        action = mm.group(1) if mm else ""

    if not (keys.allowed(row, model) or (alias and keys.allowed(row, alias))):
        reason = "该 key 已被停用" if not row["enabled"] else f"该 key 不允许使用模型 {model}"
        keys.log(row["id"], row["name"], model, full, 403, int((time.time() - started) * 1000), note_of(reason))
        return _err(reason, 403, "model_not_allowed")

    # Codex 的模型目录：给它一份「精简版」。
    # 它会把每个条目按内置默认补成 40~90KB（系统提示词 21KB、确认策略 23KB…），
    # 23 个模型的原始列表能把目录顶到 1MB 以上，Codex 会整份丢弃 —— 表现就是
    # 模型列表空白。这里每条只发 1.4KB，Codex 自己补默认值，加多少模型都不会爆。
    if request.method == "GET" and path.rstrip("/") == "v1/models" and _is_codex(request):
        entries = _codex_entries(row, cfg)
        keys.log(row["id"], row["name"], "", full, 200, int((time.time() - started) * 1000),
                 note_of(f"目录 {len(entries)} 个"))
        return JSONResponse(codexcat.catalog(entries))

    # Claude 客户端（Anthropic 协议）列模型用的是同一个 /v1/models，但返回体是
    # {"data":[{"type":"model","id":…,"display_name":…}]}。号池那边只会回它自带的
    # claude-* 名单，被白名单一过滤就是空的，所以这里自己拼。
    if (request.method == "GET" and path.rstrip("/") == "v1/models"
            and request.headers.get("anthropic-version")):
        items = _anthropic_entries(row, cfg)
        keys.log(row["id"], row["name"], "", full, 200, int((time.time() - started) * 1000),
                 note_of(f"Claude 目录 {len(items)} 个"))
        return JSONResponse({
            "data": items,
            "has_more": False,
            "first_id": items[0]["id"] if items else "",
            "last_id": items[-1]["id"] if items else "",
        })

    if alias and gemini_direct.is_direct_target(alias) and action in ("generateContent", "streamGenerateContent"):
        return await _gemini_direct(row, model, alias, action, raw, full, started, note_of)

    route_headers = {}
    wire_model = model
    selected_account = ""

    async def apply_plus_route(force: bool = False):
        nonlocal model, raw, wire_model, selected_account, route_note, route_headers
        if request.method != "POST":
            return None
        decision = await request.app.state.plus_fallback.decide(cfg, row, model, path, force)
        if not decision["reason"]:
            return None
        route_note = decision["reason"]
        selected_account = decision.get("account") or ""
        route_headers = {"x-aipool-route": decision["mode"],
                         "x-aipool-requested-model": quote(asked, safe="/"),
                         "x-aipool-actual-model": quote(decision["actual"], safe="/")}
        if decision["mode"] == "blocked":
            status = 403 if "未授权" in route_note else 503
            keys.log(row["id"], row["name"], model, full, status,
                     int((time.time() - started) * 1000), note_of())
            return _err(route_note, status, "fallback_blocked")
        if decision["mode"] in ("fallback", "account"):
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("请求不是对象")
                if payload.get("previous_response_id"):
                    reason = "切换备用通道需要完整对话历史，不能沿用 Plus 的 previous_response_id；请开启新会话或发送完整历史"
                    keys.log(row["id"], row["name"], model, full, 409,
                             int((time.time() - started) * 1000), note_of(reason))
                    return _err(reason, 409, "fallback_context_required")
                payload["model"] = decision.get("wire_model") or decision["actual"]
                raw = json.dumps(payload, ensure_ascii=False).encode()
                model = decision["actual"]
                wire_model = payload["model"]
            except (ValueError, TypeError):
                return _err("备用路由只接受 JSON 对话请求", 400, "invalid_request")
        return None

    blocked = await apply_plus_route()
    if blocked is not None:
        return blocked

    headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQ}
    headers["authorization"] = f"Bearer {cfg['internal_key']}"
    up_path = request.url.path
    if alias:
        up_path = up_path.replace("/models/" + model, "/models/" + alias, 1)
    url = f"http://127.0.0.1:{int(cfg['pool_port'])}{up_path}"
    if request.url.query:
        url += "?" + request.url.query

    try:
        req = http().build_request(request.method, url, headers=headers, content=raw)
        up = await http().send(req, stream=True)
        # 仅在生成内容尚未输出、上游明确返回 429 时强制刷新额度，并最多
        # 重试一次到新账号/显式备用通道。普通网络错误与 5xx 仍不切号。
        if (up.status_code == 429
                and model in {m["id"] for m in plus_fallback.sources(cfg)}):
            before_route = (model, wire_model, selected_account)
            blocked = await apply_plus_route(force=True)
            if blocked is not None:
                await up.aclose()
                return blocked
            after_route = (model, wire_model, selected_account)
            if after_route != before_route:
                await up.aclose()
                req = http().build_request(request.method, url, headers=headers, content=raw)
                up = await http().send(req, stream=True)
            else:
                # 429 也可能是瞬时频率限制；保留原错误，后台尽快再次检查。
                request.app.state.plus_fallback.at = 0.0
                request.app.state.plus_fallback.wake.set()
        if up.status_code == 429 and selected_account:
            request.app.state.plus_fallback.at = 0.0
            request.app.state.plus_fallback.wake.set()
    except Exception as e:
        keys.log(row["id"], row["name"], model, full, 502, int((time.time() - started) * 1000), note_of(str(e)))
        return _err(f"号池不可用：{e}", 502, "upstream_unreachable")

    # 号池会在响应头里回 X-Cpa-Trace-Id=<时间>-<auth_index>-<请求>-<trace>，
    # 第二段就是这次实际服务的账号；翻译成邮箱记进日志，才能按账号统计。
    trace = up.headers.get("x-cpa-trace-id") or up.headers.get("X-Cpa-Trace-Id") or ""
    account = selected_account
    if trace:
        try:
            account = await asyncio.to_thread(poolapi.account_of_trace, trace) or selected_account
        except Exception:
            account = selected_account

    passthrough = {k: v for k, v in up.headers.items() if k.lower() not in DROP_RESP}
    passthrough.update(route_headers)
    ctype = up.headers.get("content-type", "")
    streaming = "event-stream" in ctype or (_is_gen(full) and "application/json" not in ctype)

    if streaming:
        async def gen():
            try:
                async for chunk in _public_model_chunks(up.aiter_bytes(), wire_model, model):
                    yield chunk
            finally:
                await up.aclose()
                keys.log(row["id"], row["name"], model, full, up.status_code,
                         int((time.time() - started) * 1000), note_of(), account)
        return StreamingResponse(gen(), status_code=up.status_code, headers=passthrough,
                                 media_type=ctype or None)

    content = await up.aread()
    await up.aclose()
    if wire_model != model:
        content = content.replace(wire_model.encode(), model.encode())

    anthropic = path.startswith("v1/messages")

    # 第三方通道回的 401/403 说的是「它的 Key 被中转方拒了」，跟我们自己的密钥无关。
    # 原样透传会让 Claude 客户端以为自己的登录失效（弹重新登录、把模型标成不可用），
    # Codex 也会误判成鉴权问题。统一改成 502 + 人话，日志里仍记上游真实状态码。
    prov = _provider_of(model, cfg)
    if prov and up.status_code in (401, 403):
        reason = (f"通道「{prov['label']}」授权失败（上游 {up.status_code}）："
                  f"这把 Key 被中转方拒绝，请到「设置 → 第三方通道」更换")
        keys.log(row["id"], row["name"], model, full, 502,
                 int((time.time() - started) * 1000), note_of(reason), account)
        return _relay_err(reason, 502, anthropic=anthropic)

    # Claude 客户端要 Anthropic 形状的错误体，new-api 系中转给的是它自己那套，
    # 不包一层的话界面上只剩一句 unknown，看不出到底是谁拒的。
    if anthropic and up.status_code >= 400:
        content = _shape_err(content, up.status_code)

    if request.method == "GET" and full.rstrip("/").endswith("/v1/models") and up.status_code == 200:
        allow = _allow_list(row, cfg)
        if allow:
            try:
                payload = json.loads(content)
                if isinstance(payload, dict):
                    content = json.dumps(_filter_models(payload, allow), ensure_ascii=False).encode()
            except Exception:
                pass

    # Gemini CLI 用 /v1beta/models 列模型，把改过名的别名补进去，
    # 否则它只看得见号池里那几个名字，选 Pro 的时候找不到。
    if (request.method == "GET" and path.rstrip("/") == "v1beta/models"
            and up.status_code == 200 and alias is None):
        try:
            payload = json.loads(content)
            if isinstance(payload, dict) and isinstance(payload.get("models"), list):
                have = {m.get("name") for m in payload["models"] if isinstance(m, dict)}
                for m in gemini_direct.extra_models():
                    if m["name"] not in have:
                        payload["models"].append(m)
                        have.add(m["name"])
                # 用户在「模型」页手工加的模型名也补进来，否则 Gemini CLI 选不到
                for m in (((cfg.get("pools") or {}).get("gemini") or {}).get("models") or []):
                    if not isinstance(m, dict):
                        continue
                    aid = str(m.get("alias") or "")
                    full_name = "models/" + aid
                    if not aid or full_name in have:
                        continue
                    label = str(m.get("display") or aid)
                    payload["models"].append({
                        "name": full_name, "displayName": label, "description": label,
                        "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
                        "supportedInputModalities": ["text", "image"],
                        "supportedOutputModalities": ["text"],
                        "inputTokenLimit": 1048576, "outputTokenLimit": 65536,
                    })
                    have.add(full_name)
                content = json.dumps(payload, ensure_ascii=False).encode()
        except Exception:
            pass

    keys.log(row["id"], row["name"], model, full, up.status_code,
             int((time.time() - started) * 1000), note_of(), account)
    return Response(content=content, status_code=up.status_code, headers=passthrough,
                    media_type=ctype or None)


app = create_app()
