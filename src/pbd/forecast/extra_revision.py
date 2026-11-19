"""P2 추가 확인 A (2026-11-19): 사용통계 사후 수정. **사용자 가설, 판본 하나뿐이라 간접 시험만 함.**

가설(사용자): "공개된 지 얼마 안 된 달의 사용통계는 덜 집계되어 있고, 나중에 위로 수정된다."
사실: 같은 달을 다른 시점에 받은 판본이 없어 수정 크기는 직접 잴 수 없다 (BACKLOG X11, LIMITATIONS L17).

a. 민감도: 평가 정답(예측 대상 달)에서 분석 끝 기준 마지막 12개월(2025-01 ~ 2025-12)을 뺀 MASE·WAPE (기존 예측값 t3_pred.csv).
b. 간접 흔적: 단위 = 약효분류군(주, 96개월 원외 관측 성분만 분류별 합, 구성 고정) / 성분(보조). 비율 r = y_m ÷ y_{m−12}.
   최근 달 k = 1 ~ 12(2025-12 = 1 … 2025-01 = 12), 오래된 달 = 2023-01 ~ 2024-12. 판단(기본값, DECISIONS 2026-11-19):
   단위별 하락률 = 최근 3개월 r 중앙값 ÷ 오래된 달 r 중앙값 − 1. 하락률의 단위 간 중앙값 ≤ −5% 그리고 하락률 < 0인 단위 ≥ 70%이면
   "덜 집계 흔적 있음", 아니면 "흔적 없음". 실제 수요 감소와 구분할 수 없다.
사용: python -m pbd.forecast.extra_revision  (출력 outputs/p2/extra_revision_*.csv, reports/figures/p2_revision_ratio.png)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pbd.common import DB_PATH, ROOT, connect
from pbd.forecast import rolling as R

OUT = ROOT / "outputs/p2"
FIG = ROOT / "reports/figures"
END = "2025-12"
RECENT = R.months("2025-01", END)
OLD = R.months("2023-01", "2024-12")
DROP_CUT, SHARE_CUT, RECENT_N = -0.05, 0.70, 3


def sensitivity() -> pd.DataFrame:
    sc = pd.read_csv(OUT / "t3_pred.csv", dtype={"c4": str})
    sc = sc[sc["model"].isin(["M0", "M2", "M3"])].dropna(subset=["yhat"])
    rows = []
    for (lag, m), g in sc.groupby(["lag", "model"]):
        for part, d in (("전체", g), ("마지막 12개월 뺌", g[~g["target"].isin(RECENT)])):
            rows.append(dict(lag=lag, model=m, part=part, n=len(d), mase=d["sae"].mean(), wape=d["ae"].sum() / d["y"].sum()))
    return pd.DataFrame(rows)


def ratios(unit: pd.DataFrame, key: str) -> pd.DataFrame:
    """unit: (key, month, qty). r = y_m / y_{m−12}"""
    w = unit.pivot(index=key, columns="month", values="qty")
    out = []
    for m in RECENT + OLD:
        p = R.madd(m, -12)
        if p in w.columns:
            r = (w[m] / w[p]).rename("r").reset_index().assign(month=m)
            out.append(r)
    d = pd.concat(out, ignore_index=True).dropna()
    d = d[np.isfinite(d["r"]) & (d["r"] > 0)]
    d["k"] = d["month"].map(lambda m: R.midx(END) - R.midx(m) + 1 if m in RECENT else None)
    return d


def judge(d: pd.DataFrame, key: str) -> tuple[dict, pd.DataFrame]:
    rec = d[d["k"].between(1, RECENT_N)].groupby(key)["r"].median()
    old = d[d["k"].isna()].groupby(key)["r"].median()
    drop = (rec / old - 1).dropna()
    v = dict(units=len(drop), median_drop=float(drop.median()), share_negative=float((drop < 0).mean()))
    v["verdict"] = "덜 집계 흔적 있음" if v["median_drop"] <= DROP_CUT and v["share_negative"] >= SHARE_CUT else "흔적 없음"
    by_k = d.assign(grp=d["k"].fillna(0).astype(int)).groupby("grp")["r"].describe(percentiles=[0.25, 0.5, 0.75])
    return v, by_k.reset_index()


def figure(dc: pd.DataFrame, di: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for f in ("/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/Supplemental/AppleGothic.ttf"):
        try:
            font_manager.fontManager.addfont(f)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=f).get_name()
            break
        except Exception:
            continue
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6), dpi=150, sharey=True)
    for ax, d, title in ((axes[0], dc, "약효분류군"), (axes[1], di, "성분")):
        groups = [d[d["k"].isna()]["r"]] + [d[d["k"] == k]["r"] for k in range(12, 0, -1)]
        labels = ["오래된 달\n(2023~24)"] + [str(k) for k in range(12, 0, -1)]
        ax.boxplot(groups, showfliers=False, widths=0.6)
        ax.set_xticks(range(1, len(labels) + 1), labels, fontsize=7)
        ax.axhline(1, color="gray", lw=0.8)
        ax.set_title(title)
        ax.set_xlabel("분석 끝(2025-12)에서 몇 개월 전")
    axes[0].set_ylabel("작년 같은 달 대비 비율")
    fig.tight_layout()
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / "p2_revision_ratio.png")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    s = sensitivity()
    s.to_csv(OUT / "extra_revision_sensitivity.csv", index=False)
    con = connect(DB_PATH, read_only=True)
    ing = con.execute("""with u as (select c4 from fc_panel group by 1 having count(*) filter (where observed) = 96)
                         select p.c4, p.month, p.claim_qty qty from fc_panel p join u using (c4)""").df()
    cls = con.execute("""with u as (select c4 from fc_panel group by 1 having count(*) filter (where observed) = 96)
                         select k.cls, p.month, sum(p.claim_qty) qty from fc_panel p join u using (c4) join fc_c4_class k using (c4)
                         group by 1, 2""").df()
    dc, di = ratios(cls, "cls"), ratios(ing, "c4")
    vc, kc = judge(dc, "cls")
    vi, ki = judge(di, "c4")
    v = pd.DataFrame([dict(unit="약효분류군 (주)", **vc), dict(unit="성분 (보조)", **vi)])
    v.to_csv(OUT / "extra_revision_verdict.csv", index=False)
    pd.concat([kc.assign(unit="약효분류군"), ki.assign(unit="성분")]).to_csv(OUT / "extra_revision_by_k.csv", index=False)
    figure(dc, di)
    with pd.option_context("display.width", 200):
        print(s.round(4).to_string(index=False))
        print(v.round(4).to_string(index=False))
        print(kc[["grp", "count", "25%", "50%", "75%"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
