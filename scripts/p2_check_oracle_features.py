"""P2 점검: 금액 목표에서 오라클이 개선되지 않은 원인 (읽기 전용, 2026-11-19 사용자 지시로 정식 포함).

1. 오라클 가격 특징(o_price_ratio)이 실제 단가 변화 log(금액/수량)[t] − [a]를 따라가는가 (특징 생성 코드 점검):
   상관(전체 / 특징 ≠ 0 칸), 특징 ≠ 0 칸 기울기.
2. 금액 변화 중 단가 몫: log 금액 변화 = log 수량 변화 + log 단가 변화. 단가 몫 = Var(단가) ÷ Var(금액).
   개선 상한 = 1 − √(1 − 단가 몫): 단가 부분을 완벽히 맞혀도 줄일 수 있는 오차(표준편차) 비율 (단가와 나머지가 독립이라는 가정의 근사).
3. 학습 행 중 오라클 가격 특징이 0이 아닌 행 수 (origin별): 모델이 그 효과를 배울 예가 얼마나 되나.
대상: 성분 32개, origin 2023-01 ~ 2025-11, 예측 대상 M+1, lag 5(주)·0(참고).
사용: python -I scripts/p2_check_oracle_features.py   (출력 outputs/p2/oracle_check.csv, oracle_check_train.csv)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from pbd.common import DB_PATH, connect  # noqa: E402
from pbd.forecast import rolling as R  # noqa: E402
from pbd.forecast import signals as S  # noqa: E402
from pbd.forecast.t2_baseline import LAGS, ORIGINS, load  # noqa: E402
import p2_diag_oracle_offset as D  # noqa: E402

OUT = ROOT / "outputs/p2"


def main() -> None:
    con = connect(DB_PATH, read_only=True)
    tb = S.load_tables(con)
    qty, series, cls = load(target="claim_qty")
    amt, _, _ = load(target="claim_amt")
    wide = lambda p: p[p["usable"]].pivot(index="c4", columns="month", values="claim_qty")   # noqa: E731
    yq, ya = wide(qty), wide(amt)
    ev = con.execute("""select product_code, market_c4, strftime(effective_date, '%Y-%m') m from event_ledger_v5
                        where event_type = 'PRICE_CHANGE'""").df()
    j = ev.merge(tb["psm"][["product_code", "list_month", "c4"]], left_on=["product_code", "m"], right_on=["product_code", "list_month"], how="left")
    c4_match = float((j["market_c4"] == j["c4"]).mean())
    rows, trows = [], []
    fo = R.months(S.FIRST_SNAPSHOT, ORIGINS[-1])
    for lag in LAGS:
        cells = []
        for m in ORIGINS:
            a, t = R.madd(m, -lag), R.madd(m, 1)
            f = D.oracle_features(con, tb, m, a, t).set_index("c4").reindex(series)
            for c in series:
                if not all(x in yq.columns for x in (a, t)) or c not in yq.index:
                    continue
                dq, da = np.log(yq.loc[c, t] / yq.loc[c, a]), np.log(ya.loc[c, t] / ya.loc[c, a])
                cells.append(dict(c4=c, origin=m, da=da, dq=dq, dp=da - dq, pr=f.loc[c, "o_price_ratio"]))
        d = pd.DataFrame(cells).dropna()
        nz = d[d["pr"] != 0]
        share = d["dp"].var() / d["da"].var()
        rows.append(dict(lag=lag, c4_link_match=c4_match, n_eval=len(d), n_nonzero=len(nz), corr_all=d["dp"].corr(d["pr"]),
                         corr_nonzero=nz["dp"].corr(nz["pr"]), slope_nonzero=np.polyfit(nz["pr"], nz["dp"], 1)[0],
                         var_amount=d["da"].var(), var_quantity=d["dq"].var(), var_price=d["dp"].var(), price_share=share,
                         improvement_ceiling=1 - np.sqrt(1 - share), abs_price_change_median=d["dp"].abs().median()))
        ft = D.table(lambda m, a, t: D.oracle_features(con, tb, m, a, t), fo, lag, series, D.OFEAT)
        for o in ORIGINS:
            last = R.midx(o) - lag
            hist = R.available(qty, o, lag)
            y = R.to_matrix(hist, series, R.P["start_month"], R.mstr(last))
            tr = R.train_frame(y, R.P["start_month"], series, cls, lag + 1, last, ft)
            trows.append(dict(lag=lag, origin=o, train_rows=len(tr), rows_with_feature=int(tr["o_price_ratio"].notna().sum()),
                              rows_price_nonzero=int((tr["o_price_ratio"].fillna(0) != 0).sum())))
        print(f"lag {lag} 끝", flush=True)
    r, tr = pd.DataFrame(rows), pd.DataFrame(trows)
    OUT.mkdir(parents=True, exist_ok=True)
    r.to_csv(OUT / "oracle_check.csv", index=False)
    tr.to_csv(OUT / "oracle_check_train.csv", index=False)
    with pd.option_context("display.width", 250):
        print(r.round(4).to_string(index=False))
        print(tr.groupby("lag")[["train_rows", "rows_price_nonzero"]].agg(["min", "median", "max"]))


if __name__ == "__main__":
    main()
