"""P2 추가 지시 A (2026-11-19): 청구금액 가중 WAPE. 새 학습 없이 예측값(score 결과)으로 계산한다.

가중 WAPE = Σ_i w_i · WAPE_i, WAPE_i = 성분 i의 Σ|예측 − 실제| ÷ Σ 실제, w_i = 그 비교 집합 안에서 성분의 2022 원외 청구금액 비중.
"""
from __future__ import annotations

import pandas as pd

from pbd.common import DB_PATH, connect


def amt2022(c4s: list[str]) -> pd.Series:
    con = connect(DB_PATH, read_only=True)
    d = con.execute("""select c4, sum(claim_amt) amt from fc_panel where month like '2022-%' and observed group by 1""").df()
    return d.set_index("c4")["amt"].reindex(c4s)


def weighted_wape(sc: pd.DataFrame, w: pd.Series) -> float:
    """sc: 한 모델·한 설정의 score 결과(c4, ae, y). 예측이 없는 칸은 뺀다"""
    v = sc.dropna(subset=["yhat"]) if "yhat" in sc else sc
    per = v.groupby("c4").apply(lambda g: g["ae"].sum() / g["y"].sum(), include_groups=False)
    ww = w.reindex(per.index)
    return float((per * ww).sum() / ww.sum())


def table(sc: pd.DataFrame, keys: list[str], w: pd.Series) -> pd.DataFrame:
    rows = []
    for k, g in sc.groupby(keys):
        v = g.dropna(subset=["yhat"])
        rows.append(dict(zip(keys, k if isinstance(k, tuple) else (k,)), n=len(v), mase=v["sae"].mean(),
                         wape=v["ae"].sum() / v["y"].sum(), wape_amt=weighted_wape(v, w)))
    return pd.DataFrame(rows)


def main() -> None:
    """성분 32 주 결과(t3_pred.csv, 시차 0·5, M0·M2·M3)의 청구금액 가중 WAPE → outputs/p2/extra_weighted_32.csv"""
    from pbd.common import ROOT
    out = ROOT / "outputs/p2"
    sc = pd.read_csv(out / "t3_pred.csv", dtype={"c4": str})
    sc = sc[sc["model"].isin(["M0", "M2", "M3"])]
    t = table(sc, ["model", "lag"], amt2022(sorted(sc["c4"].unique())))
    t.to_csv(out / "extra_weighted_32.csv", index=False)
    print(t.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
