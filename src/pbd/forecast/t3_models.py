"""P2 T3 (최소판): M2·M3 롤링 평가, M3 대 M2 검정, 가상 재고 시뮬레이션(비용 비율 하나).

- 모델: M0(T2), M2(신호 없음), M3(신호 특징, 기준 스냅샷 s = M, 주), M3p(s = M − 1, 보수적 민감도). 같은 하이퍼파라미터·시드.
- lag = 0, 3, 5 (t2_baseline.LAGS). origin 2023-01 ~ 2025-11, 예측 대상 M+1.
- 검정: 성분별 평균 척도 오차(MASE) 차이 M3 − M2에 부호 검정과 Wilcoxon 부호 순위 검정. p ≥ 0.05면 "차이 없음".
- 가상 재고 시뮬레이션 (전국 성분 단위, 약국 재고가 아니다): 매달 목표 재고 S를 두고 실제 수요 D(사용통계 실측)를 맞는다.
  달마다 독립(이월 없음), 리드타임 1개월 = origin M에 정한 S가 M+1에 쓰인다. 결품 비용 / 재고 비용 = 3 (가정값,
  params order_sim.cost_ratios의 가운데 값) → 분위 τ = 3 / (1 + 3) = 0.75. 기준 정책 S = M0 × (1 + 0.1) (안전계수 0.1은 가정값).
  예측 정책 S = M2 / M3의 0.75 분위 예측(LightGBM quantile).
사용: python -m pbd.forecast.t3_models  (make p2-t3)
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
COST_RATIO = R.P["order_sim"]["cost_ratios"][1]
TAU = COST_RATIO / (1 + COST_RATIO)
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
                mean_diff=per.mean(), median_diff=per.median(), a_better_series=k, sign_p=sign_p, wilcoxon_p=wil_p,
                verdict=verdict)


def order_sim(stock: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    act = panel[panel["usable"]][["c4", "month", "claim_qty"]].rename(columns={"month": "target", "claim_qty": "d"})
    d = stock.merge(act, on=["c4", "target"]).dropna(subset=["s"])
    d["short"] = (d["d"] - d["s"]).clip(lower=0)
    d["left"] = (d["s"] - d["d"]).clip(lower=0)
    days = d["target"].map(lambda m: calendar.monthrange(int(m[:4]), int(m[5:7]))[1])
    d["inv_days"] = d["left"] / (d["d"] / days)
    d["cost"] = d["left"] + COST_RATIO * d["short"]
    rows = []
    for (lag, pol), g in d.groupby(["lag", "policy"]):
        rows.append(dict(lag=lag, policy=pol, n=len(g), shortage_rate=g["short"].sum() / g["d"].sum(),
                         stockout_cells=(g["short"] > 0).mean(), avg_inventory_days=g["inv_days"].mean(),
                         cost_rel=g["cost"].sum()))
    r = pd.DataFrame(rows)
    base = r[r["policy"] == "기준 M0×1.1"].set_index("lag")["cost_rel"]
    r["cost_rel"] = r["cost_rel"] / r["lag"].map(base)
    return r


def main() -> None:
    panel, series, cls = load()
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
        stocks.append(m0.assign(s=m0["yhat"] * (1 + SAFETY), policy="기준 M0×1.1")[["c4", "target", "origin", "lag", "s", "policy"]])
        for name, kw in (("M2", {}), ("M3", dict(feat=f_same))):
            q = R.rolling(panel, R.gbm, ORIGINS, lag, series, cls=cls, quantile=TAU, **kw)
            stocks.append(q.assign(s=q["yhat"], policy=f"{name} P{int(TAU * 100)}")[["c4", "target", "origin", "lag", "s", "policy"]])
            print(f"lag {lag} {name} 분위 끝", flush=True)
    pred = pd.concat(preds, ignore_index=True)
    sc = R.score(pred, panel, scale)
    sc["flu"] = sc["target"].str[5:7].astype(int).isin(R.P["flu_season_months"])
    fe = pd.concat(feats, ignore_index=True)
    fe["has_signal"] = (fe["cut_frac"] > 0) | (fe["susp_frac"] > 0) | (fe["listing_n"] > 0)
    sc = sc.merge(fe[["c4", "origin", "lag", "has_signal"]], on=["c4", "origin", "lag"], how="left")
    OUT.mkdir(parents=True, exist_ok=True)
    sc.to_csv(OUT / "t3_pred.csv", index=False)
    fe.to_csv(OUT / "t3_features.csv", index=False)
    err = pd.concat([table(sc, ["model", "lag"]).assign(part="전체"),
                     table(sc, ["model", "lag", "flu"]).assign(part="유행기(11~3월) 여부"),
                     table(sc, ["model", "lag", "has_signal"]).assign(part="origin에 창 안 신호 여부")], ignore_index=True)
    err.to_csv(OUT / "t3_errors.csv", index=False)
    cmp_ = pd.DataFrame([compare(sc, a, b, lag) for lag in LAGS for a, b in (("M3", "M2"), ("M3p", "M2"), ("M2", "M0"))])
    cmp_.to_csv(OUT / "t3_compare.csv", index=False)
    sim = order_sim(pd.concat(stocks, ignore_index=True), panel)
    sim.to_csv(OUT / "t3_order_sim.csv", index=False)
    with pd.option_context("display.width", 250):
        print(err[err["part"] == "전체"].round(4).to_string(index=False))
        print(cmp_.round(4).to_string(index=False))
        print(sim.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
