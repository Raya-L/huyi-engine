"""
engine/simulate.py —— 随机模式与敏感性（§4 第 6 小节、§5 第 4 块）

run_stochastic：按转移矩阵抽数万条病程路径，算每条路径的现值，取 P10/P50/P90。
                A6 先行版：参数取基准值，只对"病程路径"抽样；
                B5 拿到参数区间（低/基准/高）后，在 _draw_params 里接三角/贝塔抽样即可。
run_sensitivity：8 个参数逐个在低/高值之间摆动，看 10 年缺口现值变化，取影响最大的 3 个。

硬规则：随机种子、模拟次数全部从配置读（p["seed"] / p["n_sims"]），代码里不写死。
"""
import math
import numpy as np

from engine.schema import Household, Params, Distribution
from engine.model import (
    STATES, IDX, load_disease, load_params, _transition_matrix,
    run_deterministic_full, val, _adl_lost_n,
)


def _rng(p: Params, seed: int = None) -> np.random.Generator:
    s = seed if seed is not None else int(val(p, "seed"))
    return np.random.default_rng(s)


def _draw_paths(init_state: str, P: np.ndarray, T: int, n: int,
                rng: np.random.Generator) -> np.ndarray:
    """向量化抽路径：返回 (n, T) 的状态下标矩阵。"""
    states = np.full(n, IDX[init_state], dtype=np.int8)
    cdf = np.cumsum(P, axis=1)
    out = np.empty((n, T), dtype=np.int8)
    for t in range(T):
        u = rng.random(n)
        nxt = (u[:, None] > cdf[states]).sum(axis=1)
        states = np.minimum(nxt, len(STATES) - 1).astype(np.int8)
        out[:, t] = states
    return out


def _pct(x: np.ndarray) -> dict:
    q = np.percentile(x, [10, 50, 90])
    return {"P10": float(q[0]), "P50": float(q[1]), "P90": float(q[2])}


def _ins_matrix(h: Household, paths: np.ndarray, init) -> np.ndarray:
    """
    逐路径、逐保单算商保给付（§4 商业保险触发）。
    一次性给付按"这条路径当年是否处于 S"判定，不做概率摊薄，避免高估。
    """
    n, T = paths.shape
    out = np.zeros((n, T))
    if not h.policies:
        return out
    is_S = (paths == IDX["S1"]) | (paths == IDX["S2"])
    lost_n = _adl_lost_n(h)
    for pol in h.policies:
        trig = pol.trigger.value
        if trig == "ci_dementia":
            if init.cause != "cognitive":
                continue
            hit = is_S
        elif trig == "ltc_adl":
            hit = is_S if lost_n >= (pol.adl_n or 0) else np.zeros_like(is_S)
        elif trig == "ltc_official":
            hit = is_S if init.grade >= (pol.min_level or 0) else np.zeros_like(is_S)
        else:
            continue

        wait = int(pol.wait_months or 0)
        start_t = math.ceil(wait / 12) if wait else 1        # 1-based
        for t in range(T):
            yr = t + 1
            if yr < start_t:
                continue
            if pol.payout_type.value == "lump":
                if yr == start_t:
                    out[:, t] += np.where(hit[:, t], float(pol.amount), 0.0)
            else:
                if pol.term_years and yr - start_t + 1 > pol.term_years:
                    continue
                out[:, t] += np.where(hit[:, t], float(pol.amount), 0.0)
    return out


