"""
engine/mapping.py —— §3 初始状态与失能资格（A6 第 9 件，按规格书实现）

map_initial_state：
  1) 由 ADL/Cog/Sense 三合计分别定级（2021 标准表2 分档）；
  2) 认知与感知觉取较重一项，再与 ADL 按表3组合法定测算等级；
  3) 有有效期内官方结论 -> 以官方结论为准；
  4) 城市对重度失智另有认定（青岛：主因认知 + MMSE ≤ 阈值）-> 按细则参照等级；
  5) 主因：cognition 为 moderate/severe 或 cdr≥2 记认知；否则若认知/感知觉把等级抬高也记认知，否则躯体；
  6) 状态：0 级不进测算；1 级 L；2 级 M；3–5 级按是否走完“持续期+办理期”分 S1/S2；
  7) 资格：国家框架起步阶段只保 3–5 级；城市细则覆盖中度的，2 级也算有资格。

代码里不写死任何月数/阈值，全部从 assessment.yaml 与城市配置读取。
"""
import os
import datetime
import yaml

from engine.schema import Household, InitialState, Params

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")


def _load_yaml(path: str):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _val(p: dict, *path):
    """与 model.val 同样的取数规则，避免循环引用。"""
    cur = p
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    if isinstance(cur, dict) and "value" in cur:
        return cur["value"]
    return cur


def _grade_from_sum(s: int, bands: dict) -> str:
    for name in ("intact", "mild", "moderate", "severe"):
        lo, hi = bands[name]
        if lo <= s <= hi:
            return name
    # 落在两档之间的空档（分档按 5 分整档设置，正常取不到）：归入距离最近的一档
    return min(("intact", "mild", "moderate", "severe"),
               key=lambda n: min(abs(s - bands[n][0]), abs(s - bands[n][1])))


def _state_from_grade(grade: int, months_in_state: int, persist: float,
                      process: float, reapply_months: float,
                      months_since_last_fail) -> str:
    """grade 0–5 -> 初始状态 L/M/S1/S2（§3 第二步）。"""
    if grade <= 1:
        return "L"
    if grade == 2:
        return "M"
    via_reapply = (months_since_last_fail is not None
                   and float(months_since_last_fail) < float(reapply_months))
    if via_reapply:
        return "S1"                      # 未过再次申请间隔，即使已重度也按 S1 计
    threshold = float(persist) + float(process)
    return "S2" if float(months_in_state or 0) >= threshold else "S1"


def map_initial_state(h: Household, p: Params = None) -> InitialState:
    a = _load_yaml(os.path.join(CONFIG_DIR, "assessment.yaml"))
    adl_b = a["adl_grades"]; cog_b = a["cog_grades"]; sense_b = a["sense_grades"]
    rows = a["combo_rows"]; cols = a["combo_cols"]; table = a["combo_grade"]

    if p is None:                              # 未传参数则按户所在城市自行加载
        from engine.model import load_params
        p = load_params(h.city.value)

    adl_sum = sum([h.adl.eat, h.adl.dress, h.adl.groom, h.adl.bowel, h.adl.bladder,
                   h.adl.toilet, h.adl.walk, h.adl.transfer, h.adl.stairs, h.adl.bath])
    cog_sum = h.cog.time + h.cog.person + h.cog.place + h.cog.memory
    sense_sum = h.sense.vision + h.sense.hearing + h.sense.comm

    adl_g = _grade_from_sum(adl_sum, adl_b)
    cog_g = _grade_from_sum(cog_sum, cog_b)
    sense_g = _grade_from_sum(sense_sum, sense_b)
    cog_or_sense = "severe" if (cog_g == "severe" or sense_g == "severe") else \
                   "moderate" if (cog_g == "moderate" or sense_g == "moderate") else \
                   "mild" if (cog_g == "mild" or sense_g == "mild") else "intact"

    ri = rows.index(adl_g); ci = cols.index(cog_or_sense)
    grade = int(table[ri][ci])

    # —— 主因 ——
    cdr_val = h.cdr.value if h.cdr else None
    if h.cognition.value in ("moderate", "severe") or (cdr_val is not None and float(cdr_val) >= 2):
        cause = "cognitive"
    else:
        adl_only = int(table[ri][cols.index("intact")])
        cause = "cognitive" if adl_only < grade else "somatic"

    # —— 官方结论优先 ——
    use_official = False
    ol = h.official_level.value if h.official_level else "none"
    if ol not in (None, "none"):
        valid = True
        if h.official_valid_until:
            try:
                valid = datetime.date.fromisoformat(h.official_valid_until) >= datetime.date.today()
            except ValueError:
                valid = True
        if valid:
            grade = int(ol); use_official = True

    # —— 城市细则对重度失智的认定（青岛） ——
    dementia_qualified = False
    rule = _val(p, "dementia_rule")
    if isinstance(rule, dict) and _val(p, "covers_dementia") and cause == "cognitive":
        thr = rule.get("mmse_threshold")
        mmse = h.mmse
        if thr is not None and mmse is not None and int(mmse) <= int(thr):
            ref = int(rule.get("institution_ref_level") or grade)
            if not use_official:               # 官方结论优先于本市参照口径
                grade = max(grade, ref)
            dementia_qualified = True

    # —— 状态与资格 ——
    persist = _val(p, "persist_months") or 0
    process = _val(p, "process_months") or 0
    reapply = _val(p, "reapply_months") or 0
    state = _state_from_grade(grade, h.months_in_state, persist, process,
                              reapply, h.months_since_last_fail)

    covers_mod = _val(p, "covers_moderate")
    if isinstance(covers_mod, dict):
        covers_mod = bool(covers_mod.get(h.insured_type.value, False))
    eligible = grade >= 3 or (grade == 2 and bool(covers_mod))
    sev_level = grade if grade >= 3 else None

    return InitialState(grade=grade, state=state, cause=cause,
                        eligible=eligible, use_official=use_official,
                        sev_level=sev_level, dementia_qualified=dementia_qualified)
