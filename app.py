"""
app.py —— Streamlit 四页页面（A6 第 5/10 件）

页面只调用 engine 的函数，只负责显示，绝不在页面里做加减乘除（规格书§7 + A6 注意）。
本地运行：  streamlit run app.py
"""
import pandas as pd
import streamlit as st

from engine.schema import (
    Household, ADL, Cog, Sense, Sex, City, InsuredType, Cognition,
    CareMode, Policy, PayoutType, Trigger, validate_input,
)
from engine.model import load_params, val
from engine.report import build_report


# ---------- 录入页用到的选项（照 §2 取值范围；标签文字待替换为 2021 标准原文） ----------
ADL_ITEMS = {
    "eat": "进食", "dress": "穿衣", "groom": "面部与口腔清洁", "bowel": "大便控制",
    "bladder": "小便控制", "toilet": "用厕", "walk": "平地行走", "transfer": "床椅转移",
    "stairs": "上下楼", "bath": "洗澡",
}
# ADL 各项目允许的档位（§2：walk/transfer 0/5/10/15；groom/bath 0/5；其余 0/5/10）
ADL_SCORES = {
    "eat": [0, 5, 10], "dress": [0, 5, 10], "groom": [0, 5], "bowel": [0, 5, 10],
    "bladder": [0, 5, 10], "toilet": [0, 5, 10], "walk": [0, 5, 10, 15],
    "transfer": [0, 5, 10, 15], "stairs": [0, 5, 10], "bath": [0, 5],
}
COG_ITEMS = {"time": "时间定向", "person": "人物定向", "place": "空间定向", "memory": "记忆力"}
SENSE_ITEMS = {"vision": "视力", "hearing": "听力", "comm": "沟通能力"}

CITY_LABEL = {"qingdao": "青岛", "chongqing": "重庆", "beijing": "北京", "national": "国家基准"}
INSURED_LABEL = {"employee": "职工", "resident": "居民", "none": "未参保"}
COG_LABEL = {"none": "无", "suspected": "疑似", "mild": "轻度", "moderate": "中度", "severe": "重度"}
MODE_LABEL = {"family": "家人照护", "live_in": "住家保姆", "home_visit": "居家上门护理",
              "day_care": "日间照料", "institution": "机构照护"}


# 敏感性参数的页面名称（不在页面上显示代码名和内部备注）
PARAM_CN = {"price_monthly": "照护价格档次", "p_MS": "中度转重度的年转移概率", "mu_S": "重度阶段死亡率",
            "rho": "长护险支付比例", "lambda": "年度支付限额", "duration_cap": "支付时长上限",
            "g": "照护价格年增长率", "r": "折现率"}


# ===================== 页面 1：说明与知情同意 =====================
def page_intro(tab):
    with tab:
        st.title("护忆有方 · 家庭照护费用与保障测算")
        st.markdown("""
        **用途**：帮助失智/失能老人家庭估算未来 5 年、10 年的照护花费，
        以及长护险和已有商业保险能付多少、家里还要自掏多少。

        **我们如何保护您**：
        - 不收集姓名、身份证号、手机号、详细住址；城市只精确到地级市。
        - 不保存您录入的内容：只在本次打开的页面里计算，不写入任何数据库，关闭页面即清空。
        - 不做医学诊断、不做失能等级评定；结果仅为财务测算与科普参考。
        - 不推销保险：页面不出现任何保险公司或保险产品名称，也不推荐购买。
        """)
        st.checkbox("我已阅读并理解上述说明", key="consent_intro")


