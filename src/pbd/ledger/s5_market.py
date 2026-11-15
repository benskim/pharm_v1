"""P1 S5: 분석 B·C (docs/PLAN_P1.md §5 B1, B2, C).

data/interim/pbd.duckdb의 S2·S3 표와 stg_usage에서 outputs/s5/*.csv를 만든다.
- B1 전가율: 성분×월(전월 → 그 달)마다 목록 가격 변화(제품 수 가중, 두 달 모두 reimbursed인 같은 제품의 로그 변화 평균)와
  성분 원외 단가(청구금액 ÷ 청구수량) 로그 변화. 목록 가격이 바뀐 쌍에서 기울기(OLS, 성분 단위 부트스트랩 95% 구간).
  1에서 벗어나는 부분은 구성 변화(제품 비중, 신호 없는 제품, 수량 단위)이며 "잔차"로만 말한다.
- B2 진입 군집: 한 관측월에 실질 신규 품목(LISTING new_item)을 낸 업체가 5곳 이상인 성분×월.
  같은 시기 기존 제품의 가격 변경과 분리하지 않는다. 사용통계가 진입 전에 관측됐는지를 함께 적는다.
- C 대체조제 그룹: 그룹(①주성분 및 투여제형코드 × 주성분코드)별 최저·최고·대조약 상한금액과 성분 원외 금액. 절감액은 계산하지 않는다.
사용: PYTHONPATH=src python -m pbd.ledger.s5_market   (make p1-s5)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pbd.common import ROOT, connect, load_yaml

OUT = ROOT / "outputs/s5"
PARAMS = load_yaml("params.yaml")
B2_MIN_FIRMS = 5            # PLAN §5 B2: 한 달에 새 업체 5곳 이상
B2_WINDOW = (-3, 3)         # 군집 관측월 기준 전후 개월
C_MONTH = "2025-12"         # C 순위표 기준 달 (분석 끝)
N_BOOT = 1000


def _dump(df: pd.DataFrame, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / f"{name}.csv", index=False, lineterminator="\n")


def _usage(con) -> None:
    """성분×월 원외 합계와 단가. 관측 여부는 usage_c4_month.observed_out"""
    con.execute("""create or replace temp table _up as
        select u.c4, u.month, sum(u.claim_amt) amt, sum(u.claim_qty) qty, sum(u.claim_amt) / nullif(sum(u.claim_qty), 0) up
        from stg_usage u where u.in_out = 'OUT' group by 1, 2""")


# ---------------------------------------------------------------- B1
def ols_slope(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """y = a + b x 의 (b, a)"""
    xm, ym = x.mean(), y.mean()
    b = ((x - xm) * (y - ym)).sum() / ((x - xm) ** 2).sum()
    return float(b), float(ym - b * xm)


def boot_slope(df: pd.DataFrame, seed: int, n_boot: int = N_BOOT) -> tuple[float, float]:
    """성분(c4) 단위로 다시 뽑는 부트스트랩 95% 구간 (같은 성분의 여러 달은 독립이 아니다)"""
    rng = np.random.default_rng(seed)
    groups = [g[["x", "y"]].to_numpy() for _, g in df.groupby("c4", sort=True)]
    out = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(groups), len(groups))
        a = np.concatenate([groups[i] for i in idx])
        if np.ptp(a[:, 0]) == 0:
            continue
        out.append(ols_slope(a[:, 0], a[:, 1])[0])
    return float(np.quantile(out, 0.025)), float(np.quantile(out, 0.975))


def build_b1_pairs(con) -> pd.DataFrame:
    """성분×월 쌍마다 목록 가격 변화, 원외 단가 변화, 탈락 단계"""
    con.execute("""create or replace temp table _b1 as
        with cal as (select prev_month m0, list_month m1 from s3_cal where prev_month is not null),
             pr as (select a.c4, c.m1 as month, a.product_code,
                           a.state_asof s0, b.state_asof s1, a.price_listed p0, b.price_listed p1
                    from cal c join product_state_month a on a.list_month = c.m0
                    join product_state_month b on b.list_month = c.m1 and b.product_code = a.product_code and b.c4 = a.c4),
             pairs as (select c4, month, count(*) n_both,
                              count(*) filter (where s0 = 'reimbursed' and s1 = 'reimbursed' and p0 > 0 and p1 > 0) n_basket,
                              avg(ln(p1 / p0)) filter (where s0 = 'reimbursed' and s1 = 'reimbursed' and p0 > 0 and p1 > 0) dlog_list,
                              count(*) filter (where s0 = 'reimbursed' and s1 = 'reimbursed' and p0 > 0 and p1 > 0 and p1 <> p0) n_changed
                       from pr group by 1, 2),
             -- 월 중간에 적용된 가격 변경: 유효일이 전월 또는 그 달 안이고 1일이 아님 → 사용통계의 두 달에 나뉘어 들어간다
             mid as (select distinct e.market_c4 c4, c.m1 as month from event_ledger e join cal c
                       on strftime(e.effective_date, '%Y-%m') in (c.m0, c.m1)
                     where e.event_type = 'PRICE_CHANGE' and day(e.effective_date) <> 1),
             -- 구성 변화: 그 관측월에 성분 안의 진입·퇴출·양도양수·정지·복귀 이벤트
             comp as (select market_c4 c4, observed_month as month, count(*) n_comp from event_ledger
                      where event_type in ('LISTING', 'DELISTING', 'TRANSFER', 'SUSPEND', 'RESUME') group by 1, 2)
        select p.*, c.m0 prev_month,
               coalesce(u0.observed_out, false) obs0, coalesce(u1.observed_out, false) obs1,
               (select count(*) from usage_c4_month z where z.c4 = p.c4 and z.observed_out) n_obs_months,
               ln(q1.up / q0.up) dlog_up, q0.amt amt0, q1.amt amt1,
               mid.c4 is not null mid_month_change, coalesce(comp.n_comp, 0) n_comp
        from pairs p join cal c on c.m1 = p.month
        left join usage_c4_month u0 on u0.c4 = p.c4 and u0.month = c.m0
        left join usage_c4_month u1 on u1.c4 = p.c4 and u1.month = p.month
        left join _up q0 on q0.c4 = p.c4 and q0.month = c.m0
        left join _up q1 on q1.c4 = p.c4 and q1.month = p.month
        left join mid on mid.c4 = p.c4 and mid.month = p.month
        left join comp on comp.c4 = p.c4 and comp.month = p.month""")
    df = con.execute("select * from _b1 order by c4, month").df()
    stage = np.select(
        [df["n_basket"] == 0,
         ~(df["obs0"] & df["obs1"]) | df["dlog_up"].isna(),
         df["n_changed"] == 0,
         df["mid_month_change"]],
        ["1_no_basket", "2_usage_unobserved", "3_no_list_change", "4_mid_month_change"], default="target")
    df["stage"] = stage
    df["usage_never_observed"] = df["n_obs_months"] == 0
    df["year"] = df["month"].str[:4]
    return df


def b1_summary(pairs: pd.DataFrame, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(탈락 단계표, 기울기 표)"""
    st = pairs.groupby("stage").agg(pairs=("c4", "size"), c4s=("c4", "nunique")).reset_index()
    sub = pairs[pairs["stage"] == "2_usage_unobserved"]
    extra = pd.DataFrame([
        {"stage": "2a_usage_never_observed", "pairs": int(sub["usage_never_observed"].sum()),
         "c4s": sub.loc[sub["usage_never_observed"], "c4"].nunique()},
        {"stage": "2b_usage_partly_unobserved", "pairs": int((~sub["usage_never_observed"]).sum()),
         "c4s": sub.loc[~sub["usage_never_observed"], "c4"].nunique()}])
    st = pd.concat([pd.DataFrame([{"stage": "0_all_pairs", "pairs": len(pairs), "c4s": pairs["c4"].nunique()}]), st, extra]) \
        .sort_values("stage").reset_index(drop=True)
    t = pairs[pairs["stage"] == "target"].rename(columns={"dlog_list": "x", "dlog_up": "y"})
    rows = []
    subsets = [("all", "전체", t), ("no_composition_change", "구성 변화 이벤트 없음", t[t["n_comp"] == 0]),
               ("list_cut_ge_5pct", "목록 인하 5% 이상", t[t["x"] <= np.log(0.95)]),
               # 민감도: 단가가 50% 넘게 움직인 쌍 제외 (결과 변수로 고른 것이라 주 결과로 쓰지 않는다)
               ("excl_abs_up_chg_gt_50pct", "단가 변화 ±50% 초과 쌍 제외 (민감도)", t[t["y"].abs() <= np.log(1.5)])]
    subsets += [(f"year_{y}", f"{y}년", g) for y, g in t.groupby("year")]
    for i, (key, label, g) in enumerate(subsets):
        if len(g) < 3 or g["x"].nunique() < 2:
            rows.append({"subset": key, "label": label, "pairs": len(g), "c4s": g["c4"].nunique()})
            continue
        b, a = ols_slope(g["x"].to_numpy(), g["y"].to_numpy())
        lo, hi = boot_slope(g, seed + i)
        big = g[g["x"].abs() >= np.log(1.01)]            # 비율 y/x는 목록 변화가 1% 이상인 쌍에서만 (작은 x로 나누면 흔들린다)
        rows.append({"subset": key, "label": label, "pairs": len(g), "c4s": g["c4"].nunique(),
                     "slope": round(b, 3), "slope_lo": round(lo, 3), "slope_hi": round(hi, 3), "intercept": round(a, 4),
                     "pairs_list_chg_ge_1pct": len(big),
                     "median_ratio_ge_1pct": round(float((big["y"] / big["x"]).median()), 3) if len(big) else None,
                     "median_list_chg_pct": round((np.exp(g["x"].median()) - 1) * 100, 2),
                     "median_up_chg_pct": round((np.exp(g["y"].median()) - 1) * 100, 2),
                     "share_up_same_sign": round(float((np.sign(g["y"]) == np.sign(g["x"])).mean()), 3),
                     "pairs_abs_up_chg_gt_50pct": int((g["y"].abs() > np.log(1.5)).sum())})
    return st, pd.DataFrame(rows)


