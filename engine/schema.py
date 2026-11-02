"""
engine/schema.py  —— 输入输出的数据结构（A6 第 2 件）

规则（规格书§2、§7）：
- 字段名只用规格书“代码名”列，且本文件是唯一定义处，别处都引用这里。
- ADL 10 项、认知 4 项、感知觉 3 项用嵌套 dataclass，代码名即 adl.eat / cog.time / sense.vision。
- Params / InitialState / YearTable / Distribution / 报告结构在本文件一并铺好，后续只填实现。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
import datetime


# ---------------- 枚举（取值严格照 §2） ----------------
class Sex(str, Enum):
    male = "male"
    female = "female"


class City(str, Enum):
    qingdao = "qingdao"
    chongqing = "chongqing"
    beijing = "beijing"
    national = "national"


class District(str, Enum):
    shijingshan = "shijingshan"
    other = "other"


class InsuredType(str, Enum):
    employee = "employee"
    resident = "resident"
    none = "none"


class EnrollStatus(str, Enum):
    continuous = "continuous"
    new = "new"
    lapsed = "lapsed"


class OfficialLevel(str, Enum):
    none = "none"
    lv0 = "0"
    lv1 = "1"
    lv2 = "2"
    lv3 = "3"
    lv4 = "4"
    lv5 = "5"


class Cognition(str, Enum):
    none = "none"
    suspected = "suspected"
    mild = "mild"
    moderate = "moderate"
    severe = "severe"


class Cdr(str, Enum):
    v05 = "0.5"
    v1 = "1"
    v2 = "2"
    v3 = "3"


class CareMode(str, Enum):
    family = "family"
    live_in = "live_in"
    home_visit = "home_visit"
    day_care = "day_care"
    institution = "institution"


class PayoutType(str, Enum):
    annual = "annual"
    lump = "lump"


class Trigger(str, Enum):
    ltc_adl = "ltc_adl"            # 商业护理保险：ADL 丧失达约定项数
    ci_dementia = "ci_dementia"   # 重疾险：严重阿尔茨海默病/严重痴呆
    ltc_official = "ltc_official" # 以社保失能等级评估结论为准


# ---------------- 分块字段（§2 B 老人现状） ----------------
@dataclass
class ADL:  # 日常生活活动 10 项，每项 0–100 合计；分值照 C1 原文
    eat: int
    dress: int
    groom: int
    bowel: int
    bladder: int
    toilet: int
    walk: int
    transfer: int
    stairs: int
    bath: int


@dataclass
class Cog:  # 认知能力 4 项，每项 0–4，合计 0–16；照 C2 原文
    time: int
    person: int
    place: int
    memory: int


@dataclass
class Sense:  # 感知觉与沟通 3 项，每项 0–4，合计 0–12；照 C3 原文
    vision: int
    hearing: int
    comm: int


# ---------------- 商业保险（§2 D，最多 3 份） ----------------
@dataclass
class Policy:
    payout_type: PayoutType
    amount: float
    trigger: Trigger
    term_years: int
    wait_months: int
    # 按 trigger 补充的字段（可空）
    adl_n: Optional[int] = None          # ltc_adl：丧失项数
    persist_days: Optional[int] = None   # ltc_adl：持续天数（常见 180）
    min_level: Optional[int] = None      # ltc_official：最低等级


# ---------------- 主结构：一户家庭（§2 全部字段） ----------------
@dataclass
class Household:
    # A. 基本情况（必填）
    age: int
    sex: Sex
    city: City
    insured_type: InsuredType
    # B. 老人现状
    adl: ADL
    cog: Cog
    sense: Sense
    # C. 照护安排（必填）
    cognition: Cognition
    months_in_state: int
    care_mode: CareMode
    escalate_to_institution: bool
    caregiver_income_loss: float
    # D. 已有保障与家庭资金（必填）
    policies: List[Policy]
    subsidy_monthly: float
    family_funds: float
    family_monthly: float
    # —— 以下为“否/可空”字段，结构里必须预留位置 ——
    district: Optional[District] = None            # 仅 beijing 时填
    enroll_status: EnrollStatus = EnrollStatus.continuous
    lapse_years: Optional[int] = None              # 仅 lapsed 时填
    official_level: OfficialLevel = OfficialLevel.none
    official_valid_until: Optional[str] = None     # 结论书日期 YYYY-MM-DD
    cdr: Optional[Cdr] = None
    mmse: Optional[int] = None                     # 0–30，仅用于青岛失智初筛判断
    months_since_last_fail: Optional[int] = None
    dementia_unit: bool = False                    # 仅 institution 时填
    # 校验通过但需要提醒用户的事项（不阻断提交，例如“官方结论已过期”）
    warnings: List[str] = field(default_factory=list)


# ---------------- 引擎中间/输出结构 ----------------
@dataclass
class InitialState:  # map_initial_state 的返回（§3）
    grade: int            # 测算等级 0–5
    state: str           # L / M / S1 / S2
    cause: str           # cognitive / somatic
    eligible: bool       # 长护险资格
    use_official: bool   # 是否采用官方结论
    sev_level: Optional[int] = None   # 重度等级 3/4/5（非重度为 None）
    dementia_qualified: bool = False  # 是否按本市细则认定为重度失智（青岛）


# YearTable：run_deterministic 的返回，逐年明细（§5 第 2 块）。
# 用 list[dict] 表示，一行一年；列名与 §5 第 2 块一致。
YearTable = List[dict]

# Distribution：run_stochastic 的返回（§5），各项累计值的 P10/P50/P90。
Distribution = dict

# Params：load_params 的返回，合并国家基准与城市配置后的全部参数（§6）。
# 骨架阶段用 dict 承载；B5 可继续细化为 dataclass。
Params = dict

# 报告结构：build_report 的返回（§5），8 块 + 合规提示 + 运行留痕。
Report = dict


# ---------------- validate_input：真实现（§2 校验规则） ----------------
def validate_input(raw: dict):
    """
    页面提交的字典 -> (Household, []) 或 (None, [错误信息...])。
    按 §2 取值范围校验；并实现两条特殊规则：
      1) ADL=100 且 Cog=16 且 Sense=12，且 cognition 为 none/suspected -> 不进测算。
      2) official_level 已过期 -> 提示重新评估，改按录入的 17 项测算。
    """
    errors: List[str] = []

    # —— 取值范围校验（§2）——
    if not (60 <= int(raw.get("age", 0)) <= 100):
        errors.append("age 需在 60–100 之间")

    # ADL 每项只能取规定档位（2021 评估标准表 C1；分工表2）
    ADL_TIERS = {
        "walk": [0, 5, 10, 15], "transfer": [0, 5, 10, 15],
        "groom": [0, 5], "bath": [0, 5],
        "eat": [0, 5, 10], "dress": [0, 5, 10], "bowel": [0, 5, 10],
        "bladder": [0, 5, 10], "toilet": [0, 5, 10], "stairs": [0, 5, 10],
    }
    adl_fields = list(ADL_TIERS.keys())
    adl_sum = 0
    for f in adl_fields:
        v = int(raw.get(f, 0))
        if v not in ADL_TIERS[f]:
            errors.append(f"adl.{f} 只能取 {ADL_TIERS[f]} 分")
        adl_sum += v

    cog_fields = ["time", "person", "place", "memory"]
    cog_sum = sum(int(raw.get(f, 0)) for f in cog_fields)
    sense_fields = ["vision", "hearing", "comm"]
    sense_sum = sum(int(raw.get(f, 0)) for f in sense_fields)
    if not (0 <= cog_sum <= 16):
        errors.append("认知合计需在 0–16")
    if not (0 <= sense_sum <= 12):
        errors.append("感知觉合计需在 0–12")

    # MMSE 可空（Task5）：没做过则为 None；填了必须在 0–30
    mmse = raw.get("mmse")
    mmse_val = None
    if mmse not in (None, ""):
        try:
            mmse_val = int(mmse)
            if not (0 <= mmse_val <= 30):
                errors.append("mmse 需在 0–30 之间")
        except (TypeError, ValueError):
            errors.append("mmse 格式错误")

    # —— 特殊规则 1：满分但不进测算 ——
    if adl_sum == 100 and cog_sum == 16 and sense_sum == 12 \
       and raw.get("cognition") in (None, "none", "suspected"):
        errors.append("SPECIAL:NO_MODEL")  # 页面改为输出科普+就诊提示

    # —— 特殊规则 2：official_level 过期 ——
    # §2：结论已过期的要提示“需重新评估”，并改按录入项目测算（不阻断提交）
    warnings: List[str] = []
    ol = raw.get("official_level")
    vu = raw.get("official_valid_until")
    if ol not in (None, "none") and vu:
        try:
            if datetime.date.fromisoformat(str(vu)[:10]) < datetime.date.today():
                warnings.append("结论已过期，需重新评估：本次已按录入的 17 项测算，"
                                "不再采用原结论等级。")
        except (ValueError, TypeError):
            warnings.append("结论书日期格式无法识别，已按录入的 17 项测算。")

    if errors:
        return None, errors

    # —— 通过校验，组装 Household（嵌套结构）——
    h = Household(
        age=int(raw["age"]),
        sex=Sex(raw["sex"]),
        city=City(raw["city"]),
        district=District(raw["district"]) if raw.get("district") else None,
        insured_type=InsuredType(raw["insured_type"]),
        enroll_status=EnrollStatus(raw.get("enroll_status", "continuous")),
        lapse_years=raw.get("lapse_years"),
        official_level=OfficialLevel(raw.get("official_level", "none")),
        official_valid_until=raw.get("official_valid_until"),
        adl=ADL(**{f: int(raw[f]) for f in adl_fields}),
        cog=Cog(**{f: int(raw[f]) for f in cog_fields}),
        sense=Sense(**{f: int(raw[f]) for f in sense_fields}),
        cognition=Cognition(raw["cognition"]),
        cdr=Cdr(raw["cdr"]) if raw.get("cdr") else None,
        mmse=mmse_val,
        months_in_state=int(raw["months_in_state"]),
        months_since_last_fail=raw.get("months_since_last_fail"),
        care_mode=CareMode(raw["care_mode"]),
        dementia_unit=bool(raw.get("dementia_unit", False)),
        escalate_to_institution=bool(raw.get("escalate_to_institution", False)),
        caregiver_income_loss=float(raw.get("caregiver_income_loss", 0)),
        policies=[],  # 商业保险明细由页面逐项收集后填入
        subsidy_monthly=float(raw.get("subsidy_monthly", 0)),
        family_funds=float(raw.get("family_funds", 0)),
        family_monthly=float(raw.get("family_monthly", 0)),
    )
    h.warnings = warnings
    return h, []
