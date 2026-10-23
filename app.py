"""
app.py —— Streamlit 四页页面外壳（A6 第 5 件）

页面只调用 engine 的 7 个函数，只负责显示，绝不在页面里做加减乘除（规格书§7 + A6 注意）。
本地运行：streamlit run app.py
"""
import streamlit as st

from engine.schema import (
    Household, ADL, Cog, Sense, Sex, City, InsuredType, Cognition, CareMode, validate_input,
)
from engine.model import load_params, run_deterministic
from engine.mapping import map_initial_state
from engine.simulate import run_stochastic, run_sensitivity
from engine.report import build_report


# ---------- 录入页用到的选项（照 §2 取值范围；标签文字待替换为 2021 标准原文） ----------
# ADL 各项目分值档位（§2：walk/transfer 0/5/10/15；groom/bath 0/5；其余 0/5/10）
ADL_ITEMS = {
    "eat": "进食", "dress": "穿衣", "groom": "面部与口腔清洁", "bowel": "大便控制",
    "bladder": "小便控制", "toilet": "用厕", "walk": "平地行走", "transfer": "床椅转移",
    "stairs": "上下楼", "bath": "洗澡",
}
ADL_SCORES = {
    "eat": [0, 5, 10], "dress": [0, 5, 10], "groom": [0, 5], "bowel": [0, 5, 10],
    "bladder": [0, 5, 10], "toilet": [0, 5, 10], "walk": [0, 5, 10, 15],
    "transfer": [0, 5, 10, 15], "stairs": [0, 5, 10], "bath": [0, 5],
}
COG_ITEMS = {"time": "时间定向", "person": "人物定向", "place": "空间定向", "memory": "记忆力"}
SENSE_ITEMS = {"vision": "视力", "hearing": "听力", "comm": "沟通能力"}


def build_sample_household() -> Household:
    """结果页在没有录入数据时用的样例家庭。"""
    return Household(
        age=78, sex=Sex.female, city=City.qingdao, insured_type=InsuredType.employee,
        adl=ADL(10, 15, 5, 10, 10, 10, 15, 15, 10, 5),
        cog=Cog(2, 2, 1, 1), sense=Sense(2, 3, 2),
        cognition=Cognition.severe, months_in_state=24, care_mode=CareMode.institution,
        escalate_to_institution=True, caregiver_income_loss=3000.0,
        policies=[], subsidy_monthly=200.0, family_funds=300000.0, family_monthly=8000.0,
        dementia_unit=True,
    )


# ===================== 页面 1：说明与知情同意 =====================
def page_intro(tab):
    with tab:
        st.title("护忆有方 · 家庭照护费用与保障测算")
        st.markdown("""
        **用途**：帮助失智/失能老人家庭估算未来 5 年、10 年的照护花费，
        以及长护险和已有商业保险能付多少、家里还要自掏多少。

        **我们如何保护您**：
        - 不收集姓名、身份证号、手机号、详细住址；城市只精确到地级市。
        - 不保存您录入的内容（本工具在您本地浏览器中运行，提交即算，不留底）。
        - 不做医学诊断、不做失能等级评定；结果仅为财务测算与科普参考。
        """)
        st.checkbox("我已阅读并理解上述说明", key="consent_intro")