# ---------------------------------------------------------------- B2
def build_b2(con) -> dict[str, pd.DataFrame]:
    con.execute(f"""create or replace temp table _b2c as
        with e as (select e.market_c4 c4, e.observed_month as month, e.product_code, q.firm, e.effective_date
                   from event_ledger e join product_state_month q on q.product_code = e.product_code and q.list_month = e.observed_month
                   where e.event_type = 'LISTING' and e.kind = 'new_item')
        select c4, month, count(distinct firm) n_entry_firms, count(*) n_entry_products,
               min(effective_date) eff_min, max(effective_date) eff_max
        from e group by 1, 2""")
    dist = con.execute("""select case when n_entry_firms >= 10 then '10+' when n_entry_firms >= 5 then '5~9'
                                      when n_entry_firms >= 2 then '2~4' else '1' end bucket,
                                 substr(month, 1, 4) as year, count(*) n from _b2c group by 1, 2 order by 1, 2""").df()
    lo, hi = B2_WINDOW
    con.execute(f"""create or replace temp table _b2 as
        with cl as (select c.*, k.mi, k.prev_month from _b2c c join s3_cal k on k.list_month = c.month where n_entry_firms >= {B2_MIN_FIRMS}),
             w as (select cl.c4, cl.month, k.list_month wm, k.mi - cl.mi rel from cl join s3_cal k on k.mi between cl.mi + {lo} and cl.mi + {hi}),
             firms as (select w.c4, w.month, w.rel, count(distinct q.firm) filter (where q.state_asof = 'reimbursed') n_firms,
                              count(*) filter (where q.state_asof = 'reimbursed') n_products,
                              min(q.price_listed) filter (where q.state_asof = 'reimbursed') min_price
                       from w left join product_state_month q on q.c4 = w.c4 and q.list_month = w.wm group by 1, 2, 3),
             -- 군집 전월에 이미 있던 제품의 가격 변경 (관측월 m−1 ~ m+3). 기존 제품 인하와 진입을 분리하지 않는다
             pre as (select cl.c4, cl.month, q.product_code, q.product_role from cl join product_state_month q
                       on q.c4 = cl.c4 and q.list_month = cl.prev_month and q.state_asof = 'reimbursed'),
             pc as (select pre.c4, pre.month, count(distinct pre.product_code) n_pre,
                           count(distinct pre.product_code) filter (where pre.product_role = 'reference') n_pre_ref,
                           count(distinct e.product_code) n_pre_price_change,
                           count(distinct e.product_code) filter (where pre.product_role = 'reference') n_pre_ref_price_change
                    from pre left join event_ledger e on e.product_code = pre.product_code and e.event_type = 'PRICE_CHANGE'
                         and e.observed_month in (select k.list_month from s3_cal k, s3_cal m0 where m0.list_month = pre.month
                                                  and k.mi between m0.mi - 1 and m0.mi + 3)
                    group by 1, 2),
             ref as (select cl.c4, cl.month, avg(b.price_listed / a.price_listed - 1) ref_price_chg
                     from cl join s3_cal k3 on k3.mi = least(cl.mi + 3, 48)
                     join product_state_month a on a.c4 = cl.c4 and a.list_month = cl.prev_month and a.product_role = 'reference' and a.state_asof = 'reimbursed'
                     join product_state_month b on b.product_code = a.product_code and b.list_month = k3.list_month and b.price_listed > 0
                     group by 1, 2)
        select cl.c4, cl.month, cl.n_entry_firms, cl.n_entry_products, cl.eff_min, cl.eff_max,
               (select min(q.ingredient_code) from product_state_month q where q.c4 = cl.c4 and q.list_month = cl.month) ingredient_code,
               (select min(b.ingredient_name) from stg_benefit b where b.c4 = cl.c4 and b.list_month = cl.month and b.ingredient_name is not null) ingredient_name,
               f0.n_firms firms_before, f1.n_firms firms_month, f3.n_firms firms_after3,
               f0.min_price min_price_before, f3.min_price min_price_after3,
               pc.n_pre, pc.n_pre_ref, pc.n_pre_price_change, pc.n_pre_ref_price_change, ref.ref_price_chg,
               cl.mi + {hi} <= 48 post_window_complete,
               z.first_observed_month_out,
               (select count(*) from w join usage_c4_month u on u.c4 = w.c4 and u.month = w.wm and u.observed_out
                 where w.c4 = cl.c4 and w.month = cl.month and w.rel < 0) n_obs_before,
               (select count(*) from w join usage_c4_month u on u.c4 = w.c4 and u.month = w.wm and u.observed_out
                 where w.c4 = cl.c4 and w.month = cl.month and w.rel > 0) n_obs_after,
               p0.up up_before, p1.up up_month, p3.up up_after3, p0.qty qty_before, p3.qty qty_after3
        from cl
        left join firms f0 on f0.c4 = cl.c4 and f0.month = cl.month and f0.rel = -1
        left join firms f1 on f1.c4 = cl.c4 and f1.month = cl.month and f1.rel = 0
        left join firms f3 on f3.c4 = cl.c4 and f3.month = cl.month and f3.rel = {hi}
        left join pc on pc.c4 = cl.c4 and pc.month = cl.month
        left join ref on ref.c4 = cl.c4 and ref.month = cl.month
        left join (select c4, min(first_observed_month_out) first_observed_month_out from usage_c4_month group by 1) z on z.c4 = cl.c4
        left join s3_cal km on km.list_month = cl.month
        left join s3_cal k0 on k0.mi = km.mi - 1
        left join s3_cal k3 on k3.mi = km.mi + {hi}
        left join _up p0 on p0.c4 = cl.c4 and p0.month = k0.list_month
        left join _up p1 on p1.c4 = cl.c4 and p1.month = cl.month
        left join _up p3 on p3.c4 = cl.c4 and p3.month = k3.list_month""")
    cl = con.execute("select * from _b2 order by month, c4").df()
    cl["up_chg_after3_pct"] = ((cl["up_after3"] / cl["up_before"] - 1) * 100).round(1)
    cl["qty_chg_after3_pct"] = ((cl["qty_after3"] / cl["qty_before"] - 1) * 100).round(1)
    cl["ref_price_chg_pct"] = (cl["ref_price_chg"] * 100).round(1)
    cl["min_price_chg_after3_pct"] = ((cl["min_price_after3"] / cl["min_price_before"] - 1) * 100).round(1)
    # 탈락 단계: 군집 → 사후 창 완결 → 진입 전 3개월 사용통계 모두 관측 → 사후 3개월 모두 관측
    st = [("0_clusters", cl),
          ("1_post_window_complete", cl[cl["post_window_complete"]])]
    st.append(("2_usage_observed_all_3_before", st[-1][1][st[-1][1]["n_obs_before"] == 3]))
    st.append(("3_usage_observed_all_3_after", st[-1][1][st[-1][1]["n_obs_after"] == 3]))
    steps = pd.DataFrame([{"stage": k, "clusters": len(g), "c4s": g["c4"].nunique()} for k, g in st])
    cl["case_usable"] = cl.index.isin(st[-1][1].index)
    return {"b2_cluster_dist": dist, "b2_selection": steps, "b2_clusters": cl.drop(columns=["ref_price_chg"])}


