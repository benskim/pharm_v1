"""P2 v3 분류군 비교 (docs/PLAN_P2_v3.md). 기대값은 손으로 만든 합성 자료에서 손으로 계산했다."""
import numpy as np
import pandas as pd
import pytest

from pbd.forecast_v3 import group_compare as gc


def test_assign_atc2_most_products_then_smaller_code():
    p = pd.DataFrame({
        "c4": ["1000"] * 5 + ["2000"] * 4 + ["3000"],
        "product_code": ["a1", "a2", "a3", "a3", "a4", "b1", "b2", "b3", "b4", "c1"],
        # 1000: C09 제품 3개(a1,a2,a3 — a3 두 행은 한 제품), N02 1개 → C09
        # 2000: R05 2개, A02 2개 동점 → 작은 코드 A02.  3000: 형식 틀림 → 미연결.  4000: 제품 없음
        "atc": ["C09AA01", "c09ca", "C09DB", "C09DB", "N02BE01", "R05X", "R05CB", "A02BC", " a02bx ", "ZZ"],
    })
    a = gc.assign_atc2(p, ["1000", "2000", "3000", "4000"]).set_index("c4")
    assert a.loc["1000", "atc2"] == "C09" and a.loc["1000", "atc2_n"] == 2
    assert a.loc["1000", "atc2_top_share"] == pytest.approx(3 / 4)
    assert not a.loc["1000", "atc2_tie"]
    assert a.loc["2000", "atc2"] == "A02" and a.loc["2000", "atc2_tie"]
    assert a.loc["3000", "atc2"] == gc.UNMAPPED and a.loc["4000", "atc2"] == gc.UNMAPPED


def _cells():
    rows = []
    # 성분 X: y = 100, 110 / G 90, 110 / B1 100, 100 / B2 = B3 = y
    for t, y, g, b1 in [("2023-01", 100, 90, 100), ("2023-02", 110, 110, 100)]:
        for m, v in {"G": g, "B1": b1, "B2": y, "B3": y}.items():
            rows.append(("X", t, m, v))
    # 2022-12는 평가 기간 밖, 2023-03은 B3 없음 → 빠짐, 2023-04는 dropped, 2023-05는 정답 0
    for m in gc.MODELS:
        rows.append(("X", "2022-12", m, 1.0))
        rows.append(("X", "2023-04", m, 1.0))
        rows.append(("X", "2023-05", m, 1.0))
        if m != "B3":
            rows.append(("X", "2023-03", m, 1.0))
    pred = pd.DataFrame(rows, columns=["c4", "t", "model", "yhat"])
    act = pd.DataFrame({"c4": "X", "t": ["2022-12", "2023-01", "2023-02", "2023-03", "2023-04", "2023-05"],
                        "y": [5, 100, 110, 5, 5, 0]})
    drop = pd.DataFrame({"c4": ["X"], "t": ["2023-04"]})
    return gc.eval_cells(pred, act, drop)


def test_eval_cells_and_errors():
    c = _cells()
    assert list(c.t) == ["2023-01", "2023-02"]
    e = gc.ingredient_errors(c).loc["X"]
    assert e.wape_G == pytest.approx(10 / 210)
    assert e.bias_G == pytest.approx(-10 / 210)
    assert e.wape_B1 == pytest.approx(10 / 210)   # |100−100| + |100−110|
    assert e.bias_B1 == pytest.approx(-10 / 210)
    assert e.wape_B3 == 0 and e.n_cells == 2


def _ing():
    # 군 A: 성분 a1(w .3, WAPE_G .1), a2(w .1, .3) → 가중 (.03 + .03)/.4 = .15
    # 군 B: 성분 b1(w .6, .05) → .05 → 순위 1
    d = pd.DataFrame({"c4": ["a1", "a2", "b1"], "grp": ["A", "A", "B"], "w": [.3, .1, .6],
                      "ing_name": ["가", "나", "다"], "n_cells": [36, 30, 36],
                      "wape_G": [.1, .3, .05], "wape_B3": [.2, .2, .04], "wape_B1": [.2, .4, .1], "wape_B2": [.5, .5, .5],
                      "bias_G": [.1, -.1, 0.0], "bias_B3": 0.0, "bias_B1": 0.0, "bias_B2": 0.0}).set_index("c4")
    for c in gc.PATTERN_COLS:
        d[c] = [1.0, 3.0, np.nan]
    return d


