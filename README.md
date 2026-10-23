# 护忆有方测算引擎（huyi-engine）

面向失智/失能老人家庭的长期照护费用与保障缺口测算工具。
本仓库当前为 **A6 技术骨架**：目录、数据结构、空函数、配置文件结构、页面外壳均已搭好，
公式与真实参数由后续任务（B5 填实现、A3/A5 填参数与价格）补充。

## 环境
- Python 3.11
- 安装依赖：`pip install -r requirements.txt`

## 本地运行
```bash
streamlit run app.py
```
浏览器打开提示的本地地址即可。录入一个样例家庭应能走到结果页，8 块内容以假数据显示。

## 目录
```
engine/      引擎代码（schema / mapping / model / simulate / report）
config/      配置文件（national.yaml + cities/*.yaml + settings/assessment/disease + cost/*.csv）
tests/       B6 手算对账案例（空壳）
app.py       Streamlit 四页页面外壳
```

## 约定（规格书§7）
- 代码里不准写死数字，所有参数从配置文件读取。
- 字段名只用规格书§2 的“代码名”，且只在 `engine/schema.py` 里定义一次。
- 页面只调用 7 个引擎函数，不在页面里做加减乘除。
