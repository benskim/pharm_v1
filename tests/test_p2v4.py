"""P2 v4 (PLAN_P2_v4): 기대값은 합성 자료·손 계산"""
import numpy as np
import pytest

from pbd.forecast_v2.calendar_kr import workdays
from pbd.forecast_v4 import etscache as EC
from pbd.forecast_v4 import interval as IV
from pbd.forecast_v4 import run as R
from pbd.forecast_v4 import workday as WD

TEMP = ["2020-08-17", "2023-10-02", "2024-10-01", "2025-01-27", "2025-06-03"]


def _world(beta=0.03, n=4, T=72, noise=0.0):
    rng = np.random.default_rng(0)
    c = rng.integers(18, 24, T).astype(float)
    m = np.arange(T)
    return 1000 * np.exp(beta * c + 0.2 * np.sin(2 * np.pi * m / 12) + 0.002 * m + rng.normal(0, noise, (n, T))), c


def test_beta_recovered_and_no_future_leak():
    Y, c = _world()
    r = WD.betas(Y, c, 60, 1.345, 24)
    assert np.allclose(r["beta"], 0.03, atol=1e-8) and r["mu"] == pytest.approx(0.03, abs=1e-8)
    Y2, c2 = Y.copy(), c.copy()
    Y2[:, 61:] = 7.0
    c2[61:] = 1.0
    r2 = WD.betas(Y2, c2, 60, 1.345, 24)
    assert (r2["beta"] == r["beta"]).all()
    assert (R.adjusted(Y2, r["beta"], c2, 60, 22) == R.adjusted(Y, r["beta"], c, 60, 22)).all()


def test_huber_resists_outlier_and_short_series_is_nan():
    v = np.tile([1.0, -1.0, 2.0], 20)
    u = np.vstack([0.5 * v, 0.5 * v])
    u[0, 0] = 50.0
    u[1, 10:] = np.nan
    b, _ = WD.huber(u, v, 1.345, 24)
    assert b[0] == pytest.approx(0.5, abs=1e-3) and np.isnan(b[1])


def test_dl_hand():
    # 같은 분산 1, 값 0·4: 고정 평균 2, Q = 8, c = 2 − 1 = 1, τ² = (8 − 1)/1 = 7, b = 7/8 → 0.25, 3.75. 결측은 평균 2
    r = WD.dl(np.array([0.0, 4.0, np.nan]), np.array([1.0, 1.0, np.nan]))
    assert r["tau2"] == pytest.approx(7) and np.allclose(r["est"], [0.25, 3.75, 2.0])
    # 분산이 크면 τ² = 0 → 모두 평균
    assert np.allclose(WD.dl(np.array([0.0, 4.0]), np.array([100.0, 100.0]))["est"], 2.0)


def test_wd_known_only_adds_back_temporary_holidays(monkeypatch):
    # 2025-01: 평일 23일 − 1/1 − 1/27(임시) − 1/28 ~ 1/30 = 18. 임시를 모르면 19
    assert workdays("2025-01") == 18 and WD.wd_known("2025-01", TEMP) == 19
    assert WD.wd_known("2025-02", TEMP) == workdays("2025-02")
    for t in ("2020-08", "2023-10", "2024-10", "2025-06"):
        assert WD.wd_known(t, TEMP) == workdays(t) + 1


def test_restore_changes_only_by_calendar():
    p, b = np.array([100.0]), np.array([0.02])
    assert R.restore(p, b, 22, 22)[0] == 100.0
    assert R.restore(p, b, 20, 22)[0] == pytest.approx(100 * np.exp(-0.04))


def test_same_calendar_each_year_gives_b3():
    """근무일수가 해마다 같으면 계절에 흡수되어 보정이 예측을 (거의) 바꾸지 않는다"""
    from pbd.forecast_v2.ets import ets_path
    rng = np.random.default_rng(1)
    c = np.tile(np.array([22, 18, 21, 21, 21, 19, 22, 22, 17, 21, 22, 20.0]), 7)
    y = np.exp(5 + 0.1 * np.sin(2 * np.pi * np.arange(60) / 12) + rng.normal(0, 0.03, 60))[None, :]
    b = np.array([0.02])
    adj = R.adjusted(y, b, c, 59, 22)
    w = R.restore(ets_path(adj[0], 12)[0][5:6], b, c[65], 22)[0]
    assert w == pytest.approx(ets_path(y[0], 12)[0][5], rel=0.01)


def test_rank_quantile():
    s = np.arange(1, 10.0)                     # n = 9, (n + 1)·0.9 = 9번째, 0.8 → 8번째
    assert IV.rank_q(s, 0.9) == 9 and IV.rank_q(s, 0.8) == 8
    assert IV.rank_q(np.array([1.0, np.nan]), 0.9) == 1


