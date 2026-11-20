"""P2 v2 DM 검정·판정·지표·기준 시험. 기대값은 손 계산 (구현 출력으로 만들지 않는다)."""
from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from pbd.forecast_v2 import dm as DM
from pbd.forecast_v2.baselines import b1, b2
from pbd.forecast_v2.metrics import decompose, weighted_wape


def test_dm_unweighted_hln_hand():
    """D = 1,2,3,4, h = 2, 지연 1: 평균 2.5, γ0 = 1.25, γ1 = 0.3125, V = 1.875/4, c = √(1.5/4), S = 2.5·√0.8 = √5"""
    r = DM.dm(np.array([1.0, 2, 3, 4]), h=2, lag_max=1, fallback=1, robust=1)
    assert r["mean"] == pytest.approx(2.5)
    assert not r["fallback"]
    assert r["p"] == pytest.approx(2 * stats.t.sf(np.sqrt(5), 3))
    # 90% 구간 반폭 = t0.95(3) · √V / c = t0.95(3) · √(0.46875/0.375)
    assert r["hi90"] - 2.5 == pytest.approx(stats.t.ppf(0.95, 3) * np.sqrt(0.46875 / 0.375))


def test_dm_fallback_bartlett_hand():
    """D = 1,−1,1,−1, 지연 1: γ0 = 1, γ1 = −0.75 → 가중 없는 V = (1 − 1.5)/4 < 0 → Bartlett 1: (1 + 2·0.5·(−0.75))/4 = 0.0625"""
    D = np.array([1.0, -1, 1, -1])
    g = DM._gammas(D, 1)
    assert g == pytest.approx([1.0, -0.75])
    assert DM.var_bartlett(g, 4, 1) == pytest.approx(0.0625)
    r = DM.dm(D, h=2, lag_max=1, fallback=1, robust=1)
    assert r["fallback"] and r["mean"] == pytest.approx(0.0) and r["p"] == pytest.approx(1.0)


def test_hln_factor():
    assert DM.hln(36, 6) == pytest.approx(np.sqrt((36 + 1 - 12 + 30 / 36) / 36))


def _r(m, p, pr, lo, hi):
    return dict(mean=np.array([m]), p=np.array([p]), p_robust=np.array([pr]), lo90=np.array([lo]), hi90=np.array([hi]))


@pytest.mark.parametrize("r,r1,r2,expected", [
    (_r(-0.01, 0.01, 0.02, -0.02, -0.001), -1, -1, DM.BETTER),
    (_r(-0.01, 0.01, 0.02, -0.02, -0.001), -1, +1, DM.PERIOD),
    (_r(0.01, 0.01, 0.02, 0.001, 0.02), 1, 1, DM.WORSE),
    (_r(-0.01, 0.01, 0.20, -0.02, -0.001), -1, -1, DM.HOLD),       # 두 p 판정이 갈림
    (_r(0.001, 0.5, 0.6, -0.004, 0.004), 1, -1, DM.EQUIV),        # 구간 ⊂ (−0.005, 0.005)
    (_r(0.001, 0.5, 0.6, -0.006, 0.004), 1, -1, DM.HOLD),
])
def test_verdict_table(r, r1, r2, expected):
    assert DM.verdict(r, np.array([r1]), np.array([r2]), 0.005)[0] == expected


def test_power_size_and_large_effect():
    """iid 표준정규: 효과 0이면 '나음'이 드묾(≤ 5%), 효과 −2이면 거의 항상 '나음'"""
    cov = np.eye(36)
    rng = np.random.default_rng(1)
    r0 = DM.power(cov, 0.0, 4000, rng, 6, 0.005, slice(12, 36), slice(0, 22))
    r2 = DM.power(cov, -2.0, 2000, rng, 6, 0.005, slice(12, 36), slice(0, 22))
    assert r0[DM.BETTER] <= 0.05
    assert r2[DM.BETTER] >= 0.99


def test_decompose_sum_equals_weighted_wape_hand():
    """성분 2 × 달 3. 성분1: y = 10,10,20, ŷ = 12,10,15 → |e| = 2,0,5, Y = 40. 성분2: y = 5,5,5, ŷ = 5,6,4, 셋째 칸 제외 → |e| = 0,1, Y = 10.
    w = 0.6, 0.4 → d = (0.6·2/40, 0.6·0/40 + 0.4·1/10, 0.6·5/40) = (0.03, 0.04, 0.075), 합 0.145"""
    y = np.array([[10.0, 10, 20], [5, 5, 5]])
    yh = np.array([[12.0, 10, 15], [5, 6, 4]])
    mask = np.array([[True, True, True], [True, True, False]])
    w = np.array([0.6, 0.4])
    assert decompose(y, yh, w, mask) == pytest.approx([0.03, 0.04, 0.075])
    assert weighted_wape(y, yh, w, mask) == pytest.approx(0.145)


def test_baselines_hand():
    """성분 1, 30개월: y_m = m + 1 (1 ~ 30). h = 6, 대상 열 j = 29(30번째 달), a = 23.
    B1 = y_{j−12} = 18. B2 = 18 · Σ y_{12..23} / Σ y_{0..11} = 18 · (13+…+24) / (1+…+12) = 18 · 222 / 78"""
    Y = np.arange(1, 31, dtype=float)[None, :]
    assert b1(Y, 29, 6)[0] == 18
    assert b2(Y, 29, 6)[0] == pytest.approx(18 * 222 / 78)
    assert np.isnan(b2(Y, 28, 6)[0])              # a − 23 < 0
    Y2 = Y.copy()
    Y2[0, 5] = np.nan                               # 24개월 안 미관측 → 예측 안 함
    assert np.isnan(b2(Y2, 29, 6)[0])
    Y3 = Y.copy()
    Y3[0, 17] = 0.0                                 # y_{t−12} = 0 → 예측 안 함
    assert np.isnan(b1(Y3, 29, 6)[0])


def test_q1_overall_uses_holm_for_strongest():
    """종합이 '나음'이 아니면 가장 강한 기준과의 문장도 Holm(3) p로 판정한다.
    G 대 B2: 원 p 0.04(나쁨) → Holm: p 정렬 0.001(B1)·0.02(B3)·0.04(B2) → 3·0.001, 2·0.02, 1·0.04 → 누적 최대 0.04 → 여전히 나쁨.
    p를 0.04, 0.03, 0.001로 바꾸면 B2 Holm = max(0.003, 2·0.03, 1·0.04) → B2 0.06(> 0.05) → 판단 보류"""
    from pbd.forecast_v2.evaluate import holm, q1_overall
    assert holm([0.001, 0.04, 0.02]) == pytest.approx([0.003, 0.04, 0.04])
    base = dict(lo90=0.001, hi90=0.02, r1_mean=1.0, r2_mean=1.0, diff=0.01)
    comps = {"B1": dict(base, diff=-0.03, lo90=-0.04, hi90=-0.02, r1_mean=-1, r2_mean=-1, p=0.001, verdict=DM.BETTER, metric_b=0.10),
             "B2": dict(base, p=0.04, verdict=DM.WORSE, metric_b=0.05),
             "B3": dict(base, p=0.03, verdict=DM.WORSE, metric_b=0.07)}
    r = q1_overall(comps, 0.005)
    assert r["strongest"] == "B2" and r["holm"]
    assert r["individual"]["B2"]["p"] == pytest.approx(0.06)
    assert r["text"] == "판단 보류"
