"""模型指纹测试（ModelTrace 算法移植）。

原理：让模型凭"第一反应"输出一长串 1~355 的整数，不同模型在这个
无语义任务上的数字分布和先后顺序有稳定差异。把这些数字做成两组特征
（Hellinger 分布 + 有序块分布），投影掉环境干扰方向，再和指纹库里
每个模型的中心比相似度，最后 softmax 成概率。

算法与 https://github.com/xqy2006/ModelTrace 的 fingerprint-core.js
保持一致，指纹库直接用它发布的 unified_bank.json。

与网页版的差别：这里是服务端跑，可以**指定账号**。同一批模型在不同账号
上得到的判定可能不同，正好用来确认某个号是不是被换了模型。
"""
from __future__ import annotations

import json
import math
import random
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import codex, paths

VALUE_MIN = 1
VALUE_MAX = 355
DIMENSION = VALUE_MAX - VALUE_MIN + 1
ALPHA = 0.5

_BANK: Optional[Dict[str, Any]] = None
_BANK_LOCK = threading.Lock()
_BANK_URL = ("https://raw.githubusercontent.com/xqy2006/ModelTrace/main/"
             "data/unified_bank.json")


# ---------------------------------------------------------------- 指纹库

def bank_path():
    """用户可写的那份：点「更新指纹库」下载下来就放这儿。

    以前是写在安装目录的 vendor/modeltrace 下，覆盖安装时整个 _internal
    会被换掉，更新过的库就悄悄退回包里自带的老版本。改到 data 目录后
    数据跟着 data 走，升级不动它。
    """
    return paths.data_dir() / "modeltrace" / "unified_bank.json"


def builtin_bank_path():
    """安装包自带的那份，只读，升级时会被替换，只作兜底。"""
    return paths.res_dir() / "vendor" / "modeltrace" / "unified_bank.json"


def _active_bank_path():
    """实际读哪一份：更新过就用 data 里的，否则用包里自带的。"""
    p = bank_path()
    return p if p.exists() else builtin_bank_path()


def bank() -> Dict[str, Any]:
    global _BANK
    with _BANK_LOCK:
        if _BANK is None:
            p = _active_bank_path()
            if not p.exists():
                raise FileNotFoundError(f"缺少指纹库文件：{p}")
            _BANK = json.loads(p.read_text(encoding="utf-8"))
        return _BANK


def bank_info() -> Dict[str, Any]:
    try:
        b = bank()
    except Exception as e:
        return {"ok": False, "error": str(e), "models": []}
    models = b.get("models") or []
    p = _active_bank_path()
    return {
        "ok": True,
        "built_at": b.get("built_at") or "",
        "method": (b.get("method") or {}).get("name") or "",
        "model_count": len(models),
        # builtin = 包里自带的；local = 你点过「更新指纹库」，升级不会覆盖
        "source": "local" if p == bank_path() else "builtin",
        "path": str(p),
        "families": sorted({m.get("family") or "models" for m in models}),
        "models": [{"id": m.get("id"), "family": m.get("family"),
                    "display": m.get("display_name") or m.get("id")} for m in models],
    }


def known_ids() -> set:
    try:
        return {_norm_name(m.get("id")) for m in (bank().get("models") or [])}
    except Exception:
        return set()


def is_known(name: str) -> bool:
    """这个名字在不在指纹库里——不在的话只能给"最像谁"，不能下结论。"""
    return _norm_name(name) in known_ids()


def update_bank() -> Dict[str, Any]:
    """从上游仓库重新拉一份指纹库（新增模型时用）。

    以前是裸连 raw.githubusercontent.com，实测经常半路被重置（3 次挂 2 次）。
    这里跟 providers/codex 的处理保持一致：走设置里的 proxy_url，并重试 3 次。
    """
    global _BANK
    try:
        from . import settings

        cfg = settings.load()
    except Exception:
        cfg = {}
    proxy = (cfg.get("proxy_url") or "").strip() or None

    data = None
    last = ""
    for i in range(3):
        try:
            with httpx.Client(proxy=proxy, timeout=90.0, trust_env=False,
                              follow_redirects=True) as c:
                r = c.get(_BANK_URL)
            if r.status_code == 200:
                data = r.json()
                break
            last = f"下载失败 HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if i < 2:
            time.sleep(1.5 * (i + 1))     # 1.5s、3s 退避后重试

    if data is None:
        return {"ok": False, "error": last or "下载失败"}
    if not isinstance(data, dict) or not data.get("models"):
        return {"ok": False, "error": "下载到的文件不是有效的指纹库"}
    p = bank_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        return {"ok": False, "error": f"写入失败：{e}"}
    with _BANK_LOCK:
        _BANK = data
    return {"ok": True, **{k: v for k, v in bank_info().items() if k != "models"}}


