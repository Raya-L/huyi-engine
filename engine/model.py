"""
engine/model.py —— §4 推移/成本/支付/缺口  +  load_params（A6 第 2/7 件）

约定（规格书§7）：
- 金额用元浮点；比例用小数；时间带单位后缀。
- 代码里不准出现写死数字，所有参数从配置读取。
- load_params：先读 national.yaml，递归合并 cities/<city>.yaml（标量参数原子替换，
  结构化字典逐层合并），再合并 settings.yaml。返回嵌套字典；用 val() 取值。
"""
import os
import csv
import copy
import math
import numpy as np

from engine.schema import Household, Params, YearTable, InitialState

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")

# 状态（§4.1）：轻度 / 中度 / 重度首年 / 重度持续 / 死亡
STATES = ("L", "M", "S1", "S2", "D")
IDX = {s: i for i, s in enumerate(STATES)}


# ----------------------------- 配置加载 -----------------------------
def _load_yaml(path: str):
    import yaml
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base, override):
    """递归合并。标量参数节点（含 'value' 键）整体替换；结构化字典逐层合并；列表直接替换。"""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return copy.deepcopy(override)
    if "value" in override:          # override 是标量参数节点 -> 整体替换
        return copy.deepcopy(override)
    if "value" in base and "value" not in override:  # base 是占位标量、override 是结构化 -> 替换
        return copy.deepcopy(override)
    result = dict(base)
    for k, v in override.items():
        result[k] = _deep_merge(base.get(k), v)
    return result


def val(p: dict, *path):
    """从嵌套配置取标量值；遇到 {value:...} 自动解包。"""
    cur = p
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    if isinstance(cur, dict) and "value" in cur:
        return cur["value"]
    return cur


def start_ym(p: dict, ov: dict = None):
    """测算起点（年, 月）。settings.yaml 的 start_ym 为 "auto" 时取当天；也可写 "2026-01"。
    overrides 里的 start_ym 优先（测试与复算用）。"""
    v = (ov or {}).get("start_ym") or val(p, "start_ym") or "auto"
    if isinstance(v, (tuple, list)):
        return int(v[0]), int(v[1])
    if str(v) == "auto":
        import datetime
        d = datetime.date.today()
        return d.year, d.month
    y, m = str(v).split("-")[:2]
    return int(y), int(m)


def rule_year(y0: int, m0: int, t: int) -> int:
    """第 t 个测算年（从 y0 年 m0 月起算的第 t 个 12 个月）的年中所在日历年。"""
    return y0 + t - 1 + (m0 - 1 + 6) // 12


def period_label(y0: int, m0: int, t: int) -> str:
    """第 t 个测算年的起止月，如“2026.11—2027.10”。"""
    ys, ms = y0 + t - 1, m0
    me = ms - 1 if ms > 1 else 12
    ye = ys + 1 if ms > 1 else ys
    return f"{ys}.{ms:02d}—{ye}.{me:02d}"


def load_params(city: str = "national", version: str = None) -> Params:
    """
    合并国家基准 + 城市配置 + 测算设置后的全部参数（§6）。
    城市文件只写与 national 不同的项。返回嵌套字典（标量 param 节点形如 {value, source}）。
    """
    merged = _deep_merge(_load_yaml(os.path.join(CONFIG_DIR, "national.yaml")),
                         _load_yaml(os.path.join(CONFIG_DIR, "cities", f"{city}.yaml"))
                         if city not in (None, "national") else {})
    # 合并 settings.yaml（g / r / n_sims / seed），作为顶层标量
    s = _load_yaml(os.path.join(CONFIG_DIR, "settings.yaml"))
    for k in s:
        merged[k] = s[k]
    return merged


def load_disease(cause: str = "cognitive") -> dict:
    """取某主因的病程参数（p_LM/p_MS/mu_L/mu_M/mu_S）。"""
    d = _load_yaml(os.path.join(CONFIG_DIR, "disease.yaml"))
    node = d.get(cause, d.get("cognitive", {}))
    # disease.yaml 里每项也是 {value, source} 结构，统一解包成纯数值
    out = {}
    for k, v in node.items():
        if isinstance(v, dict) and "value" in v:
            out[k] = float(v["value"])
        elif isinstance(v, (int, float)):
            out[k] = float(v)
    return out


