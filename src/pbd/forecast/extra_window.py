"""P2 평가 기간 맞춤 비교 (2026-11-19 사용자 지시, 기존 예측값만 — 새 학습 없음).

상위 200개에서 ① 공개 시차 5·다음 달(extra_top200_pred.csv, origin 2023-01 ~ 2025-11, 대상 달 2023-02 ~ 2025-12)과
② 공개 시차 0·6개월 앞(extra_horizon_top200_pred.csv h = 6, origin 2023-01 ~ 2024-12, 대상 달 2023-07 ~ 2025-06)은
같은 대상 달에서 같은 함수 호출(gbm(available(panel, t − 1, 5), t − 1, 5))이라 예측이 같다. 이 스크립트는 그것을 확인하고
평가 대상 달을 나눠 오차와 성분별 검정(부호·Wilcoxon, M2 − M0)을 낸다. 32개 성분도 같은 방식으로.
사용: python -m pbd.forecast.extra_window  (출력 outputs/p2/extra_window.csv, extra_window_months.csv)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from pbd.common import ROOT
from pbd.forecast import extra_weight as W

OUT = ROOT / "outputs/p2"
SETS = (("상위 200", "extra_top200_pred.csv", "extra_horizon_top200_pred.csv"), ("성분 32", "t3_pred.csv", "extra_horizon_pred.csv"))


def test(d: pd.DataFrame) -> tuple[int, int, float, float]:
    per = lambda x: x.groupby("c4")["ae"].sum() / x.groupby("c4")["y"].sum()   # noqa: E731
    di = (per(d[d.model == "M2"]) - per(d[d.model == "M0"])).dropna()
    k, n = int((di < 0).sum()), int((di != 0).sum())
    return k, len(di), stats.binomtest(k, n, 0.5).pvalue, stats.wilcoxon(di).pvalue


def main() -> None:
    rows, months = [], []
    for name, f1, f2 in SETS:
        a = pd.read_csv(OUT / f1, dtype={"c4": str})
        a = a[(a["lag"] == 5) & a["model"].isin(["M0", "M2"])]
        b = pd.read_csv(OUT / f2, dtype={"c4": str})
        b = b[(b["h"] == 6) & b["model"].isin(["M0", "M2"])]
        key = ["c4", "target", "model"]
        m = a.merge(b, on=key, suffixes=("_1", "_2"))
        same = float(np.abs(m["yhat_1"] - m["yhat_2"]).max())
        w = W.amt2022(sorted(a["c4"].unique()))
        common = set(b["target"])
        for part, d in (("① 시차 5·다음 달 전체", a), ("② 시차 0·6개월 앞 전체", b),
                        ("①과 ②에 공통인 대상 달", a[a["target"].isin(common)]), ("①에만 있는 대상 달", a[~a["target"].isin(common)])):
            k, n, sp, wp = test(d)
            m0, m2 = d[d.model == "M0"], d[d.model == "M2"]
            rows.append(dict(set=name, part=part, months=d["target"].nunique(), first=d["target"].min(), last=d["target"].max(),
                             wape_m0=m0["ae"].sum() / m0["y"].sum(), wape_m2=m2["ae"].sum() / m2["y"].sum(),
                             wape_amt_m0=W.weighted_wape(m0, w), wape_amt_m2=W.weighted_wape(m2, w),
                             m2_better=k, n_series=n, sign_p=sp, wilcoxon_p=wp, same_prediction_maxdiff=same))
        for (t, mo), g in a[~a["target"].isin(common)].groupby(["target", "model"]):
            months.append(dict(set=name, target=t, model=mo, wape=g["ae"].sum() / g["y"].sum()))
    r = pd.DataFrame(rows)
    r.to_csv(OUT / "extra_window.csv", index=False)
    pd.DataFrame(months).to_csv(OUT / "extra_window_months.csv", index=False)
    with pd.option_context("display.width", 250):
        print(r.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
