"""P2 T3 신호 특징 시험.
1) 합성 표(손 계산): 창, 급여 중 제품만, 막힌 신호 제외, 외부 집행정지의 보이는 달, s 뒤에 처음 보인 신호 제외.
2) 실제 원장: origin 이후 스냅샷을 지우고(build_asof) 만든 특징 = 전체 원장에서 만든 특징. 일부러 누수를 넣으면 실패하는지도 본다.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from pbd.common import DB_PATH, connect
from pbd.forecast import signals as S


def _tables():
    sig = pd.DataFrame([
        # 성분 X: p1 인하 예고(10%), p2 인하 예고지만 집행정지 중, p3 창 밖, p4 s 뒤에 처음 보임
        ("PRICE_NOTICE", "p1", "2024-05", dt.date(2024, 7, 1), 90.0, "X"),
        ("PRICE_NOTICE", "p2", "2024-05", dt.date(2024, 7, 1), 50.0, "X"),
        ("PRICE_NOTICE", "p3", "2024-05", dt.date(2024, 9, 1), 50.0, "X"),
        ("PRICE_NOTICE", "p4", "2024-07", dt.date(2024, 7, 1), 50.0, "X"),
        # 성분 Y: q1 급여중지 예고, q2 급여중지 예고지만 외부 집행정지(결정 2024-05-20 → 2024-06부터 보임), q3 이미 중지된 제품
        ("SUSPEND_NOTICE", "q1", "2024-06", dt.date(2024, 7, 10), None, "Y"),
        ("SUSPEND_NOTICE", "q2", "2024-06", dt.date(2024, 7, 10), None, "Y"),
        ("PRICE_NOTICE", "q3", "2024-06", dt.date(2024, 7, 1), 1.0, "Y"),
        ("LISTING_NOTICE", "n1", "2024-06", dt.date(2024, 7, 15), None, "Y"),
    ], columns=["signal_type", "product_code", "first_month", "signal_date", "amount", "market_c4"])
    sig["sig_month"] = pd.to_datetime(sig["signal_date"]).dt.strftime("%Y-%m")
    stay = pd.DataFrame([("p2", "2024-06", None, "list", None), ("q2", "2024-04", None, "external", dt.date(2024, 5, 20)),
                         ("p1", "2023-01", "2023-05", "list", None)],     # p1의 옛 집행정지: 2023-05에 사라짐 → 막지 않음
                        columns=["product_code", "first_month", "end_month", "target_source", "stay_decision_date"])
    stay["known_month"] = ["2024-06", "2024-06", "2023-01"]
    psm = pd.DataFrame([(p, m, c4, st, 100.0) for m in ("2024-05", "2024-06")
                        for p, c4, st in (("p1", "X", "reimbursed"), ("p2", "X", "reimbursed"), ("p3", "X", "reimbursed"),
                                          ("p4", "X", "reimbursed"), ("q1", "Y", "reimbursed"), ("q2", "Y", "reimbursed"),
                                          ("q3", "Y", "suspended"))],
                       columns=["product_code", "list_month", "c4", "state_as_known", "price_listed"])
    return dict(sig=sig, stay=stay, psm=psm)


def test_features_hand_calculated():
    f = S.features(_tables(), s="2024-06", a="2024-03", t="2024-07").set_index("c4")
    # X: 급여 중 4개. 보이고·창 안·막히지 않은 인하 = p1 하나 (p2 막힘, p3 창 밖, p4는 2024-07에 처음 보임)
    assert f.loc["X", "cut_frac"] == pytest.approx(1 / 4)
    assert f.loc["X", "price_ratio"] == pytest.approx(np.log(0.9) / 4)
    assert f.loc["X", "n_prod"] == pytest.approx(np.log1p(4))
    # Y: 급여 중 2개(q3는 중지). q2는 외부 집행정지가 2024-06부터 보여 제외 → q1만
    assert f.loc["Y", "susp_frac"] == pytest.approx(1 / 2)
    assert f.loc["Y", "cut_frac"] == 0 and f.loc["Y", "price_ratio"] == 0     # q3: 급여 중이 아니라 제외
    assert f.loc["Y", "listing_n"] == pytest.approx(np.log1p(1))


def test_external_stay_not_visible_before_known_month():
    tb = _tables()
    tb["psm"] = pd.concat([tb["psm"], tb["psm"][tb["psm"]["list_month"] == "2024-05"].assign(list_month="2024-04")])
    tb["sig"].loc[tb["sig"]["product_code"].isin(["q1", "q2"]), "first_month"] = "2024-04"
    f = S.features(tb, s="2024-05", a="2024-02", t="2024-07").set_index("c4")   # 2024-05: 외부 집행정지 아직 안 보임
    assert f.loc["Y", "susp_frac"] == pytest.approx(2 / 2)


# ---------------------------------------------------------------- 실제 원장: origin 이후 스냅샷을 지워도 같은가
ASOF = ["2023-06", "2025-07", "2025-10"]   # 2025-07: 경보제약 급여중지 예고가 보이고, 외부 집행정지(결정 2025-07-09)는 아직 안 보이는 달.
# 2025-10: 다음 달 스냅샷에 그 달 날짜의 등재 예고('25.11.23 등재)가 처음 보인다 → 누수 주입 1이 드러나는 달


@pytest.fixture(scope="module")
def full():
    if not DB_PATH.exists():
        pytest.skip("DB 없음")
    return S.load_tables(connect(DB_PATH, read_only=True))


@pytest.fixture(scope="module")
def asof():
    from pbd.ledger.s6_report import build_asof
    return {s: S.load_tables(build_asof(s)) for s in ASOF}


def _eq(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    a = a.sort_values("c4").reset_index(drop=True)
    b = b.sort_values("c4").reset_index(drop=True)
    # 허용오차 없이 같은 값 (검토 c11)
    return a.shape == b.shape and (a["c4"] == b["c4"]).all() and np.array_equal(a[list(S.FEATURES)].to_numpy(),
                                                                                 b[list(S.FEATURES)].to_numpy())


@pytest.mark.parametrize("s", ASOF)
@pytest.mark.parametrize("lag", [0, 3, 5])
def test_features_equal_after_deleting_later_snapshots(full, asof, s, lag):
    """origin M = s (주 정의). M+1 이후 스냅샷을 지우고 다시 만든 v5 원장의 특징 = 전체 원장의 특징"""
    from pbd.forecast.rolling import madd
    a, t = madd(s, -lag), madd(s, 1)
    assert _eq(S.features(full, s, a, t), S.features(asof[s], s, a, t))


def test_leak_injection_is_detected(full, asof, monkeypatch):
    """누수 주입: s 뒤에 처음 보인 신호도 쓰게 하면 위 시험이 (적어도 한 달·lag에서) 실패해야 한다"""
    from pbd.forecast.rolling import madd
    monkeypatch.setattr(S, "visible_signals", lambda sig, s_: sig)
    diffs = [(s, lag) for s in ASOF for lag in (0, 3, 5)
             if not _eq(S.features(full, s, madd(s, -lag), madd(s, 1)), S.features(asof[s], s, madd(s, -lag), madd(s, 1)))]
    assert diffs


def test_leak_injection_outcome_is_detected(full, asof):
    """누수 주입 2: 원장의 최종 결과로 미실현·미결 신호를 지우면(사후 정보) 원장 판정이 갈리는 달에서 특징이 달라진다"""
    from pbd.forecast.rolling import madd
    con = connect(DB_PATH, read_only=True)
    bad = con.execute("""select product_code, first_month, signal_date from signal_v5
                         where signal_type in ('PRICE_NOTICE', 'SUSPEND_NOTICE') and outcome in ('NOT_REALIZED', 'CENSORED')""").df()
    key = set(zip(bad["product_code"], bad["first_month"], pd.to_datetime(bad["signal_date"])))
    leaky = dict(full)
    sig = full["sig"]
    leaky["sig"] = sig[[k not in key for k in zip(sig["product_code"], sig["first_month"], pd.to_datetime(sig["signal_date"]))]]
    s = "2025-07"
    diffs = [lag for lag in (3, 5) if not _eq(S.features(leaky, s, madd(s, -lag), madd(s, 1)),
                                              S.features(asof[s], s, madd(s, -lag), madd(s, 1)))]
    assert diffs, "사후 결과로 거른 특징이 as-of 특징과 같다: 이 달에서는 주입을 잡지 못함"


@pytest.mark.parametrize("s", ["2025-07", "2025-10"])
@pytest.mark.parametrize("lag", [0, 5])
def test_m3_end_to_end_equal_after_deleting_later_snapshots(full, asof, s, lag):
    """검토 c2·c3: origin s의 M3 학습 행 전체(2022-01 ~ s의 모든 pseudo-origin) 특징과 M3 예측이,
    s 이후 스냅샷을 지우고 다시 만든 원장에서도 같다 (허용오차 0)"""
    from pbd.forecast import rolling as R
    from pbd.forecast.t2_baseline import load
    origins = R.months(S.FIRST_SNAPSHOT, s)
    panel, series, cls = load()
    fa = S.feature_table(full, lag, "same", origins, series)
    fb = S.feature_table(asof[s], lag, "same", origins, series)
    key = ["c4", "origin"]
    fa, fb = fa.sort_values(key).reset_index(drop=True), fb.sort_values(key).reset_index(drop=True)
    assert fa[key].equals(fb[key]) and np.array_equal(fa[list(S.FEATURES)].to_numpy(), fb[list(S.FEATURES)].to_numpy())
    pa = R.gbm(R.available(panel, s, lag), s, lag, series, cls=cls, feat=fa)
    pb = R.gbm(R.available(panel, s, lag), s, lag, series, cls=cls, feat=fb)
    assert pa.equals(pb)