# ===================== 页面 2：录入（分四组） =====================
def page_input(tab):
    with tab:
        st.title("录入")
        st.caption("家属可自行填写，约 5 分钟。带 * 为必填。")

        raw = {}
        # —— A. 基本情况 ——
        st.subheader("一、基本情况")
        raw["age"] = st.number_input("年龄*", 60, 100, 78)
        raw["sex"] = st.selectbox("性别*", ["male", "female"],
                                  format_func=lambda x: {"male": "男", "female": "女"}[x])
        raw["city"] = st.selectbox("所在城市*", list(CITY_LABEL),
                                   format_func=lambda x: CITY_LABEL[x])
        raw["district"] = st.selectbox("区县（仅北京时填）", [None, "shijingshan", "other"],
                                       format_func=lambda x: {None: "不适用", "shijingshan": "石景山区",
                                                              "other": "其他区"}[x])
        raw["insured_type"] = st.selectbox("长护险参保类别*", list(INSURED_LABEL),
                                           format_func=lambda x: INSURED_LABEL[x])
        raw["enroll_status"] = st.selectbox(
            "参保连续性", ["continuous", "new", "lapsed"],
            format_func=lambda x: {"continuous": "连续参保", "new": "新参保", "lapsed": "曾断保"}[x])
        if raw["enroll_status"] == "lapsed":
            raw["lapse_years"] = st.number_input("断保年数", 0, 20, 0)

        # —— B. 老人现状 ——
        st.subheader("二、老人现状（照 2021 年评估标准原文勾选）")
        st.markdown("**日常生活活动 10 项**（分值照 §2，标签文字待替换为标准原文）")
        c1, c2 = st.columns(2)
        for i, (k, label) in enumerate(ADL_ITEMS.items()):
            with (c1 if i % 2 == 0 else c2):
                raw[k] = st.selectbox(f"{label}（adl.{k}）", ADL_SCORES[k],
                                      format_func=lambda x: f"{x} 分")
        st.markdown("**认知能力 4 项**（每项 0–4，照 C2 原文）")
        c1, c2 = st.columns(2)
        for i, (k, label) in enumerate(COG_ITEMS.items()):
            with (c1 if i % 2 == 0 else c2):
                raw[k] = st.slider(f"{label}（cog.{k}）", 0, 4, 0)
        st.markdown("**感知觉与沟通 3 项**（每项 0–4，照 C3 原文）")
        c1, c2, c3 = st.columns(3)
        for i, (k, label) in enumerate(SENSE_ITEMS.items()):
            with (c1, c2, c3)[i]:
                raw[k] = st.slider(f"{label}（sense.{k}）", 0, 4, 0)

        # —— C. 照护安排 ——
        st.subheader("三、照护安排")
        raw["cognition"] = st.selectbox("门诊认知诊断*", list(COG_LABEL),
                                        format_func=lambda x: COG_LABEL[x])
        raw["cdr"] = st.selectbox("门诊量表结果 CDR", [None, "0.5", "1", "2", "3"],
                                  format_func=lambda x: "未做" if x is None else f"CDR {x}")
        # Task5：MMSE 可空——没做过就不填，不能默认 0 分
        did_mmse = st.checkbox("做过 MMSE 量表", value=False,
                               help="青岛对重度失智的认定要先看 MMSE 初筛分；没做过就留空")
        raw["mmse"] = st.number_input("门诊 MMSE 得分（0–30）", 0, 30, 0) if did_mmse else None
        raw["months_in_state"] = st.number_input("当前状况已持续（月）*", 0, 120, 24)
        raw["months_since_last_fail"] = st.number_input(
            "上次评估未通过距今（月，可空）", 0, 120, 0) or None
        raw["official_level"] = st.selectbox(
            "已有官方失能等级结论（有效期内）", ["none", "0", "1", "2", "3", "4", "5"],
            format_func=lambda x: "没有" if x == "none" else f"{x} 级")
        if raw["official_level"] != "none":
            raw["official_valid_until"] = st.date_input("结论书出具日期").isoformat()
        raw["care_mode"] = st.selectbox("当前照护方式*", list(MODE_LABEL),
                                        format_func=lambda x: MODE_LABEL[x])
        raw["dementia_unit"] = False
        if raw["care_mode"] == "institution":
            raw["dementia_unit"] = st.checkbox("机构是否为认知症专区", value=False)
        raw["escalate_to_institution"] = st.checkbox("病情加重到重度时是否考虑转入机构", value=False)
        raw["caregiver_income_loss"] = st.number_input("主要照护家属因照护减少的收入（元/月）",
                                                       0, 50000, 0)

        # —— D. 已有保障与家庭资金 ——
        st.subheader("四、已有保障与家庭资金")
        st.caption("商业保险最多录 3 份；没有就都填 0。")
        policies = []
        for i in range(1, 4):
            with st.expander(f"商业保险第 {i} 份", expanded=(i == 1)):
                amt = st.number_input(f"保额/年给付（元）· 第 {i} 份", 0, 5000000, 0, key=f"amt{i}")
                if amt:
                    pt = st.selectbox("给付方式", ["lump", "annual"], key=f"pt{i}",
                                      format_func=lambda x: {"lump": "一次性给付", "annual": "按年给付"}[x])
                    tg = st.selectbox("触发条件", ["ci_dementia", "ltc_adl", "ltc_official"],
                                      key=f"tg{i}",
                                      format_func=lambda x: {"ci_dementia": "重疾险：严重阿尔茨海默病/严重痴呆",
                                                             "ltc_adl": "护理险：ADL 丧失达约定项数",
                                                             "ltc_official": "护理险：以社保失能等级为准"}[x])
                    ty = st.number_input("给付年限（按年给付时填）", 0, 20, 1, key=f"ty{i}")
                    wm = st.number_input("等待期（月）", 0, 24, 0, key=f"wm{i}")
                    extra = {}
                    if tg == "ltc_adl":
                        extra["adl_n"] = st.number_input("约定丧失项数", 1, 10, 3, key=f"an{i}")
                    if tg == "ltc_official":
                        extra["min_level"] = st.number_input("最低等级", 1, 5, 3, key=f"ml{i}")
                    policies.append(Policy(payout_type=PayoutType(pt), amount=float(amt),
                                           trigger=Trigger(tg), term_years=int(ty),
                                           wait_months=int(wm), **extra))

        raw["subsidy_monthly"] = st.number_input("已领取政府补贴（元/月）", 0, 10000, 0)
        raw["family_funds"] = st.number_input("可用于照护的家庭存款（元）", 0, 10000000, 0)
        raw["family_monthly"] = st.number_input("每月可持续投入照护的收入（元/月）", 0, 200000, 0)

        if st.button("提交并校验", type="primary"):
            h, errors = validate_input(raw)
            if errors:
                for e in errors:
                    if e == "SPECIAL:NO_MODEL":
                        st.warning("按现在勾选的情况，老人日常活动、认知、感知觉都是完好，"
                                   "不属于失能测算范围。建议先到记忆门诊做一次评估；"
                                   "如果只是担心，可以先看「说明」页的科普内容。")
                    else:
                        st.error(e)
            else:
                h.policies = policies
                st.session_state["household"] = h
                st.success("校验通过，已暂存。请到「合规提示」页勾选后再看结果。")
                for w in getattr(h, "warnings", []):
                    st.warning(w)