def test_group_table_weighted_rank_and_bootstrap():
    t = gc.group_table(_ing(), "grp", seed=1, n_boot=500).set_index("group")
    assert list(t.index) == ["B", "A"] and t.loc["B", "rank_G"] == 1
    assert t.loc["A", "wape_G"] == pytest.approx(0.15)
    assert t.loc["A", "wape_B1"] == pytest.approx((.06 + .04) / .4)
    assert t.loc["A", "G_gain_vs_B1"] == pytest.approx(1 - .15 / .25)
    assert t.loc["A", "best_model"] == "G" and t.loc["B", "best_model"] == "B3"
    assert t.loc["A", "bias_G"] == pytest.approx((.03 - .01) / .4)
    assert t.loc["A", "top_ingredients"] == "가 / 나" and t.loc["A", "top1_w_share_in_group"] == pytest.approx(.75)
    assert t.loc["A", "seas_strength_median"] == 2.0 and t.loc["A", "seas_strength_wmean"] == pytest.approx(1.5)
    assert .1 <= t.loc["A", "wape_G_lo90"] <= .15 <= t.loc["A", "wape_G_hi90"] <= .3
    assert np.isnan(t.loc["B", "wape_G_lo90"]) and t.loc["B", "n_pattern"] == 0
    assert t.small_group.all()
    t2 = gc.group_table(_ing(), "grp", seed=1, n_boot=500).set_index("group")
    assert t2.loc["A", "wape_G_lo90"] == t.loc["A", "wape_G_lo90"]  # 시드 고정 재현


def _months(start_year, n):
    return [f"{start_year + k // 12}-{k % 12 + 1:02d}" for k in range(n)]


def test_pattern_metrics_pure_season_and_trend():
    idx = _months(2023, 36)
    mon = np.array([int(k[5:]) for k in idx])
    # 순수 계절: 겨울(11~3월) 2배, 나머지 1 → 계절성 강도 ≈ 1, 증가율 ≈ 0, 겨울/여름 = 2
    s = pd.Series(np.where(np.isin(mon, [11, 12, 1, 2, 3]), 200.0, 100.0), index=idx)
    prev = pd.Series(np.where(np.isin(np.arange(1, 13), [11, 12, 1, 2, 3]), 200.0, 100.0), index=_months(2022, 12))
    m = gc.pattern_metrics(s, prev)
    assert m["seas_strength"] > 0.95
    assert m["winter_summer"] == pytest.approx(2.0)
    assert abs(m["annual_growth"]) < 0.05
    assert m["yoy_sd"] == pytest.approx(0.0, abs=1e-12)
    # 순수 추세: 해마다 10% 증가 → 연간 증가율 0.10, 계절성 강도 ≈ 0
    tr = pd.Series(100 * 1.1 ** (np.arange(36) / 12), index=idx)
    m2 = gc.pattern_metrics(tr)
    assert m2["annual_growth"] == pytest.approx(0.10, rel=1e-6)
    assert m2["trend_strength"] > 0.95 and np.isnan(m2["yoy_sd"])
    # 결측·0이 있으면 계산하지 않는다
    bad = s.copy()
    bad.iloc[3] = 0
    assert all(np.isnan(v) for v in gc.pattern_metrics(bad).values())


def test_pattern_resid_sd_grows_with_noise():
    idx = _months(2023, 36)
    rng = np.random.default_rng(0)
    base = np.log(100) + 0.2 * np.sin(2 * np.pi * np.arange(36) / 12)
    lo = gc.pattern_metrics(pd.Series(np.exp(base + rng.normal(0, .01, 36)), index=idx))
    hi = gc.pattern_metrics(pd.Series(np.exp(base + rng.normal(0, .2, 36)), index=idx))
    assert hi["resid_sd"] > 5 * lo["resid_sd"] and hi["seas_strength"] < lo["seas_strength"]