def b2_case_series(con, c4: str, month: str) -> pd.DataFrame:
    """사례 하나의 월별 업체 수·최저/최고가·원외 단가 (군집 관측월 −3 ~ +3)"""
    lo, hi = B2_WINDOW
    return con.execute(f"""with m0 as (select mi from s3_cal where list_month = ?)
        select k.list_month as month, k.mi - m0.mi rel,
               count(distinct q.firm) firms_all, count(q.product_code) products_all,
               count(distinct q.firm) filter (where q.state_asof = 'reimbursed') firms,
               count(*) filter (where q.state_asof = 'reimbursed') products,
               min(q.price_listed) filter (where q.state_asof = 'reimbursed') min_price,
               max(q.price_listed) filter (where q.state_asof = 'reimbursed') max_price,
               round(any_value(u.up), 1) up, any_value(z.observed_out) observed_out
        from m0, s3_cal k left join product_state_month q on q.c4 = ? and q.list_month = k.list_month
        left join _up u on u.c4 = ? and u.month = k.list_month
        left join usage_c4_month z on z.c4 = ? and z.month = k.list_month
        where k.mi between m0.mi + {lo} and m0.mi + {hi} group by 1, 2 order by 1""", [month, c4, c4, c4]).df()


# ---------------------------------------------------------------- C
def build_c(con, month: str = C_MONTH) -> dict[str, pd.DataFrame]:
    """대체조제 그룹 순위표. 그룹 = (sub_group_code, ingredient_code), 그 달 대체조제 목록에 있고 state_asof = reimbursed,
    상한금액이 있는 제품. 성분 원외 금액은 성분4 단위라 같은 성분4의 여러 그룹이 같은 금액을 가진다."""
    # 표본 회귀 (PLAN F10): 대체조제 목록 원본 행 기준 (정지·산정불가 포함, 대체조제 목록 금액)
    f10 = con.execute("""with g as (select group_code, ingredient_code, count(*) n, min(price) mn, max(price) mx
                                    from stg_substitute where list_month = ? group by 1, 2)
        select count(*) groups, sum((n >= 2)::int) groups_ge2, round(median(case when n >= 2 then mx / mn end), 2) med_max_min,
               round(quantile_cont(case when n >= 2 then mx / mn end, 0.9), 2) p90_max_min from g""", [month]).df()
    year = month[:4]
    con.execute("""create or replace temp table _c as
        with p as (select sub_group_code grp, ingredient_code, c4, product_code, firm, price_listed price, sub_ref_drug ref
                   from product_state_month where list_month = ? and in_substitute_list and state_asof = 'reimbursed' and price_listed > 0),
             g as (select grp, ingredient_code, min(c4) c4, count(*) n_products, count(distinct firm) n_firms,
                          min(price) min_price, max(price) max_price, median(price) median_price,
                          count(*) filter (where ref) n_ref, min(price) filter (where ref) ref_min, max(price) filter (where ref) ref_max
                   from p group by 1, 2),
             ua as (select c4, sum(amt) amt_year, count(*) filter (where z.observed_out) n_obs
                    from _up join usage_c4_month z using (c4, month) where substr(month, 1, 4) = ? group by 1)
        select g.*, (select min(s.ingredient_name) from stg_substitute s where s.list_month = ? and s.ingredient_code = g.ingredient_code) ingredient_name,
               round(max_price / min_price, 3) max_min_ratio,
               round(ref_max / min_price, 3) ref_max_to_min,
               (select count(*) from g g2 where g2.c4 = g.c4) groups_in_c4,
               ua.amt_year c4_out_amt_year, ua.n_obs c4_obs_months
        from g left join ua on ua.c4 = g.c4""", [month, year, month])
    allg = con.execute("""select dense_rank() over (order by c4_out_amt_year desc) c4_rank, *
        from _c order by c4_out_amt_year desc nulls last, c4, n_products desc, grp, ingredient_code""").df()
    allg.loc[allg["c4_out_amt_year"].isna(), "c4_rank"] = None
    sel = pd.DataFrame([
        {"stage": "0_groups", "groups": len(allg)},
        {"stage": "1_products_ge2", "groups": int((allg["n_products"] >= 2).sum())},
        {"stage": "2_c4_usage_observed_12m", "groups": int(((allg["n_products"] >= 2) & (allg["c4_obs_months"] == 12)).sum())},
        {"stage": "2x_c4_usage_never_observed", "groups": int(((allg["n_products"] >= 2) & allg["c4_out_amt_year"].isna()).sum())},
    ])
    rank = allg[(allg["n_products"] >= 2) & (allg["c4_obs_months"] == 12)].copy()
    rank.insert(0, "rank", range(1, len(rank) + 1))
    return {"c_f10_sample_check": f10, "c_groups_all": allg, "c_selection": sel, "c_rank": rank}