def test_interval_score_hand_and_pinball():
    # 구간 [0, 1], 90%: 안 → 1. 아래로 0.5 → 1 + 20·0.5 = 11
    assert IV.iscore(0.5, 0.0, 1.0, 0.9) == pytest.approx(1) and IV.iscore(-0.5, 0.0, 1.0, 0.9) == pytest.approx(11)
    pin = lambda x, q, tau: (tau - (x < q)) * (x - q)
    assert (2 / 0.1) * (pin(-0.5, 0, .05) + pin(-0.5, 1, .95)) == pytest.approx(11)


def test_scale_separates_and_short_gets_overall():
    S = np.full((3, 12), np.nan)
    S[0] = np.tile([0.01, 0.012], 6)
    S[1] = np.tile([0.3, 0.36], 6)
    S[2, :2] = 5.0
    s = IV.scale(S, 6)
    assert s[0] < 0.02 and s[1] > 0.25 and s[0] < s[2] < s[1]


def test_fit_uses_leave_one_month_out(monkeypatch):
    S = np.ones((2, 5)) * np.array([1, 2, 3, 4, 5.0])
    seen = []
    real = IV.scale
    monkeypatch.setattr(IV, "scale", lambda x, m: (seen.append(x.copy()), real(x, m))[1])
    IV.fit(S, [0.9], 2)
    assert seen[0].shape == (2, 5) and [x.shape for x in seen[1:]] == [(2, 4)] * 5
    assert all(j + 1 not in seen[j + 1][0] for j in range(5))


def test_window_scores_no_leak_and_range():
    Y = np.full((2, 20), 100.0)
    P = {t: np.full(2, 110.0) for t in range(5, 20)}
    s = R.window_scores(Y, P, 12, 4, 5)
    assert s.shape == (2, 4) and np.allclose(s, np.log(1.1))
    Y2 = Y.copy(); Y2[:, 13:] = 1
    P2 = {t: (v if t <= 12 else v * 9) for t, v in P.items()}
    assert (R.window_scores(Y2, P2, 12, 4, 5) == s).all()
    assert R.window_scores(Y, P, 6, 24, 5).shape == (2, 2)      # 첫 달 앞은 쓰지 않음


def test_guard_2026_and_lock():
    with pytest.raises(RuntimeError):
        R.guard_months(["2025-12", "2026-01"])
    R.guard_months(["2025-12"])
    with pytest.raises(PermissionError):
        R.lock_check("no-such-tag")


def test_ets_store_matches_direct_and_reuses(tmp_path):
    from pbd.forecast_v2.ets import ets_path
    rng = np.random.default_rng(2)
    x = np.exp(5 + 0.1 * np.sin(2 * np.pi * np.arange(48) / 12) + rng.normal(0, 0.05, 48))
    xs = [x, x, x[:20]]
    st = EC.EtsStore(tmp_path / "e.sqlite", workers=1)
    r = st.many(xs)
    assert st.fits == 2 and np.allclose(r[0][0], ets_path(x, 12)[0], rtol=1e-9) and (r[0][0] == r[1][0]).all()
    assert r[2][1] is not None and np.isnan(r[2][0]).all()
    st2 = EC.EtsStore(tmp_path / "e.sqlite", workers=1)
    r2 = st2.many(xs, recheck=2)
    assert st2.fits == 0 and st2.hits == 2 and (r2[0][0] == r[0][0]).all()
    # 캐시 값이 틀리면 멈춘다
    st2.con.execute("update f set v = ? where why is null", [np.ones(12).tobytes()]); st2.con.commit()
    with pytest.raises(RuntimeError):
        EC.EtsStore(tmp_path / "e.sqlite", workers=1).many(xs, recheck=2)


def test_common_bias_hand():
    from pbd.forecast_v4.report import common_bias
    y = np.array([[1.0, 1.0], [2.0, 2.0]])
    p = y * np.array([[np.e, 1.0], [1.0, 1.0]])
    # 성분 몫 v·y = w/2로 같음 → 첫 달 (0.25·1 + 0.25·0) ÷ 0.5 = 0.5
    assert np.allclose(common_bias(y, p, np.array([.5, .5]), np.ones((2, 2), bool)), [0.5, 0.0])


def test_first_tau_has_min_rows():
    import yaml
    cfg = yaml.safe_load(R.CFG_PATH.read_text(encoding="utf-8"))
    months = R.mrange("2018-01", "2025-12")
    a = months.index(cfg["first_tau"]) - cfg["h"]
    u, _ = WD.diff_rows(np.full((1, 96), 5.0), np.arange(96.0), a)
    assert np.isfinite(u).sum() >= cfg["beta"]["min_rows"]
