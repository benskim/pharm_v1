"""P2 추가 확인 B (2026-11-19, 사용자 지시): 몇 개월 앞까지 믿을 만한가. 주 결과는 동결이라 이 파일만 새로 둔다.

공개 시차 0(그 달까지의 사용통계를 바로 쓴다고 둠), 예측 기간 h = 1 ~ 12개월, M0·M2, 성분 32, 기존 하이퍼파라미터·시드.
origin M = 2023-01 ~ 2024-12(24개)로 고정해 모든 h가 같은 origin을 쓴다(대상 M + h ≤ 2025-12).
구현: rolling.gbm·m0는 '대상 = origin + 1'이므로 origin′ = M + h − 1, lag′ = h − 1로 부른다
(available(panel, origin′, lag′) = month ≤ M, 대상 = M + h). 누수 없음: 입력은 M 이하 사용통계뿐.
판단 기준(DECISIONS 2026-11-19, 실행 전 기록): 지표 WAPE(%), 보조 MASE. 가설 "1개월 앞 대비 WAPE가 상대 10% 이상 커지는
첫 개월 수가 4개월 이상"(M2로 판정). "믿을 만함" = M2 WAPE < M0 WAPE, 보고 = M2가 M0를 이기는 마지막 개월 수.
사용: python -m pbd.forecast.extra_horizon  (출력 outputs/p2/extra_horizon.csv, reports/figures/p2_horizon_curve.png)
"""
from __future__ import annotations

import pandas as pd

from pbd.common import ROOT
from pbd.forecast import extra_weight as W
from pbd.forecast import rolling as R
from pbd.forecast.t2_baseline import load

OUT = ROOT / "outputs/p2"
FIG = ROOT / "reports/figures"
ORIGINS_FIXED = R.months("2023-01", "2024-12")
HORIZONS = range(1, 13)


def verdict(t: pd.DataFrame) -> dict:
    m2 = t[t["model"] == "M2"].set_index("h")["wape"]
    m0 = t[t["model"] == "M0"].set_index("h")["wape"]
    grow = m2 / m2.loc[1] - 1
    first = next((int(h) for h, g in grow.items() if g >= 0.10), None)
    win = [int(h) for h in m2.index if m2.loc[h] < m0.loc[h]]
    last_win = max(win) if win else None
    contiguous = next((h - 1 for h in HORIZONS if h not in win), 12)   # 1개월부터 끊김 없이 이기는 마지막 개월
    return dict(first_h_10pct=first, hypothesis_ge4=(first is not None and first >= 4) if first is not None else True,
                last_h_m2_beats_m0=last_win, last_contiguous_h_m2_beats_m0=contiguous)


def figure(t: pd.DataFrame) -> None:
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
    fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=150)
    for m, lab, st in (("M0", "작년 같은 달 값", "--"), ("M2", "기계학습 예측 모델", "-")):
        d = t[t["model"] == m].sort_values("h")
        ax.plot(d["h"], 100 * d["wape"], st, marker="o", ms=3, label=lab)
    ax.set_xlabel("몇 개월 앞 (자료가 바로 공개된다고 둠)")
    ax.set_ylabel("오차 WAPE (%)")
    ax.set_xticks(list(HORIZONS))
    ax.grid(alpha=0.3)
    ax.legend(frameon=False)
    fig.tight_layout()
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / "p2_horizon_curve.png")


def main() -> None:
    panel, series, cls = load()
    scale = R.scale12(panel, series)
    preds = []
    for h in HORIZONS:
        o2 = [R.madd(m, h - 1) for m in ORIGINS_FIXED]
        for name, fn in (("M0", R.m0), ("M2", R.gbm)):
            p = R.rolling(panel, fn, o2, h - 1, series, cls=cls)
            preds.append(p.assign(origin=[R.madd(o, 1 - h) for o in p["origin"]], model=name, h=h))
            print(f"h {h} {name}", flush=True)
    sc = R.score(pd.concat(preds, ignore_index=True), panel, scale)
    sc.to_csv(OUT / "extra_horizon_pred.csv", index=False)
    t = W.table(sc, ["h", "model"], W.amt2022(series))   # 청구금액 가중 WAPE 포함 (추가 지시 A)
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "extra_horizon.csv", index=False)
    pd.DataFrame([verdict(t)]).to_csv(OUT / "extra_horizon_verdict.csv", index=False)
    figure(t)
    print(t.pivot_table(index="h", columns="model", values=["wape", "mase"]).round(4).to_string())
    print(verdict(t))


if __name__ == "__main__":
    main()
