"""
engine/mapping.py —— §3 初始状态与失能资格

map_initial_state：A6 写假实现，只返回格式正确的 InitialState。
B5 再填入真实的分档与组合逻辑（2021 年标准表 2/表 3）。
"""
from engine.schema import Household, InitialState


def map_initial_state(h: Household) -> InitialState:
    # 占位，B5替换（§3）
    # TODO(B5): 按 ADL/Cog/Sense 三分级组合，得到 grade(0–5)、state(L/M/S1/S2)、cause、eligible
    return InitialState(
        grade=3,
        state="S1",
        cause="cognitive",
        eligible=True,
        use_official=False,
    )