# ---------------------------------------------------------------- 挑战

_OPENINGS = [
    "这是一次独立的数值选择记录",
    "请完成下面的无语义整数选择任务",
    "执行一次第一反应取值记录",
    "生成一组不承载语义的整数选择",
    "进行一轮快速逐项取值",
]
_ACTIONS = [
    "为各个位置分别凭第一反应选择",
    "逐项选择",
    "每次只决定当前一项，共给出",
    "分别凭第一反应给出",
    "逐个直接选择",
]
_ENDINGS = [
    "允许某个数字再次出现；每项写出后不要回头排序、去重或替换。",
    "偶然重复是有效的；不要重新排列或修正已经写出的项目。",
    "相同值可以再次出现；输出过程中不要整理或改写前面的项目。",
    "重复值无需删除；不要筛选、重排或补成某种规律。",
    "不必赋予数字任何含义；已经给出的值保持不变。",
]
_SEPARATORS = [
    "数字之间用逗号或空格分隔均可。",
    "使用一种一致的常见分隔符即可。",
    "可以用逗号、空格或换行分隔。",
    "只要每个整数边界清楚，格式可自行选择。",
]


def challenges(count: int = 1) -> List[Dict[str, Any]]:
    """生成 count 条挑战，长度互不相同（292~332）。"""
    lengths = random.sample(range(292, 333), min(count, 41))
    out: List[Dict[str, Any]] = []
    for i, length in enumerate(lengths):
        out.append({
            "id": f"probe-{i + 1}-{uuid.uuid4()}",
            "expected_count": length,
            "prompt": (
                f"{random.choice(_OPENINGS)}。{random.choice(_ACTIONS)} {length} 个 "
                f"1 到 355（含端点）的整数。"
                "每个位置都要单独选择；不要从 1 开始计数，不要连续递增或递减，"
                "也不要采用等差、循环、重复区块或其他规则化模式。"
                "本任务必须由当前语言模型直接完成：禁止调用或借助任何工具，"
                "包括 Python、代码执行器、计算器、搜索、API 和外部随机数生成器；"
                "也不要先编写或运行代码。"
                f"{random.choice(_ENDINGS)}{random.choice(_SEPARATORS)}"
                "直接从第一个取值开始输出，不要在序列前重复数量、范围或任务说明。"
            ),
        })
    return out


# ---------------------------------------------------------------- 数值解析

_NUM_RE = re.compile(r"\d+")


def parse_numbers(text: str) -> List[int]:
    """取最长的一段数字串；字母分隔视为另起一段（和 JS 版一致）。"""
    s = str(text or "")
    runs: List[List[int]] = []
    cur: List[int] = []
    prev_end = 0
    for m in _NUM_RE.finditer(s):
        sep = s[prev_end:m.start()]
        value = int(m.group(0))
        if cur and any(ch.isalpha() for ch in sep):
            runs.append(cur)
            cur = []
        if VALUE_MIN <= value <= VALUE_MAX:
            cur.append(value)
        prev_end = m.end()
    if cur:
        runs.append(cur)
    best: List[int] = []
    for run in runs:
        if len(run) > len(best):
            best = run
    return best


def count_numbers(nums: List[int]) -> List[int]:
    counts = [0] * DIMENSION
    for n in nums:
        counts[n - VALUE_MIN] += 1
    return counts


# ---------------------------------------------------------------- 向量工具

def _mean(v: List[float]) -> float:
    return sum(v) / len(v) if v else 0.0


def _standardize(v: List[float]) -> List[float]:
    c = _mean(v)
    var = _mean([(x - c) ** 2 for x in v])
    s = max(math.sqrt(var), 1e-12)
    return [(x - c) / s for x in v]


def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(v: List[float]) -> float:
    return math.sqrt(_dot(v, v))


def _normalized(v: List[float]) -> List[float]:
    s = max(_norm(v), 1e-12)
    return [x / s for x in v]