def load_cost(city: str) -> dict:
    """读 config/cost/<city>.csv，索引为 (care_mode, dementia_unit_cn, severity_cn)。"""
    path = os.path.join(CONFIG_DIR, "cost", f"{city}.csv")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            out[(row["care_mode"], row["dementia_unit"], row["severity"])] = row
    return out


# ----------------------------- 结算方式 / 支付比例 -----------------------------
def settlement_for(p: Params, insured_type: str, year: int) -> str:
    """
    按日期从 settlement_periods 取出当年的结算方式：
    fixed_amount / cap_then_rho / rho_on_eligible_cost / none。
    重庆 2026-12-31 及以前 -> fixed_amount；2027-01-01 起 -> rho_on_eligible_cost。
    标注“情景设定”的对照情景段不参与基准测算（北京 2029 起按国家基准那段）。
    """
    periods = val(p, "settlement_periods") or []
    for period in periods:
        if period.get("status") == "情景设定":
            continue
        applies = period.get("applies_to") or []
        if insured_type not in applies:
            continue
        frm, until = period.get("from"), period.get("until")
        if frm and year < int(str(frm)[:4]):
            continue
        if until and year > int(str(until)[:4]):
            continue
        return period.get("mode")
    return "none"


def rho_for(p: Params, insured_type: str, year: int) -> float:
    """当年适用的长护险支付比例（含过渡期切换与青岛居民 2027 年前分档）。无待遇城市返回 0。"""
    key = "rho_employee" if insured_type == "employee" else "rho_resident"
    base = val(p, key) or 0.0

    # 生效日之前的分档（青岛居民 2027 年前的 tier1/tier2，不知档位取 model_default）
    eff = val(p, key, "effective_from")
    if eff and year < int(str(eff)[:4]):
        early = val(p, key, "before_2027", "model_default")
        if early is not None:
            return float(early)

    # 过渡期结束后 -> 情景 B（国家基准），见 §6 过渡期切换
    tey = val(p, "transition_end_year")
    if tey and year > int(tey):
        sb = val(p, "post_transition", "scenario_B") or {}
        if isinstance(sb, dict) and key in sb:
            return float(sb[key])
    return float(base)


def R_d_for(p: Params, insured_type: str, level: int, dementia: bool) -> float:
    """床日支付标准 R_d(ℓ)。青岛按 dementia_* / 职工居民分档列表；重庆按 severe 结构。"""
    Rd = val(p, "R_d")
    if not isinstance(Rd, dict):
        return 0.0
    key = "employee" if insured_type == "employee" else "resident"
    dk = f"dementia_{key}"
    if dementia and dk in Rd:
        return float(Rd[dk])
    if "severe" in Rd and isinstance(Rd["severe"], dict):     # 重庆结构
        s = Rd["severe"]
        if dementia and "institution_dementia" in s:
            return float(s["institution_dementia"])
        return float(s.get("institution_centralized", s.get("institution_visit", 0.0)))
    if key in Rd and isinstance(Rd[key], list):               # 青岛结构
        i = max(0, min(len(Rd[key]) - 1, (level or 5) - 3))
        return float(Rd[key][i])
    return 0.0


def covers_dementia(p: Params, insured_type: str) -> bool:
    """本市是否对重度失智单独认定（青岛 true，其余 false）。"""
    v = val(p, "covers_dementia")
    if isinstance(v, dict):
        return bool(v.get(insured_type, False))
    return bool(v)


# ----------------------------- 状态推移矩阵 -----------------------------
def _transition_matrix(dp: dict) -> np.ndarray:
    """5 状态 [L, M, S1, S2, D] 转移矩阵（spec §4.1）。"""
    pLM, pMS = dp["p_LM"], dp["p_MS"]
    muL, muM, muS = dp["mu_L"], dp["mu_M"], dp["mu_S"]
    L = [1 - pLM - muL, pLM, 0.0, 0.0, muL]
    M = [0.0, 1 - pMS - muM, pMS, 0.0, muM]
    S1 = [0.0, 0.0, 0.0, 1 - muS, muS]
    S2 = [0.0, 0.0, 0.0, 1 - muS, muS]
    D = [0.0, 0.0, 0.0, 0.0, 1.0]
    return np.array([L, M, S1, S2, D])