def test_merge_small_groups_to_upper_then_overall():
    g = pd.Series(["21"] * 3 + ["22"] + ["23"] * 2 + ["31"] + [gc.UNMAPPED] + ["C09"] * 2 + ["C10"])
    m = gc.merge_small_groups(g, 3)
    # 21(3개) 유지, 22(1)+23(2) → 2_기타(3개) 유지, 31(1) → 3_기타(1개) → 전체_기타, 미연결은 합치지 않음
    # C09(2)+C10(1) → C_기타(3개)
    assert list(m) == ["21"] * 3 + ["2_기타"] * 3 + ["전체_기타", gc.UNMAPPED] + ["C_기타"] * 3


def test_between_share_hand_computed():
    # A: e = 1, 3 (w 1, 1), B: e = 5 (w 2). 평균 3.5, 군 평균 2, 5
    # 군간 = 2·1.5² + 2·1.5² = 9, 전체 = 2.5² + 0.5² + 2·1.5² = 11 → 9/11
    d = pd.DataFrame({"grp": ["A", "A", "B"], "w": [1.0, 1.0, 2.0], "wape_G": [1.0, 3.0, 5.0]})
    for c in gc.BETWEEN_COLS[1:] + ("log_wape_G", "log_wape_B3"):
        d[c] = np.nan
    r = gc.between_share(d, "grp").set_index("metric")
    assert r.loc["wape_G", "eta2_weighted"] == pytest.approx(9 / 11)
    assert "wape_B3" not in r.index  # 값이 없는 지표는 행을 만들지 않는다


# ---------------------------------------------------------------- 다시 묶기 (regroup)
from pbd.forecast_v3 import regroup as rg  # noqa: E402


def test_eta2_matches_between_share():
    # 위 손 계산 예: 9/11
    e, w, lab = np.array([1.0, 3.0, 5.0]), np.array([1.0, 1.0, 2.0]), np.array([0, 0, 1])
    assert rg.eta2(e, w, lab) == pytest.approx(9 / 11)
    assert rg.eta2(e, w, np.array([0, 1, 2])) == pytest.approx(1.0)   # 성분마다 군 → 1
    assert rg.eta2(e, w, np.array([0, 0, 0])) == pytest.approx(0.0)   # 한 군 → 0


def test_block_permutation_keeps_blocks_together():
    # 블록 4개(성분 2개씩), 묶음 = 블록 0,1 → 0 / 블록 2,3 → 1. 값이 묶음과 완전히 갈림 → η² = 1
    e = np.array([0, 0, 0, 0, 10, 10, 10, 10.0])
    w = np.ones(8)
    block = np.array([0, 0, 1, 1, 2, 2, 3, 3])
    lab = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    obs, null_mean, p = rg.perm_test(e, w, lab, np.random.default_rng(0), n_perm=500, block=block)
    assert obs == pytest.approx(1.0)
    # 블록 배정은 C(4,2) = 6가지, 그중 2가지가 η² = 1, 나머지 4가지는 0 → 순열 평균 ≈ 1/3, p ≈ 1/3
    assert null_mean == pytest.approx(1 / 3, abs=0.07)
    assert p == pytest.approx(1 / 3, abs=0.07)


def test_ward_clusters_deterministic_and_separates():
    f = pd.DataFrame({"x": [0, 0.1, 5, 5.1, 10], "y": [1.0] * 5}, index=["E", "D", "C", "B", "A"])  # y 상수 → 표준화 뒤 0
    c = rg.ward_clusters(f, 3)
    assert c["A"] == 1 and c["B"] == c["C"] == 2 and c["D"] == c["E"] == 3   # 번호는 정렬된 군 순서로 처음 나온 순
    assert c.equals(rg.ward_clusters(f.sample(frac=1, random_state=1), 3))


# ---------------------------------------------------------------- 혼합 묶음 (hybrid)
from pbd.forecast_v3 import hybrid as hy  # noqa: E402


def _toy():
    # ATC 1단계 A: A01(10개), A02(4개), A03(1개) / C: C01(6개), C02(2개)
    n = pd.Series({"A01": 10, "A02": 4, "A03": 1, "C01": 6, "C02": 2})
    top = pd.Series({g: g[0] for g in n.index})
    # 패턴: A02·A03은 가깝고 A01은 멀다. C01·C02는 가깝다. C02는 A쪽 값과도 가깝지만 ATC 1단계가 달라 합치면 안 된다
    z = pd.DataFrame({"x": [0.0, 5.0, 5.2, 5.1, 5.15]}, index=n.index)
    return n, top, z


