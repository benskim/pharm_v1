"""EWC 주 검정 후보 시험 (검증 V1·V6). 기대값은 손 계산과 독립 구현(scipy DCT-II) (구현 출력으로 만들지 않는다)."""
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats
from scipy.fft import dct

from pbd.forecast_v2 import dm as DM


def test_v1_hand_T4():
    """D = 1, 2, 3, 4 → x = −1.5, −0.5, 0.5, 1.5.
    Λ_1 = √(1/2)·Σ cos(π(t − ½)/4)·x_t = −√(1/2)·(3cos(π/8) + cos(3π/8)), Λ_2 = √(1/2)·cos(π/4)·(−1.5 + 0.5 − 0.5 + 1.5) = 0.
    ν = 1: Ω̂ = Λ_1², S = 2.5 / √(Ω̂/4), p = 양측 t(1). ν = 2: Ω̂ = Λ_1²/2"""
    lam1 = -math.sqrt(0.5) * (3 * math.cos(math.pi / 8) + math.cos(3 * math.pi / 8))
    D = np.array([1.0, 2, 3, 4])
    assert DM.ewc_omega(D, 1) == pytest.approx(lam1 ** 2, abs=1e-12)
    assert DM.ewc_omega(D, 2) == pytest.approx(lam1 ** 2 / 2, abs=1e-12)
    r = DM.ewc(D, 1)
    s = 2.5 / math.sqrt(lam1 ** 2 / 4)
    assert r["p"] == pytest.approx(2 * stats.t.sf(s, 1), abs=1e-12)
    assert r["hi90"] - 2.5 == pytest.approx(stats.t.ppf(0.95, 1) * math.sqrt(lam1 ** 2 / 4), abs=1e-12)
    assert not r["invalid"]


@pytest.mark.parametrize("nu", [1, 3, 4, 6])
def test_v1_matches_scipy_dct(nu):
    """독립 구현: 정규화 DCT-II 계수 j ≥ 1 = √(2/T) Σ x_n cos(πj(2n+1)/(2T)) = Λ_j"""
    rng = np.random.default_rng(3)
    D = rng.standard_normal(36)
    c = dct(D - D.mean(), type=2, norm="ortho")
    assert DM.ewc_omega(D, nu) == pytest.approx(np.mean(c[1:nu + 1] ** 2), abs=1e-12)


def test_v1_mean_shift_invariance():
    rng = np.random.default_rng(4)
    D = rng.standard_normal(36)
    assert DM.ewc_omega(D + 7.0, 4) == pytest.approx(DM.ewc_omega(D, 4), abs=1e-12)
    r0, r1 = DM.ewc(D, 4), DM.ewc(D + 1.0, 4)
    assert r1["se"] == pytest.approx(r0["se"], abs=1e-12)
    assert r1["mean"] - r0["mean"] == pytest.approx(1.0, abs=1e-12)


def test_nu_rule():
    assert DM.nu_rule(36) == 4 and DM.nu_rule(24) == 3     # 0.4·36^(2/3) = 4.36, 0.4·24^(2/3) = 3.33


def test_v6_constant_series_is_hold():
    D = np.full(36, -0.01)                                   # Ω̂ = 0
    r = DM.ewc(D, 4)
    assert r["invalid"] and np.isnan(r["p"])
    assert DM.verdict_single(r, np.array(-0.01), np.array(-0.01), 0.005)[()] == DM.HOLD


def test_v6_nan_series_is_hold():
    D = np.random.default_rng(5).standard_normal(36)
    D[10] = np.nan
    r = DM.ewc(D, 4)
    assert r["invalid"] and np.isnan(r["p"]) and np.isnan(r["mean"])
    assert DM.verdict_single(r, np.array(np.nan), np.array(np.nan), 0.005)[()] == DM.HOLD


def test_v6_vectorized_mixed():
    rng = np.random.default_rng(6)
    D = rng.standard_normal((3, 36))
    D[1] = 0.0
    D[2, 0] = np.inf
    r = DM.ewc(D, 4)
    assert r["invalid"].tolist() == [False, True, True]


def _r(m, p, lo, hi):
    return dict(mean=np.array([m]), p=np.array([p]), lo90=np.array([lo]), hi90=np.array([hi]))


@pytest.mark.parametrize("r,r1,r2,expected", [
    (_r(-0.01, 0.01, -0.02, -0.001), -1, -1, DM.BETTER),
    (_r(-0.01, 0.01, -0.02, -0.001), -1, +1, DM.PERIOD),
    (_r(0.01, 0.01, 0.001, 0.02), 1, 1, DM.WORSE),
    (_r(0.001, 0.5, -0.004, 0.004), 1, -1, DM.EQUIV),       # 구간 ⊂ (−0.005, 0.005)
    (_r(0.001, 0.5, -0.006, 0.004), 1, -1, DM.HOLD),
    (_r(-0.01, np.nan, np.nan, np.nan), -1, -1, DM.HOLD),   # invalid
])
def test_verdict_single_table(r, r1, r2, expected):
    """검정 하나 판정: 다른 검정과의 불일치 규칙이 없다"""
    assert DM.verdict_single(r, np.array([r1]), np.array([r2]), 0.005)[0] == expected
