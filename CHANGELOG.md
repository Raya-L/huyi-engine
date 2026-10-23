# Changelog

## v0.1 (2026-10-23)  A6 技术骨架
- 建立仓库目录（规格书§7）。
- `engine/schema.py`：24 个输入字段（Household）+ Params / InitialState / YearTable / Distribution / 报告结构。
- 7 个函数：load_params、validate_input 真实现；其余 5 个写假实现（注释“占位，B5替换”）。
- 配置文件结构：national.yaml、cities/{qingdao,chongqing,beijing}.yaml、settings.yaml、assessment.yaml、disease.yaml（空）、cost/*.csv（表头）。
- app.py：Streamlit 四页外壳（说明/录入/合规提示/结果 8 块假数据）。
- 引擎版本号：v0.1
