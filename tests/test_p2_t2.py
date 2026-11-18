"""P2 T2 누수 시험: origin M의 예측은 M − lag보다 뒤의 사용통계를 바꾸거나 지워도 같아야 한다 (PLAN_P2 §7).
합성 데이터(시드 고정)로 시험하고, 일부러 누수를 넣었을 때 이 시험이 실제로 실패하는지도 확인한다."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pbd.forecast import rolling as R

SERIES = ["A", "B", "C"]
CLS = {"A": 141, "B": 222, "C": 618}


@pytest.fixture(scope="module")
def panel():
    rng = np.random.default_rng(7)
    rows = []
    for k, c in enumerate(SERIES):
        for i, m in enumerate(R.months("2018-01", "2025-12")):
            q = 1000 * (k + 1) * (1 + 0.3 * np.cos(2 * np.pi * i / 12)) * np.exp(rng.normal(0, 0.05))
            rows.append(dict(c4=c, month=m, claim_qty=q, usable=not (c == "C" and m == "2021-05")))
    return pd.DataFrame(rows)


def _perturb(panel, origin, lag, mode):
    cut = R.madd(origin, -lag)
    fut = panel["month"] > cut
    if mode == "drop":
        return panel[~fut]
    p = panel.copy()
    p.loc[fut, "claim_qty"] = p.loc[fut, "claim_qty"] * 10 + 7
    return p


def _invariant(model, panel, origin, lag, **kw):
    a = model(R.available(panel, origin, lag), origin, lag, SERIES, cls=CLS, **kw)
    out = []
    for mode in ("scale", "drop"):
        p = _perturb(panel, origin, lag, mode)
        b = model(R.available(p, origin, lag), origin, lag, SERIES, cls=CLS, **kw)
        out.append(np.allclose(a["yhat"].to_numpy(), b["yhat"].to_numpy(), equal_nan=True, rtol=0, atol=0))
    return all(out), a


@pytest.mark.parametrize("lag", [0, 3, 5])
@pytest.mark.parametrize("origin", ["2023-01", "2024-06", "2025-11"])
def test_m0_no_leak(panel, origin, lag):
    ok, a = _invariant(R.m0, panel, origin, lag)
    assert ok
    assert (a["target"] == R.madd(origin, 1)).all()
    exp = panel[panel["month"] == R.madd(origin, -11)].set_index("c4")["claim_qty"]   # 손 계산: t − 12 = M − 11
    assert np.allclose(a.set_index("c4")["yhat"], exp.reindex(SERIES))


@pytest.mark.parametrize("lag", [0, 3])
def test_m2_no_leak(panel, lag):
    ok, a = _invariant(R.gbm, panel, "2024-06", lag)
    assert ok and a["yhat"].notna().all()


def test_m2_same_seed_same_output(panel):
    a = R.gbm(R.available(panel, "2024-06", 3), "2024-06", 3, SERIES, cls=CLS)
    b = R.gbm(R.available(panel, "2024-06", 3), "2024-06", 3, SERIES, cls=CLS)
    assert a.equals(b)


def test_m0_undefined_beyond_lag_11(panel):
    with pytest.raises(ValueError):
        R.m0(R.available(panel, "2024-06", 12), "2024-06", 12, SERIES)


# ---------------------------------------------------------------- 일부러 누수를 넣었을 때 시험이 실패하는가
def _naive_last(hist, origin, lag, series, **_):
    """입력 표에서 가장 최근 값을 쓰는 모델. 입력이 잘려 있으면 누수가 없다"""
    last = hist[hist["usable"]].sort_values("month").groupby("c4")["claim_qty"].last()
    return pd.DataFrame(dict(c4=series, target=R.madd(origin, 1), yhat=[last.get(c, np.nan) for c in series]))


def test_leak_injection_is_detected(panel, monkeypatch):
    ok, _ = _invariant(_naive_last, panel, "2024-06", 3)
    assert ok                                               # 정상: 잘린 입력이라 불변
    monkeypatch.setattr(R, "available", lambda p, o, l: p)  # 누수 주입: 자르지 않는다
    ok, _ = _invariant(_naive_last, panel, "2024-06", 3)
    assert not ok                                           # 시험이 누수를 잡는다
    with pytest.raises(AssertionError):                     # M2는 잘리지 않은 입력을 스스로 거부한다
        R.gbm(R.available(panel, "2024-06", 3), "2024-06", 3, SERIES, cls=CLS)


def test_leak_injection_lag_ignored_is_detected(panel, monkeypatch):
    """누수 주입 2: lag를 무시하고 M까지 준다 (lag 0과 3을 바꿔 쓰는 실수)"""
    real = R.available
    monkeypatch.setattr(R, "available", lambda p, o, l: real(p, o, 0))
    ok, _ = _invariant(_naive_last, panel, "2024-06", 3)
    assert not ok


def test_scale_uses_only_2022_and_before(panel):
    s = R.scale12(panel, SERIES)
    p = panel.copy()
    p.loc[p["month"] > "2022-12", "claim_qty"] *= 5
    assert s.equals(R.scale12(p, SERIES))