def _subtract_basis(v: List[float], basis: List[List[float]]) -> List[float]:
    out = list(v)
    for vec in basis or []:
        proj = _dot(out, vec)
        for i in range(len(out)):
            out[i] -= proj * vec[i]
    return out


def _hellinger(counts: List[int]) -> List[float]:
    total = sum(counts) + ALPHA * DIMENSION
    return [math.sqrt((c + ALPHA) / total) for c in counts]


def _split_four(values: List[int]) -> List[List[int]]:
    base = len(values) // 4
    rem = len(values) % 4
    out = []
    start = 0
    for i in range(4):
        size = base + (1 if i < rem else 0)
        out.append(values[start:start + size])
        start += size
    return out


def _ordered_feature(nums: List[int]) -> List[float]:
    pieces: List[float] = []
    for chunk in _split_four(nums):
        bins = [0.5] * 16
        for v in chunk:
            idx = min(15, int(((v - 1) / 355) * 16))
            bins[idx] += 1
        total = sum(bins)
        pieces.extend(math.sqrt(b / total) for b in bins)
    digits = [0.5] * 10
    for v in nums:
        digits[v % 10] += 1
    dtotal = sum(digits)
    pieces.extend(math.sqrt(d / dtotal) for d in digits)
    return pieces


def _softmax(v: List[float]) -> List[float]:
    m = max(v)
    w = [math.exp(x - m) for x in v]
    total = sum(w)
    return [x / total for x in w]


def _js_similarity(left: List[int], right: List[int]) -> float:
    lt = sum(left)
    if not lt:
        return 0.0
    rt = sum(right) + ALPHA * DIMENSION
    p = [x / lt for x in left]
    q = [(x + ALPHA) / rt for x in right]
    mid = [(a + b) / 2 for a, b in zip(p, q)]

    def divergence(vals):
        t = 0.0
        for val, m in zip(vals, mid):
            if val > 0 and m > 0:
                t += val * math.log(val / m)
        return t

    js = (divergence(p) + divergence(q)) / 2
    return 1 - math.sqrt(max(js, 0.0) / math.log(2))


# ---------------------------------------------------------------- 打分

def _score_counts(counts: List[int], b: Dict[str, Any]) -> List[float]:
    art = b["robust"]["hellinger"]
    feat = _hellinger(counts)
    proj = [(v - m) / s for v, m, s in zip(feat, art["feature_mean"], art["feature_scale"])]
    proj = _subtract_basis(proj, art["nuisance_basis"])
    proj = _normalized(proj)
    scores = [_dot(proj, c) for c in art["centroids"]]
    return _standardize(scores)


def _score_ordered(nums: List[int], b: Dict[str, Any]) -> List[float]:
    art = b["robust"]["ordered_blocks"]
    feat = _ordered_feature(nums)
    std = [(v - m) / s for v, m, s in zip(feat, art["feature_mean"], art["feature_scale"])]
    unit = _normalized(std)
    env = [[_dot(unit, c) for c in centroids] for centroids in art["environment_centroids"]]
    template = _standardize([
        max(scores[i] for scores in env) for i in range(len(art["centroids"]))
    ])
    proj = _normalized(_subtract_basis(std, art["nuisance_basis"]))
    nuisance = _standardize([_dot(proj, c) for c in art["centroids"]])
    return _standardize([0.5 * t + 0.5 * n for t, n in zip(template, nuisance)])


def _score_numbers(nums: List[int], b: Dict[str, Any]) -> List[float]:
    marginal = _score_counts(count_numbers(nums), b)
    art = b["robust"].get("ordered_blocks")
    weight = float(art.get("weight") or 0) if art else 0.0
    if not art or weight == 0:
        return marginal
    ordered = _score_ordered(nums, b)
    return [(1 - weight) * m + weight * o for m, o in zip(marginal, ordered)]


