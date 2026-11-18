"""P2 T3 (최소판): M2·M3 롤링 평가, M3 대 M2 검정, 가상 재고 시뮬레이션.

- 모델: M0(T2), M2(신호 없음), M3(신호 특징, 기준 스냅샷 s = M, 주), M3p(s = M − 1, 보수적 민감도). 같은 하이퍼파라미터·시드.
- lag = 5(주), 0(참고) (t2_baseline.LAGS, lag 3은 제외 — 사용자 결정 2026-11-18). origin 2023-01 ~ 2025-11, 예측 대상 M+1.
- 검정: 성분별 평균 척도 오차(MASE) 차이 M3 − M2에 부호 검정과 Wilcoxon 부호 순위 검정, Holm 보정. p ≥ 0.05면 "차이 없음".
- 가상 재고 시뮬레이션 (전국 성분 단위, 약국 재고가 아니다): 매달 목표 재고 S를 두고 실제 수요 D(사용통계 실측)를 맞는다.
  달마다 독립(이월 없음), 리드타임 1개월 = origin M에 정한 S가 M+1에 쓰인다. 결품 비용 / 재고 비용 = 1, 3, 9 (가정값,
  params order_sim.cost_ratios) → 분위 τ = c / (1 + c) = 0.5, 0.75, 0.9. 기준 정책 S = M0 × (1 + 0.1) (안전계수 0.1은 가정값).
  예측 정책 S = M2 / M3의 τ 분위 예측(LightGBM quantile). 분위 형태의 기준 M0 분위 = y_{t−12} × 과거 계절 비율의 τ 분위
  (검토 a10). 포함률(실수요 ≤ S인 칸 비율)을 함께 낸다 (τ 근처면 분위가 맞다).
사용: python -m pbd.forecast.t3_models [claim_amt]  (make p2-t3)
"""
from __future__ import annotations

import calendar

import numpy as np
import pandas as pd
from scipy import stats

from pbd.common import DB_PATH, ROOT, connect
from pbd.forecast import rolling as R
from pbd.forecast import signals as S
from pbd.forecast.t2_baseline import LAGS, ORIGINS, load, table

OUT = ROOT / "outputs/p2"
COST_RATIOS = tuple(R.P["order_sim"]["cost_ratios"])     # 결품 비용 / 재고 비용 = 1, 3, 9 (가정값) → 분위 τ = 0.5, 0.75, 0.9
SAFETY = 0.1
ALPHA = 0.05


def compare(sc: pd.DataFrame, a: str, b: str, lag: int) -> dict:
    """성분별 평균 척도 오차 차이 (a − b). 음수 = a가 나음"""
    x = sc[(sc["lag"] == lag) & sc["model"].isin([a, b])].pivot_table(index=["c4", "origin"], columns="model", values="sae")
    x = x.dropna()
    per = (x[a] - x[b]).groupby(level="c4").mean()
    k = int((per < 0).sum())
    n = int((per != 0).sum())
    sign_p = stats.binomtest(k, n, 0.5).pvalue if n else np.nan
    wil_p = stats.wilcoxon(per).pvalue if n else np.nan
    verdict = "차이 없음" if not (sign_p < ALPHA and wil_p < ALPHA) else (f"{a}가 나음" if per.median() < 0 else f"{b}가 나음")
    return dict(lag=lag, a=a, b=b, n_series=len(per), n_cells=len(x), mase_a=x[a].mean(), mase_b=x[b].mean(),
                mean_diff=per.mean(), median_diff=per.median(), rel_diff=x[a].mean() / x[b].mean() - 1, a_better_series=k,
                sign_p=sign_p, wilcoxon_p=wil_p, verdict=verdict)


def holm(p: pd.Series) -> pd.Series:
    """Holm 보정 p (검토 a12). 한 표 안의 비교 전체를 한 묶음으로"""
    o = p.sort_values()
    m = len(o)
    adj = (o * np.arange(m, 0, -1)).cummax().clip(upper=1)
    return adj.reindex(p.index)


def add_holm(c: pd.DataFrame) -> pd.DataFrame:
    c = c.copy()
    c["sign_p_holm"] = holm(c["sign_p"])
    c["wilcoxon_p_holm"] = holm(c["wilcoxon_p"])
    ok = (c["sign_p_holm"] < ALPHA) & (c["wilcoxon_p_holm"] < ALPHA)
    c["verdict_holm"] = np.where(~ok, "차이 없음", np.where(c["median_diff"] < 0, c["a"] + "가 나음", c["b"] + "가 나음"))
    return c


