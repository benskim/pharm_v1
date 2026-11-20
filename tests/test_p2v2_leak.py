"""P2 v2 누수 시험 (PLAN_P2_v2 §10 L1 · L2 · L5 · L6 · L7). 합성 자료만 쓴다(실제 평가 기간 정답을 읽지 않음).
기대값은 불변성(같아야 함)과 일부러 만든 누수 함수(달라져야 함)에서 온다."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pbd.forecast_v2 import dryrun as DR
from pbd.forecast_v2.backtest import Backtest, matrices
from pbd.forecast_v2.list_info import ListData
from pbd.forecast_v2.q2 import ListCache

SEED = 20261114


@pytest.fixture(scope="module")
def world():
    W = DR.world()
    pn = DR.panel(W)
    t = pd.DataFrame({"c4": W["c4"], "cls3": [f"{c:03d}" for c in W["cls"]], "w": 1 / DR.N, "w_alt": 1 / DR.N,
                      "in_h": False})
    return W, pn, t


def _bt(pn, t):
    Y, A, C, months, _ = matrices(pn, t, "2018-01", "2025-12")
    return Backtest(Y, A, months, t, SEED, C)


def _perturb_after(pn, a):
    q = pn.copy()
    late = q.month > a
    q.loc[late, "claim_qty"] = q.loc[late, "claim_qty"] * 3.0 + 7.0
    q.loc[late, "claim_amt"] = q.loc[late, "claim_amt"] * 0.1
    return q


def test_L1_predictions_invariant_to_usage_after_a(world):
    """사용통계 a 뒤를 바꿔도 대상월 t(= a + 6)의 B0 ~ B3·G 예측이 같다"""
    W, pn, t = world
    tm, h = "2024-01", 6
    a = "2023-07"
    p1 = _bt(pn, t).predict(tm, h)
    p2 = _bt(_perturb_after(pn, a), t).predict(tm, h)
    for m in ("B0", "B1", "B2", "B3", "G"):
        assert np.allclose(p1[m], p2[m], equal_nan=True), m


def test_L2_list_info_invariant_to_lists_after_M(world):
    """목록 M 뒤를 지우거나 바꿔도 origin M의 ΔK·특징이 같다"""
    W, _, t = world
    ld = DR.lists(W)
    a, M, tm = "2023-07", "2023-12", "2024-01"
    f1 = ld.info(a, M, tm)
    psm2 = ld.psm[ld.psm.list_month <= M]
    tok2 = ld.tok[ld.tok.list_month <= M]
    f2 = ListData(psm2, tok2, ld.stay).info(a, M, tm)
    pd.testing.assert_frame_equal(f1, f2)
    psm3 = ld.psm.copy()
    psm3.loc[psm3.list_month > M, "price_listed"] *= 0.5
    f3 = ListData(psm3, ld.tok, ld.stay).info(a, M, tm)
    pd.testing.assert_frame_equal(f1, f3)


def test_L6_post_hoc_columns_not_used(world):
    """사후 칸(state_revised 등)을 엉터리로 넣어도 결과가 같다"""
    W, _, _ = world
    ld = DR.lists(W)
    psm = ld.psm.assign(state_revised="delisted", revised_differs=True, outcome="NOT_REALIZED")
    f1 = ld.info("2023-07", "2023-12", "2024-01")
    f2 = ListData(psm, ld.tok, ld.stay).info("2023-07", "2023-12", "2024-01")
    pd.testing.assert_frame_equal(f1, f2)


# L5: 일부러 만든 누수 예측 함수 5가지를 불변성 점검이 잡는다
def _leak_fns():
    def uses_target(Y, j, h):
        return Y[:, j]

    def uses_a_plus_1(Y, j, h):
        return Y[:, j - h + 1]

    def uses_full_mean(Y, j, h):
        return np.nanmean(Y, axis=1)

    def uses_last_col(Y, j, h):
        return Y[:, -1]

    def uses_future_growth(Y, j, h):
        return Y[:, j - 12] * Y[:, j] / Y[:, j - 12]

    return [uses_target, uses_a_plus_1, uses_full_mean, uses_last_col, uses_future_growth]


def leak_detected(fn, Y, j, h) -> bool:
    """a 뒤 열을 바꾸고 예측이 달라지면 누수"""
    Y2 = Y.copy()
    Y2[:, j - h + 1:] = Y2[:, j - h + 1:] * 3.0 + 7.0
    return not np.allclose(fn(Y, j, h), fn(Y2, j, h), equal_nan=True)


@pytest.mark.parametrize("fn", _leak_fns(), ids=lambda f: f.__name__)
def test_L5_leak_injection_is_caught(world, fn):
    W, pn, t = world
    bt = _bt(pn, t)
    assert leak_detected(fn, bt.Y, bt.col("2024-01"), 6)


def test_L5_real_baselines_pass_leak_check(world):
    from pbd.forecast_v2.baselines import b1, b2
    W, pn, t = world
    bt = _bt(pn, t)
    for fn in (b1, b2):
        assert not leak_detected(fn, bt.Y, bt.col("2024-01"), 6)


def test_L7_deterministic(world):
    W, pn, t = world
    p1 = _bt(pn, t).predict("2024-01", 6, models=("G",))
    p2 = _bt(pn, t).predict("2024-01", 6, models=("G",))
    assert np.array_equal(p1["G"], p2["G"], equal_nan=True)


def test_listcache_rho_and_shift(world):
    W, _, t = world
    ld = DR.lists(W)
    lc = ListCache(ld, t.c4, lambda M: 0.5)
    i = W["c4"].index("S001")
    L = W["L"][i]
    from pbd.forecast_v2.timeaxis import madd
    x = lc.x(madd(L, -6), L)                      # s = M = L − 1, t = L
    assert x[i, 1] == pytest.approx(0.5 * np.log(W["d"][i]))   # dNr = ΔN · ρ
    assert ListCache(ld, t.c4, lambda M: 1.0, s_offset=-1).s_of("2023-07") == "2023-11"
