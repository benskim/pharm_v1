"""P2 T2 기준선 (최소판): M0 롤링 origin 오차표. lag = 0, 3 (사용자 지시 2026-11-18), 5 (PLAN_P2 §3 기본, DECISIONS 2026-11-16).
origin M = 2023-01 ~ 2025-11 (예측 대상 M+1 ≤ 2025-12).
사용: python -m pbd.forecast.t2_baseline  (make p2-t2)
"""
from __future__ import annotations

import pandas as pd

from pbd.common import DB_PATH, ROOT, connect
from pbd.forecast import rolling as R

OUT = ROOT / "outputs/p2"
LAGS = (0, 3, 5)
ORIGINS = R.months(R.P["origin_start"], R.madd(R.P["end_month"], -1))


TARGETS = ("claim_qty", "claim_amt")


def load(con=None, target: str = "claim_qty"):
    """대상 성분의 패널. 목표 변수는 claim_qty(주) 또는 claim_amt(청구금액, 긍정 대조). 모델 코드가 같은 칸 이름을
    쓰도록 목표 값을 claim_qty 칸에 넣는다"""
    assert target in TARGETS
    con = con or connect(DB_PATH, read_only=True)
    panel = con.execute(f"""select p.c4, p.month, p.{target} claim_qty, p.usable from fc_panel p join fc_target t using (c4)
                           order by 1, 2""").df()
    tgt = con.execute("select c4, cls from fc_target order by c4").df()
    return panel, list(tgt["c4"]), dict(zip(tgt["c4"], tgt["cls"]))


def table(scored: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    for k, g in scored.groupby(keys):
        rows.append(dict(zip(keys, k if isinstance(k, tuple) else (k,)), **R.summary(g)))
    return pd.DataFrame(rows)


def main() -> None:
    panel, series, cls = load()
    scale = R.scale12(panel, series)
    allp = []
    for lag in LAGS:
        allp.append(R.rolling(panel, R.m0, ORIGINS, lag, series).assign(model="M0"))
    pred = pd.concat(allp, ignore_index=True)
    sc = R.score(pred, panel, scale)
    sc["flu"] = sc["target"].str[5:7].astype(int).isin(R.P["flu_season_months"])
    OUT.mkdir(parents=True, exist_ok=True)
    sc.to_csv(OUT / "t2_m0_pred.csv", index=False)
    t = pd.concat([table(sc, ["model", "lag"]).assign(part="전체"),
                   table(sc, ["model", "lag", "flu"]).assign(part="유행기 여부")], ignore_index=True)
    t.to_csv(OUT / "t2_m0_errors.csv", index=False)
    print(f"origin {ORIGINS[0]} ~ {ORIGINS[-1]} ({len(ORIGINS)}개), 성분 {len(series)}")
    print(t.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
