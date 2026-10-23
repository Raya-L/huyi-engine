"""
engine/simulate.py —— 随机模式与敏感性（§4）

run_stochastic / run_sensitivity：A6 写假实现，返回格式正确的 Distribution。
B5 再填入蒙特卡洛抽样与参数摆动逻辑。
"""
from engine.schema import Household, Params, Distribution


def run_stochastic(h: Household, p: Params, T: int = 10, n: int = 2000, seed: int = 20261025) -> Distribution:
    # 占位，B5替换（§4）
    return {
        "cost_pv": {"P10": 300000.0, "P50": 500000.0, "P90": 800000.0},
        "ltc_pv": {"P10": 100000.0, "P50": 200000.0, "P90": 350000.0},
        "gap_pv": {"P10": 100000.0, "P50": 250000.0, "P90": 450000.0},
    }


def run_sensitivity(h: Household, p: Params, T: int = 10, keys: list = None) -> dict:
    # 占位，B5替换（§5 第 4 块）
    # 返回每个参数低/高值对应的 10 年缺口现值变化，按影响排序
    keys = keys or ["g", "r", "lambda", "rho_employee"]
    return {k: {"low": 200000.0, "high": 300000.0} for k in keys}
