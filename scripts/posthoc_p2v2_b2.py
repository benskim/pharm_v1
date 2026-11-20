"""사후 탐색(사전등록 목록 밖, 판정에 쓰지 않음): P2 v2 백테스트 B2 오차의 성분별 기여.
입력: outputs/p2v2/backtest/predictions.csv(주 결과), outputs/p2v2/target.csv, fc_panel(2023-01 ~ 2025-12 정답).
평가 칸을 단순화했다(정답 관측·> 0, B1·B2·B3·G 모두 예측). 파이프라인 평가 칸(재등장 첫 달 등 제외)과 조금 다르다.
사용: python -I scripts/posthoc_p2v2_b2.py → outputs/p2v2/posthoc/b2_contrib.csv, b2_top_series.csv
잠금 파일(scripts/p2v2_*.py)이 아니다. 백테스트 결과를 바꾸지 않는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/p2v2/posthoc"


def main() -> None:
    p = pd.read_csv(ROOT / "outputs/p2v2/backtest/predictions.csv", dtype={"c4": str})
    t = pd.read_csv(ROOT / "outputs/p2v2/target.csv", dtype={"c4": str, "cls3": str}).set_index("c4")
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    y = con.execute("""select c4, month t, claim_qty y from fc_panel
                       where observed and month between '2023-01' and '2025-12'""").df()
    p = p[p.t >= "2023-01"]
    w = p.pivot_table(index=["c4", "t"], columns="model", values="yhat").reset_index().merge(y, on=["c4", "t"])
    w = w.dropna(subset=["B1", "B2", "B3", "G", "y"])
    w = w[w.y > 0]
    Y = w.groupby("c4").y.sum()
    rows = {}
    for m in ("B1", "B2", "B3", "G"):
        e = (w[m] - w.y).abs().groupby(w.c4).sum() / Y
        rows[m] = e * t.w.reindex(e.index)
    c = pd.DataFrame(rows)
    c["w"] = t.w.reindex(c.index)
    c["cls3"] = t.cls3.reindex(c.index)
    c = c.sort_values("B2", ascending=False)
    name = con.execute("""select c4, arg_max(ingredient_name, n) from (select c4, ingredient_name, count(distinct product_code) n
                          from stg_benefit where list_month = '2022-07' group by 1, 2) group by 1""").df()
    c = c.join(name.set_index("c4").iloc[:, 0].rename("name_2022_07"))
    OUT.mkdir(parents=True, exist_ok=True)
    c.to_csv(OUT / "b2_contrib.csv", float_format="%.6g")
    top = c.index[0]
    s = w[w.c4 == top][["t", "y", "B1", "B2", "B3", "G"]].assign(b2_over_y=lambda d: d.B2 / d.y)
    s.to_csv(OUT / "b2_top_series.csv", index=False, float_format="%.6g")
    tot = c[["B1", "B2", "B3", "G"]].sum()
    tot = tot.astype(float)
    print("단순화 칸 기준 금액 가중 WAPE:", tot.round(4).to_dict())
    print(f"B2 기여 1위 {top} ({c.loc[top, 'name_2022_07']}, 분류 {c.loc[top, 'cls3']}, w {c.loc[top, 'w']:.4f}): "
          f"{c.loc[top, 'B2']:.4f} = B2 전체의 {c.loc[top, 'B2'] / tot['B2']:.1%}")
    print("B2 기여 상위 5:", c.B2.head(5).round(4).to_dict())
    ex = tot - c.loc[top, ["B1", "B2", "B3", "G"]].astype(float)
    print("그 성분을 뺀 합(가중치 다시 정규화 안 함):", ex.round(4).to_dict())
    print(s.loc[s.b2_over_y.idxmax()].to_dict())


if __name__ == "__main__":
    sys.exit(main())
