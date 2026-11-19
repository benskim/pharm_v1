"""P2 추가 확인 C·D (2026-11-19, 사용자 지시): 상위 200개 성분(C)과 층화 200개 성분(D). 주 결과(성분 32개)는 동결이라 이 파일만 새로 둔다.

D 층화 200 (DECISIONS 2026-11-19, 실행 전 기록): 대상 = 96개월 원외 모두 관측 성분(생존 편향 그대로). 층 = 약효분류번호 앞 2자리
× 2022 원외 청구금액 3분위 × 업체 수 구간(3~5, 6~10, 11+; 2022-12 급여목록). 200개를 층 크기에 비례 배분(최대 나머지), 층마다 최소 1개,
층 안은 시드 고정 무작위 추출. 새 표 `fc_target_strat200`.
예측값은 `extra_<kind>_pred.csv`로 저장하고 청구금액 가중 WAPE(추가 지시 A)를 함께 낸다.

대상: 약효분류 묶음 조건 없이, 96개월 원외 모두 관측 + 2022 원외 청구수량 상위 `p2.top_n`(= 200). 새 표 `fc_target_top200`
(`fc_target`은 그대로). 모델·특징·평가는 기존 코드(rolling·signals·t3_models) 그대로 부른다.
비교: 시차 5(주)·0(참고), 다음 달, M0·M2·M3, E1(신호 열 0, 2022 이전 빈칸), 같은 학습 기간(대상 달 2022-02 ~) M2·M3.
사용: python -m pbd.forecast.extra_top200 [top200|strat200]  (출력 outputs/p2/extra_<kind>_*.csv)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pbd.common import DB_PATH, ROOT, connect
from pbd.forecast import rolling as R
from pbd.forecast import extra_weight as W
from pbd.forecast import signals as S
from pbd.forecast.t2_baseline import LAGS, ORIGINS
from pbd.forecast.t3_models import add_holm, compare

OUT = ROOT / "outputs/p2"


def build_target(top_n: int = R.P["top_n"], n_months: int = 96) -> pd.DataFrame:
    """fc_target_top200과 탈락 단계표 (쓰기는 이 함수에서만, 짧게 열고 닫는다)"""
    con = connect(DB_PATH)
    con.execute("""create or replace table fc_target_top200 as
        with u as (select c4, count(*) filter (where observed) n_obs, sum(claim_qty) filter (where month like '2022-%') qty2022
                   from fc_panel group by 1)
        select u.c4, k.cls, u.qty2022, row_number() over (order by u.qty2022 desc, u.c4) qty_rank
        from u left join fc_c4_class k using (c4) where u.n_obs = ?
        qualify qty_rank <= ? order by u.c4""", [n_months, top_n])
    steps = con.execute(f"""
        with u as (select c4, count(*) filter (where observed) n_obs from fc_panel group by 1)
        select '① 사용통계에 있는 성분4' step, (select count(distinct c4) from stg_usage) n
        union all select '② 원외 관측 1개월 이상', count(*) filter (where n_obs > 0) from u
        union all select '③ {n_months}개월 원외 모두 관측', count(*) filter (where n_obs = {n_months}) from u
        union all select '④ 2022 원외 청구수량 상위 {top_n}', (select count(*) from fc_target_top200)""").df()
    cov = con.execute("""select (select count(*) from fc_target_top200) n,
        sum(claim_amt) filter (where c4 in (select c4 from fc_target_top200)) / sum(claim_amt) amt_share
        from fc_panel where month like '2022-%' and observed""").df()
    con.close()
    steps["dropped"] = (steps["n"].shift(1) - steps["n"]).astype("Int64")
    steps.to_csv(OUT / "extra_top200_steps.csv", index=False)
    cov.to_csv(OUT / "extra_top200_coverage.csv", index=False)
    return steps


FIRM_BINS = ((3, 5, "3~5"), (6, 10, "6~10"), (11, 10 ** 9, "11+"))


def allocate(sizes: pd.Series, total: int) -> pd.Series:
    """층 크기에 비례(최대 나머지), 층마다 최소 1개, 층 크기를 넘지 않음"""
    raw = total * sizes / sizes.sum()
    a = np.floor(raw).astype(int).clip(lower=1)
    a = np.minimum(a, sizes)
    rem = (raw - np.floor(raw)).sort_values(ascending=False)
    while a.sum() < total:
        for k in rem.index:
            if a.sum() >= total:
                break
            if a[k] < sizes[k]:
                a[k] += 1
    while a.sum() > total:
        k = a[a > 1].sort_values(ascending=False).index[0]
        a[k] -= 1
    return a


def build_strat(total: int = R.P["top_n"], n_months: int = 96) -> pd.DataFrame:
    con = connect(DB_PATH)
    e = con.execute("""
        with u as (select c4, count(*) filter (where observed) n_obs, sum(claim_amt) filter (where month like '2022-%') amt2022
                   from fc_panel group by 1),
        f as (select c4, count(distinct firm) nf from stg_benefit where list_month = '2022-12' and row_kind = 'product_candidate' group by 1)
        select u.c4, k.cls, u.amt2022, f.nf from u left join fc_c4_class k using (c4) left join f using (c4) where u.n_obs = ?
        order by u.c4""", [n_months]).df()
    assert (e["nf"] >= 3).all(), "업체 수 2 이하 성분이 있다 — 구간 규칙(3~5, 6~10, 11+)에 없는 값"
    e["cls2"] = e["cls"].map(lambda x: str(int(x))[:2] if pd.notna(x) else "NA")
    e["amt_tertile"] = pd.qcut(e["amt2022"].rank(method="first"), 3, labels=["하", "중", "상"]).astype(str)
    e["firm_bin"] = e["nf"].map(lambda n: next(lab for lo, hi, lab in FIRM_BINS if lo <= n <= hi))
    e["stratum"] = e["cls2"] + "|" + e["amt_tertile"] + "|" + e["firm_bin"]
    sizes = e.groupby("stratum").size()
    alloc = allocate(sizes, total)
    rng = np.random.default_rng(R.SEED)
    pick = []
    for st in sorted(alloc.index):
        c = sorted(e[e["stratum"] == st]["c4"])
        pick += list(rng.choice(c, size=int(alloc[st]), replace=False))
    t = e[e["c4"].isin(pick)].copy()
    con.register("_t", t[["c4", "cls", "stratum"]])
    con.execute("create or replace table fc_target_strat200 as select * from _t order by c4")
    top = set(con.execute("select c4 from fc_target_top200").df()["c4"]) if con.execute(
        "select count(*) from duckdb_tables() where table_name = 'fc_target_top200'").fetchone()[0] else set()
    con.close()
    st = e.groupby("stratum").agg(eligible=("c4", "size"), eligible_amt=("amt2022", "sum")).join(alloc.rename("picked"))
    st["picked_amt"] = t.groupby("stratum")["amt2022"].sum()
    st = st.fillna({"picked_amt": 0}).reset_index()
    st.to_csv(OUT / "extra_strat200_strata.csv", index=False)
    tot = con_amt_total()
    pd.DataFrame([dict(n=len(t), strata=len(alloc), amt_share=t["amt2022"].sum() / tot, overlap_top200=len(set(t["c4"]) & top),
                       eligible=len(e), eligible_amt_share=e["amt2022"].sum() / tot)]).to_csv(OUT / "extra_strat200_coverage.csv", index=False)
    return st


def con_amt_total() -> float:
    con = connect(DB_PATH, read_only=True)
    return float(con.execute("select sum(claim_amt) from fc_panel where month like '2022-%' and observed").fetchone()[0])


def load200(table_name: str = "fc_target_top200"):
    con = connect(DB_PATH, read_only=True)
    panel = con.execute(f"""select p.c4, p.month, p.claim_qty, p.usable from fc_panel p join {table_name} t using (c4)
                           order by 1, 2""").df()
    tgt = con.execute(f"select c4, cls from {table_name} order by c4").df()
    return panel, list(tgt["c4"]), dict(zip(tgt["c4"], tgt["cls"].fillna(-1).astype(int)))


def main(kind: str = "top200") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if kind == "top200":
        steps = build_target()
        print(steps.to_string(index=False), flush=True)
        panel, series, cls = load200("fc_target_top200")
    else:
        st = build_strat()
        print(st.to_string(index=False), flush=True)
        panel, series, cls = load200("fc_target_strat200")
    scale = R.scale12(panel, series)
    tb = S.load_tables(connect(DB_PATH, read_only=True))
    fo = R.months(S.FIRST_SNAPSHOT, ORIGINS[-1])
    preds, nz = [], []
    for lag in LAGS:
        f = S.feature_table(tb, lag, "same", fo, series)
        nz.append(dict(lag=lag, nonzero=float(((f["cut_frac"] > 0) | (f["susp_frac"] > 0) | (f["listing_n"] > 0)).mean())))
        null = f.assign(**{c: 0.0 for c in S.FEATURES})
        runs = {"M0": (R.m0, {}), "M2": (R.gbm, {}), "M3": (R.gbm, dict(feat=f)), "E1_null": (R.gbm, dict(feat=null)),
                "E4_M2_2022": (R.gbm, dict(train_lo="2022-02")), "E4_M3_2022": (R.gbm, dict(feat=f, train_lo="2022-02"))}
        for name, (fn, kw) in runs.items():
            preds.append(R.rolling(panel, fn, ORIGINS, lag, series, cls=cls, **kw).assign(model=name))
            print(f"lag {lag} {name}", flush=True)
    sc = R.score(pd.concat(preds, ignore_index=True), panel, scale)
    sc.to_csv(OUT / f"extra_{kind}_pred.csv", index=False)
    err = W.table(sc, ["model", "lag"], W.amt2022(series))
    pairs = (("M2", "M0"), ("M3", "M2"), ("E1_null", "M2"), ("M3", "E1_null"), ("E4_M3_2022", "E4_M2_2022"))
    cmp_ = add_holm(pd.DataFrame([compare(sc, a, b, lag) for lag in LAGS for a, b in pairs]))
    err.to_csv(OUT / f"extra_{kind}_errors.csv", index=False)
    cmp_.to_csv(OUT / f"extra_{kind}_compare.csv", index=False)
    pd.DataFrame(nz).to_csv(OUT / f"extra_{kind}_nonzero.csv", index=False)
    with pd.option_context("display.width", 250):
        print(err.round(4).to_string(index=False))
        print(cmp_[["lag", "a", "b", "mase_a", "mase_b", "rel_diff", "a_better_series", "n_series", "verdict_holm"]].round(4).to_string(index=False))


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "top200")