# ----------------------------- 成本查表 -----------------------------
def _cost_lookup(cost: dict, care_mode: str, dementia_unit: bool, severity: str,
                 fallback_mode: str = None):
    """
    severity: 'light' / 'mid' / 'severe'。
    返回 (row, note)。note 为空串表示精确命中；否则说明替代口径。
    顺序：精确 -> 同照护方式忽略失智专区 -> 回退照护方式 -> 未命中。
    """
    sev_cn = {"light": "轻度", "mid": "中度", "severe": "重度"}[severity]
    du = "是" if dementia_unit else "否"

    row = cost.get((care_mode, du, sev_cn))
    if row:
        return row, ""
    for d in ("否", "是"):
        row = cost.get((care_mode, d, sev_cn))
        if row:
            return row, f"{care_mode}/{sev_cn} 无失智专区样本，按失智专区={'是' if d == '是' else '否'} 替代"
    if fallback_mode:
        for d in (du, "否", "是"):
            row = cost.get((fallback_mode, d, sev_cn))
            if row:
                return row, f"{care_mode} 无价格样本，按 {fallback_mode} 价格替代（偏高，缺口偏保守）"
    return None, f"{care_mode}/{sev_cn} 无价格样本，按 0 计入（结果不可用，需补样本）"


# ----------------------------- 商业保险触发 -----------------------------
# §4 ltc_adl 的六项基本日常生活活动（穿衣、移动、行动、如厕、进食、洗澡）
ADL_BASIC6 = ("dress", "transfer", "walk", "toilet", "eat", "bath")


def _adl_lost_n(h: Household, mode: str = "lowest") -> int:
    """
    ADL 丧失项数（§4：ltc_adl 用六项基本活动判断）。
    2021 标准里分越高越好（合计 100 = 完好），故“丧失”默认取该项最低档 0 分；
    mode="non_self" 时改为“非自理档”（敏感性分析用），由配置开关切换。
    """
    if mode == "non_self":
        top = {"dress": 10, "transfer": 15, "walk": 15,
               "toilet": 10, "eat": 10, "bath": 5}
        return sum(1 for k, t in top.items() if int(getattr(h.adl, k)) < t)
    return sum(1 for k in ADL_BASIC6 if int(getattr(h.adl, k)) == 0)


def _cost_amounts(row: dict, variant: str = None):
    """
    从成本表一行取 (月总费用, 月护理费 N)。
    - N/O 分列且随档位有变化 -> 直接用 N_sfx + O_sfx；
    - 成本表只给了合计价的低/高（N、O 各档相同）-> 用 total_sfx，并按中档 N 占比拆出护理费，
      保证与 §4 的 C = 12×(N+O) 口径衔接。
    """
    sfx = variant or "mid"
    Ns = [float(row[f"N_{k}"]) for k in ("low", "mid", "high")]
    Os = [float(row[f"O_{k}"]) for k in ("low", "mid", "high")]
    varies = (len(set(Ns)) > 1) or (len(set(Os)) > 1)
    if varies:
        return Ns[("low", "mid", "high").index(sfx)] + Os[("low", "mid", "high").index(sfx)], \
            Ns[("low", "mid", "high").index(sfx)]
    base = Ns[1] + Os[1]
    tot = row.get(f"total_{sfx}")
    try:
        tot = float(tot) if tot not in (None, "") else None
    except ValueError:
        tot = None
    if tot and tot > 0 and base > 0:
        return tot, tot * Ns[1] / base
    return base, Ns[1]


def _grade_of_state(init, s: str) -> int:
    """各状态对应的社保失能等级（供 ltc_official 触发判断）。
    起始状态取测算等级；进展到的状态取该状态的最低可能等级：中度 2 级，重度取重度等级（缺省 3）。"""
    if s == init.state and s in ("L", "M"):
        return int(init.grade or 0)
    if s == "L":
        return 1
    if s == "M":
        return max(2, int(init.grade or 0)) if init.state == "M" else 2
    return int(init.sev_level or max(3, int(init.grade or 0)))