def disclosure_check(con) -> pd.DataFrame:
    """목록의 성분×월 업체·제품 수(state_asof = reimbursed)와 사용통계 원외 관측 여부.
    DATA.md의 비공개 규칙(품목 수 또는 업체 수 2개 이하 제외)을 목록 쪽 수로 대조한다. 규칙을 정하지 않고 건수만 낸다."""
    return con.execute("""with l as (select c4, list_month as month, count(distinct firm) nf, count(*) np
                                      from product_state_month where state_asof = 'reimbursed' group by 1, 2)
        select case when nf <= 2 and np <= 2 then 'a_firms_le2_products_le2' when nf <= 2 then 'b_firms_le2_products_ge3'
                    else 'c_firms_ge3' end list_bucket,
               count(*) c4_months, count(*) filter (where z.observed_out) observed_out,
               round(avg(coalesce(z.observed_out, false)::int), 3) share_observed
        from l left join usage_c4_month z using (c4, month) group by 1 order by 1""").df()


def write_all(con) -> dict[str, pd.DataFrame]:
    seed = PARAMS["seed"]
    _usage(con)
    pairs = build_b1_pairs(con)
    st, slopes = b1_summary(pairs, seed)
    tabs = {"b1_pairs": pairs, "b1_selection": st, "b1_slopes": slopes}
    tabs.update(build_b2(con))
    tabs.update(build_c(con))
    tabs["b_disclosure_check"] = disclosure_check(con)
    for k, v in tabs.items():
        _dump(v, k)
    return tabs


def main() -> None:
    con = connect(read_only=False)
    t = write_all(con)
    s = t["b1_slopes"].set_index("subset")
    print(f"B1 대상 쌍 {int(s.loc['all', 'pairs'])}, 기울기 {s.loc['all', 'slope']} "
          f"[{s.loc['all', 'slope_lo']}, {s.loc['all', 'slope_hi']}]")
    print(f"B2 군집 {len(t['b2_clusters'])}, C 순위 그룹 {len(t['c_rank'])}")
    con.close()


if __name__ == "__main__":
    main()
