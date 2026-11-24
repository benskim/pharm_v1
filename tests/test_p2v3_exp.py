"""P2 v3 실험 r2 (PLAN_P2_v3 §13): 기대값은 손으로 만든 자료에서 손으로 계산했다."""
import numpy as np
import pytest

from pbd.forecast_v2.gbm import Ctx, fit_predict
from pbd.forecast_v2.timeaxis import mrange
from pbd.forecast_v3 import exp as X


def _ctx(n=12, T=40, seed=0):
    rng = np.random.default_rng(seed)
    m = np.arange(T)
    Y = 100 * np.exp(0.2 * np.sin(2 * np.pi * m / 12)[None, :] + rng.normal(0, 0.05, (n, T)))
    A = Y * 10
    return Ctx(Y, A, mrange("2018-01", "2021-04")[:T], np.array([11, 21] * (n // 2)))


def test_fit_predict_ext_without_extras_equals_v2():
    c = _ctx()
    a, h = 33, 6
    p0, n0 = fit_predict(c, a, h, seed=1)
    p1, n1, _ = X.fit_predict_ext(c, a, h, seed=1)
    assert n0 == n1 and np.allclose(p0, p1, rtol=0, atol=0, equal_nan=True)


def test_pick_alpha_tie_within_float_error_takes_smaller():
    assert X.pick_alpha(np.array([0.1 + 1e-17, 0.1, 0.1 - 1e-17, 0.3])) == 0
    assert X.pick_alpha(np.full(3, np.nan)) == 0


def test_shrunk_alpha_hand_computed():
    Lg = np.array([0.30, 0.20, 0.10])   # 군: α 큰 쪽이 좋음
    L1 = np.array([0.10, 0.20, 0.30])   # ATC 1단계: α 작은 쪽
    L0 = np.array([0.10, 0.20, 0.30])
    # b = 1 → 군 곡선 그대로 → α 번호 2. b = 0 → 상위 곡선 → 0. b = 0.5 → 0.5·Lg + 0.5·L1 = [0.2, 0.2, 0.2] 같음 → 작은 α 0
    assert X.shrunk_alpha(Lg, L1, L0, 1.0, 1.0) == 2
    assert X.shrunk_alpha(Lg, L1, L0, 0.0, 1.0) == 0
    assert X.shrunk_alpha(Lg, L1, L0, 0.5, 1.0) == 0
    assert X.shrunk_alpha(np.full(3, np.nan), L1, L0, 0.9, 1.0) == 0   # 군 곡선 없음 → 상위


def _e1_world():
    n, T, a = 4, 20, 13
    Y = np.full((n, T), 100.0)
    G, B = {}, {}
    for t in range(T):
        G[t] = np.array([98.0, 102.0, 121.0, 119.0])    # 성분 0·1(A01)은 G가 거의 맞고, 2·3(C01)은 B3가 거의 맞다
        B[t] = np.array([80.0, 79.0, 99.0, 102.0])
    return Y, G, B, a, np.array(["A01", "A01", "C01", "C01"])


def test_e1_weights_follow_group_and_ignore_future():
    Y, G, B, a, lower = _e1_world()
    w = np.ones(4)
    r = X.e1_weights(Y, G, B, a, w, lower, window=12, min_cells=6)
    assert r["alpha_group"]["A01"] == 0.0 and r["alpha_group"]["C01"] == 1.0   # 군 신호가 뚜렷 → 줄여도 군 쪽
    assert r["taus"] == 12 and r["n_used"] == 4
    Y2 = Y.copy(); Y2[:, a + 1:] *= 5.0                  # origin 뒤 정답·예측을 바꿔도
    G2 = {t: (v * 0.1 if t > a else v) for t, v in G.items()}
    r2 = X.e1_weights(Y2, G2, B, a, w, lower, 12, 6)
    assert r2["alpha_group"] == r["alpha_group"] and r2["alpha_global"] == r["alpha_global"]


def test_comove_excludes_self_and_future():
    T = 30
    Y = np.full((3, T), 100.0)
    Y[1, 15] = 150.0                                     # 성분 1만 ap = 15에서 50% 늘어남
    g = np.array([0, 0, 1])
    co1, co3 = X.comove(Y, g, 15)
    assert co1[0] == pytest.approx(np.log(1.5))          # 성분 0 = 다른 성분(1)의 흐름
    assert co1[1] == pytest.approx(0.0)                  # 성분 1 = 자기를 뺀 성분 0의 흐름
    assert np.isnan(co1[2])                              # 혼자인 군
    assert co3[0] == pytest.approx(np.log(350 / 300))
    Y2 = Y.copy(); Y2[:, 16:] = 999.0                    # ap 뒤 값을 바꿔도
    Y2[0, 15] = 777.0                                    # 자기 값을 바꿔도(성분 0 기준)
    c1b, _ = X.comove(Y2, g, 15)
    assert c1b[0] == pytest.approx(co1[0])


def test_lock_check_refuses_without_tag():
    with pytest.raises(PermissionError):
        X.lock_check("p2v3-no-such-tag-for-test")
