"""P2 유의성 검정 (2026-11-19 사용자 지시, 기존 예측값만 사용 — 새 학습 없음). 판단 기준은 DECISIONS 2026-11-19에 먼저 기록.

대상: 상위 200(extra_top200_pred.csv), 층화 200(extra_strat200_pred.csv). 시차 5·0, 다음 달. 비교 M2 − M0, M3 − M2.
① 비가중: 성분별 WAPE 차이 d_i = WAPE_i(a) − WAPE_i(b)에 부호 검정·Wilcoxon (기존 방식, 음수 = a가 나음)
② DM(비가중): origin별 전체 WAPE 차이(35개)의 평균 = 0, Newey–West 분산(지연 = 시차, 겹치는 예측 대비), t 분포(자유도 T − 1)
③ DM(금액 가중): origin별 금액 가중 WAPE 차이로 같은 검정
Holm 보정: 검정 종류마다 이 표의 비교 8개를 한 묶음으로. 판정(비가중) = 부호·Wilcoxon 둘 다 Holm 뒤 p < 0.05.
사용: python -m pbd.forecast.extra_signif  (출력 outputs/p2/extra_signif.csv)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from pbd.common import ROOT
from pbd.forecast import extra_weight as W
from pbd.forecast.t3_models import holm

OUT = ROOT / "outputs/p2"
SETS = (("상위 200", "extra_top200_pred.csv"), ("층화 200", "extra_strat200_pred.csv"))
PAIRS = (("M2", "M0"), ("M3", "M2"))
LAGS = (5, 0)
ALPHA = 0.05


def nw_t(d: np.ndarray, lags: int) -> tuple[float, float]:
    """평균 = 0 검정 (Newey–West 분산, Bartlett 가중). (t, 양측 p)"""
    d = d[np.isfinite(d)]
    T = len(d)
    u = d - d.mean()
    v = u @ u / T
    for k in range(1, min(lags, T - 1) + 1):
        v += 2 * (1 - k / (lags + 1)) * (u[k:] @ u[:-k]) / T
    t = d.mean() / np.sqrt(v / T)
    return float(t), float(2 * stats.t.sf(abs(t), df=T - 1))


def one(sc: pd.DataFrame, a: str, b: str, lag: int, w: pd.Series) -> dict:
    x = sc[(sc["lag"] == lag) & sc["model"].isin([a, b])].dropna(subset=["yhat"])
    key = ["c4", "origin"]
    xa, xb = x[x.model == a].set_index(key), x[x.model == b].set_index(key)
    common = xa.index.intersection(xb.index)
    xa, xb = xa.loc[common].reset_index(), xb.loc[common].reset_index()
    per = lambda d: d.groupby("c4")["ae"].sum() / d.groupby("c4")["y"].sum()   # noqa: E731
    di = (per(xa) - per(xb)).dropna()
    k, n = int((di < 0).sum()), int((di != 0).sum())
    sign_p = stats.binomtest(k, n, 0.5).pvalue
    wil_p = stats.wilcoxon(di).pvalue
    tot = lambda d: d.groupby("origin")["ae"].sum() / d.groupby("origin")["y"].sum()   # noqa: E731
    dm_t, dm_p = nw_t((tot(xa) - tot(xb)).to_numpy(), lag)
    def wt(d):
        g = d.assign(r=d["ae"] / d["y"], w=d["c4"].map(w))
        return g.groupby("origin").apply(lambda h: (h["r"] * h["w"]).sum() / h["w"].sum(), include_groups=False)
    dmw_t, dmw_p = nw_t((wt(xa) - wt(xb)).to_numpy(), lag)
    wa, wb = xa["ae"].sum() / xa["y"].sum(), xb["ae"].sum() / xb["y"].sum()
    return dict(lag=lag, a=a, b=b, n_series=len(di), wape_a=wa, wape_b=wb, wape_amt_a=W.weighted_wape(xa, w), wape_amt_b=W.weighted_wape(xb, w),
                median_diff=float(di.median()), a_better_series=k, sign_p=sign_p, wilcoxon_p=wil_p, dm_t=dm_t, dm_p=dm_p, dmw_t=dmw_t, dmw_p=dmw_p)


def main() -> None:
    rows = []
    for name, f in SETS:
        sc = pd.read_csv(OUT / f, dtype={"c4": str})
        w = W.amt2022(sorted(sc["c4"].unique()))
        for lag in LAGS:
            for a, b in PAIRS:
                rows.append(dict(set=name, **one(sc, a, b, lag, w)))
    t = pd.DataFrame(rows)
    for c in ("sign_p", "wilcoxon_p", "dm_p", "dmw_p"):
        t[c + "_holm"] = holm(t[c])
    sig = (t["sign_p_holm"] < ALPHA) & (t["wilcoxon_p_holm"] < ALPHA)
    t["verdict"] = np.where(~sig, "유의한 차이 없음", np.where(t["median_diff"] < 0, t["a"] + "가 나음", t["b"] + "가 나음"))
    t["dm_verdict"] = np.where(t["dm_p_holm"] >= ALPHA, "유의한 차이 없음", np.where(t["dm_t"] < 0, t["a"] + "가 나음", t["b"] + "가 나음"))
    t["dmw_verdict"] = np.where(t["dmw_p_holm"] >= ALPHA, "유의한 차이 없음", np.where(t["dmw_t"] < 0, t["a"] + "가 나음", t["b"] + "가 나음"))
    t.to_csv(OUT / "extra_signif.csv", index=False)
    with pd.option_context("display.width", 260):
        print(t[["set", "lag", "a", "b", "wape_a", "wape_b", "wape_amt_a", "wape_amt_b", "a_better_series", "sign_p_holm", "wilcoxon_p_holm",
                 "verdict", "dm_p_holm", "dm_verdict", "dmw_p_holm", "dmw_verdict"]].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
