"""
tests/test_cases.py —— B6 手算对账 + 限额单点校验（A6 第 8 件）

跑法：  pytest -q
判据：  3 个手算案例的逐年缺口 G、长护险 B、5 年与 10 年缺口现值 PV，
        全部与 handcalc_expected.json 的相对误差 < 1%；
        另加一个单年案例，验证“触及年度限额”时按限额封顶并置 hit_cap。
"""
import os
import sys
import json

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.schema import (                                                # noqa: E402
    Household, ADL, Cog, Sense, Sex, City, InsuredType, Cognition, CareMode,
    InitialState, Policy, PayoutType, Trigger, validate_input,
)
from engine.model import load_params, run_deterministic_full, _simulate       # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                    "handcalc_expected.json")
TOL = 0.01          # 1% 相对误差


def _load_expected():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def _household(city, insured, dementia_unit, cognition="severe", policies=None):
    return Household(
        age=80, sex=Sex.female, city=City(city), insured_type=InsuredType(insured),
        adl=ADL(0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        cog=Cog(0, 0, 0, 0), sense=Sense(0, 0, 0),
        cognition=Cognition(cognition), months_in_state=24,
        care_mode=CareMode.institution, escalate_to_institution=False,
        caregiver_income_loss=0.0, policies=policies or [],
        subsidy_monthly=0.0, family_funds=0.0, family_monthly=0.0,
        dementia_unit=dementia_unit,
    )


# B6 表 1：三个案例的输入（与 B6_手算校验案例.xlsx 一致）
CASES = {
    "A": dict(
        city="qingdao", insured="employee", dementia_unit=True,
        init=InitialState(grade=2, state="M", cause="cognitive", eligible=True,
                          use_official=False, sev_level=5),
        policies=None,
    ),
    "B": dict(
        city="chongqing", insured="employee", dementia_unit=False,
        init=InitialState(grade=5, state="S2", cause="cognitive", eligible=True,
                          use_official=False, sev_level=5),
        policies=None,
    ),
    "C": dict(
        city="qingdao", insured="resident", dementia_unit=True,
        init=InitialState(grade=5, state="S2", cause="cognitive", eligible=True,
                          use_official=False, sev_level=5),
        policies=[Policy(payout_type=PayoutType.lump, amount=100000.0,
                         trigger=Trigger.ci_dementia, term_years=1, wait_months=0)],
    ),
}


@pytest.mark.parametrize("name", ["A", "B", "C"])
def test_b6_handcalc(name):
    """逐年缺口与长护险给付、5/10 年现值必须与手算表吻合（<1%）。"""
    exp = _load_expected()[name]
    c = CASES[name]
    h = _household(c["city"], c["insured"], c["dementia_unit"], policies=c["policies"])
    p = load_params(c["city"])

    rows, pv5, pv10, _ = _simulate(c["init"], h, p, 10)
    assert len(rows) == 10

    for i, e in enumerate(exp["rows"]):
        got = rows[i]
        assert got["year"] == e["year"], f"{name} 第 {i+1} 年年份错位"
        assert got["G"] == pytest.approx(e["G"], rel=TOL), \
            f"{name} {e['year']} 年缺口不符：engine={got['G']:.2f} hand={e['G']:.2f}"
        if e.get("B_S2") is not None:
            assert got["B_S2"] == pytest.approx(e["B_S2"], rel=TOL), \
                f"{name} {e['year']} 年长护险给付不符：engine={got['B_S2']:.2f} hand={e['B_S2']:.2f}"

    assert pv5 == pytest.approx(exp["pv5"], rel=TOL), \
        f"{name} 5 年缺口现值不符：engine={pv5:.2f} hand={exp['pv5']:.2f}"
    assert pv10 == pytest.approx(exp["pv10"], rel=TOL), \
        f"{name} 10 年缺口现值不符：engine={pv10:.2f} hand={exp['pv10']:.2f}"


def test_cap_hit_single_year():
    """
    单年验算：月护理费 3950 元、ρ=0.70、年度限额 λ×人均可支配收入 = 0.5×43377。
    ρ×E = 0.70×12×3950 = 33180 > 21688.5，故长护险当年只能付 21688.5 并置 hit_cap。
    """
    h = _household("chongqing", "employee", dementia_unit=False)
    init = InitialState(grade=5, state="S2", cause="cognitive", eligible=True,
                        use_official=False, sev_level=5)
    p = {
        "g": {"value": 0.0},
        "r": {"value": 0.017},
        "lambda": {"value": 0.5},
        "income_city": {"value": 43377.0},
        "g_income": {"value": 0.0},
        "rho_employee": {"value": 0.70},
        "R_d": {"employee": [1000.0, 1000.0, 1000.0]},   # 30.4×1000 > 3950，E 取 12×3950
        "settlement_periods": [{"mode": "cap_then_rho",
                                "applies_to": ["employee", "resident"]}],
        "cost_fallback": {"mode": "institution"},
    }
    rows, _, _, _ = _simulate(init, h, p, 1)
    r = rows[0]
    assert r["E"] == pytest.approx(12 * 3950.0)
    assert r["L_t"] == pytest.approx(0.5 * 43377.0)
    assert r["B_S2"] == pytest.approx(21688.5, rel=1e-9)
    assert r["hit_cap"] is True


def test_no_hit_cap_when_below_limit():
    """同一情形把支付比例降到 0.30，ρ×E 低于限额，不应置 hit_cap。"""
    h = _household("chongqing", "employee", dementia_unit=False)
    init = InitialState(grade=5, state="S2", cause="cognitive", eligible=True,
                        use_official=False, sev_level=5)
    p = {
        "g": {"value": 0.0}, "r": {"value": 0.017},
        "lambda": {"value": 0.5}, "income_city": {"value": 43377.0},
        "g_income": {"value": 0.0}, "rho_employee": {"value": 0.30},
        "R_d": {"employee": [1000.0, 1000.0, 1000.0]},
        "settlement_periods": [{"mode": "cap_then_rho",
                                "applies_to": ["employee", "resident"]}],
        "cost_fallback": {"mode": "institution"},
    }
    rows, _, _, _ = _simulate(init, h, p, 1)
    assert rows[0]["B_S2"] == pytest.approx(0.30 * 12 * 3950.0)
    assert rows[0]["hit_cap"] is False


def test_run_deterministic_returns_ten_years():
    """run_deterministic 走 map_initial_state，第 1 年应为 2026（Task6）。"""
    h = _household("qingdao", "employee", dementia_unit=True)
    rows = run_deterministic_full(h, load_params("qingdao"), 10)[0]
    assert [r["year"] for r in rows] == list(range(2026, 2036))


# ---------------- 录入校验（Task5） ----------------
def _raw(**kw):
    base = dict(age=78, sex="female", city="qingdao", insured_type="employee",
                cognition="severe", months_in_state=24, care_mode="institution",
                eat=10, dress=10, groom=5, bowel=10, bladder=10, toilet=10,
                walk=15, transfer=15, stairs=10, bath=5,
                time=1, person=1, place=0, memory=1,
                vision=2, hearing=3, comm=2)
    base.update(kw)
    return base


def test_mmse_can_be_empty():
    h, errs = validate_input(_raw(mmse=""))
    assert errs == [] and h.mmse is None


def test_mmse_out_of_range_rejected():
    _, errs = validate_input(_raw(mmse=35))
    assert any("mmse" in e for e in errs)


def test_adl_off_tier_rejected():
    _, errs = validate_input(_raw(groom=10))     # 面部清洁只能 0/5
    assert any("adl.groom" in e for e in errs)


def test_adl_top_tier_accepted():
    h, errs = validate_input(_raw(walk=15, transfer=15))
    assert errs == [] and h.adl.walk == 15


# ---------------- 报告层（Task9） ----------------
def _sev_raw(**kw):
    """重度 5 级画像：ADL 10 项全 0（0 分 = 完全依赖）、认知 4 项全 0。"""
    base = dict(age=82, sex="male", city="qingdao", insured_type="employee",
                cognition="severe", months_in_state=30, care_mode="institution",
                dementia_unit=True, enroll_status="continuous",
                family_funds=0, family_monthly=0, subsidy_monthly=0,
                caregiver_income_loss=0, official_level="none",
                eat=0, dress=0, groom=0, bowel=0, bladder=0, toilet=0,
                walk=0, transfer=0, stairs=0, bath=0,
                time=0, person=0, place=0, memory=0, vision=0, hearing=0, comm=0)
    base.update(kw)
    return base


def test_cost_band_not_collapsed():
    """青岛认知症专区的低/高档只写在 total_* 列，摘要区间不能塌成一个点。"""
    from engine.report import build_report
    h, errs = validate_input(_sev_raw(mmse=10, cdr="3"))
    assert errs == []
    rep = build_report(h, load_params("qingdao"), with_sensitivity=False)
    b = rep["summary"]["band_10"]
    assert b["low"] < b["mid"] < b["high"], b
    assert "约" in rep["summary"]["text_10"]


def test_beijing_counterfactual_scenario():
    """北京现行不支付：确定性缺口应等于全额自负，且对照情景（2029 起）缺口更小。"""
    from engine.report import build_report
    h, errs = validate_input(_sev_raw(city="beijing", dementia_unit=False))
    assert errs == []
    rep = build_report(h, load_params("beijing"), with_sensitivity=False)
    ps = rep["policy_scenario"]
    assert ps is not None and ps["pv10_counterfactual"] < ps["pv10_current"]
    assert ps["text"].startswith("按现行规定")
    assert any("石景山" in n for n in rep["data_notes"]) is False   # 非石景山不该有试点提示


def test_shijingshan_notice():
    from engine.report import build_report
    h, errs = validate_input(_sev_raw(city="beijing", dementia_unit=False,
                                      district="shijingshan"))
    assert errs == []
    rep = build_report(h, load_params("beijing"), with_sensitivity=False)
    assert any("石景山" in n for n in rep["data_notes"])



def test_combo_consistent_with_depletion_year():
    """10 年现值够、但逐年现金流先断时，第 6 块不能再说“可覆盖”（与第 7 块一致）。"""
    from engine.report import build_report
    raw = dict(age=79, sex="female", city="qingdao", insured_type="employee",
               cognition="moderate", cdr="2", mmse=15, months_in_state=12,
               care_mode="institution", dementia_unit=True, enroll_status="continuous",
               official_level="none", family_funds=100000, family_monthly=1500,
               subsidy_monthly=0, caregiver_income_loss=0,
               eat=10, dress=5, groom=0, bowel=10, bladder=5, toilet=5,
               walk=10, transfer=10, stairs=5, bath=0,
               time=1, person=2, place=1, memory=1, vision=3, hearing=3, comm=2)
    h, errs = validate_input(raw)
    assert errs == []
    r = build_report(h, load_params("qingdao"), with_sensitivity=False)
    assert "资金缺口" in r["timeline"]
    assert "可覆盖" not in r["combo"]["text"]
    assert "先用完" in r["combo"]["text"]
