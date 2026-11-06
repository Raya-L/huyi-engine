"""
engine/report.py —— §5 组装输出

build_report 一次返回 8 块内容 + 合规提示 + 运行留痕，页面只负责渲染，不做计算。
所有金额取整到百元（§5 首段）：避免给家属"精确到个位"的错觉。
"""
import math
import hashlib
import datetime

import numpy as np

from engine.schema import Household, Params, Report
from engine.model import (
    run_deterministic_full, val, load_cost, _cost_lookup, R_d_for,
    settlement_for, rho_for, load_params,
)

ENGINE_VERSION = "v0.1.5"
COMPLIANCE = ("本工具不做医学诊断，也不做失能等级评定，结果仅为财务测算和科普参考。"
              "请到记忆门诊就诊，到定点评估机构申请评估。")


def _covers_moderate(p: Params, insured_type: str) -> bool:
    v = val(p, "covers_moderate")
    if isinstance(v, dict):
        return bool(v.get(insured_type, False))
    return bool(v)


# ---------------- 小工具 ----------------
def _hundred(x: float) -> float:
    """金额取整到百元（§5）。"""
    return float(round((x or 0.0) / 100.0) * 100)


def _wan(x: float) -> str:
    """元 -> 万元，保留 1 位小数。"""
    return f"{_hundred(x) / 10000:.1f}"


def _money(x: float) -> str:
    """自适应单位：不足 1 万按“元”，否则按“万元”。"""
    x = _hundred(x)
    return f"{x:,.0f} 元" if abs(x) < 10000 else f"{x / 10000:.1f} 万元"


def _spread(p: dict) -> str:
    """P10/P50/P90 -> “约 X 至 Y 万元，中间情形约 Z 万元”。"""
    if not p:
        return "（随机模式未产出）"
    return (f"约 {_money(p['P10'])} 至 {_money(p['P90'])}，"
            f"中间情形约 {_money(p['P50'])}")


def _band_text(lo: float, mid: float, hi: float) -> str:
    """成本表低/中/高三档 -> 一句话区间；三档同值时只说一个数，避免“X 万至 X 万”。"""
    if abs(hi - lo) < 1e-9:
        return f"约 {_wan(lo)} 万元"
    if abs(mid - lo) < 1e-9 or abs(mid - hi) < 1e-9:
        return f"约 {_wan(lo)} 万至 {_wan(hi)} 万元"
    return f"约 {_wan(lo)} 万至 {_wan(hi)} 万元，中间情形约 {_wan(mid)} 万元"