def test_threshold_merge_stays_within_atc1():
    n, top, z = _toy()
    lab = hy.threshold_merge(z, top, tau=0.5)
    assert lab["A02"] == lab["A03"] == "A02+A03" and lab["A01"] == "A01"
    assert lab["C01"] == lab["C02"] == "C(전체)"
    assert (top.groupby(lab).nunique() == 1).all()


def test_enforce_min_size_merges_to_nearest_same_atc1():
    n, top, z = _toy()
    lab = pd.Series({g: g for g in n.index})       # 합치지 않은 상태
    out = hy.enforce_min_size(lab, n, z, top, 3)
    # A03(1개)은 가장 가까운 A02로, C02(2개)는 C01로. A01은 그대로
    assert out["A03"] == out["A02"] and out["A01"] == "A01" and out["C02"] == out["C01"]
    assert n.groupby(out).sum().min() >= 3


def test_size_merge_h3_h4():
    n, top, z = _toy()
    h3 = hy.size_merge(n, top, 5, None)
    assert h3["A01"] == "A01" and h3["A02"] == h3["A03"] == "A_기타" and h3["C01"] == "C01" and h3["C02"] == "C_기타"
    h4 = hy.size_merge(n, top, 5, z)
    # A02·A03은 A의 유일한 큰 군 A01에, C02는 C01에
    assert h4["A02"] == h4["A03"] == h4["A01"] == "A01+α" and h4["C02"] == h4["C01"] == "C01+α"
    h4b = hy.size_merge(n, top, 20, z)              # 큰 군이 없으면 ATC 1단계 전체
    assert set(h4b) == {"A(전체)", "C(전체)"}


def test_strata_permutation_never_crosses_strata():
    # 층 2개, 각 층에 블록 2개. 층마다 블록 2개를 한 묶음으로 → 층 안에서 섞어도 묶음 구조가 같아 η² 불변 → p = 1
    e = np.array([0, 1, 5, 6, 10, 11, 20, 21.0])
    w = np.ones(8)
    block = np.array([0, 0, 1, 1, 2, 2, 3, 3])
    strata = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    lab = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    obs, null_mean, p = hy.perm_test_strata(e, w, lab, np.random.default_rng(0), block, strata, n_perm=200)
    assert null_mean == pytest.approx(obs) and p == pytest.approx(1.0)


def test_adj_eta2():
    assert hy.adj_eta2(0.5, 101, 11) == pytest.approx(1 - 0.5 * 100 / 90)


# ---------------------------------------------------------------- 부분 풀링 (pooling)
from pbd.forecast_v3 import pooling as po  # noqa: E402


def test_loo_hard_hand_computed():
    e, w, lab = np.array([1.0, 3.0, 5.0, 7.0]), np.array([1.0, 1.0, 1.0, 1.0]), np.array([0, 0, 1, 2])
    p = po.loo_hard(e, w, lab)
    # 군 0: 서로의 값. 군 1·2는 혼자 → i를 뺀 전체 평균 (1+3+7)/3, (1+3+5)/3
    assert p == pytest.approx([3.0, 1.0, 11 / 3, 3.0])


def test_eb_no_group_signal_shrinks_to_grand_mean():
    # 군 평균이 모두 같으면 군간 분산 0 → 모든 군 추정 = 전체 평균
    e = np.array([0, 2, 0, 2, 0, 2, 0, 2.0])
    w = np.ones(8)
    l2 = np.array([0, 0, 1, 1, 2, 2, 3, 3])
    top2 = np.array([0, 0, 1, 1])
    m2s, m1s, mu, dg = po.eb_fit(e, w, l2, top2)
    assert mu == pytest.approx(1.0) and dg["tau2_atc2"] == 0 and dg["tau2_atc1"] == 0
    assert m2s == pytest.approx(np.ones(4)) and m1s == pytest.approx(np.ones(2))


