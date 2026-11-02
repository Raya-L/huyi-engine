# 护忆有方测算引擎（huyi-engine）

面向失智/失能老人家庭的长期照护费用与保障缺口测算工具（「互联网+」参赛项目）。

引擎已实现到 **v0.1.5**：5 状态推移、照护成本、长护险支付、缺口与折现、随机模式、
敏感性分析、§5 八块报告全部跑通；B6 三个手算案例对账误差 **0.0000%**。

## 环境
- Python 3.11（部署时在 Streamlit Cloud 的 Advanced settings 里选）
- 安装依赖：`pip install -r requirements.txt`
- 跑测试：`pip install -r requirements-dev.txt && pytest -q`

## 本地运行
```bash
streamlit run app.py
```
页面顺序：说明与知情同意 → 录入 → 合规提示（必须勾选）→ 结果（§5 八块）。

## 目录
```
engine/
  schema.py     24 个输入字段、输出结构与 validate_input
  mapping.py    §3 三分级 → 表3组合法 → 初始状态 / 主因 / 资格
  model.py      §4 推移、成本、支付、缺口 + load_params / settlement_for
  simulate.py   §4 随机模式（按转移矩阵抽路径）+ §5 敏感性
  report.py     §5 八块报告 + 合规提示 + 运行留痕
config/         national.yaml + cities/*.yaml + settings/assessment/disease + cost/*.csv
tests/          B6 手算对账（data/handcalc_expected.json）+ 限额单点 + 页面冒烟
app.py          Streamlit 四页页面
wireframes.html 页面线框（评审用）
```

## 校验口径
`pytest -q` 一共 19 个测试：

1. **B6 手算对账**：青岛职工认知中度、重庆职工认知重度、青岛居民重度（含 10 万重疾险）
   三个案例，逐年缺口与长护险给付、5 年/10 年缺口现值，与手算表相对误差 < 1%（实测 0.0000%）。
2. **限额单点**：月护理费 3950、ρ=0.70、年度限额 0.5×43377 → 长护险封顶 21688.5 且
   置 `hit_cap`；把 ρ 降到 0.30 时不封顶。
3. **录入校验**：MMSE 可空、ADL 只接受标准档位、满分不进测算。
4. **报告层**：成本三档区间不塌成一个点、北京对照情景缺口小于现行、石景山提示分流。
5. **页面冒烟**：`tests/test_pages_smoke.py` 用极简 streamlit 替身跑四个页面函数，
   不装 streamlit 也能验证“报告结构 ↔ 页面取值”一致。

## 约定（规格书§7）
- 代码里不准写死数字，所有参数从配置文件读取（连随机种子、成本回退口径都在配置里）。
- 字段名只用规格书§2 的“代码名”，且只在 `engine/schema.py` 里定义一次。
- 页面只调用引擎函数，不在页面里做加减乘除。
- 金额展示一律取整到百元（§5）。