# ===================== 页面 3：合规提示 =====================
def page_compliance(tab):
    with tab:
        st.title("合规提示")
        st.warning("""
        **本工具不诊断、不评级、不卖保险。**
        - 不做医学诊断：是否失智、属于哪一期，请到记忆门诊就诊。
        - 不做失能等级评定：长护险待遇以定点评估机构的结论为准。
        - 不推销保险：结果只说明已有保障够不够，不推荐任何保险公司或产品。

        结果仅为财务测算和科普参考，实际待遇以当地医保部门规定为准。
        """)
        st.checkbox("我已知悉", key="ack_compliance")


# ===================== 页面 4：结果（§5 八块） =====================
def page_result(tab):
    with tab:
        st.title("测算结果")
        if not st.session_state.get("ack_compliance"):
            st.info("请先到「合规提示」页勾选“我已知晓”后再查看结果。")
            return
        # Task6：没录入就不显示样例数据，避免把样例当成自己的结果
        h = st.session_state.get("household")
        if h is None:
            st.info("还没有录入信息。请先到「录入」页填写并提交，再回到本页查看结果。")
            return

        p = load_params(h.city.value)
        # 分工任务 10：北京石景山区只显示提示，不算钱（试点细则未配置，规格书“北京分区本次不做”）
        if h.city.value == "beijing" and h.district is not None and h.district.value == "shijingshan":
            st.info(val(p, "result_notice_shijingshan")
                    or "石景山区为国家长护险试点区，待遇以石景山区医保局规定为准。")
            st.caption("本工具暂未配置石景山区试点细则，因此不对石景山区给出金额测算。")
            return
        with st.spinner("正在按您的城市政策逐年测算…"):
            report = build_report(h, p)

        # ---------- 1. 摘要 ----------
        st.header("1. 摘要")
        s = report["summary"]
        st.markdown(f"**{s['text_5']}**")
        st.markdown(f"**{s['text_10']}**")
        st.caption("区间来自成本表的低、中、高三档价格，三档各算一遍；中间情形即按中档价格测算。")
        st.dataframe(pd.DataFrame([
            {"期限": "5 年", "低": f"{s['band_5']['low']:,.0f}",
             "中": f"{s['band_5']['mid']:,.0f}", "高": f"{s['band_5']['high']:,.0f}"},
            {"期限": "10 年", "低": f"{s['band_10']['low']:,.0f}",
             "中": f"{s['band_10']['mid']:,.0f}", "高": f"{s['band_10']['high']:,.0f}"},
        ]), hide_index=True, use_container_width=True)

        st.markdown("**随机模式分布（病程路径抽样，另作参考）**")
        stc = s.get("stochastic") or {}
        picks = [("累计照护成本", "cost_10"), ("长护险支付", "ltc_10"),
                 ("商业保险给付", "ins_10"), ("政府补贴", "subsidy_10"),
                 ("家庭缺口", "gap_10")]
        st.dataframe(pd.DataFrame([
            {"项目": name,
             "5 年 P10": f"{(s.get('stochastic', {}).get('gap_5') or {}).get('P10', 0):,.0f}"
             if name == "家庭缺口" else "—",
             "10 年 P10": f"{(stc.get(key) or {}).get('P10', 0):,.0f}",
             "10 年 P50": f"{(stc.get(key) or {}).get('P50', 0):,.0f}",
             "10 年 P90": f"{(stc.get(key) or {}).get('P90', 0):,.0f}"}
            for name, key in picks]), hide_index=True, use_container_width=True)
        st.caption(f"随机模式 {stc.get('n')} 次，随机种子 {stc.get('seed')}"
                   "（同输入每次结果一致，可现场复算）。金额已取整到百元。")
        if s.get("low_confidence"):
            st.warning("所选照护方式的价格样本不足，上面的数字仅供内部参考，不作为对外口径。")

        # ---------- 2. 逐年明细 ----------
        st.header("2. 逐年明细（确定性模式）")
        # 年份按文字显示，避免表格把 2026 显示成“2,026”
        st.dataframe(pd.DataFrame(report["year_table"]).astype({"年份": str}), hide_index=True,
                     use_container_width=True)
        st.caption("这一张表就是手算对账的对象：列名、口径与 B6 校验表一致。"
                   "成本按各状态概率加权，故为期望值；长护险只在“重度持续”阶段支付。")

        # ---------- 3. 分情形说明 ----------
        st.header("3. 分情形说明")
        for sc in report["scenarios"]:
            st.markdown(f"- {sc['text']}")

        # ---------- 4. 敏感性 ----------
        st.header("4. 敏感性")
        st.write(report["sensitivity"]["text"])
        top3 = report["sensitivity"]["top3"]
        if top3:
            st.dataframe(pd.DataFrame(
                [{"参数": PARAM_CN.get(t["key"], t["key"]), "低值情景 10 年缺口": f"{t['pv10_low']:,.0f}",
                  "高值情景 10 年缺口": f"{t['pv10_high']:,.0f}",
                  "摆动幅度": f"{t['swing']:,.0f}"} for t in top3]),
                hide_index=True, use_container_width=True)
        else:
            st.info("敏感性参数区间尚未补齐，本块暂不展示。")
        if report["sensitivity"]["skipped"]:
            st.caption("以下参数的区间尚未确定，暂未纳入敏感性分析：" + "、".join(
                PARAM_CN.get(d["key"], d["key"]) for d in report["sensitivity"]["skipped"]) + "。")

        # ---------- 5. 失智纳保情景 ----------
        dem = report["dementia_scenario"]
        if dem:
            st.header("5. 失智纳保情景")
            st.write(dem["text"])

        # ---------- 5b. 政策对照情景（现行无待遇的城市） ----------
        psc = report.get("policy_scenario")
        if psc:
            st.header("5. 政策对照情景")
            st.write(psc["text"])
            st.caption("对照情景只是假设，不是现行政策；实际待遇以当地医保局规定为准。")

        # ---------- 6. 保障组合区间 ----------
        st.header("6. 保障组合区间")
        st.write(report["combo"]["text"])
        st.caption(report["combo"]["note"])

        # ---------- 7. 待遇时间线 ----------
        st.header("7. 待遇时间线")
        st.write(report["timeline"])

        # ---------- 8. 限额提示 ----------
        st.header("8. 限额提示")
        st.write(report["cap_note"])

        if report["data_notes"]:
            st.divider()
            st.subheader("数据口径提示")
            for n in report["data_notes"]:
                st.caption("· " + n)

        st.divider()
        st.caption("合规提示：" + report["compliance"])
        st.caption("运行留痕：" + str(report["run_log"]))


# ===================== 入口 =====================
def main():
    st.set_page_config(page_title="护忆有方", layout="wide")
    tabs = st.tabs(["说明与知情同意", "录入", "合规提示", "结果"])
    page_intro(tabs[0])
    page_input(tabs[1])
    page_compliance(tabs[2])
    page_result(tabs[3])


if __name__ == "__main__":
    main()
