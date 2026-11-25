"""합성 자료 시험 (docs/PLAN_P2_diag.md §8)"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import p2_diag_volatile as D

N = 14


def run(E, A12=None, rng=None):
    rng = rng or np.random.default_rng(5)
    Z = lambda: rng.normal(0, .05, E.shape)
    pool = (rng.normal(0, .05, (40, 36)), np.ones(40))
    r, _ = D.analyse(E, np.full(E.shape, 100.0), np.ones(len(E)), Z(), Z() if A12 is None else A12, Z(), Z(), pool, np.random.default_rng(1))
    return {k: v["label"] for k, v in r.items()}, r


def noise(seed=0):
    return np.random.default_rng(seed).normal(0, .05, (N, 36))


def test_common_only():
    lab, _ = run(noise() + np.random.default_rng(3).normal(0, .3, 36))
    assert lab["D2"] == "큰 구성" and all(lab[k] == "근거 약함" for k in ("D2x", "D3", "D4"))


def test_level_only():
    E = noise()
    E[0, :] += .8
    lab, _ = run(E)
    assert lab["D3"] == "큰 구성" and all(lab[k] == "근거 약함" for k in ("D2", "D2x", "D4"))


def test_echo_only():
    A = np.random.default_rng(9).normal(0, .4, (N, 36))
    lab, r = run(noise() + A, A12=A)
    assert lab["D4"] == "큰 구성" and lab["D2"] == lab["D3"] == "근거 약함"
    assert abs(r["D4"]["b_echo"] - 1) < .05


def test_noise_none():
    for s in range(3):
        lab, _ = run(noise(s))
        assert "큰 구성" not in lab.values() and "일부 구성" not in lab.values()


def test_loo_factor_hand():
    E = np.array([[1.0], [2.0], [10.0]])
    F = D.loo_factor(E, np.array([1.0, 1.0, 3.0]))
    assert F.ravel().tolist() == [10.0, 10.0, 1.0]   # 자기를 뺀 누적 가중치가 절반에 처음 닿는 값


def test_wape_hand():
    E = np.log(np.array([[1.1, 0.8], [2.0, 1.0]]))
    assert abs(D.wape_by(E, np.array([[10.0, 10], [5, 5]]), np.array([3.0, 1.0])) - (3 * .15 + .5) / 4) < 1e-12


def test_covid_rows_hand():
    import p2_diag_covid as C
    from pbd.forecast_v2.timeaxis import mrange
    m = mrange("2018-01", "2023-06")
    keep = [m[j] for j in range(20, len(m)) if j not in C.covid_rows(m, 6)]
    assert keep == mrange("2019-09", "2019-12") + mrange("2023-01", "2023-06")


def test_rank_partial_control():
    import p2_diag_covid as C
    z = np.arange(200.0)
    r = np.random.default_rng(0)
    assert C.rank_partial(z + r.normal(0, 5, 200), z + r.normal(0, 5, 200), [z]) < .3