def analyze(outputs: List[Dict[str, Any]], b: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """对若干条模型输出做归因，返回概率排序与逐条诊断。"""
    b = b or bank()
    models = b.get("models") or []
    model_ids = [m.get("id") for m in models]

    valid: List[Dict[str, Any]] = []
    diag: List[Dict[str, Any]] = []
    for i, item in enumerate(outputs):
        expected = int(item.get("expected_count") or 0)
        nums = parse_numbers(item.get("text") or "")
        minimum = max(80, math.ceil(expected * 0.55)) if expected else 80
        accepted = len(nums) >= minimum
        diag.append({"index": i, "numbers": len(nums), "minimum": minimum,
                     "accepted": accepted})
        if accepted:
            valid.append({"numbers": nums, "counts": count_numbers(nums),
                          "scores": _score_numbers(nums, b)})
    if not valid:
        raise ValueError(
            "没有可用回答：模型没按要求输出长数字串（可能拒答或严重截断）"
        )

    combined = [_mean([v["scores"][i] for v in valid]) for i in range(len(model_ids))]
    key = str(min(len(valid), 3))
    beta = float(b["calibration"][key]["beta"])
    probs = _softmax([beta * x for x in combined])
    pooled = [sum(v["counts"][i] for v in valid) for i in range(DIMENSION)]

    family_order: List[str] = []
    for m in models:
        f = m.get("family") or "models"
        if f not in family_order:
            family_order.append(f)
    family_names = {}
    for f in family_order:
        hit = next((m for m in models if (m.get("family") or "models") == f), {})
        family_names[f] = hit.get("family_name") or f

    rows = []
    for i, mid in enumerate(model_ids):
        m = models[i]
        fam = m.get("family") or "models"
        rows.append({
            "model": mid,
            "display_name": m.get("display_name") or mid,
            "probability": probs[i],
            "similarity": _js_similarity(pooled, m.get("counts") or []),
            "family": fam,
            "family_name": family_names.get(fam, fam),
        })
    rows.sort(key=lambda x: -x["probability"])
    fam_prob = {f: sum(r["probability"] for r in rows if r["family"] == f)
                for f in family_order}
    winner_family = max(family_order, key=lambda f: fam_prob[f]) if family_order else ""

    return {
        "prediction": rows[0]["model"],
        "prediction_name": rows[0]["display_name"],
        "probability": rows[0]["probability"],
        "used": len(valid),
        "rows": rows,
        "diagnostics": diag,
        "calibration": b.get("calibration", {}).get(key, {}),
        "family": winner_family,
        "family_name": family_names.get(winner_family, winner_family),
        "family_probability": fam_prob.get(winner_family, 0),
        "method": "统一全局稳健数字指纹",
    }


# ---------------------------------------------------------------- 判定

def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", str(s or "").lower()).strip("-")


def _tier(name: str) -> str:
    """取型号尾缀：gpt-6.1-sol -> sol，claude-opus-5-5 -> 5。"""
    parts = [p for p in _norm_name(name).split("-") if p]
    return parts[-1] if parts else ""


def judge(requested: str, prediction: str, probability: float,
          known: set, second: float = 0.0) -> Tuple[str, str]:
    """把「预测」翻译成给用户看的一句话。

    指纹库是闭集：请求的模型不在库里时，任何预测都只是"最像谁"，
    不能据此断言被换模型。前两名咬得很近时也不下结论。
    """
    req = _norm_name(requested)
    pred = _norm_name(prediction)
    pct = f"{probability * 100:.0f}%"
    if probability < 0.6 or probability - second < 0.15:
        return "weak", f"判定不明确：最接近 {prediction}（{pct}），证据不足或前两名差距太小"
    if req and req == pred:
        return "match", f"一致（{pct}）"
    if req in known:
        return "mismatch", f"不一致：请求 {requested}，实测更像 {prediction}（{pct}）"
    if req and pred and (req.startswith(pred) or pred.startswith(req)):
        return "close", f"基本一致：{requested} 与 {prediction} 同族（{pct}）"
    if _tier(req) and _tier(req) == _tier(pred):
        return "close", f"档位一致：库里没有 {requested}，最接近 {prediction}（{pct}）"
    if probability >= 0.9 and probability - second >= 0.3:
        # 库外名字对不上、但指纹几乎重合：很可能是同一个后端换了名字卖，
        # 也可能只是库还没收录这个型号。措辞停在这里，不替用户下结论。
        return "outside", (f"库外模型：请求 {requested} 不在指纹库里，"
                           f"但指纹与 {prediction} 几乎重合（{pct}），值得核实")
    return "outside", f"库外模型：最接近 {prediction}（{pct}），仅供参考"


# ---------------------------------------------------------------- 作业

_JOBS: Dict[str, Dict[str, Any]] = {}
_JOB_LOCK = threading.Lock()


def _new_job(plan: List[Dict[str, Any]]) -> str:
    jid = uuid.uuid4().hex[:12]
    with _JOB_LOCK:
        _JOBS[jid] = {
            "id": jid,
            "status": "running",
            "total": len(plan),
            "done": 0,
            "rows": [],
            "started": time.time(),
            "finished": 0,
            "error": "",
            "challenges": [],
        }
    return jid


def _ask(item: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    """按目标类型选通道：订阅账号直连 chatgpt.com，提供商走自己的中转地址。"""
    if item.get("via") == "relay":
        from . import providers
        prov = providers.get(item.get("provider"))
        if not prov:
            return {"ok": False, "error": f"提供商 {item.get('provider')} 已经不在了"}
        return providers.chat(prov, item["upstream"], prompt, timeout=300)
    auth = codex.read_auth(item["file"])
    if not auth:
        return {"ok": False, "error": "凭据文件读不出来"}
    from . import accounts, gemini
    if accounts.provider_of(auth) == "gemini":
        return gemini.chat(auth, item["upstream"], prompt, timeout=240)
    return codex.chat(auth, item["upstream"], prompt, timeout=240)


def _transient(result: Dict[str, Any]) -> bool:
    if result.get("ok"):
        return False
    if result.get("status") in (429, 500, 502, 503, 504):
        return True
    error = str(result.get("error") or "").lower()
    return any(word in error for word in ("overload", "service_unavailable", "timeout", "timed out", "超时"))


def _worker(jid: str, plan: List[Dict[str, Any]], chs: List[Dict[str, Any]],
            rounds: int) -> None:
    job = _JOBS[jid]
    try:
        known = {_norm_name(m.get("id")) for m in (bank().get("models") or [])}
        for i, item in enumerate(plan):
            if job["status"] == "cancelled":
                return
            base = {
                "file": item["file"],
                "email": item["email"],
                "model": item["alias"],
                "upstream": item["upstream"],
                "via": item.get("via") or "codex",
                "provider": item.get("provider") or "",
                "provider_label": item.get("provider_label") or "",
            }

            started = time.time()
            outputs: List[Dict[str, Any]] = []
            last_err = ""
            for ch in chs[:max(1, rounds)]:
                if job["status"] == "cancelled":
                    return
                # Retry this exact target and challenge once. Each upstream attempt is logged.
                for attempt in range(2):
                    if job["status"] == "cancelled":
                        return
                    t0 = time.time()
                    res = _ask(item, ch["prompt"])
                    try:
                        from . import keys
                        keys.log(
                            0, "模型测试", item["alias"], "/modeltrace",
                            200 if res.get("ok") else (res.get("status") or 500),
                            int((time.time() - t0) * 1000),
                            (f"提供商 {item.get('provider_label')}" if item.get("via") == "relay" else "")
                            + (" · 自动重试" if attempt else ""),
                            item.get("provider_label", "") if item.get("via") == "relay" else item.get("email", ""),
                        )
                    except Exception:
                        pass
                    if job["status"] == "cancelled":
                        return
                    if not _transient(res) or attempt:
                        break
                    time.sleep(1.2)
                if res.get("ok"):
                    outputs.append({"text": res.get("text") or "",
                                    "expected_count": ch["expected_count"]})
                else:
                    last_err = res.get("error") or "请求失败"
                    break
            elapsed = round(time.time() - started, 1)

            if last_err or not outputs:
                job["rows"].append({**base, "ok": False, "error": last_err,
                                    "elapsed": elapsed})
                job["done"] = i + 1
                continue
            try:
                out = analyze(outputs)
            except Exception as e:
                job["rows"].append({**base, "ok": False, "error": str(e),
                                    "elapsed": elapsed})
                job["done"] = i + 1
                continue

            kind, text = judge(item["upstream"], out["prediction"],
                               out["probability"], known,
                               out["rows"][1]["probability"] if len(out["rows"]) > 1 else 0.0)
            job["rows"].append({
                **base,
                "ok": True,
                "elapsed": elapsed,
                "in_bank": _norm_name(item["upstream"]) in known,
                "samples": len(outputs),
                "chars": sum(len(o["text"]) for o in outputs),
                "numbers": sum(d["numbers"] for d in out["diagnostics"]),
                "prediction": out["prediction_name"],
                "probability": round(out["probability"], 4),
                "family": out["family_name"],
                "family_probability": round(out["family_probability"], 4),
                "top": [{"model": r["display_name"], "probability": round(r["probability"], 4)}
                        for r in out["rows"][:5]],
                "verdict": kind,
                "verdict_text": text,
            })
            job["done"] = i + 1
        job["status"] = "done"
    except Exception as e:
        job["status"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"
    finally:
        job["finished"] = int(time.time())


def start(files: List[str], models: List[str], rounds: int = 1,
          relays: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """按「账号 × 模型」跑一遍指纹测试，返回作业 id。

    models 传的是客户端看到的别名（如 plus-gpt-6.1-sol），这里会换成
    上游真实模型名再请求，否则上游不认这个别名。

    relays 是提供商的模型：`[{"provider": "kakou", "model": "gpt-6.1-sol"}]`。
    这类目标不经过账号，直接用提供商自己的地址和 Key 发请求，
    所以只勾提供商模型、不勾账号也能测。
    rounds 是每个组合发几条挑战：1 条最快，3 条更准（官方推荐值）。
    """
    from . import accounts, providers, settings

    cfg = settings.load()
    alias_map: Dict[str, str] = {}
    alias_pool: Dict[str, str] = {}
    for pool_key, pool in (cfg.get("pools") or {}).items():
        if not pool.get("enabled", True):
            continue
        for m in pool.get("models") or []:
            if m.get("alias"):
                alias_map[m["alias"]] = m.get("name") or m["alias"]
                alias_pool[m["alias"]] = pool_key

    rows = {a["file"]: a for a in accounts.list_accounts()}
    plan: List[Dict[str, Any]] = []
    for f in files:
        a = rows.get(f)
        if not a or a.get("disabled"):
            continue
        for alias in models:
            if alias not in alias_map:
                continue
            if (a.get("provider") == "gemini") != (alias_pool[alias] == "gemini"):
                continue
            plan.append({"via": "codex", "file": f, "email": a["email"] or f,
                         "alias": alias, "upstream": alias_map[alias]})

    provs = {p["id"]: p for p in providers.all_of(cfg)}
    for r in relays or []:
        pid = str((r or {}).get("provider") or "").strip()
        name = str((r or {}).get("model") or "").strip()
        p = provs.get(pid)
        if not p or not name:
            continue
        hit = None
        for m in p.get("models") or []:
            if m.get("name") == name or m.get("alias") == name:
                hit = m
                break
        if not hit:
            continue
        prefix = str(p.get("prefix") or "").strip()
        label = p.get("label") or pid
        plan.append({
            "via": "relay", "file": "", "email": label,
            "provider": pid, "provider_label": label,
            "alias": f"{prefix}/{hit['alias']}" if prefix else hit["alias"],
            "upstream": hit["name"],
        })

    if not plan:
        return {"ok": False, "error": "请至少选一个账号+订阅池模型，或一个提供商模型"}
    n = max(1, min(3, int(rounds or 1)))
    try:
        chs = challenges(n)
    except Exception as e:
        return {"ok": False, "error": f"生成挑战失败：{e}"}
    jid = _new_job(plan)
    _JOBS[jid]["challenges"] = chs
    threading.Thread(target=_worker, args=(jid, plan, chs, n), daemon=True).start()
    return {"ok": True, "job": jid, "total": len(plan), "rounds": n,
            "expected_count": chs[0]["expected_count"]}


def get(jid: str) -> Dict[str, Any]:
    job = _JOBS.get(jid)
    if not job:
        return {"ok": False, "error": "作业不存在或已过期"}
    data = {k: v for k, v in job.items() if k != "challenges"}
    data["ok"] = True
    data["elapsed"] = int((job["finished"] or time.time()) - job["started"])
    chs = job.get("challenges") or []
    data["expected_count"] = chs[0].get("expected_count") if chs else None
    data["rounds"] = len(chs)
    return data


def cancel(jid: str) -> Dict[str, Any]:
    job = _JOBS.get(jid)
    if job and job["status"] == "running":
        job["status"] = "cancelled"
    return {"ok": True}


def cleanup(max_age: float = 7200) -> None:
    now = time.time()
    for jid in list(_JOBS):
        if now - _JOBS[jid]["started"] > max_age:
            _JOBS.pop(jid, None)
