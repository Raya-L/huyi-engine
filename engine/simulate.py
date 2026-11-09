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
    run_deterministic_full, val, _adl_lost_n, trigger_states, policy_wait_year,
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


def _ins_matrix(h: Household, paths: np.ndarray, init, dp: dict) -> np.ndarray:
    """
    逐路径、逐保单算商保给付，与确定性模式（model._insurance_pay）同一口径：
    - 一次性给付：路径首次进入触发状态的那一年给付；若此时仍在剩余等待期内，
      推迟到等待期结束那一年，届时须仍存活（当年去世的按“死于等待期结束之后”的概率 1−frac 计）。
    - 按年给付：等待期结束后，按年初、年末是否处于触发状态的平均（半周期）计。
    触发状态按病情逐年更新（ltc_official 的社保等级随进展到中度、重度而提高）。
    """
    n, T = paths.shape
    out = np.zeros((n, T))
    if not h.policies:
        return out
    lost_n = _adl_lost_n(h)
    prev_all = np.concatenate([np.full((n, 1), IDX[init.state], dtype=np.int8), paths], axis=1)
    mu = {"L": dp["mu_L"], "M": dp["mu_M"], "S1": dp["mu_S"], "S2": dp["mu_S"]}
    for pol in h.policies:
        T_set = trigger_states(pol, init, lost_n)
        if not T_set:
            continue
        tidx = [IDX[s] for s in T_set]
        inT = np.isin(prev_all, tidx)                     # (n, T+1)，第 0 列为起始状态
        dead = prev_all == IDX["D"]
        tw, frac = policy_wait_year(pol)
        amt = float(pol.amount)
        if pol.payout_type.value == "lump":
            for t in range(1, T + 1):
                if t < tw:
                    continue
                if t == tw:
                    # 年初已在触发状态（含等待期内触发的）：年末存活全额；当年去世按 1−frac
                    at_start = inT[:, t - 1]
                    pay = np.where(at_start & ~dead[:, t], amt,
                                   np.where(at_start & dead[:, t], amt * (1.0 - frac), 0.0))
                    newly = (~inT[:, t - 1]) & inT[:, t]
                    out[:, t - 1] += pay + np.where(newly, amt, 0.0)
                else:
                    newly = (~inT[:, t - 1]) & inT[:, t]
                    out[:, t - 1] += np.where(newly, amt, 0.0)
        else:
            for t in range(1, T + 1):
                if t < tw:
                    continue
                if pol.term_years and t - tw + 1 > pol.term_years:
                    continue
                out[:, t - 1] += amt * 0.5 * (inT[:, t - 1].astype(float) + inT[:, t])
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

    alive = paths != IDX["D"]
    # 半周期口径（与确定性模式的 π̄ 一致）：当年费用 = 年初状态与年末状态各计半年
    prev = np.concatenate([np.full((paths.shape[0], 1), IDX[init.state], dtype=np.int8),
                           paths[:, :-1]], axis=1)

    def _unit(st):
        return ((st == IDX["L"]) * cL + (st == IDX["M"]) * cM
                + ((st == IDX["S1"]) | (st == IDX["S2"])) * cS)

    cost = 0.5 * (_unit(prev) + _unit(paths))
    ltc = 0.5 * ((prev == IDX["S2"]).astype(float) + (paths == IDX["S2"])) * bS
    ins = _ins_matrix(h, paths, init, dp)                # 逐路径、逐保单
    sb = alive * subs_raw
    # 商业保险超出当年缺口的部分结转下年（与 model._simulate 同一口径）
    pre_gap = np.maximum(0.0, cost - ltc - sb)
    gap = np.zeros_like(pre_gap, dtype=float)
    carry = np.zeros(pre_gap.shape[0])
    for t in range(pre_gap.shape[1]):
        avail = carry + ins[:, t]
        gap[:, t] = np.maximum(0.0, pre_gap[:, t] - avail)
        carry = np.maximum(0.0, avail - pre_gap[:, t])

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