# ===================== 页面 2：录入（分四组） =====================
def page_input(tab):
    with tab:
        st.title("录入")
        st.caption("家属在门诊/保险/社区工作人员陪同下 5 分钟内填完。带 * 为必填。")

        raw = {}
        # —— A. 基本情况 ——
        st.subheader("一、基本情况")
        raw["age"] = st.number_input("年龄*", 60, 100, 78)
        raw["sex"] = st.selectbox("性别*", ["male", "female"])
        raw["city"] = st.selectbox("所在城市*", ["qingdao", "chongqing", "beijing", "national"])
        raw["district"] = st.selectbox("区县（仅北京时填）", [None, "shijingshan", "other"])
        raw["insured_type"] = st.selectbox("长护险参保类别*", ["employee", "resident", "none"])
        raw["enroll_status"] = st.selectbox("参保连续性", ["continuous", "new", "lapsed"])
        if raw["enroll_status"] == "lapsed":
            raw["lapse_years"] = st.number_input("断保年数", 0, 20, 0)

        # —— B. 老人现状 ——
        st.subheader("二、老人现状（照 2021 年评估标准原文勾选）")
        st.markdown("**日常生活活动 10 项**（分值照 §2，标签文字待替换为标准原文）")
        for k, label in ADL_ITEMS.items():
            raw[k] = st.selectbox(f"{label}（adl.{k}）", ADL_SCORES[k],
                                  format_func=lambda x: f"{x} 分")
        st.markdown("**认知能力 4 项**（每项 0–4，照 C2 原文）")
        for k, label in COG_ITEMS.items():
            raw[k] = st.slider(f"{label}（cog.{k}）", 0, 4, 0)
        st.markdown("**感知觉与沟通 3 项**（每项 0–4，照 C3 原文）")
        for k, label in SENSE_ITEMS.items():
            raw[k] = st.slider(f"{label}（sense.{k}）", 0, 4, 0)

        # —— C. 照护安排 ——
        st.subheader("三、照护安排")
        raw["cognition"] = st.selectbox("门诊认知诊断*",
                                        ["none", "suspected", "mild", "moderate", "severe"])
        raw["cdr"] = st.selectbox("门诊量表结果 CDR", [None, "0.5", "1", "2", "3"])
        raw["mmse"] = st.number_input("门诊 MMSE 得分（0–30，可空）", 0, 30, 0)
        raw["months_in_state"] = st.number_input("当前状况已持续（月）*", 0, 120, 24)
        raw["months_since_last_fail"] = st.number_input("上次评估未通过距今（月，可空）", 0, 120, 0)
        raw["care_mode"] = st.selectbox("当前照护方式*",
                                        ["family", "live_in", "home_visit", "day_care", "institution"])
        if raw["care_mode"] == "institution":
            raw["dementia_unit"] = st.checkbox("机构是否为认知症专区", value=False)
        raw["escalate_to_institution"] = st.checkbox("病情加重到重度时是否考虑转入机构", value=False)
        raw["caregiver_income_loss"] = st.number_input("主要照护家属因照护减少的收入（元/月）", 0, 50000, 0)

        # —— D. 已有保障与家庭资金 ——
        st.subheader("四、已有保障与家庭资金")
        raw["subsidy_monthly"] = st.number_input("已领取政府补贴（元/月）", 0, 10000, 0)
        raw["family_funds"] = st.number_input("可用于照护的家庭存款（元）", 0, 10000000, 0)
        raw["family_monthly"] = st.number_input("每月可持续投入照护的收入（元/月）", 0, 200000, 0)

        if st.button("提交并校验"):
            h, errors = validate_input(raw)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                st.session_state["household"] = h
                st.success("校验通过，已暂存。请到「结果」页查看（需先完成「合规提示」）。")


# ===================== 页面 3：合规提示 =====================
def page_compliance(tab):
    with tab:
        st.title("合规提示")
        st.warning("""
        **本工具不做医学诊断，也不做失能等级评定，结果仅为财务测算和科普参考。**
        请到记忆门诊就诊，到定点评估机构申请评估。
        """)
        st.checkbox("我已知晓上述提示，并同意继续查看结果", key="ack_compliance")


# ===================== 页面 4：结果（§5 八块，假数据） =====================
def page_result(tab):
    with tab:
        st.title("测算结果")
        if not st.session_state.get("ack_compliance"):
            st.info("请先到「合规提示」页勾选“我已知晓”后再查看结果。")
            return

        h = st.session_state.get("household") or build_sample_household()
        p = load_params(h.city.value)
        report = build_report(h, p)

        st.header("1. 摘要")
        st.write(report["summary"])
        st.header("2. 逐年明细")
        st.write("（确定性模式逐年表，由 run_deterministic 结果渲染）")
        st.header("3. 分情形说明")
        st.write(report["scenarios"])
        st.header("4. 敏感性")
        st.write(report["sensitivity"])
        st.header("5. 失智纳保情景")
        st.write(report["dementia_scenario"])
        st.header("6. 保障组合区间")
        st.write(report["combo"])
        st.header("7. 待遇时间线")
        st.write(report["timeline"])
        st.header("8. 限额提示")
        st.write(report["cap_note"])

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
