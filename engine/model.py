"""
engine/model.py —— §4 推移/成本/支付/缺口  +  load_params

load_params：A6 真实现（先读 national.yaml，再用 cities/<city>.yaml 覆盖）。
run_deterministic：A6 写假实现，返回 10 行 YearTable，列名与 §5 第 2 块一致。
"""
import os
import yaml

from engine.schema import Household, Params, YearTable

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")


def _load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_params(city: str, version: str = "v0") -> Params:
    """
    合并国家基准与城市配置后的全部政策参数（§6）。
    城市文件只写与 national 不同的项；city='national' 时不覆盖。
    """
    params: Params = _load_yaml(os.path.join(CONFIG_DIR, "national.yaml"))
    if city not in (None, "national"):
        city_path = os.path.join(CONFIG_DIR, "cities", f"{city}.yaml")
        if os.path.exists(city_path):
            params.update(_load_yaml(city_path))
    return params


def run_deterministic(h: Household, p: Params, T: int = 10) -> YearTable:
    # 占位，B5替换（§4、§5）
    # 返回 T 行逐年明细，列名严格照 §5 第 2 块：
    # 年份、存活概率、各状态概率、成本、长护险、商保、补贴、缺口、累计缺口、
    # 家庭资金余额、是否触限额（名义值/现值各一套）
    rows = []
    for t in range(1, T + 1):
        rows.append({
            "year": 2026 + t,
            "survival_prob": 1.0,
            "state_probs": {"L": 0.0, "M": 0.0, "S1": 0.5, "S2": 0.5, "D": 0.0},
            "cost": 100000.0,
            "ltc": 50000.0,
            "commercial": 10000.0,
            "subsidy": 2000.0,
            "gap": 38000.0,
            "cum_gap": 38000.0 * t,
            "family_balance": 200000.0,
            "hit_cap": False,
            "cost_pv": 100000.0,
            "cum_gap_pv": 38000.0 * t,
        })
    return rows