def trigger_states(pol, init, lost_n: int) -> set:
    """保单在哪些状态下处于“已触发”。触发集合对病情是单调的（只进不退）。"""
    trig = pol.trigger.value
    if trig == "ci_dementia":
        return {"S1", "S2"} if init.cause == "cognitive" else set()
    if trig == "ltc_official":
        return {s for s in ("L", "M", "S1", "S2")
                if _grade_of_state(init, s) >= (pol.min_level or 0)}
    if trig == "ltc_adl":
        # 只知道录入时的丧失项数；进展后的项数未知，审慎起见不推定新触发
        return {"L", "M", "S1", "S2"} if lost_n >= (pol.adl_n or 0) else set()
    return set()


def policy_wait_year(pol):
    """剩余等待期 W（月）-> 等待期结束所在的年 tw（>=1）和结束时点在该年中的位置 frac。
    W=0 时按年中给付（frac=0.5），与半周期校正一致。"""
    w = float(pol.wait_months or 0)
    tw = max(1, math.ceil(w / 12.0)) if w > 0 else 1
    frac = max(0.5, w / 12.0 - (tw - 1))
    return tw, frac


def _insurance_pay(pol, t: int, pi_prev: np.ndarray, pi_cur: np.ndarray, pibar: dict,
                   P: np.ndarray, dp: dict, T_set: set) -> float:
    """
    单份商业保险在第 t 年的期望给付（名义值）。v1.0.1 口径：
    - 一次性给付：第 t 年给付 = 保额 × 当年首次进入触发状态的概率；
      等待期内已触发的，在等待期结束当年按“届时仍存活”的概率给付。
      起始即处于触发状态、无剩余等待期的，按年中存活概率在第 1 年给付。
    - 按年给付：等待期结束后，每年 = 年给付额 × 当年处于触发状态的半周期概率，至给付年限止。
    """
    if not T_set:
        return 0.0
    tw, frac = policy_wait_year(pol)
    if t < tw:
        return 0.0
    tidx = [IDX[s] for s in T_set]
    out_idx = [i for i in range(len(STATES)) if STATES[i] not in T_set and STATES[i] != "D"]
    flow = float(sum(pi_prev[i] * P[i, j] for i in out_idx for j in tidx))
    if pol.payout_type.value == "lump":
        if t == tw:
            mu = {"L": dp["mu_L"], "M": dp["mu_M"], "S1": dp["mu_S"], "S2": dp["mu_S"]}
            alive_w = sum(float(pi_prev[IDX[s]]) * (1.0 - mu[s] * frac) for s in T_set)
            return float(pol.amount) * (alive_w + flow)
        return float(pol.amount) * flow
    if pol.term_years and t - tw + 1 > pol.term_years:
        return 0.0
    return float(pol.amount) * sum(pibar[s] for s in T_set)


def _ltc_wait_months(h: Household, p: Params) -> float:
    """长护险待遇等待期（月）：新参保按基准等待期；断保按每年延长。"""
    base = float(val(p, "wait_base_months") or 0)
    if h.enroll_status.value == "new":
        return base
    if h.enroll_status.value == "lapsed":
        return base + float(val(p, "wait_per_lapse_year") or 0) * 12 * int(h.lapse_years or 0)
    return 0.0


