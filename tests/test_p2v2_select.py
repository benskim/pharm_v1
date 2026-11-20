"""P2 v2 대상 선정·가중치·잠금 시험. 기대값은 손으로 만든 합성 자료와 손 계산 (구현 출력으로 만들지 않는다)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pbd.common import connect
from pbd.forecast_v2 import select as SEL
from pbd.forecast_v2.timeaxis import PRE_TAG_MAX, madd, mrange, panel_upto

S = dict(a0="2022-07", obs_start="2018-01", n_months=55, weight_period=["2018-12", "2019-11"],
         weight_period_alt=["2021-08", "2022-07"], class_list_month="2022-07", subgroup_h=[141, 222, 229, 618])
MONTHS = mrange("2018-01", "2025-12")


def _panel():
    """A: 계속 관측, 달마다 금액 10. B: 2020-05 미관측. C: 계속 관측이나 가중치 기간 금액 0.
    D: 2022-07까지 계속 관측(금액 30), 2022-08부터 미관측. E: 2022-08에 처음 나타남"""
    rows = []
    for m in MONTHS:
        rows.append(("A", m, True, 5.0, 10.0))
        rows.append(("B", m, m != "2020-05", 5.0 if m != "2020-05" else None, 10.0 if m != "2020-05" else None))
        wp = "2018-12" <= m <= "2019-11"
        rows.append(("C", m, True, 1.0, 0.0 if wp else 7.0))
        rows.append(("D", m, m <= "2022-07", 2.0 if m <= "2022-07" else None, 30.0 if m <= "2022-07" else None))
        rows.append(("E", m, m >= "2022-08", 1.0 if m >= "2022-08" else None, 99.0 if m >= "2022-08" else None))
    return pd.DataFrame(rows, columns=["c4", "month", "observed", "claim_qty", "claim_amt"])


def _cls():
    return pd.DataFrame({"c4": ["A", "D"], "cls": [141, 999]})


def test_selection_and_weights_hand():
    p = _panel()
    t, steps, drop = SEL.select_target(p[p.month <= "2022-07"], _cls(), S)
    assert t.c4.tolist() == ["A", "D"]
    # 가중치 기간 12개월: A 120, D 360 → 0.25, 0.75. A18 기간 12개월: 같은 비
    assert np.allclose(t.w, [0.25, 0.75]) and np.allclose(t.w_alt, [0.25, 0.75])
    assert [s["n_c4"] for s in steps] == [4, 3, 2]          # E는 ① 단계에서 없음
    assert dict(zip(drop.c4, drop.reason)) == {"B": "원외 관측 54/55개월", "C": "가중치 기간 원외 청구금액 합 0 ≤ 0"}
    assert t.set_index("c4").in_h.to_dict() == {"A": True, "D": False}
    assert t.set_index("c4").cls3.to_dict() == {"A": "141", "D": "999"}
    assert SEL.eff_n(t.w) == pytest.approx(1 / (0.25 ** 2 + 0.75 ** 2))   # 1.6


def test_missing_class_is_000():
    p = _panel()
    t, _, _ = SEL.select_target(p[p.month <= "2022-07"], pd.DataFrame({"c4": ["A"], "cls": [141]}), S)
    assert t.set_index("c4").cls3["D"] == "000"


def test_selection_invariant_to_data_after_a0():
    """L3·L4: 2022-07 뒤 자료를 바꿔도 대상·가중치가 같다"""
    p = _panel()
    q = p.copy()
    late = q.month > "2022-07"
    q.loc[late, "claim_amt"] = 1e9
    q.loc[late & (q.c4 == "A"), "observed"] = False
    q.loc[late, "observed"] = ~q.loc[late, "observed"]
    t1, s1, d1 = SEL.select_target(p[p.month <= "2022-07"], _cls(), S)
    t2, s2, d2 = SEL.select_target(q[q.month <= "2022-07"], _cls(), S)
    pd.testing.assert_frame_equal(t1, t2)
    assert s1 == s2 and d1.equals(d2)


def test_select_rejects_panel_after_a0():
    with pytest.raises(AssertionError):
        SEL.select_target(_panel(), _cls(), S)


def test_class_from_list_month_only():
    """분류 = 2022-07 목록만, 제품 수 최다, 같으면 작은 번호. 다른 달 목록은 영향 없음"""
    con = connect(None)
    con.execute("""create table stg_benefit as select * from (values
        ('2022-07', 'product_candidate', '000000001', 222, 'A'), ('2022-07', 'product_candidate', '000000002', 141, 'A'),
        ('2022-07', 'product_candidate', '000000003', 618, 'B'), ('2022-07', 'product_candidate', '000000004', 618, 'B'),
        ('2022-07', 'product_candidate', '000000005', 141, 'B'),
        ('2022-08', 'product_candidate', '000000006', 999, 'A'), ('2022-08', 'product_candidate', '000000007', 999, 'A'),
        ('2022-07', 'non_product', 'x', 111, 'A'))
        t(list_month, row_kind, product_code, class_code, c4)""")
    r = SEL.c4_class(con, "2022-07").set_index("c4").cls.to_dict()
    assert r == {"A": 141, "B": 618}


def test_panel_upto_lock():
    con = connect(None)
    con.execute("""create table fc_panel as select * from (values ('A', '2022-12', true, 1.0, 2), ('A', '2023-01', true, 1.0, 2))
                   t(c4, month, observed, claim_qty, claim_amt)""")
    assert panel_upto(con, "2022-12").month.tolist() == ["2022-12"]
    with pytest.raises(AssertionError):
        panel_upto(con, madd(PRE_TAG_MAX, 1))
