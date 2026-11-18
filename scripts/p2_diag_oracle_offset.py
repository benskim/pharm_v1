"""P2 진단 실험 (검토 1차 b9·b6). 주 모델은 바꾸지 않는다. 일부러 누수를 넣는 실험(b9)이 있어 src/pbd/forecast/가 아니라 scripts/에 둔다.

b9 오라클 상한: 신호 특징 대신 **사후 원장의 실제 이벤트**(창 a+1 ~ t에 유효일이 있는 약가 변경(예고 여부 무관)·등재·삭제·급여중지)를
   특징으로 넣은 M3o. 일부러 미래를 본다. 오라클도 M2(같은 결측 구조의 E1)를 못 이기면 수량 예측에는 이 정보의 몫이 작다(목표 쪽),
   오라클은 이기고 신호(M3)는 못 이기면 신호의 범위·시점이 문제다.
b6 특징 시점 이동: 신호 특징의 창을 k개월 옮긴 M3(k) (창 a+1+k ~ t+k, k = −3 … +3). 기준 스냅샷 s = M은 그대로라 누수는 아니다.
   가장 좋은 k가 0이 아니면 창 정렬이 틀렸을 수 있다.
비교 기준: E1(신호 열을 0으로 둔 M3 = '2022 이전 NaN / 이후 0' 시기 표시만 가진 모델). 신호·오라클 내용의 몫 = 모델 − E1.
또 같은 학습 기간(대상 달 2022-02 이후)에서 M2 대 오라클도 본다.
사용: python -I scripts/p2_diag_oracle_offset.py   (출력 outputs/p2/diag_*.csv)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # -I 실행에서도 pbd를 찾도록
from pbd.common import DB_PATH, ROOT, connect  # noqa: E402
from pbd.forecast import rolling as R  # noqa: E402
from pbd.forecast import signals as S  # noqa: E402
from pbd.forecast.t2_baseline import LAGS, ORIGINS, load  # noqa: E402
from pbd.forecast.t3_models import add_holm, compare  # noqa: E402

OUT = ROOT / "outputs/p2"
OFFS = (-3, -2, -1, 0, 1, 2, 3)
OFEAT = ("n_prod", "o_cut_frac", "o_price_ratio", "o_listing_n", "o_delist_frac", "o_susp_frac")


def oracle_features(con, tb, s: str, a: str, t: str) -> pd.DataFrame:
    """사후 이벤트(누수 의도) 특징. n_prod는 신호 특징과 같은 정의(s월 급여 중 제품 수)"""
    psm = tb["psm"]
    cur = psm[(psm["list_month"] == s) & (psm["state_as_known"] == "reimbursed")]
    n = cur.groupby("c4")["product_code"].nunique()
    ev = con.execute("""select event_type, product_code, market_c4 c4, strftime(effective_date, '%Y-%m') m, price_before, price_after
                        from event_ledger_v5 where event_type in ('PRICE_CHANGE', 'LISTING', 'DELISTING', 'SUSPEND')
                        and effective_date is not null and strftime(effective_date, '%Y-%m') between ? and ?""",
                     [R.madd(a, 1), t]).df()
    out = pd.DataFrame(index=n.index)
    out["n_prod"] = np.log1p(n)
    pc = ev[ev["event_type"] == "PRICE_CHANGE"].copy()
    pc = pc[(pc["price_before"] > 0) & (pc["price_after"] > 0)]
    pc["r"] = np.log(pc["price_after"] / pc["price_before"])
    pr = pc.groupby(["c4", "product_code"])["r"].sum().reset_index()
    out["o_cut_frac"] = (pr[pr["r"] < 0].groupby("c4").size() / n).reindex(out.index).fillna(0)
    out["o_price_ratio"] = (pr.groupby("c4")["r"].sum() / n).reindex(out.index).fillna(0)
    out["o_listing_n"] = np.log1p(ev[ev["event_type"] == "LISTING"].groupby("c4").size().reindex(out.index).fillna(0))
    for k, et in (("o_delist_frac", "DELISTING"), ("o_susp_frac", "SUSPEND")):
        out[k] = (ev[ev["event_type"] == et].groupby("c4")["product_code"].nunique() / n).reindex(out.index).fillna(0)
    return out.reset_index().rename(columns={"index": "c4"})


def table(fn, origins, lag, c4s, cols) -> pd.DataFrame:
    rows = []
    for m in origins:
        f = fn(m, R.madd(m, -lag), R.madd(m, 1))
        rows.append(f[f["c4"].isin(c4s)].assign(origin=m))
    return pd.concat(rows, ignore_index=True)[["c4", "origin", *cols]]


def main(target: str = "claim_qty") -> None:
    """target = claim_amt이면 오라클만(M2·E1·M3o) 돌리고 출력 이름에 _claim_amt를 붙인다 (금액 긍정 대조, 사용자 지시 2026-11-19)"""
    amt = target == "claim_amt"
    sfx = "_claim_amt" if amt else ""
    panel, series, cls = load(target=target)
    scale = R.scale12(panel, series)
    con = connect(DB_PATH, read_only=True)
    tb = S.load_tables(con)
    fo = R.months(S.FIRST_SNAPSHOT, ORIGINS[-1])
    preds, cover = [], []
    for lag in LAGS:
        base = S.feature_table(tb, lag, "same", fo, series)
        null = base.assign(**{c: 0.0 for c in S.FEATURES})
        orc = table(lambda m, a, t: oracle_features(con, tb, m, a, t), fo, lag, series, OFEAT)
        cover.append(dict(lag=lag, oracle_nonzero=float((orc[list(OFEAT[1:])] != 0).any(axis=1).mean()),
                          signal_nonzero=float((base[["cut_frac", "susp_frac", "listing_n"]] > 0).any(axis=1).mean())))
        runs = {"M2": {}, "E1_null": dict(feat=null), "M3o_oracle": dict(feat=orc),
                "M2_2022": dict(train_lo="2022-02"), "M3o_oracle_2022": dict(feat=orc, train_lo="2022-02")}
        for k in (() if amt else OFFS):
            runs[f"M3_off{k:+d}"] = dict(feat=table(lambda m, a, t, k=k: S.features(tb, m, R.madd(a, k), R.madd(t, k)), fo, lag, series, S.FEATURES))
        for name, kw in runs.items():
            preds.append(R.rolling(panel, R.gbm, ORIGINS, lag, series, cls=cls, **kw).assign(model=name))
            print(f"lag {lag} {name}", flush=True)
    sc = R.score(pd.concat(preds, ignore_index=True), panel, scale)
    pairs = [("M3o_oracle", "E1_null"), ("M3o_oracle", "M2"), ("M3o_oracle_2022", "M2_2022")] + [(f"M3_off{k:+d}", "E1_null") for k in (() if amt else OFFS)]
    cmp_ = add_holm(pd.DataFrame([compare(sc, a, b, lag) for lag in LAGS for a, b in pairs]))
    OUT.mkdir(parents=True, exist_ok=True)
    cmp_.to_csv(OUT / f"diag_compare{sfx}.csv", index=False)
    pd.DataFrame(cover).to_csv(OUT / f"diag_coverage{sfx}.csv", index=False)
    sc.groupby(["lag", "model"])["sae"].mean().unstack().to_csv(OUT / f"diag_mase{sfx}.csv")
    with pd.option_context("display.width", 250):
        print(cmp_[["lag", "a", "b", "mase_a", "mase_b", "rel_diff", "a_better_series", "verdict_holm"]].round(4).to_string(index=False))
        print(pd.DataFrame(cover))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "claim_qty")
