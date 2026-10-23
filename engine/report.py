"""
engine/report.py —— §5 组装输出

build_report：A6 写假实现，返回 8 块内容 + 合规提示 + 运行留痕的字典。
B5 再接入真实计算。页面只负责把这个字典渲染出来。
"""
import hashlib
import datetime
from engine.schema import Household, Params, Report


def build_report(h: Household, p: Params) -> Report:
    # 占位，B5替换（§5）
    report: Report = {
        # 1. 摘要
        "summary": "约 X 万至 Y 万元，中间情形约 Z 万元（5 年/10 年两档）",
        # 2. 逐年明细（确定性模式）
        "year_table": [],   # 由 run_deterministic 结果填入
        # 3. 分情形说明
        "scenarios": "若按中度情形测算，每年照护费约 X 元，长护险约付 Y 元；重度按 3/4/5 级各一句",
        # 4. 敏感性
        "sensitivity": "8 个参数逐个摆动，取影响最大的 3 个画龙卷风图",
        # 5. 失智纳保情景
        "dementia_scenario": "若本市对重度失智单独认定，10 年缺口约减少 X 元",
        # 6. 保障组合区间
        "combo": "需补足额 = 10 年缺口(P50/P90) − 家庭可用资金现值；换算为年给付 A 至 B 元",
        # 7. 待遇时间线
        "timeline": "若今天提出申请，按现行规定最早约 X 年 X 月起可享受待遇；结论到期前需重新评估",
        # 8. 限额提示
        "cap_note": "这一年长护险已按年度最高限额支付，超出部分需自付",
        # 每次运行附带两样东西：
        "compliance": ("本工具不做医学诊断，也不做失能等级评定，结果仅为财务测算和科普参考。"
                       "请到记忆门诊就诊，到定点评估机构申请评估。"),
        "run_log": {
            "input_hash": hashlib.md5(str(h).encode()).hexdigest()[:8],
            "params_version": p.get("version", "v0"),
            "engine_version": "v0.1",
            "seed": 20261025,
            "run_time": datetime.datetime.now().isoformat(timespec="seconds"),
        },
    }
    return report
