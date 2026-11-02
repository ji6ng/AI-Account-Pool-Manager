"""客户端 Key 与请求日志（SQLite）。

只保留两件事：谁能用、能用哪些模型。不做额度计量，表结构刻意保持简单。
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from . import paths

_lock = threading.RLock()
_db_path = None

# 「真正进了号池」的请求才算一次调用。下面三类只是路过网关，既不算成功也不算失败，
# 日志和统计都不显示：
#   key_id=0    —— Key 没匹配上，请求当场被拒，压根没转发；
#   status=403  —— 这把 Key 没勾选该模型，同样没转发；
#   /v1/models  —— 客户端拉模型目录，没有调用任何模型。
_REAL = ("(account<>'' OR (key_id>0 AND status<>403 AND path NOT LIKE '%/models'))")

SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    secret      TEXT NOT NULL UNIQUE,
    models      TEXT NOT NULL DEFAULT '*',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reqlog (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    key_id  INTEGER NOT NULL DEFAULT 0,
    name    TEXT NOT NULL DEFAULT '',
    model   TEXT NOT NULL DEFAULT '',
    path    TEXT NOT NULL DEFAULT '',
    status  INTEGER NOT NULL DEFAULT 0,
    ms      INTEGER NOT NULL DEFAULT 0,
    note    TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_reqlog_ts ON reqlog(ts);
"""

KEY_PREFIX = "qk-"


def path():
    global _db_path
    if _db_path is None:
        _db_path = paths.data_dir() / "pool.db"
    return _db_path


@contextmanager
def conn():
    c = sqlite3.connect(path(), timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        with c:
            yield c
    finally:
        c.close()


def init() -> None:
    with _lock, conn() as c:
        c.executescript(SCHEMA)
        # 老库没有 account 列（v1.1 之前），补上；SQLite 不支持 ADD COLUMN IF NOT EXISTS
        cols = {r[1] for r in c.execute("PRAGMA table_info(reqlog)").fetchall()}
        if "account" not in cols:
            c.execute("ALTER TABLE reqlog ADD COLUMN account TEXT NOT NULL DEFAULT ''")
        n = c.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
        if n == 0:
            c.execute(
                "INSERT INTO keys(name, secret, models, enabled, created_at) VALUES(?,?,?,?,?)",
                ("默认客户端", KEY_PREFIX + secrets.token_urlsafe(24), "*", 1,
                 time.strftime("%Y-%m-%d %H:%M:%S")),
            )


# ---------------------------------------------------------------- keys

def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": r["id"],
        "name": r["name"],
        "secret": r["secret"],
        "models": r["models"],
        "enabled": bool(r["enabled"]),
        "created_at": r["created_at"],
        "model_list": [m.strip() for m in (r["models"] or "").split(",") if m.strip()],
    }


def list_keys() -> List[Dict[str, Any]]:
    with conn() as c:
        return [_row(r) for r in c.execute("SELECT * FROM keys ORDER BY id")]


def find_by_secret(secret: str) -> Optional[Dict[str, Any]]:
    with conn() as c:
        r = c.execute("SELECT * FROM keys WHERE secret=?", (secret,)).fetchone()
        return _row(r) if r else None