def _add_months(d: datetime.date, months: float) -> datetime.date:
    y = d.year + int((d.month - 1 + months) // 12)
    m = int((d.month - 1 + months) % 12) + 1
    return datetime.date(y, m, min(d.day, 28))


def _annuity_factor(r: float, n: int) -> float:
    """年中折现的年金现值系数，用于把一次性缺口换算成“每年给付多少”。"""
    return sum(1.0 / ((1 + r) ** (t - 0.5)) for t in range(1, n + 1))


# ---------------- 第 3 块：分情形说明 ----------------
def _scenarios(h: Household, p: Params, rows: list) -> list:
    """
    对每个状态给一句“若按某程度情形测算，每年照护费约 X 元，长护险约付 Y 元”（§5 第 3 块）。
    重度再按 3、4、5 级各给一句。首年口径，用第 1 行的价格水平。
    """
    cost = load_cost(h.city.value)
    fb = val(p, "cost_fallback", "mode")
    r0 = rows[0]
    growth = r0["growth"]
    insured = h.insured_type.value
    mode = r0["mode"]
    out = []

    for key, sev, name in (("L", "light", "轻度"), ("M", "mid", "中度"), ("S1", "severe", "重度")):
        row, note = _cost_lookup(cost, h.care_mode.value, h.dementia_unit, sev, fb)
        c = 12.0 * (float(row["N_mid"]) + float(row["O_mid"])) * growth if row else 0.0
        if key == "S1":
            continue
        if mode == "none":
            pay = 0.0
            tail = "本市目前对这类情形不支付长护险"
        elif key == "M" and not _covers_moderate(p, insured):
            pay = 0.0
            tail = "中度不在本市长护险支付范围"
        else:
            pay = 0.0
            tail = "长护险只在“重度持续”阶段开始支付，这一段需自付"
        out.append({"state": key, "label": name,
                    "cost_year1": _hundred(c), "ltc_year1": _hundred(pay),
                    "text": f"若按{name}情形测算，每年照护费约 {_hundred(c):,.0f} 元，"
                            f"长护险约付 {_hundred(pay):,.0f} 元（{tail}）。"})

    # 重度 3/4/5 级
    row, _ = _cost_lookup(cost, h.care_mode.value, h.dementia_unit, "severe", fb)
    c_sev = 12.0 * (float(row["N_mid"]) + float(row["O_mid"])) * growth if row else 0.0
    for lv in (3, 4, 5):
        # 3/4/5 级是“失能等级”口径，R_d 按等级取，不套用失智专区的五级标准
        Rd = R_d_for(p, insured, lv, False)
        E = 12.0 * min(float(row["N_mid"]) if row else 0.0, 30.4 * Rd) if Rd else 0.0
        rho = rho_for(p, insured, rows[0]["year"]) if mode != "none" else 0.0
        if mode == "fixed_amount":
            pay = E
        elif mode in ("cap_then_rho", "rho_on_eligible_cost"):
            pay = min(rho * E * growth, r0["L_t"])
        else:
            pay = 0.0
        out.append({"state": "S", "label": f"重度 {lv} 级",
                    "cost_year1": _hundred(c_sev), "ltc_year1": _hundred(pay),
                    "text": f"若按重度 {lv} 级情形测算，每年照护费约 {_hundred(c_sev):,.0f} 元，"
                            f"长护险约付 {_hundred(pay):,.0f} 元。"})
    return out


# ---------------- 第 7 块：待遇时间线 ----------------
def _timeline(h: Household, p: Params, meta: dict, init) -> str:
    persist = float(val(p, "persist_months") or 0)
    process = float(val(p, "process_months") or 0)
    wait = float(val(p, "wait_base_months") or 0)
    if h.enroll_status.value == "new":
        wait_total = wait
    elif h.enroll_status.value == "lapsed":
        wait_total = wait + float(val(p, "wait_per_lapse_year") or 0) * 12 * int(h.lapse_years or 0)
    else:
        wait_total = 0.0

    today = datetime.date.today()
    months = persist + process + wait_total
    earliest = _add_months(today, months)

    bs = val(p, "benefit_start")
    start_note = ""
    if bs and str(bs) not in ("None", "—"):
        try:
            bs_d = datetime.date.fromisoformat(str(bs)[:10])
            if bs_d > earliest:
                earliest = bs_d
                start_note = f"（受本市待遇起始日 {bs_d:%Y 年 %m 月} 限制）"
        except ValueError:
            pass

    valid_years = float(val(p, "level_valid_years") or 0)
    parts = [f"若今天提出申请，按现行规定最早约在 {earliest.year} 年 {earliest.month} 月起可享受待遇"
             + start_note + "。"]
    parts.append(f"算法：失能持续期 {persist:g} 个月 + 评估办理期 {process:g} 个月"
                 + (f" + 等待期 {wait_total:g} 个月" if wait_total else "")
                 + "，逐段相加。")
    if valid_years:
        parts.append(f"结论有效期约 {valid_years:g} 年，到期前需重新评估，否则待遇会中断。")
    if meta.get("depleted_year"):
        parts.append(f"按当前家庭资金，预计 {meta['depleted_year']} 年出现资金缺口。")
    else:
        parts.append("按当前家庭资金，10 年内可覆盖，不会出现资金耗尽。")
    return "".join(parts)


# ---------------- 第 8 块：限额提示 ----------------
def _cap_note(rows: list) -> str:
    hit = [r for r in rows if r.get("hit_cap")]
    if not hit:
        return "测算期内长护险未触及年度最高限额。"
    yrs = "、".join(f"{r['year']} 年" for r in hit[:4])
    more = f" 等 {len(hit)} 个年度" if len(hit) > 4 else ""
    return (f"{yrs}{more}长护险已按年度最高限额支付，超出部分需自付。"
            "这就是为什么政策写的支付比例是七八成，实际报销下来可能只有两三成。")


# ---------------- 第 5 块附带：政策对照情景（现行无待遇的城市） ----------------
def _policy_scenario(h: Household, p: Params, T: int, pv10: float):
    """
    现行结算方式为 none 的城市（北京），配置里若有 status=情景设定 的对照政策段，
    按该段再算一次，给出“若按国家基准起付”的缺口，供结果页对照。
    返回 None 表示本市无需对照情景。
    """
    import copy
    periods = val(p, "settlement_periods") or []
    if not isinstance(periods, list):
        return None
    cf = [x for x in periods if isinstance(x, dict) and x.get("status") == "情景设定"]
    if not cf:
        return None
    seg = dict(cf[0])
    key = "employee" if h.insured_type.value == "employee" else "resident"
    rho_cf = seg.get(f"rho_{key}")
    if rho_cf is None:
        return None

    p2 = copy.deepcopy(p)
    p2["settlement_periods"] = [dict(seg, status="对照情景")]
    p2[f"rho_{key}"] = {"value": float(rho_cf)}
    _, _, pv10_cf, _ = run_deterministic_full(h, p2, T)

    frm = str(seg.get("from") or "")[:4]
    tpl = val(p, "result_notice_other") or \
        "按现行规定，长护险目前不支付，10 年缺口约 X 万元；若按国家基准起付，约 Y 万元。"
    text = (tpl.replace("X", _wan(pv10)).replace("Y", _wan(pv10_cf)))
    return {"applies": True, "from_year": int(frm) if frm.isdigit() else None,
            "rho": float(rho_cf), "pv10_current": _hundred(pv10),
            "pv10_counterfactual": _hundred(pv10_cf),
            "delta_pv10": _hundred(max(0.0, pv10 - pv10_cf)), "text": text}


# ---------------- 第 6 块：保障组合区间 ----------------
def _combo(h: Household, p: Params, band10: dict, T: int, rows: list = None,
           depleted_year: int = None) -> dict:
    """
    与摘要同一口径：用成本表低/中/高三档的 10 年缺口现值（中间 = mid，偏重 = high），
    和家庭可用资金现值比较，倒推需要补充的年给付额。
    """
    r = float(val(p, "r") or 0.0)
    avail = float(h.family_funds or 0.0) + \
        sum(12.0 * float(h.family_monthly or 0.0) / ((1 + r) ** (t - 0.5)) for t in range(1, T + 1))
    af = _annuity_factor(r, T)

    out = {"available_pv": _hundred(avail), "term_years": T, "tiers": []}
    for label, key in (("中间情形", "mid"), ("偏重情形", "high")):
        gap = band10.get(key, 0.0)
        need = max(0.0, gap - avail)
        item = {"label": label, "gap_pv": _hundred(gap), "need_pv": _hundred(need),
                "annual_low": _hundred(need / af) if af else 0.0,
                "annual_high": _hundred(need * 1.2 / af) if af else 0.0}
        out["tiers"].append(item)

    t0 = out["tiers"][0]
    # 10 年总量够、但缺口集中在前几年时，逐年现金流会先断：与第 7 块“资金缺口年份”保持一致
    shortfall = max([0.0] + [-float(r.get("balance", 0.0)) for r in (rows or [])])
    if t0["need_pv"] <= 0 and depleted_year:
        out["text"] = (
            f"按 10 年总额看，现有资金大致够用；但照护缺口集中在前几年，按逐年现金流预计 "
            f"{depleted_year} 年资金先用完，最紧的时候约差 {_wan(_hundred(shortfall))} 万元，"
            f"需要提前准备这笔周转资金。")
        out["shortfall_peak"] = _hundred(shortfall)
    elif t0["need_pv"] <= 0:
        out["text"] = "现有资金在中间情形下可覆盖，暂不需要额外补充商业护理保险。"
    else:
        out["text"] = (
            f"需补足额约 {_wan(t0['need_pv'])} 万元（中间情形）至 "
            f"{_wan(out['tiers'][1]['need_pv'])} 万元（偏重情形）。"
            f"若用年给付型护理保险补足，约需年给付 {t0['annual_low']:,.0f} 至 "
            f"{out['tiers'][1]['annual_high']:,.0f} 元、给付期 {T} 年。"
        )
    out["note"] = "家庭可用资金取存款 + 每月可持续投入，按同一折现率折为现值。本金不作收益假设。"
    return out


# ================= 主函数 =================
def build_report(h: Household, p: Params, T: int = 10, with_sensitivity: bool = True) -> Report:
    from engine.simulate import run_stochastic, run_sensitivity

    rows, pv5, pv10, meta = run_deterministic_full(h, p, T)
    init = meta.get("init")
    dist = run_stochastic(h, p, T)

    # —— 1. 摘要 ——
    # 分工任务9：用成本表低、中、高三档各算一次，写成“约 X 万至 Y 万元，中间情形约 Z 万元”
    band = {}
    for side in ("low", "mid", "high"):
        _, p5, p10, _ = run_deterministic_full(h, p, T, {"cost_variant": side})
        band[side] = (_hundred(p5), _hundred(p10))
    lo5, mid5, hi5 = band["low"][0], band["mid"][0], band["high"][0]
    lo10, mid10, hi10 = band["low"][1], band["mid"][1], band["high"][1]
    if lo5 > hi5:
        lo5, hi5 = hi5, lo5
    if lo10 > hi10:
        lo10, hi10 = hi10, lo10

    h5 = dist["horizons"].get("5", {})
    h10 = dist["horizons"].get("10", dist["horizons"].get(str(T), {}))

    summary = {
        "band_5": {"low": lo5, "mid": mid5, "high": hi5},
        "band_10": {"low": lo10, "mid": mid10, "high": hi10},
        "text_5": f"5 年内，家里还要自掏的照护缺口{_band_text(lo5, mid5, hi5)}。",
        "text_10": f"10 年内，家里还要自掏的照护缺口{_band_text(lo10, mid10, hi10)}。",
        # 随机模式（规格书§5 首块要求 P10/P50/P90，作补充口径一并给出）
        "stochastic": {
            "n": dist.get("n"), "seed": dist.get("seed"),
            "gap_5": (h5.get("gap") or {}), "gap_10": (h10.get("gap") or {}),
            "cost_10": (h10.get("cost") or {}), "ltc_10": (h10.get("ltc") or {}),
            "ins_10": (h10.get("ins") or {}), "subsidy_10": (h10.get("subsidy") or {}),
        },
        "deterministic_pv5": _hundred(pv5),
        "deterministic_pv10": _hundred(pv10),
        "low_confidence": bool(meta.get("low_confidence")),
    }

    # —— 2. 逐年明细 ——
    year_table = [{
        "年份": r["year"], "存活概率": round(r["surv"], 3),
        "轻度": round(r["pi_L"], 3), "中度": round(r["pi_M"], 3),
        "重度首年": round(r["pi_S1"], 3), "重度持续": round(r["pi_S2"], 3),
        "照护成本": _hundred(r["exp_cost"]), "长护险支付": _hundred(r["exp_ltc"]),
        "商业保险": _hundred(r["ins"]), "政府补贴": _hundred(r["subsidy"]),
        "缺口": _hundred(r["G"]), "累计缺口": _hundred(r["cum_G"]),
        "家庭资金余额": _hundred(r["balance"]), "缺口现值": _hundred(r["PV"]),
        "触及年度限额": bool(r["hit_cap"]),
    } for r in rows]

    # —— 3. 分情形说明 ——
    scenarios = _scenarios(h, p, rows)

    # —— 4. 敏感性 ——
    if with_sensitivity:
        sens = run_sensitivity(h, p, T)
    else:
        sens = {"items": [], "top3": [], "base_pv10": pv10, "skipped": []}

    # —— 5. 失智纳保情景（仅认知主因显示） ——
    dementia = None
    if init is not None and init.cause == "cognitive":
        already = bool(val(p, "covers_dementia"))
        if already:
            dementia = {"applies": True, "already_covered": True, "delta_pv10": 0.0,
                        "text": "本市已把重度失智单独列入保障对象（按失能五级执行），"
                                "上面的测算已经包含这部分待遇，无需再做假设。"}
        else:
            ref = int(val(p, "dementia_rule", "institution_ref_level") or 5) \
                if isinstance(val(p, "dementia_rule"), dict) else 5
            _, _, pv10_dem, _ = run_deterministic_full(h, p, T, {"sev_level": ref})
            delta = max(0.0, pv10 - pv10_dem)
            dementia = {"applies": True, "already_covered": False,
                        "delta_pv10": _hundred(delta),
                        "text": f"若本市对重度失智单独认定（按失能五级标准支付），"
                                f"10 年缺口约减少 {_wan(delta)} 万元。"}

    # —— 5b. 政策对照情景（现行无待遇的城市，如北京） ——
    policy_cf = _policy_scenario(h, p, T, mid10)

    # —— 6. 保障组合区间 ——
    combo = _combo(h, p, summary["band_10"], T, rows, meta.get("depleted_year"))

    # —— 7. 待遇时间线 ——
    timeline = _timeline(h, p, meta, init) if init is not None else ""

    # —— 8. 限额提示 ——
    cap_note = _cap_note(rows)

    # —— 附加：成本口径与数据缺口提示 ——
    data_notes = list(meta.get("cost_notes") or [])
    if init is not None and init.grade == 0:
        data_notes.append("按 2021 标准组合法为 0 级，属“未达失能”，不进入测算；"
                          "页面应改为科普与就诊提示。")
    # 市级口径未定的提示（北京：全市细则缺失；石景山另按试点口径）
    if not any(v for k, v in (val(p, "settlement_periods") or [{}])[0].items()
               if k == "mode" and v not in (None, "none")):
        if h.district == "shijingshan" and val(p, "result_notice_shijingshan"):
            data_notes.append(str(val(p, "result_notice_shijingshan")))
        elif val(p, "not_found_notice"):
            data_notes.append(str(val(p, "not_found_notice")))

    report: Report = {
        "summary": summary,
        "year_table": year_table,
        "scenarios": scenarios,
        "sensitivity": {
            "base_pv10": _hundred(sens["base_pv10"]),
            "top3": [{**it,
                      "pv10_low": _hundred(it["pv10_low"]),
                      "pv10_high": _hundred(it["pv10_high"]),
                      "swing": _hundred(it["swing"])} for it in sens["top3"]],
            "all": len(sens["items"]),
            "skipped": sens["skipped"],
            "text": "参数逐个在低值与高值之间摆动，其余取基准，记录 10 年缺口现值的变化；"
                    "影响最大的三个见龙卷风图。",
        },
        "dementia_scenario": dementia,
        "policy_scenario": policy_cf,
        "combo": combo,
        "timeline": timeline,
        "cap_note": cap_note,
        "data_notes": data_notes,
        "compliance": COMPLIANCE,
        "run_log": {
            "input_hash": hashlib.md5(repr(h).encode("utf-8")).hexdigest()[:8],
            "params_version": str(p.get("version") or "unknown"),
            "engine_version": ENGINE_VERSION,
            "seed": int(val(p, "seed") or 0),
            "n_sims": dist.get("n"),
            "run_time": datetime.datetime.now().isoformat(timespec="seconds"),
        },
    }
    return report