def test_eb_strong_signal_keeps_group_means_and_empty_group_uses_parent():
    # 군 안 분산이 아주 작고 군 평균이 크게 다르면 줄임이 거의 없다. 비어 있는 ATC 2단계 군(3)은 자기 ATC 1단계(1) 추정을 쓴다
    e = np.array([0, 0.01, 10, 10.01, 20, 20.01])
    w = np.ones(6)
    l2 = np.array([0, 0, 1, 1, 2, 2])
    top2 = np.array([0, 0, 1, 1])
    m2s, m1s, mu, dg = po.eb_fit(e, w, l2, top2)
    assert m2s[:3] == pytest.approx([0.005, 10.005, 20.005], abs=0.05)
    assert m2s[3] == pytest.approx(m1s[1])


def test_loo_r2_zero_for_grand_mean():
    e, w = np.array([1.0, 2.0, 4.0]), np.ones(3)
    assert po.loo_r2(e, w, po.loo_mean(e, w)) == pytest.approx(0.0)


def test_eb_dl_matches_mom_when_groups_equal_size():
    # 군 크기가 모두 같고 ATC 1단계가 하나면 DL과 적률법의 τ² 추정이 같은 식이 된다(가중치가 같음)
    rng = np.random.default_rng(3)
    l2 = np.repeat(np.arange(5), 4)
    e = rng.normal(0, 1, 20) + np.repeat([0, 1, 2, 3, 4.0], 4)
    w = np.ones(20)
    top2 = np.zeros(5, dtype=int)
    a = po.eb_fit(e, w, l2, top2, "mom")[3]["tau2_atc2"]
    b = po.eb_fit(e, w, l2, top2, "dl")[3]["tau2_atc2"]
    # MoM: mean(d²) − σ²/4, DL: (Q − 4)/c, c = 5p − p = 4p, Q = pΣd², p = 4/σ² → (pΣd² − 4)/(4p) = Σd²/4 − σ²/4
    # 둘의 차이 = mean(d²) 대 Σd²/4 (= 5/4 · mean) → 손 계산 비
    d2 = (np.bincount(l2, weights=e) / 4 - e.mean()) ** 2
    sig2 = po.eb_fit(e, w, l2, top2, "mom")[3]["sigma2"]
    assert a == pytest.approx(d2.mean() - sig2 / 4)
    assert b == pytest.approx(d2.sum() / 4 - sig2 / 4)


# ---------------------------------------------------------------- 모델 우위 일관성 (consistency)
from pbd.forecast_v3 import consistency as cs  # noqa: E402


def test_group_wape_renormalizes_within_group_and_period():
    err = pd.DataFrame({"wape_G": [0.1, 0.3, 0.2], "wape_B3": [0.05, 0.2, 0.3]}, index=["a", "b", "c"])
    w = pd.Series({"a": 1.0, "b": 3.0, "c": 2.0, "d": 9.0})     # d는 이 구간에 칸이 없음 → 빠짐
    lab = pd.Series({"a": "X", "b": "X", "c": "Y", "d": "Y"})
    g = cs.group_wape(err, w, lab, "G")
    assert g["X"] == pytest.approx((0.1 + 0.9) / 4) and g["Y"] == pytest.approx(0.2)


def test_sign_rule_requires_all_periods():
    d = pd.DataFrame({"disc": [0.01, 0.01], "hold": [0.02, 0.02], "y2023": [0.01, 0.01], "y2024": [0.01, -0.001],
                      "y2025": [0.03, 0.03], "y2022": [-0.5, -0.5]}, index=["ok", "no"])
    r = cs.sign_rule(d)
    assert r["ok"] and not r["no"]   # 2022는 따로 보지 않는다(발견 구간에 포함)


def test_boot_delta_interval_contains_point_and_is_deterministic():
    err = pd.DataFrame({"wape_G": [0.2, 0.3, 0.25, 0.4], "wape_B3": [0.1, 0.2, 0.2, 0.3]}, index=list("abcd"))
    w = pd.Series(1.0, index=list("abcd"))
    lab = pd.Series("X", index=list("abcd"))
    a = cs.boot_delta(err, w, lab, "B3", np.random.default_rng(1), n_boot=500)
    b = cs.boot_delta(err, w, lab, "B3", np.random.default_rng(1), n_boot=500)
    assert a.equals(b) and a.loc["X", "lo90"] > 0 and a.loc["X", "lo90"] <= 0.0875 <= a.loc["X", "hi90"]
