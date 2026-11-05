"""定时向明确选中的 ChatGPT 账号发送一次简短对话，唤醒额度窗口。"""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import re
import secrets
import time
from typing import Any, Dict, List

from . import accounts, codex, settings

_MAX_TASKS = 20
_MAX_ACCOUNTS_PER_TASK = 20
_DEFAULT_PROMPT = "请只回复：已激活。"
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _eligible_accounts() -> List[Dict[str, Any]]:
    return [a for a in accounts.list_accounts()
            if a.get("pool") in ("plus", "pro")
            and a.get("provider", "codex") == "codex"
            and not a.get("disabled")]


def validate(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("任务数据格式无效")
    name = str(payload.get("name") or "").strip()
    prompt = str(payload.get("prompt") or _DEFAULT_PROMPT).strip()
    files = payload.get("accounts")
    repeat_daily = payload.get("repeat_daily")
    if not name or len(name) > 60:
        raise ValueError("任务名称不能为空，且不能超过 60 个字符")
    if not prompt or len(prompt) > 500:
        raise ValueError("激活问题不能为空，且不能超过 500 个字符")
    if not isinstance(files, list) or not files or len(files) > _MAX_ACCOUNTS_PER_TASK or any(not isinstance(f, str) for f in files):
        raise ValueError(f"请选择 1–{_MAX_ACCOUNTS_PER_TASK} 个账号")
    if len(set(files)) != len(files):
        raise ValueError("账号不能重复")
    if not isinstance(repeat_daily, bool):
        raise ValueError("每天重复必须是布尔值")
    available = {a["file"]: a for a in _eligible_accounts()}
    if any(f not in available for f in files):
        raise ValueError("只能选择已启用的 ChatGPT Plus / Pro 账号，请刷新后重试")
    if repeat_daily:
        at = str(payload.get("time") or "")
        if not _TIME_RE.fullmatch(at):
            raise ValueError("请选择有效的每日执行时间")
        return {"name": name, "prompt": prompt, "accounts": files,
                "repeat_daily": True, "time": at, "run_at": 0}
    raw = str(payload.get("run_at") or "").strip()
    try:
        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        timestamp = when.timestamp()
    except (ValueError, OverflowError, OSError):
        raise ValueError("请选择有效的一次性执行时间") from None
    if timestamp < time.time() - 60:
        raise ValueError("一次性任务时间不能早于当前时间")
    return {"name": name, "prompt": prompt, "accounts": files,
            "repeat_daily": False, "time": "", "run_at": int(timestamp)}


def _task_list() -> List[Dict[str, Any]]:
    raw = settings.load().get("activation_tasks") or []
    return [copy.deepcopy(t) for t in raw if isinstance(t, dict)]


def _save_tasks(tasks: List[Dict[str, Any]]) -> None:
    settings.save({"activation_tasks": tasks})


def due(task: Dict[str, Any], now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if not task.get("enabled") or task.get("status") == "running":
        return False
    if task.get("repeat_daily"):
        local = dt.datetime.fromtimestamp(now)
        today = local.date().isoformat()
        if task.get("last_run_date") == today:
            return False
        try:
            hh, mm = map(int, str(task.get("time") or "").split(":"))
            scheduled = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        except (TypeError, ValueError):
            return False
        return local >= scheduled
    return bool(task.get("run_at") and now >= float(task["run_at"]))


def _account_model(auth: Dict[str, Any], pool: str) -> str:
    aliases = {str(a.get("alias") or "") for a in auth.get("model_aliases") or [] if isinstance(a, dict)}
    cfg = settings.load()
    models = ((cfg.get("pools") or {}).get(pool) or {}).get("models") or []
    for model in models:
        alias = str(model.get("alias") or "")
        name = str(model.get("name") or alias)
        if alias in aliases and name:
            return name
    raise ValueError("这个账号没有已同步的可用模型；请先在账号页归池并重启号池")


def _run_account(file_name: str, prompt: str) -> Dict[str, Any]:
    auth = codex.read_auth(file_name)
    if not auth:
        return {"file": file_name, "ok": False, "error": "账号凭据无法读取"}
    email = str(auth.get("email") or file_name)
    if auth.get("disabled"):
        return {"file": file_name, "email": email, "ok": False, "error": "账号已停用"}
    pool = accounts.pool_of(auth)
    if pool not in ("plus", "pro") or accounts.provider_of(auth) != "codex":
        return {"file": file_name, "email": email, "ok": False, "error": "仅支持 ChatGPT Plus / Pro 账号"}
    try:
        model = _account_model(auth, pool)
    except ValueError as e:
        return {"file": file_name, "email": email, "ok": False, "error": str(e)}
    result = codex.chat(auth, model, prompt, timeout=100.0,
                        instructions="请简短、直接地回答，不要调用工具。")
    return {"file": file_name, "email": email, "ok": bool(result.get("ok")),
            "error": str(result.get("error") or "")[:240],
            "status": result.get("status")}


class Scheduler:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._account_slots = asyncio.Semaphore(3)
        self._running: Dict[str, asyncio.Task] = {}

    def snapshot(self) -> Dict[str, Any]:
        return {"tasks": _task_list(), "accounts": [
            {"file": a["file"], "email": a.get("email") or a["file"], "pool": a["pool"]}
            for a in _eligible_accounts()]}

    def create(self, payload: Any) -> Dict[str, Any]:
        task_data = validate(payload)
        tasks = _task_list()
        if len(tasks) >= _MAX_TASKS:
            raise ValueError(f"最多可创建 {_MAX_TASKS} 个定时任务")
        now = int(time.time())
        task = {"id": secrets.token_urlsafe(9), **task_data,
                "enabled": True, "created_at": now, "last_run_at": 0,
                "last_run_date": "", "status": "waiting", "last_results": []}
        tasks.append(task)
        _save_tasks(tasks)
        return copy.deepcopy(task)

    def set_enabled(self, task_id: str, enabled: bool) -> Dict[str, Any]:
        tasks = _task_list()
        task = next((t for t in tasks if t.get("id") == task_id), None)
        if task is None:
            raise KeyError(task_id)
        if task_id in self._running and not self._running[task_id].done():
            raise ValueError("任务执行中，暂时不能修改")
        task["enabled"] = enabled
        if enabled:
            task["status"] = "waiting"
        _save_tasks(tasks)
        return {"ok": True, "task": copy.deepcopy(task)}

    def delete(self, task_id: str) -> Dict[str, Any]:
        if task_id in self._running and not self._running[task_id].done():
            raise ValueError("任务执行中，暂时不能删除")
        tasks = _task_list()
        new = [t for t in tasks if t.get("id") != task_id]
        if len(new) == len(tasks):
            raise KeyError(task_id)
        _save_tasks(new)
        return {"ok": True}

    def run_now(self, task_id: str) -> Dict[str, Any]:
        tasks = _task_list()
        task = next((t for t in tasks if t.get("id") == task_id), None)
        if task is None:
            raise KeyError(task_id)
        existing = self._running.get(task_id)
        if existing and not existing.done():
            raise RuntimeError("这个任务正在执行")
        task["status"] = "running"
        task["last_run_at"] = int(time.time())
        if task.get("repeat_daily"):
            local = dt.datetime.fromtimestamp(time.time())
            try:
                hh, mm = map(int, str(task.get("time") or "").split(":"))
                scheduled = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if local >= scheduled:
                    task["last_run_date"] = local.date().isoformat()
            except (TypeError, ValueError):
                pass
        else:
            task["enabled"] = False
        _save_tasks(tasks)
        self._launch(task, scheduled=False)
        return {"ok": True, "status": "running"}

    def _claim_due(self, task: Dict[str, Any], now: float) -> bool:
        if not due(task, now):
            return False
        if task.get("repeat_daily"):
            task["last_run_date"] = dt.datetime.fromtimestamp(now).date().isoformat()
        else:
            task["enabled"] = False
        task["last_run_at"] = int(now)
        task["status"] = "running"
        return True

    def _launch(self, task: Dict[str, Any], scheduled: bool) -> None:
        task_id = task["id"]
        self._running[task_id] = asyncio.create_task(self._execute(copy.deepcopy(task)))

    def recover_interrupted(self) -> int:
        """Reset persisted in-flight tasks after a process/PC shutdown."""
        tasks = _task_list()
        recovered = 0
        for task in tasks:
            if task.get("status") != "running":
                continue
            task["status"] = "failed"
            task["last_results"] = [{"ok": False, "error": "管理器关闭前任务未完成；可手动重试"}]
            if task.get("repeat_daily"):
                task["last_run_date"] = ""
            recovered += 1
        if recovered:
            _save_tasks(tasks)
        return recovered

    async def _execute(self, task: Dict[str, Any]) -> None:
        async def run_limited(file_name: str):
            async with self._account_slots:
                return await asyncio.to_thread(_run_account, file_name, task["prompt"])

        outcomes = await asyncio.gather(*(run_limited(file_name) for file_name in task["accounts"]),
                                        return_exceptions=True)
        results = []
        for file_name, outcome in zip(task["accounts"], outcomes):
            if isinstance(outcome, BaseException):
                results.append({"file": file_name, "ok": False, "error": f"{type(outcome).__name__}: {outcome}"[:240]})
            else:
                results.append(outcome)
        done = int(time.time())
        tasks = _task_list()
        current = next((t for t in tasks if t.get("id") == task["id"]), None)
        if current is None:
            return
        current["last_run_at"] = done
        current["last_results"] = results
        current["status"] = "success" if all(r["ok"] for r in results) else "failed"
        if not current.get("repeat_daily"):
            current["enabled"] = False
        _save_tasks(tasks)

    async def tick(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        launched = []
        async with self._lock:
            tasks = _task_list()
            changed = False
            for task in tasks:
                running = self._running.get(str(task.get("id") or ""))
                if running and not running.done():
                    continue
                if self._claim_due(task, now):
                    launched.append(copy.deepcopy(task))
                    changed = True
            if changed:
                _save_tasks(tasks)
            for task in launched:
                self._launch(task, scheduled=True)
        return len(launched)

    async def run(self) -> None:
        self.recover_interrupted()
        while True:
            try:
                await self.tick()
            except Exception:
                pass
            await asyncio.sleep(5)

