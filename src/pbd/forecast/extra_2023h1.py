"""P2 검토 반영 B6 (2026-11-19 사용자 지시, 기존 예측값과 패널만 — 새 학습 없음).

질문: 2023년 상반기에 M0(12개월 전 값)의 오차가 큰 원인. M0의 기준 해는 2022년이다.
a. 대상 달별 WAPE (M0·M2, lag 5): 2023-01~06과 나머지.
b. 기준 해 이상 여부: 대상 달 m의 집합 합 비율 Σy(m) ÷ Σy(m − 12)를 2023 상반기·2024 상반기·2025 상반기로 비교하고,
   2022 상반기 수준이 코로나 전(2019 상반기)과 얼마나 달랐는지 Σy(2022-m) ÷ Σy(2019-m)로 본다. 성분 32는 약효분류별로도.
c. 코로나 표시 특징 확인: config/params.yaml covid_months 구간과 2022년이 표시되는지. M0는 이 특징을 쓰지 않는다.
원인 판정은 하지 않는다(전후 비교와 연관만).
사용: python -m pbd.forecast.extra_2023h1  (출력 outputs/p2/extra_2023h1_months.csv, extra_2023h1_ratio.csv)
"""
from __future__ import annotations

import pandas as pd

from pbd.common import DB_PATH, ROOT, connect
from pbd.forecast import rolling as R

OUT = ROOT / "outputs/p2"
SETS = (("상위 200", "extra_top200_pred.csv"), ("층화 200", "extra_strat200_pred.csv"), ("성분 32", "t3_pred.csv"))
H1 = ("01", "02", "03", "04", "05", "06")


def month_errors(sc: pd.DataFrame) -> pd.DataFrame:
    d = sc[(sc["lag"] == 5) & sc["model"].isin(["M0", "M2"])].dropna(subset=["yhat"])
    g = d.groupby(["target", "model"]).apply(lambda x: x["ae"].sum() / x["y"].sum(), include_groups=False).unstack()
    return g.rename(columns={"M0": "wape_m0", "M2": "wape_m2"}).reset_index()


def ratios(panel: pd.DataFrame, c4s: list[str], cls: dict | None = None) -> pd.DataFrame:
    p = panel[panel["c4"].isin(c4s) & panel["observed"]].copy()
    p["grp"] = p["c4"].map(cls) if cls else "전체"
    tot = p.groupby(["grp", "month"])["claim_qty"].sum()
    rows = []
    for grp in tot.index.get_level_values(0).unique():
        s = tot.loc[grp]
        for yr in (2022, 2023, 2024, 2025):
            for mm in H1:
                m = f"{yr}-{mm}"
                for base_yr, kind in ((yr - 1, "전년 대비"), (2019, "2019 대비")):
                    b = f"{base_yr}-{mm}"
                    if m in s.index and b in s.index and not (kind == "2019 대비" and yr != 2022):
                        rows.append(dict(grp=grp, year=yr, month=m, kind=kind, ratio=s[m] / s[b]))
    r = pd.DataFrame(rows)
    return r.groupby(["grp", "year", "kind"])["ratio"].agg(["median", "min", "max"]).reset_index()


def main() -> None:
    con = connect(DB_PATH, read_only=True)
    panel = con.execute("select c4, month, claim_qty, observed from fc_panel").df()
    cls = dict(con.execute("select c4, cls from fc_target").fetchall())
    me, ra = [], []
    for name, f in SETS:
        sc = pd.read_csv(OUT / f, dtype={"c4": str})
        e = month_errors(sc).assign(set=name)
        e["half"] = ["2023 상반기" if t.startswith("2023-") and t[5:] in H1 else "나머지" for t in e["target"]]
        me.append(e)
        c4s = sorted(sc["c4"].unique())
        ra.append(ratios(panel, c4s).assign(set=name))
        if name == "성분 32":
            ra.append(ratios(panel, c4s, cls).assign(set=name))
    me, ra = pd.concat(me, ignore_index=True), pd.concat(ra, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    me.to_csv(OUT / "extra_2023h1_months.csv", index=False)
    ra.to_csv(OUT / "extra_2023h1_ratio.csv", index=False)
    print(me.groupby(["set", "half"])[["wape_m0", "wape_m2"]].mean().round(4).to_string())
    print(ra.round(3).to_string(index=False))
    print(f"코로나 표시 구간(covid_months): {R.COVID[0]} ~ {R.COVID[1]}. 2022년 표시 여부: {R.COVID[0] <= '2022-01' <= R.COVID[1]}")


if __name__ == "__main__":
    main()
