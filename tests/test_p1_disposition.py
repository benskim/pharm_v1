"""처분 단위 집계 시험 (합성 자료, 기대값은 손 계산). 처분 단위 = (업체, 처분 종류, 예정일 또는 기간)."""
from __future__ import annotations

import importlib.util

import pandas as pd

from pbd.common import ROOT

spec = importlib.util.spec_from_file_location("disp", ROOT / "scripts/p1_disposition.py")
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def _row(pc, firm, t, d, end, outcome, blocked=False, src=None):
    return dict(product_code=pc, firm=firm, signal_type=t, signal_date=d, period_end=end, outcome=outcome,
                stay_blocked=blocked, stay_target_source=src)


def test_units_by_firm_type_date():
    sig = pd.DataFrame([
        # 가사: 급여중지 같은 기간 3제품 → 처분 1건. 1개는 판정 제외라 세지 않음, 2개 막힘(외부 근거)
        _row("1", "가사", "SUSPEND_NOTICE", "2022-08-01", "2022-08-31", "CENSORED", True, "external"),
        _row("2", "가사", "SUSPEND_NOTICE", "2022-08-01", "2022-08-31", "NOT_REALIZED", True, "external"),
        _row("3", "가사", "SUSPEND_NOTICE", "2022-08-01", "2022-08-31", "EXCLUDED"),
        # 가사: 기간이 다르면 다른 처분
        _row("4", "가사", "SUSPEND_NOTICE", "2023-01-01", None, "REALIZED"),
        # 나사: 약가 예고 같은 날 금액이 달라도 처분 1건 (금액은 키가 아님). 정시 1, 지연 1
        _row("5", "나사", "PRICE_NOTICE", "2024-07-01", None, "REALIZED"),
        _row("6", "나사", "PRICE_NOTICE", "2024-07-01", None, "REALIZED_LATE"),
        # 다사: 판정 제외만 있는 처분은 표에서 빠진다
        _row("7", "다사", "PRICE_NOTICE", "2024-07-01", None, "EXCLUDED"),
    ])
    u = D.units(sig).set_index(["firm", "signal_type", "signal_date"])
    assert len(u) == 3
    a = u.loc[("가사", "SUSPEND_NOTICE", "2022-08-01")]
    assert (a.n_products, a.n_judged, a.n_blocked, a.n_resolved, a.n_realized_family) == (3, 2, 2, 1, 0)
    assert a.any_blocked and a.all_blocked and a.external
    b = u.loc[("가사", "SUSPEND_NOTICE", "2023-01-01")]
    assert (b.n_judged, b.n_blocked, b.n_on_time) == (1, 0, 1) and not b.any_blocked
    c = u.loc[("나사", "PRICE_NOTICE", "2024-07-01")]
    assert (c.n_products, c.n_realized_family, c.n_on_time) == (2, 2, 1)
