"""P2 검토 반영 B5 (2026-11-19 사용자 지시, 기존 예측값만 — 새 학습 없음).

상위 200·층화 200(그리고 비교용 성분 32)에서 평가 대상 달의 마지막 12개월(2025-01 ~ 2025-12)을 뺀 민감도.
이유: 마지막 12개월은 심사가 덜 끝나 덜 집계됐을 수 있고(공식 작성 기준), M0(완전히 집계된 작년 값)와 M2(덜 집계된
최근 수준을 학습)에 다르게 작용할 수 있다(검토 B의 B5). 성분 32의 같은 민감도는 extra_revision.sensitivity가 이미 냈다.
지표: WAPE, 2022 청구금액 가중 WAPE, MASE, 성분별 WAPE 차(M2 − M0)의 부호·Wilcoxon 검정(extra_window.test와 같은 방식).
사용: python -m pbd.forecast.extra_recent  (출력 outputs/p2/extra_recent.csv)
"""
from __future__ import annotations

import pandas as pd

from pbd.common import ROOT
from pbd.forecast import extra_weight as W
from pbd.forecast.extra_revision import RECENT
from pbd.forecast.extra_window import test

OUT = ROOT / "outputs/p2"
SETS = (("상위 200", "extra_top200_pred.csv"), ("층화 200", "extra_strat200_pred.csv"), ("성분 32", "t3_pred.csv"))


def table() -> pd.DataFrame:
    rows = []
    for name, f in SETS:
        sc = pd.read_csv(OUT / f, dtype={"c4": str})
        sc = sc[sc["model"].isin(["M0", "M2"])].dropna(subset=["yhat"])
        w = W.amt2022(sorted(sc["c4"].unique()))
        for lag, g in sc.groupby("lag"):
            for part, d in (("전체", g), ("마지막 12개월 뺌", g[~g["target"].isin(RECENT)])):
                k, n, ps, pw = test(d)
                r = dict(set=name, lag=lag, part=part, months=d["target"].nunique(), first=d["target"].min(), last=d["target"].max(),
                         m2_better=k, n_series=n, sign_p=ps, wilcoxon_p=pw)
                for m in ("M0", "M2"):
                    x = d[d.model == m]
                    r[f"wape_{m.lower()}"] = x["ae"].sum() / x["y"].sum()
                    r[f"wape_amt_{m.lower()}"] = W.weighted_wape(x, w)
                    r[f"mase_{m.lower()}"] = x["sae"].mean()
                rows.append(r)
    return pd.DataFrame(rows)


def main() -> None:
    t = table()
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "extra_recent.csv", index=False)
    show = t[["set", "lag", "part", "months", "wape_m0", "wape_m2", "wape_amt_m0", "wape_amt_m2", "m2_better", "n_series", "sign_p"]]
    print(show.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
