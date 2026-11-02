"""
tests/test_pages_smoke.py —— 页面冒烟（不需要装 streamlit）

用一个极简的 streamlit 替身跑真实 app.py 的四个页面函数，
专抓“报告结构改了但页面还在读旧键”这类 KeyError。
（开发环境只装了引擎依赖，所以用替身；线上仍跑真 streamlit。）
"""
import sys
import types
import datetime

import pytest


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, k):
        return lambda *a, **kw: None


class _SS(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


@pytest.fixture(scope="module")
def app_module():
    st = types.ModuleType("streamlit")
    st.session_state = _SS()
    st.tabs = lambda labels: [_Ctx() for _ in labels]
    st.columns = lambda spec: [_Ctx() for _ in (range(spec) if isinstance(spec, int) else spec)]
    st.spinner = lambda *a, **kw: _Ctx()
    st.expander = lambda *a, **kw: _Ctx()
    st.button = lambda *a, **kw: False
    st.checkbox = lambda *a, **kw: False
    st.selectbox = lambda label, options, **kw: (options[0] if options else None)
    st.number_input = lambda *a, **kw: 0
    st.slider = lambda *a, **kw: 0
    st.date_input = lambda *a, **kw: datetime.date(2026, 1, 1)
    for name in ("title", "header", "subheader", "markdown", "caption", "write",
                 "info", "warning", "error", "success", "dataframe", "divider",
                 "set_page_config"):
        setattr(st, name, lambda *a, **kw: None)
    sys.modules["streamlit"] = st

    import app
    return app


def _raw(**kw):
    base = dict(age=82, sex="male", city="qingdao", insured_type="employee",
                cognition="severe", months_in_state=30, care_mode="institution",
                dementia_unit=True, enroll_status="continuous", official_level="none",
                family_funds=0, family_monthly=0, subsidy_monthly=0,
                caregiver_income_loss=0,
                eat=0, dress=0, groom=0, bowel=0, bladder=0, toilet=0,
                walk=0, transfer=0, stairs=0, bath=0,
                time=0, person=0, place=0, memory=0, vision=0, hearing=0, comm=0)
    base.update(kw)
    return base


@pytest.mark.parametrize("kw", [
    dict(mmse=10, cdr="3"),                                   # 青岛 职工 认知症专区
    dict(city="chongqing", dementia_unit=False),               # 重庆 职工
    dict(city="beijing", dementia_unit=False),                 # 北京（走对照情景）
    dict(insured_type="resident", mmse=10),                    # 青岛 居民
])
def test_page_result_renders(app_module, kw):
    from engine.schema import validate_input
    h, errs = validate_input(_raw(**kw))
    assert errs == []
    app_module.st.session_state["household"] = h
    app_module.st.session_state["ack_compliance"] = True
    app_module.page_result(_Ctx())


def test_other_pages_render(app_module):
    app_module.page_intro(_Ctx())
    app_module.page_input(_Ctx())
    app_module.page_compliance(_Ctx())


def test_result_page_without_input(app_module):
    """没录入时不能出样例数字，只给提示。"""
    app_module.st.session_state.pop("household", None)
    app_module.st.session_state["ack_compliance"] = True
    app_module.page_result(_Ctx())