def order_sim(stock: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    act = panel[panel["usable"]][["c4", "month", "claim_qty"]].rename(columns={"month": "target", "claim_qty": "d"})
    d = stock.merge(act, on=["c4", "target"]).dropna(subset=["s"])
    d["short"] = (d["d"] - d["s"]).clip(lower=0)
    d["left"] = (d["s"] - d["d"]).clip(lower=0)
    days = d["target"].map(lambda m: calendar.monthrange(int(m[:4]), int(m[5:7]))[1])
    d["inv_days"] = d["left"] / (d["d"] / days)
    d["cost"] = d["left"] + d["cr"] * d["short"]
    rows = []
    for (lag, cr, pol), g in d.groupby(["lag", "cr", "policy"]):
        rows.append(dict(lag=lag, cost_ratio=cr, tau=cr / (1 + cr), policy=pol, n=len(g), coverage=(g["d"] <= g["s"]).mean(), shortage_rate=g["short"].sum() / g["d"].sum(),
                         stockout_cells=(g["short"] > 0).mean(), avg_inventory_days=g["inv_days"].mean(),
                         cost_rel=g["cost"].sum()))
    r = pd.DataFrame(rows)
    base = r[r["policy"] == "기준 M0×1.1"].set_index(["lag", "cost_ratio"])["cost_rel"]
    r["cost_rel"] = r["cost_rel"] / [base[(l, c)] for l, c in zip(r["lag"], r["cost_ratio"])]
    return r


def main(target: str = "claim_qty") -> None:
    """target = claim_qty (주) / claim_amt (긍정 대조: 약가 예고는 수량보다 금액에 먼저 나타나야 한다). 출력 이름에 목표를 붙인다"""
    sfx = "" if target == "claim_qty" else f"_{target}"
    panel, series, cls = load(target=target)
    scale = R.scale12(panel, series)
    tb = S.load_tables(connect(DB_PATH, read_only=True))
    feat_origins = R.months(S.FIRST_SNAPSHOT, ORIGINS[-1])
    preds, stocks, feats = [], [], []
    for lag in LAGS:
        f_same = S.feature_table(tb, lag, "same", feat_origins, series)
        f_prev = S.feature_table(tb, lag, "prev", feat_origins, series)
        feats.append(f_same.assign(lag=lag))
        runs = {"M0": (R.m0, {}), "M2": (R.gbm, {}), "M3": (R.gbm, dict(feat=f_same)), "M3p": (R.gbm, dict(feat=f_prev))}
        for name, (fn, kw) in runs.items():
            preds.append(R.rolling(panel, fn, ORIGINS, lag, series, cls=cls, **kw).assign(model=name))
            print(f"lag {lag} {name} 끝", flush=True)
        m0 = preds[-4]
        cols = ["c4", "target", "origin", "lag", "s", "policy", "cr"]
        for cr in COST_RATIOS:
            tau = cr / (1 + cr)
            stocks.append(m0.assign(s=m0["yhat"] * (1 + SAFETY), policy="기준 M0×1.1", cr=cr)[cols])
            q0 = R.rolling(panel, R.m0_quantile, ORIGINS, lag, series, q=tau)
            stocks.append(q0.assign(s=q0["yhat"], policy="M0 분위", cr=cr)[cols])
            for name, kw in (("M2", {}), ("M3", dict(feat=f_same))):
                q = R.rolling(panel, R.gbm, ORIGINS, lag, series, cls=cls, quantile=tau, **kw)
                stocks.append(q.assign(s=q["yhat"], policy=f"{name} 분위", cr=cr)[cols])
                print(f"lag {lag} 비용 비율 {cr} {name} 분위 끝", flush=True)
    pred = pd.concat(preds, ignore_index=True)
    sc = R.score(pred, panel, scale)
    sc["flu"] = sc["target"].str[5:7].astype(int).isin(R.P["flu_season_months"])
    fe = pd.concat(feats, ignore_index=True)
    fe["has_signal"] = (fe["cut_frac"] > 0) | (fe["susp_frac"] > 0) | (fe["listing_n"] > 0)
    sc = sc.merge(fe[["c4", "origin", "lag", "has_signal"]], on=["c4", "origin", "lag"], how="left")
    OUT.mkdir(parents=True, exist_ok=True)
    sc.to_csv(OUT / f"t3_pred{sfx}.csv", index=False)
    fe.to_csv(OUT / f"t3_features{sfx}.csv", index=False)
    err = pd.concat([table(sc, ["model", "lag"]).assign(part="전체"),
                     table(sc, ["model", "lag", "flu"]).assign(part="유행기(11~3월) 여부"),
                     table(sc, ["model", "lag", "has_signal"]).assign(part="origin에 창 안 신호 여부")], ignore_index=True)
    err.to_csv(OUT / f"t3_errors{sfx}.csv", index=False)
    cmp_ = add_holm(pd.DataFrame([compare(sc, a, b, lag) for lag in LAGS for a, b in (("M3", "M2"), ("M3p", "M2"), ("M2", "M0"))]))
    cmp_.to_csv(OUT / f"t3_compare{sfx}.csv", index=False)
    sim = order_sim(pd.concat(stocks, ignore_index=True), panel)
    sim.to_csv(OUT / f"t3_order_sim{sfx}.csv", index=False)
    with pd.option_context("display.width", 250):
        print(err[err["part"] == "전체"].round(4).to_string(index=False))
        print(cmp_.round(4).to_string(index=False))
        print(sim.round(4).to_string(index=False))


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "claim_qty")