# ----------------------------- 成本/支付/缺口 核心 -----------------------------
def _simulate(init: InitialState, h: Household, p: Params, T: int = 10,
              overrides: dict = None):
    """
    确定性逐年计算（spec §4 + B6 表2）。返回 (rows, pv5, pv10, meta)。
    状态序：[L, M, S1, S2, D]；π̄_t = (π_{t-1}+π_t)/2（半周期修正）。

    overrides 供敏感性分析临时换参（不写死在任何地方）：
      g / r / lambda / rho / p_MS / mu_S : 数值替换
      cost_variant: "low" / "high"       : 成本取 CSV 的 N_low/O_low 或 N_high/O_high
    """
    ov = overrides or {}
    dp = load_disease(init.cause)
    for k in ("p_LM", "p_MS", "mu_L", "mu_M", "mu_S"):
        if k in ov:
            dp[k] = float(ov[k])
    P = _transition_matrix(dp)

    pi = np.zeros(len(STATES))
    pi[IDX[init.state]] = 1.0

    cost = load_cost(h.city.value)
    cm = h.care_mode.value
    du = h.dementia_unit
    insured = h.insured_type.value
    sev_level = int(ov["sev_level"]) if "sev_level" in ov else (init.sev_level or 5)
    g = float(ov.get("g", val(p, "g") or 0.0))
    r = float(ov.get("r", val(p, "r") or 0.0))
    lamb = float(ov.get("lambda", val(p, "lambda") or 0.0))
    I = float(val(p, "income_city") or 0.0)
    gI = float(val(p, "g_income") or 0.0)
    g_Rd = float(ov.get("g_Rd", val(p, "g_Rd") or 0.0))
    fallback_mode = val(p, "cost_fallback", "mode")
    variant = ov.get("cost_variant")          # None -> 取中值 mid

    lost_n = _adl_lost_n(h)
    y0, m0 = start_ym(p, ov)
    wait_m = _ltc_wait_months(h, p)
    first_pay_t = math.ceil(wait_m / 12) if wait_m else 1
    trusted_status = val(p, "cost_status_policy", "trusted") or []
    notes = []
    low_confidence = False

    rows = []
    cum_G = 0.0
    carry = 0.0                                           # 商业保险结余（结转下年）
    pv5 = pv10 = 0.0
    depleted_year = None
    for t in range(1, T + 1):
        year = rule_year(y0, m0, t)                       # 第 t 个测算年的年中所在日历年，用于套用当年政策
        pi_prev = pi.copy()
        pi = pi @ P
        pibar = 0.5 * (pi_prev + pi)
        bar = {s: float(pibar[IDX[s]]) for s in STATES}

        growth = (1 + g) ** (t - 1)
        Cs = {}
        cost_note = ""
        for s, sv in (("L", "light"), ("M", "mid"), ("S1", "severe"), ("S2", "severe")):
            row, note = _cost_lookup(cost, cm, du, sv, fallback_mode)
            if note and note not in notes:
                notes.append(note)
            cost_note = cost_note or note
            if row is None:
                monthly = 0.0
            else:
                monthly, _ = _cost_amounts(row, variant)
                # 成本表里标“样本不足”的格子不能当正式结果用（分工任务3）
                st = (row.get("status") or "").strip()
                if trusted_status and st not in trusted_status:
                    low_confidence = True
                    n2 = (f"{cm}/{sv} 这一格只有 {row.get('n_institutions')} 家机构样本"
                          f"（{st}），结论仅供内部参考，不作为对外口径")
                    if n2 not in notes:
                        notes.append(n2)
            Cs[s] = 12.0 * monthly * growth

        mode = settlement_for(p, insured, year)
        if "rho" in ov:
            rho = float(ov["rho"])
        else:
            rho = rho_for(p, insured, year) if mode != "none" else 0.0
        if mode == "none":
            rho = 0.0

        # 符合规定费用 E_t：min(N_重度 × 价格增长, 30.4 × R_d × 床日标准增长)
        # v1.0.1：床日支付标准是政策定额，不随照护价格增长；单设增长率 g_Rd（默认 0）
        c_sev, _ = _cost_lookup(cost, cm, du, "severe", fallback_mode)
        Nsev = _cost_amounts(c_sev, variant)[1] if c_sev else 0.0
        Rd = R_d_for(p, insured, sev_level, du)
        rd_growth = (1 + g_Rd) ** (t - 1)
        # 有床日标准时 E 受 min(N, 30.4×R_d) 约束；无床日标准（如单年用例）则按实际护理费计
        E = 12.0 * (min(Nsev * growth, 30.4 * Rd * rd_growth) if Rd else Nsev * growth)
        L_t = lamb * I * (1 + gI) ** (t - 1)

        if mode == "fixed_amount":
            B = E
        elif mode in ("cap_then_rho", "rho_on_eligible_cost"):
            B = min(rho * E, L_t)
        else:
            B = 0.0
        if t < first_pay_t:
            B = 0.0
        hit_cap = (mode in ("cap_then_rho", "rho_on_eligible_cost")
                   and rho * E > L_t and t >= first_pay_t)

        # 期望现金流（用半周期概率）
        exp_cost = sum(bar[s] * Cs[s] for s in ("L", "M", "S1", "S2"))
        exp_ltc = bar["S2"] * B                       # 仅“重度持续”在享受待遇
        ins = 0.0
        for pol in h.policies:
            ins += _insurance_pay(pol, t, pi_prev, pi, bar, P, dp,
                                  trigger_states(pol, init, lost_n))
        surv = 1.0 - pi[IDX["D"]]
        subsidy = 12.0 * float(h.subsidy_monthly or 0.0) * surv
        # 商业保险超出当年缺口的部分结转下年（11/03 精算决定 3；结余不计息）
        avail_ins = carry + ins
        pre_gap = max(0.0, exp_cost - exp_ltc - subsidy)
        gap = max(0.0, pre_gap - avail_ins)
        carry = max(0.0, avail_ins - pre_gap)
        cum_G += gap
        df = 1.0 / ((1 + r) ** (t - 0.5))
        PV = gap * df
        if t <= 5:
            pv5 += PV
        pv10 += PV

        # 家庭资金余额与耗尽年（§5：累计名义缺口 > 存款 + 每月可投入累计）
        avail = float(h.family_funds or 0.0) + 12.0 * float(h.family_monthly or 0.0) * t
        balance = avail - cum_G
        if depleted_year is None and balance < 0:
            depleted_year = year

        rows.append({
            "t": t, "year": year, "period": period_label(y0, m0, t),
            "surv": float(surv),
            "pi_L": float(pi[IDX["L"]]), "pi_M": float(pi[IDX["M"]]),
            "pi_S1": float(pi[IDX["S1"]]), "pi_S2": float(pi[IDX["S2"]]),
            "pi_D": float(pi[IDX["D"]]),
            "bar_L": bar["L"], "bar_M": bar["M"], "bar_S1": bar["S1"],
            "bar_S2": bar["S2"], "bar_D": bar["D"],
            "growth": growth,
            "C_L": Cs["L"], "C_M": Cs["M"], "C_S": Cs["S1"],
            "cost_note": cost_note,
            "mode": mode,
            "rho": (rho if mode != "fixed_amount" else None),
            "E": E, "L_t": L_t, "B_S2": B, "hit_cap": hit_cap,
            "ins": ins, "subsidy": float(subsidy),
            "exp_cost": float(exp_cost), "exp_ltc": float(exp_ltc),
            "opp_cost": 12.0 * float(h.caregiver_income_loss or 0.0) * float(surv),  # 单列，不计入缺口
            "G": float(gap), "cum_G": float(cum_G), "balance": float(balance),
            "df": df, "PV": float(PV),
            "K": ins,  # 兼容 B6 手算表的列名
            "carry": float(carry),  # 对应 B6 AA 列“商业保险结余”
        })

    return rows, pv5, pv10, {
        "depleted_year": depleted_year,
        "cost_notes": notes,
        "low_confidence": low_confidence,
        "first_pay_year": rule_year(y0, m0, first_pay_t),
        "start_ym": (y0, m0),
        "adl_lost_n": lost_n,
    }


def run_deterministic(h: Household, p: Params, T: int = 10) -> YearTable:
    """§4/§5：先由 map_initial_state 得初始状态，再逐年模拟。返回逐年表。"""
    rows, _, _, _ = run_deterministic_full(h, p, T)
    return rows


def run_deterministic_full(h: Household, p: Params, T: int = 10, overrides: dict = None):
    """返回 (rows, pv5, pv10, meta)，供 build_report 组装摘要与提示。"""
    from engine.mapping import map_initial_state
    init = map_initial_state(h, p)
    rows, pv5, pv10, meta = _simulate(init, h, p, T, overrides)
    meta["init"] = init
    return rows, pv5, pv10, meta