def run_stochastic(h: Household, p: Params, T: int = 10, n: int = None,
                   seed: int = None) -> Distribution:
    """
    随机模式（§4 第 6 小节）。一次跑出 5 年与 10 年两个期限、5 个金额的 P10/P50/P90。
    返回：
      {"n": 次数, "seed": 种子, "horizons": {"5": {...}, "10": {...}},
       # 兼容旧结构：10 年口径平铺一份
       "cost_pv": {...}, "ltc_pv": {...}, "ins_pv": {...},
       "subsidy_pv": {...}, "gap_pv": {...}}
    """
    from engine.mapping import map_initial_state
    init = map_initial_state(h, p)

    n = int(n if n is not None else val(p, "n_sims"))
    rng = _rng(p, seed)

    # 基准逐年表：拿到每年的单位成本与长护险给付（均未被概率加权）
    rows, _, _, _ = run_deterministic_full(h, p, T)
    cL = np.array([r["C_L"] for r in rows])
    cM = np.array([r["C_M"] for r in rows])
    cS = np.array([r["C_S"] for r in rows])
    bS = np.array([r["B_S2"] for r in rows])
    df = np.array([r["df"] for r in rows])
    subs_raw = 12.0 * float(h.subsidy_monthly or 0.0)     # 未被存活概率加权的年补贴

    dp = load_disease(init.cause)
    paths = _draw_paths(init.state, _transition_matrix(dp), T, n, rng)

    is_L = paths == IDX["L"]
    is_M = paths == IDX["M"]
    is_S1 = paths == IDX["S1"]
    is_S2 = paths == IDX["S2"]
    is_S = is_S1 | is_S2
    alive = paths != IDX["D"]

    cost = is_L * cL + is_M * cM + is_S * cS
    ltc = is_S2 * bS
    ins = _ins_matrix(h, paths, init)                    # 逐路径、逐保单
    sb = alive * subs_raw
    gap = np.maximum(0.0, cost - ltc - ins - sb)

    cost_pv, ltc_pv, ins_pv, sub_pv, gap_pv = (cost * df, ltc * df, ins * df,
                                               sb * df, gap * df)
    horizons = {}
    for k in (5, 10):
        if T < k:
            continue
        horizons[str(k)] = {
            "cost": _pct(cost_pv[:, :k].sum(axis=1)),
            "ltc": _pct(ltc_pv[:, :k].sum(axis=1)),
            "ins": _pct(ins_pv[:, :k].sum(axis=1)),
            "subsidy": _pct(sub_pv[:, :k].sum(axis=1)),
            "gap": _pct(gap_pv[:, :k].sum(axis=1)),
        }

    flat = horizons.get("10") or horizons.get(str(T)) or {}
    return {
        "n": n, "seed": int(val(p, "seed")) if seed is None else int(seed),
        "horizons": horizons,
        "cost_pv": flat.get("cost", {}),
        "ltc_pv": flat.get("ltc", {}),
        "ins_pv": flat.get("ins", {}),
        "subsidy_pv": flat.get("subsidy", {}),
        "gap_pv": flat.get("gap", {}),
    }


# ----------------------------- 敏感性（§5 第 4 块） -----------------------------
# 参数名 -> _simulate 的 overrides 键。price_monthly 走成本表 low/high 两列。
_SENS_MAP = {
    "price_monthly": "cost_variant",
    "p_MS": "p_MS",
    "mu_S": "mu_S",
    "rho": "rho",
    "lambda": "lambda",
    "g": "g",
    "r": "r",
    "duration_cap": None,      # 支付标准与时长限额：待参数组给出可摆动口径
}


def run_sensitivity(h: Household, p: Params, T: int = 10, keys: list = None) -> dict:
    """
    8 个参数逐个摆动，记录 10 年缺口现值变化（§5 第 4 块）。
    返回：
      {"items": [按 |swing| 降序的前若干个], "base_pv10": 基准, "skipped": [缺区间的参数+原因]}
    低/高值只从 config/settings.yaml 的 sensitivity 段读取；缺了就跳过并如实回报，绝不猜。
    """
    cfg = val(p, "sensitivity") or {}
    keys = keys or [k for k in _SENS_MAP if k in cfg]

    _, _, base_pv10, _ = run_deterministic_full(h, p, T)
    items, skipped = [], []
    for k in keys:
        ov_key = _SENS_MAP.get(k)
        node = cfg.get(k) or {}

        if k == "price_monthly":
            res = {}
            for side in ("low", "high"):
                _, _, pv, _ = run_deterministic_full(h, p, T, {"cost_variant": side})
                res[side] = pv
        elif ov_key is None:
            skipped.append({"key": k, "reason": node.get("status") or "未定义摆动口径"})
            continue
        elif node.get("low") is None or node.get("high") is None:
            skipped.append({"key": k, "reason": node.get("status") or "低/高值待补"})
            continue
        else:
            res = {}
            for side in ("low", "high"):
                _, _, pv, _ = run_deterministic_full(h, p, T, {ov_key: float(node[side])})
                res[side] = pv

        items.append({
            "key": k,
            "pv10_low": res["low"], "pv10_high": res["high"],
            "swing": res["high"] - res["low"],
            "abs_swing": abs(res["high"] - res["low"]),
            "source": node.get("source"), "status": node.get("status"),
        })

    items.sort(key=lambda x: x["abs_swing"], reverse=True)
    return {"items": items, "top3": items[:3], "base_pv10": base_pv10, "skipped": skipped}
