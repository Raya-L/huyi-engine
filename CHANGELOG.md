# Changelog

## v0.1.5 (2026-11-02)  联调修正

### 修正（都是联调时才暴露的口径问题）
- **ADL 计分方向**：2021 标准里分越高越好（合计 100 = 完好），原实现把“失能判断”写反了。
  `_adl_lost_n()` 改为只取六项基本活动（穿衣/移动/行动/如厕/进食/洗澡）、以最低档 0 分判为丧失，
  并保留 `mode="non_self"` 的敏感性口径。
- **成本低/中/高三档**：青岛部分格子只给了合计价的低/高（N、O 三档相同），直接用 N_low/O_low
  会让三档算出同一个数。新增 `_cost_amounts()`：N/O 分列有变化时用 N+O；否则用 `total_*`
  并按中档 N 占比拆出护理费。中档结果与 B6 手算一致，不受影响。
- **摘要区间文案**：三档同值时不再输出“约 X 万至 X 万元”。
- **保障组合区间**与摘要改为同一口径（成本表低/中/高档确定性现值），不再一处用随机 P50、一处用确定性。
- **分档空档兜底**：合计分落在两档之间时归入最近一档，不再一律当成“重度”。

### 新增
- `report.py::_policy_scenario()`：现行无待遇的城市（北京），按配置里 `status=情景设定` 的
  对照政策段再算一次，结果页给出“现行 10 年缺口 X 万元 / 2029 年起付约 Y 万元”。
- 结果页补“政策对照情景”块；摘要页改为三档区间表 + 随机模式 P10/P50/P90 表；
  样本不足时显著提示“不作为对外口径”。
- 北京提示按区县分流：石景山走 `result_notice_shijingshan`，其他区走 `not_found_notice`。
- `validate_input` 补“官方结论过期”提示（§2）：过期不阻断提交，只在页面提醒
  “已按录入的 17 项重新测算”，存于 `Household.warnings`。
- 新增 `tests/test_pages_smoke.py`：用一个极简 streamlit 替身跑真实 `app.py` 的四个页面函数，
  不装 streamlit 也能在 CI 里抓“报告结构改了、页面还在读旧键”这类错误。共 19 个测试。

## v0.1.4 (2026-11-02)  A6 引擎实现 + 配置接入 v0.1.4

### 配置
- 全线替换为 `config_v0.1.4`（national + 青岛/重庆/北京），旧 v0 配置删除。
- `disease.yaml`：补认知主因（p_LM 0.26 / p_MS 0.31 / μ_L 0.10 / μ_M 0.15 / μ_S 0.37）
  与躯体主因（占位）两套，逐项带 source。
- `settings.yaml`：g 0.05、r 0.017；新增 `cost_fallback`（成本缺样本时的回退口径）、
  `sensitivity`（8 个参数的摆动区间，缺区间的显式标 null + 待补原因）。
- `cost/{qingdao,chongqing,beijing}.csv`：由 `cost_draft.csv` 按城市拆分，保留原列。
- `requirements.txt`：删掉首行 `python==3.11`（Python 版本在部署平台选），
  另出 `requirements-dev.txt` 放 pytest。

### 引擎（engine/）
- `model.py`
  - `load_params`：改为递归 deep-merge（标量参数节点整体替换、结构化字典逐层合并），
    再合并 settings；新增 `val()` 统一解包 `{value, source}`。
  - 新增 `settlement_for()`：按 `settlement_periods` 的 from/until 取当年结算方式
    （fixed_amount / cap_then_rho / rho_on_eligible_cost / none），标注“情景设定”的
    对照段不参与基准；无匹配返回 none（重庆居民 2027 年前、北京现行）。
  - `rho_for()`：去掉写死的 0.75/0.80，改读 `before_2027.model_default` 与
    `transition_end_year` → `post_transition.scenario_B`。
  - `R_d_for()`：兼容青岛分档列表与重庆 severe 结构；按失智/等级取标准。
  - `_simulate()`：逐年限额 L_t = λ×人均可支配收入、触及限额置 `hit_cap`；
    补政府补贴 S_t、存活概率、家庭资金余额、耗尽年、家属误工机会成本（单列不计入缺口）；
    商保支持 lump/annual × ci_dementia/ltc_adl/ltc_official，含等待期与给付年限。
  - `run_deterministic()` / `run_deterministic_full()`：第 1 年 = 2026（2025+t）。
- `mapping.py`：按 §3 三步实现；0 级不进测算、1 级 L、2 级 M、3–5 级按
  `persist_months + process_months` 分 S1/S2；`months_since_last_fail < reapply_months`
  强制按 S1；官方结论优先；青岛重度失智认定（MMSE ≤ 9 按五级）。
- `simulate.py`
  - `run_stochastic()`：按转移矩阵抽路径（默认 2000 次，种子只从配置读），
    输出 5 年/10 年 × 成本/长护险/商保/补贴/缺口 的 P10/P50/P90。
  - `run_sensitivity()`：8 个参数逐个摆低/高值，输出 10 年缺口现值变化并排序；
    区间缺失的参数跳过并回报原因，不猜数。
- `report.py`：`build_report()` 输出 §5 八块 + 合规提示 + 运行留痕，金额取整到百元。

### 页面（app.py）
- 录入页：MMSE 改为“做过才填”（可空）；ADL 按标准档位下拉；补商业保险（最多 3 份）、
  官方失能等级、结论书日期。
- 结果页：没录入时不再显示样例家庭，改为提示先去录入页。
- 结果页按 §5 八块渲染（含逐年明细表、敏感性表、数据口径提示、运行留痕）。

### 校验
- `tests/test_cases.py` + `tests/data/handcalc_expected.json`：
  B6 三个手算案例的逐年缺口、长护险给付、5/10 年现值全部 0.0000% 误差通过；
  另加“触及年度限额”单点案例（ρ×12×3950 = 33180 > 0.5×43377 → 封顶 21688.5 且 hit_cap）。
- 引擎版本号：v0.1.0

## v0.1 (2026-10-23)  A6 技术骨架
- 建立仓库目录（规格书§7）。
- `engine/schema.py`：24 个输入字段（Household）+ Params / InitialState / YearTable / Distribution / 报告结构。
- 7 个函数：load_params、validate_input 真实现；其余 5 个写假实现（注释“占位，B5替换”）。
- 配置文件结构：national.yaml、cities/{qingdao,chongqing,beijing}.yaml、settings.yaml、assessment.yaml、disease.yaml（空）、cost/*.csv（表头）。
- app.py：Streamlit 四页外壳（说明/录入/合规提示/结果 8 块假数据）。
- 引擎版本号：v0.1