def create_key(name: str, models: str = "*", secret: str = "") -> Dict[str, Any]:
    if not str(models or "").strip() or not any(m.strip() for m in str(models).split(",")):
        return {"ok": False, "error": "请至少选择一个模型，或明确允许全部模型"}
    secret = (secret or "").strip() or (KEY_PREFIX + secrets.token_urlsafe(24))
    if find_by_secret(secret):
        return {"ok": False, "error": "这个 key 已存在"}
    with conn() as c:
        cur = c.execute(
            "INSERT INTO keys(name, secret, models, enabled, created_at) VALUES(?,?,?,1,?)",
            (name or "未命名", secret, models or "*", time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        return {"ok": True, "id": cur.lastrowid, "secret": secret}


def update_key(key_id: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    if "models" in patch and (not str(patch["models"] or "").strip() or
                               not any(m.strip() for m in str(patch["models"]).split(","))):
        return {"ok": False, "error": "请至少选择一个模型，或明确允许全部模型"}
    rows = {k: v for k, v in patch.items() if k in ("name", "models", "secret")}
    if "secret" in rows:
        rows["secret"] = str(rows["secret"]).strip()
        other = find_by_secret(rows["secret"])
        if other and other["id"] != key_id:
            return {"ok": False, "error": "这个 key 已被其他条目占用"}
    if "enabled" in patch:
        rows["enabled"] = 1 if patch["enabled"] else 0
    if not rows:
        return {"ok": True}
    sets = ", ".join(f"{k}=?" for k in rows)
    with conn() as c:
        result = c.execute(f"UPDATE keys SET {sets} WHERE id=?", (*rows.values(), key_id))
        if not result.rowcount:
            return {"ok": False, "error": "客户端密钥不存在"}
    return {"ok": True}


def delete_key(key_id: int) -> Dict[str, Any]:
    with conn() as c:
        c.execute("DELETE FROM keys WHERE id=?", (key_id,))
    return {"ok": True}


def allowed(row: Dict[str, Any], model: str) -> bool:
    if not row.get("enabled"):
        return False
    models = row.get("model_list") or []
    if not models or "*" in models:
        return True
    if not model:
        return True
    return model in models


# ---------------------------------------------------------------- 日志

_log_n = 0


def log(key_id: int, name: str, model: str, p: str, status: int, ms: int,
        note: str = "", account: str = "") -> None:
    global _log_n
    try:
        with conn() as c:
            c.execute(
                "INSERT INTO reqlog(ts, key_id, name, model, path, status, ms, note, account) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (time.time(), key_id, name, model, p, status, ms, note, account),
            )
        _log_n += 1
        if _log_n % 500 == 0:      # 攒够 500 条才清一次，别每条都扫表
            trim(50000)
    except Exception:
        pass


def list_logs(limit: int = 80) -> List[Dict[str, Any]]:
    with conn() as c:
        rows = c.execute(f"SELECT * FROM reqlog WHERE {_REAL} ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    return [{
        "time": time.strftime("%H:%M:%S", time.localtime(r["ts"])),
        "name": r["name"], "model": r["model"], "path": r["path"],
        "status": r["status"], "ms": r["ms"], "note": r["note"],
        "account": r["account"] if "account" in r.keys() else "",
    } for r in rows]


def clear_logs() -> None:
    with conn() as c:
        c.execute("DELETE FROM reqlog")


def trim(keep: int = 500) -> None:
    try:
        with conn() as c:
            c.execute("DELETE FROM reqlog WHERE id NOT IN "
                      "(SELECT id FROM reqlog ORDER BY id DESC LIMIT ?)", (keep,))
    except Exception:
        pass


def today_count() -> int:
    start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    with conn() as c:
        r = c.execute(f"SELECT COUNT(*) FROM reqlog WHERE ts>=? AND {_REAL}",
                      (start,)).fetchone()
        return int(r[0])


# 进了号池却认不出账号身份的请求，SQL 里统一归到这个标记下。
# 概览的「各账号」不再单独列一行（认不出来的一律不展示），只在需要时用于分组。
_UN = "#unassigned"
_KIND_RANK = {"account": 0, "provider": 1}


def req_stats(days: int = 30,
              providers: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """按天统计请求：成功/失败数、成功率、以及连续 days 天的方块数据。

    只统计真正进了号池的请求（见 `_REAL`）：Key 没匹配上、模型没授权、
    以及只拉模型目录的请求都不算调用，既不显示也不算进成功率。

    providers 传供应商的 {prefix,label}，用来把 `kakou/xxx` 这类请求认领给供应商——
    它们走 api-keys 而不是 auth 文件，拿不到 auth_index，没法靠 trace 归到账号。

    「各账号」只列认得出归属的：既没有 account、也对不上任何供应商前缀的请求，
    归到 `_UN` 之后直接跳过——概览不再显示「未归属」这一行。
    注意这些请求仍然计入上面的成功/失败总数，所以各行相加可能小于总数。
    """
    days = max(1, min(int(days or 30), 90))
    today = date.today()
    first = today - timedelta(days=days - 1)
    start_dt = time.mktime(first.timetuple())

    # 供应商按模型前缀归位；前缀来自设置，用命名参数塞进去，避免拼串被注入
    prov_cases = ""
    params: Dict[str, Any] = {"start": start_dt, "un": _UN}
    kind_of: Dict[str, str] = {}
    for i, p in enumerate(providers or []):
        pfx = str(p.get("prefix") or "").strip()
        if not pfx:
            continue
        label = str(p.get("label") or pfx)
        params[f"p{i}"] = pfx + "/%"
        params[f"l{i}"] = label
        prov_cases += f" WHEN model LIKE :p{i} THEN :l{i}"
        kind_of[label] = "provider"

    buckets: Dict[str, Dict[str, int]] = {}
    per_acc: Dict[str, Dict[str, Dict[str, int]]] = {}
    ok = fail = 0
    try:
        with conn() as c:
            rows = c.execute(
                "SELECT date(ts,'unixepoch','localtime') AS d, "
                "SUM(CASE WHEN status BETWEEN 200 AND 399 THEN 1 ELSE 0 END) AS o, "
                "SUM(CASE WHEN status BETWEEN 200 AND 399 THEN 0 ELSE 1 END) AS f "
                f"FROM reqlog WHERE ts>=? AND {_REAL} GROUP BY d",
                (start_dt,),
            ).fetchall()
            # 同一批数据按「谁处理的」再切一次：概览要给每个号一条自己的方块
            rows2 = c.execute(
                "SELECT who, d, "
                "SUM(CASE WHEN status BETWEEN 200 AND 399 THEN 1 ELSE 0 END) AS o, "
                "SUM(CASE WHEN status BETWEEN 200 AND 399 THEN 0 ELSE 1 END) AS f "
                "FROM (SELECT CASE "
                "  WHEN account<>'' THEN account "
                + prov_cases +
                "  ELSE :un END AS who, "
                "  date(ts,'unixepoch','localtime') AS d, status "
                f"FROM reqlog WHERE ts>=:start AND {_REAL}) GROUP BY who, d",
                params,
            ).fetchall()
        for r in rows:
            buckets[str(r["d"])] = {"ok": int(r["o"] or 0), "fail": int(r["f"] or 0)}
            ok += int(r["o"] or 0)
            fail += int(r["f"] or 0)
        for r in rows2:
            who = str(r["who"] or _UN)
            per_acc.setdefault(who, {})[str(r["d"])] = {
                "ok": int(r["o"] or 0), "fail": int(r["f"] or 0)}
    except Exception:
        pass

    series: List[Dict[str, Any]] = []
    for i in range(days):
        d = first + timedelta(days=i)
        b = buckets.get(d.isoformat()) or {"ok": 0, "fail": 0}
        series.append({
            "date": d.isoformat(),
            "label": d.strftime("%m-%d"),
            "ok": b["ok"], "fail": b["fail"], "total": b["ok"] + b["fail"],
        })

    total = ok + fail
    t = buckets.get(today.isoformat()) or {"ok": 0, "fail": 0}

    accounts: List[Dict[str, Any]] = []
    for who, days_map in per_acc.items():
        if who == _UN:
            continue          # 认不出归属的请求不列行，也不进「各账号」
        a_ok = a_fail = 0
        cells = []
        for i in range(days):
            d = first + timedelta(days=i)
            b = days_map.get(d.isoformat()) or {"ok": 0, "fail": 0}
            cells.append([b["ok"], b["fail"]])
            a_ok += b["ok"]
            a_fail += b["fail"]
        accounts.append({"name": who, "kind": kind_of.get(who, "account"), "tip": "",
                         "ok": a_ok, "fail": a_fail,
                         "total": a_ok + a_fail, "series": cells})
    accounts.sort(key=lambda x: (_KIND_RANK.get(str(x.get("kind")), 9), -x["total"]))

    return {
        "days": series,
        "ok": ok, "fail": fail, "total": total,
        "rate": round(ok * 100.0 / total, 1) if total else 0.0,
        "today": {"ok": t["ok"], "fail": t["fail"], "total": t["ok"] + t["fail"]},
        "accounts": accounts,
        "window": days,
    }
