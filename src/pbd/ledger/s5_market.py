"""P1 S5 (v5 원장, 2026-11-18 이전: state_month_v5·ev_v5·cal_v5, state = 그 달 1일 as_known, 실질 신규 = LISTING new_listing): 분석 B·C (docs/PLAN_P1.md §5 B1, B2, C).

data/interim/pbd.duckdb의 S2·S3 표와 stg_usage에서 outputs/s5/*.csv를 만든다.
- B1 전가율: 성분×월(전월 → 그 달)마다 목록 가격 변화(제품 수 가중, 두 달 모두 reimbursed인 같은 제품의 로그 변화 평균)와
  성분 원외 단가(청구금액 ÷ 청구수량) 로그 변화. 목록 가격이 바뀐 쌍에서 기울기(OLS, 성분 단위 부트스트랩 95% 구간).
  1에서 벗어나는 부분은 구성 변화(제품 비중, 신호 없는 제품, 수량 단위)이며 "잔차"로만 말한다.
- B2 진입 군집: 한 관측월에 실질 신규 품목(v5 LISTING new_listing, 허가 연결 있음)을 낸 업체가 5곳 이상인 성분×월.
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
        with cal as (select prev_month m0, list_month m1 from cal_v5 where prev_month is not null),
             pr as (select a.c4, c.m1 as month, a.product_code,
                           a.state s0, b.state s1, a.price_listed p0, b.price_listed p1
                    from cal c join state_month_v5 a on a.list_month = c.m0
                    join state_month_v5 b on b.list_month = c.m1 and b.product_code = a.product_code and b.c4 = a.c4),
             pairs as (select c4, month, count(*) n_both,
                              count(*) filter (where s0 = 'reimbursed' and s1 = 'reimbursed' and p0 > 0 and p1 > 0) n_basket,
                              -- 소수 12자리 반올림: 병렬 집계의 덧셈 순서로 끝자리가 달라지지 않게 (같은 입력 → 같은 출력, 2026-11-17)
                              round(avg(ln(p1 / p0)) filter (where s0 = 'reimbursed' and s1 = 'reimbursed' and p0 > 0 and p1 > 0), 12) dlog_list,
                              count(*) filter (where s0 = 'reimbursed' and s1 = 'reimbursed' and p0 > 0 and p1 > 0 and p1 <> p0) n_changed
                       from pr group by 1, 2),
             -- 월 중간에 적용된 가격 변경: 유효일이 전월 또는 그 달 안이고 1일이 아님 → 사용통계의 두 달에 나뉘어 들어간다
             mid as (select distinct e.market_c4 c4, c.m1 as month from ev_v5 e join cal c
                       on strftime(e.effective_date, '%Y-%m') in (c.m0, c.m1)
                     where e.event_type = 'PRICE_CHANGE' and day(e.effective_date) <> 1),
             -- 구성 변화: 그 관측월에 성분 안의 진입·퇴출·양도양수·정지·복귀 이벤트
             comp as (select market_c4 c4, observed_month as month, count(*) n_comp from ev_v5
                      where event_type in ('LISTING', 'DELISTING', 'SUCCESSION', 'DELISTING_STAY', 'SUSPEND', 'SUSPEND_LIFT') group by 1, 2),
             -- 등재·삭제만 (B1 대조군 좁힌 기준선, 사용자 결정 2026-11-16)
             ld as (select market_c4 c4, observed_month as month, count(*) n_ld from ev_v5
                    where event_type in ('LISTING', 'DELISTING') group by 1, 2)
        select p.*, c.m0 prev_month,
               coalesce(u0.observed_out, false) obs0, coalesce(u1.observed_out, false) obs1,
               (select count(*) from usage_c4_month z where z.c4 = p.c4 and z.observed_out) n_obs_months,
               round(ln(q1.up / q0.up), 12) dlog_up, q0.amt amt0, q1.amt amt1,   -- 청구수량 합(실수)의 끝자리 차이를 막는 반올림
               mid.c4 is not null mid_month_change, coalesce(comp.n_comp, 0) n_comp, coalesce(ld.n_ld, 0) n_listing_delisting
        from pairs p join cal c on c.m1 = p.month
        left join usage_c4_month u0 on u0.c4 = p.c4 and u0.month = c.m0
        left join usage_c4_month u1 on u1.c4 = p.c4 and u1.month = p.month
        left join _up q0 on q0.c4 = p.c4 and q0.month = c.m0
        left join _up q1 on q1.c4 = p.c4 and q1.month = p.month
        left join mid on mid.c4 = p.c4 and mid.month = p.month
        left join comp on comp.c4 = p.c4 and comp.month = p.month
        left join ld on ld.c4 = p.c4 and ld.month = p.month""")
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


def effective_month_prices(con) -> pd.DataFrame:
    """제품×달의 '유효일 기준' 목록가: 그 달 일수 가중 평균 상한금액.

    기본값은 그 달 스냅샷 상한금액(관측월 기준과 같다). PRICE_CHANGE 이벤트(관측월 o, 유효일 e, 직전 스냅샷 금액 b → o의 금액 a)마다
    - e가 o보다 앞 달이면: e의 달 ~ o 전달의 각 달에서 max(e, 그 달 1일)부터 말일까지를 a로 본다 (그 달 스냅샷 금액이 b일 때만)
    - e가 o 안이고 1일이 아니면: o의 1일 ~ e 전날을 b로 본다
    판매량이 아니라 달력 일수로 나눈다. exposure_frac(급여 일수 비율, 사후 칸)은 쓰지 않는다."""
    psm = con.execute("""select product_code, list_month, c4, state, price_listed from state_month_v5""").df()
    ev = con.execute("""select e.product_code, e.observed_month o, e.effective_date e, b.price_listed b, a.price_listed a
        from ev_v5 e join cal_v5 k on k.list_month = e.observed_month
        join state_month_v5 a on a.product_code = e.product_code and a.list_month = e.observed_month
        join state_month_v5 b on b.product_code = e.product_code and b.list_month = k.prev_month
        where e.event_type = 'PRICE_CHANGE' and a.price_listed > 0 and b.price_listed > 0""").df()
    psm["p_eff"] = psm["price_listed"].astype(float)
    idx = psm.set_index(["product_code", "list_month"]).index
    pos = pd.Series(range(len(psm)), index=idx)
    months = sorted(psm["list_month"].unique())
    for r in ev.itertuples(index=False):
        e = pd.Timestamp(r.e)
        o0 = pd.Timestamp(r.o + "-01")
        if e < o0:
            for m in months:
                ms = pd.Timestamp(m + "-01")
                if ms < e.replace(day=1) or ms >= o0:
                    continue
                k = (r.product_code, m)
                if k not in pos.index:
                    continue
                i = pos[k]
                if psm.at[i, "price_listed"] != r.b:
                    continue
                dim = ms.days_in_month
                frac = (dim - (max(e, ms) - ms).days) / dim       # a가 적용된 일수 비율
                psm.at[i, "p_eff"] = r.b * (1 - frac) + r.a * frac
        elif e > o0 and e.strftime("%Y-%m") == r.o:
            i = pos[(r.product_code, r.o)]
            frac_before = (e - o0).days / o0.days_in_month
            psm.at[i, "p_eff"] = r.b * frac_before + r.a * (1 - frac_before)
    return psm


def b1_effective_pairs(con, pairs: pd.DataFrame) -> pd.DataFrame:
    """관측월 기준 쌍(build_b1_pairs)의 목록 변화를 유효일 기준(일수 가중 평균 상한금액)으로 바꾼 값.
    바구니(두 달 모두 reimbursed, 금액 있음)와 원외 단가는 그대로. stage_eff_daywt: 월 중간 변경을 일수로 나눠 넣는다 (제외 4 없음).
    stage_eff_excl_mid: 같은 목록 변화에 관측월 기준의 제외 4(월 중간 적용 변경)를 그대로 둔다."""
    psm = effective_month_prices(con)
    con.register("_peff", psm[["product_code", "list_month", "c4", "state", "p_eff"]])
    x = con.execute("""with cal as (select prev_month m0, list_month m1 from cal_v5 where prev_month is not null)
        select a.c4, c.m1 as month,
               round(avg(ln(b.p_eff / a.p_eff)), 12) dlog_list_eff,      -- 소수 12자리 반올림 (병렬 집계 끝자리, 2026-11-17)
               count(*) filter (where abs(ln(b.p_eff / a.p_eff)) > 1e-12) n_changed_eff
        from cal c join _peff a on a.list_month = c.m0
        join _peff b on b.list_month = c.m1 and b.product_code = a.product_code and b.c4 = a.c4
        where a.state = 'reimbursed' and b.state = 'reimbursed' and a.p_eff > 0 and b.p_eff > 0
        group by 1, 2""").df()
    con.unregister("_peff")
    df = pairs.merge(x, on=["c4", "month"], how="left")
    df["n_changed_eff"] = df["n_changed_eff"].fillna(0).astype(int)
    base = [df["n_basket"] == 0, ~(df["obs0"] & df["obs1"]) | df["dlog_up"].isna(), df["n_changed_eff"] == 0]
    df["stage_eff_daywt"] = np.select(base, ["1_no_basket", "2_usage_unobserved", "3_no_list_change"], default="target")
    df["stage_eff_excl_mid"] = np.select(base + [df["mid_month_change"]],
                                         ["1_no_basket", "2_usage_unobserved", "3_no_list_change", "4_mid_month_change"], default="target")
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


def weighted_quantile(v: np.ndarray, w: np.ndarray, q: float) -> float:
    """가중 분위수: 정렬한 값의 누적 가중치가 q 이상이 되는 첫 값"""
    o = np.argsort(v)
    v, w = v[o], w[o]
    c = np.cumsum(w) / w.sum()
    return float(v[np.searchsorted(c, q)])


EXCL_ORDER = [("usage_unobserved", "전월 또는 그 달 원외 사용통계 미관측"),
              ("no_basket", "두 달 모두 reimbursed이고 금액이 있는 제품 없음"),
              ("no_list_change", "목록 가격이 바뀐 제품 없음 (x = 0)"),
              ("mid_month_change", "월 중간 적용 가격 변경"),
              ("target", "대상")]


def b1_main(df: pd.DataFrame, basis: str, subset: str = "all") -> tuple[pd.DataFrame, pd.DataFrame]:
    """B1 주 지표(사용자 결정 2026-11-16 [1]). df: 쌍 표, 칸 stage, x, y, amt1, obs0, obs1.
    - 같은 방향 비율, 쌍별 비율 y/x 중앙값과 사분위 (|x| 1% 이상)
    - 대조군: 같은 단계를 통과했지만 목록 가격이 바뀌지 않은 쌍(stage 3). 대상 쌍 i마다 대조군에서
      (가) y의 부호가 x_i와 같은 비율, (나) |x_i| 이상 오른 비율, |x_i| 이상 내린 비율, x_i 방향으로 |x_i| 이상 움직인 비율을 구해 평균한다
    - 원외 금액(그 달) 가중 같은 방향 비율, 가중 비율 중앙값
    반환: (지표 표, 제외 내역표)"""
    t = df[df["stage"] == "target"]
    ctl = df[df["stage"] == "3_no_list_change"]["y"].to_numpy()
    x, y, w = t["x"].to_numpy(), t["y"].to_numpy(), t["amt1"].to_numpy()
    same = np.sign(y) == np.sign(x)
    big = np.abs(x) >= np.log(1.01)
    r = y[big] / x[big]
    cs = np.sort(ctl)
    n = len(cs)
    ge = lambda a: n - np.searchsorted(cs, a, side="left")          # 대조군 y >= a 개수
    le = lambda a: np.searchsorted(cs, a, side="right")             # 대조군 y <= a 개수
    p_pos, p_neg, p_zero = (cs > 0).mean(), (cs < 0).mean(), (cs == 0).mean()
    exp_sign = np.where(x < 0, p_neg, p_pos).mean()
    up_ge = np.array([ge(abs(v)) for v in x]) / n
    dn_ge = np.array([le(-abs(v)) for v in x]) / n
    exp_size = np.where(x < 0, dn_ge, up_ge).mean()
    tgt_size = (same & (np.abs(y) >= np.abs(x))).mean()
    rows = [
        ("pairs", "대상 쌍", len(t)), ("c4s", "대상 성분", t["c4"].nunique()),
        ("share_same_sign", "같은 방향 비율", same.mean()),
        ("pairs_ratio", "비율 계산 쌍 (|x| 1% 이상)", int(big.sum())),
        ("ratio_median", "쌍별 비율 y/x 중앙값", np.median(r)),
        ("ratio_q25", "비율 1사분위", np.quantile(r, 0.25)), ("ratio_q75", "비율 3사분위", np.quantile(r, 0.75)),
        ("ctl_pairs", "대조군 쌍 (목록 변화 없음, 원외 두 달 관측)", n),
        ("ctl_share_up", "대조군: 원외 단가 오름", p_pos), ("ctl_share_down", "대조군: 내림", p_neg), ("ctl_share_flat", "대조군: 그대로", p_zero),
        ("ctl_expected_same_sign", "대조군 기준선: 대상 x와 같은 부호 비율 (쌍별 평균)", exp_sign),
        ("share_same_sign_ge_size", "대상: x 방향으로 |x| 이상 움직인 비율", tgt_size),
        ("ctl_share_up_ge_size", "대조군: |x_i| 이상 오른 비율 (쌍별 평균)", up_ge.mean()),
        ("ctl_share_down_ge_size", "대조군: |x_i| 이상 내린 비율 (쌍별 평균)", dn_ge.mean()),
        ("ctl_expected_same_sign_ge_size", "대조군 기준선: x_i 방향으로 |x_i| 이상 (쌍별 평균)", exp_size),
        ("w_share_same_sign", "원외 금액 가중 같은 방향 비율", (w * same).sum() / w.sum()),
        ("w_ratio_median", "원외 금액 가중 비율 중앙값 (|x| 1% 이상)", weighted_quantile(r, w[big], 0.5)),
    ]
    main = pd.DataFrame([{"basis": basis, "subset": subset, "metric": k, "label": lab, "value": round(float(v), 4)} for k, lab, v in rows])
    # 제외 내역: 사용통계 미관측을 먼저 떼고, 나머지를 사유별로 (서로 겹치지 않음, 합 100%)
    unobs = ~(df["obs0"] & df["obs1"]) | df["y"].isna()
    cat = np.select([unobs, df["n_basket"] == 0, df["stage"] == "3_no_list_change", df["stage"] == "4_mid_month_change"],
                    [k for k, _ in EXCL_ORDER[:4]], default="target")
    vc = pd.Series(cat).value_counts()
    ex = pd.DataFrame([{"basis": basis, "category": k, "label": lab, "pairs": int(vc.get(k, 0)),
                        "c4s": df.loc[cat == k, "c4"].nunique(), "share_pct": round(vc.get(k, 0) / len(df) * 100, 2)}
                       for k, lab in EXCL_ORDER])
    return main, ex


# ---------------------------------------------------------------- 비공개 규칙 대조 (관찰, 규칙은 바꾸지 않음)
def disclosure_detail(con) -> dict[str, pd.DataFrame]:
    """목록 업체 수(reimbursed)와 원외 관측의 불일치 두 묶음.
    - 업체 3곳 이상인데 원외 미관측: 그 성분×월 reimbursed 제품의 투여 경로(주성분코드 7번째 글자 B = 주사)로 나누고, 원내 관측 여부
    - 업체 2곳 이하인데 원외 관측: 건수와 예시만 (원인은 추측하지 않는다)"""
    con.execute("""create or replace temp table _disc as
        with l as (select c4, list_month as month, count(distinct firm) nf, count(*) np,
                          count(*) filter (where substr(ingredient_code, 7, 1) = 'B') n_inj
                   from state_month_v5 where state = 'reimbursed' group by 1, 2)
        select l.*, case when n_inj = np then 'injection_only' when n_inj > 0 then 'injection_mixed' else 'non_injection' end route_group,
               coalesce(z.observed_out, false) obs_out, coalesce(z.observed_any, false) and not coalesce(z.observed_out, false) obs_in_only
        from l left join usage_c4_month z using (c4, month)""")
    ge3 = con.execute("""select route_group, count(*) c4_months, count(distinct c4) c4s,
               count(*) filter (where obs_in_only) in_observed, round(avg(obs_in_only::int), 3) share_in_observed
        from _disc where nf >= 3 and not obs_out group by 1 order by 1""").df()
    ex_ge3 = con.execute("""select * from (select d.route_group, d.c4, d.month, d.nf, d.np, d.obs_in_only,
               (select min(b.ingredient_name) from stg_benefit b where b.c4 = d.c4 and b.list_month = d.month and b.ingredient_name is not null) ingredient_name,
               row_number() over (partition by route_group order by hash(d.c4 || d.month)) rn
        from _disc d where nf >= 3 and not obs_out) where rn <= 5 order by route_group, rn""").df()
    le2 = con.execute("""select d.c4, d.month, d.nf, d.np,
               (select min(b.ingredient_name) from stg_benefit b where b.c4 = d.c4 and b.list_month = d.month and b.ingredient_name is not null) ingredient_name
        from _disc d where nf <= 2 and obs_out order by hash(d.c4 || d.month) limit 5""").df()
    n_le2 = con.execute("select count(*), count(distinct c4) from _disc where nf <= 2 and obs_out").fetchone()
    le2.insert(0, "total_c4_months", n_le2[0])
    le2.insert(1, "total_c4s", n_le2[1])
    return {"b_disclosure_ge3_unobserved": ge3, "b_disclosure_ge3_examples": ex_ge3, "b_disclosure_le2_observed": le2}


# ---------------------------------------------------------------- B2
def b2_scope(cl: pd.DataFrame) -> pd.DataFrame:
    """B2 사례 표 밖 군집의 관측 범위 (관측 = 3개월 모두 원외 관측). 사후 창 미완결은 따로"""
    def cat(r):
        if not r["post_window_complete"]:
            return "0_post_window_incomplete"
        pre, post = r["n_obs_before"] == 3, r["n_obs_after"] == 3
        return {(True, True): "1_both", (True, False): "2_before_only", (False, True): "3_after_only"}.get((pre, post), "4_neither")
    c = cl.assign(scope=cl.apply(cat, axis=1))
    out = c.groupby("scope").agg(clusters=("c4", "size"), c4s=("c4", "nunique"),
                                 before_0m=("n_obs_before", lambda s: int((s == 0).sum())),
                                 before_1_2m=("n_obs_before", lambda s: int(s.between(1, 2).sum())),
                                 after_0m=("n_obs_after", lambda s: int((s == 0).sum())),
                                 after_1_2m=("n_obs_after", lambda s: int(s.between(1, 2).sum()))).reset_index()
    return out


def build_b2(con) -> dict[str, pd.DataFrame]:
    con.execute(f"""create or replace temp table _b2c as
        with e as (select e.market_c4 c4, e.observed_month as month, e.product_code, q.firm, e.effective_date
                   from ev_v5 e join state_month_v5 q on q.product_code = e.product_code and q.list_month = e.observed_month
                   where e.event_type = 'LISTING' and e.kind = 'new_listing' and e.link_basis is null)
        select c4, month, count(distinct firm) n_entry_firms, count(*) n_entry_products,
               min(effective_date) eff_min, max(effective_date) eff_max
        from e group by 1, 2""")
    dist = con.execute("""select case when n_entry_firms >= 10 then '10+' when n_entry_firms >= 5 then '5~9'
                                      when n_entry_firms >= 2 then '2~4' else '1' end bucket,
                                 substr(month, 1, 4) as year, count(*) n from _b2c group by 1, 2 order by 1, 2""").df()
    lo, hi = B2_WINDOW
    con.execute(f"""create or replace temp table _b2 as
        with cl as (select c.*, k.mi, k.prev_month from _b2c c join cal_v5 k on k.list_month = c.month where n_entry_firms >= {B2_MIN_FIRMS}),
             w as (select cl.c4, cl.month, k.list_month wm, k.mi - cl.mi rel from cl join cal_v5 k on k.mi between cl.mi + {lo} and cl.mi + {hi}),
             firms as (select w.c4, w.month, w.rel, count(distinct q.firm) filter (where q.state = 'reimbursed') n_firms,
                              count(*) filter (where q.state = 'reimbursed') n_products,
                              min(q.price_listed) filter (where q.state = 'reimbursed') min_price
                       from w left join state_month_v5 q on q.c4 = w.c4 and q.list_month = w.wm group by 1, 2, 3),
             -- 군집 전월에 이미 있던 제품의 가격 변경 (관측월 m−1 ~ m+3). 기존 제품 인하와 진입을 분리하지 않는다
             pre as (select cl.c4, cl.month, q.product_code, q.product_role from cl join state_month_v5 q
                       on q.c4 = cl.c4 and q.list_month = cl.prev_month and q.state = 'reimbursed'),
             pc as (select pre.c4, pre.month, count(distinct pre.product_code) n_pre,
                           count(distinct pre.product_code) filter (where pre.product_role = 'reference') n_pre_ref,
                           count(distinct e.product_code) n_pre_price_change,
                           count(distinct e.product_code) filter (where pre.product_role = 'reference') n_pre_ref_price_change
                    from pre left join ev_v5 e on e.product_code = pre.product_code and e.event_type = 'PRICE_CHANGE'
                         and e.observed_month in (select k.list_month from cal_v5 k, cal_v5 m0 where m0.list_month = pre.month
                                                  and k.mi between m0.mi - 1 and m0.mi + 3)
                    group by 1, 2),
             ref as (select cl.c4, cl.month, avg(b.price_listed / a.price_listed - 1) ref_price_chg
                     from cl join cal_v5 k3 on k3.mi = least(cl.mi + 3, 48)
                     join state_month_v5 a on a.c4 = cl.c4 and a.list_month = cl.prev_month and a.product_role = 'reference' and a.state = 'reimbursed'
                     join state_month_v5 b on b.product_code = a.product_code and b.list_month = k3.list_month and b.price_listed > 0
                     group by 1, 2)
        select cl.c4, cl.month, cl.n_entry_firms, cl.n_entry_products, cl.eff_min, cl.eff_max,
               (select min(q.ingredient_code) from state_month_v5 q where q.c4 = cl.c4 and q.list_month = cl.month) ingredient_code,
               (select min(b.ingredient_name) from stg_benefit b where b.c4 = cl.c4 and b.list_month = cl.month and b.ingredient_name is not null) ingredient_name,
               f0.n_firms firms_before, f1.n_firms firms_month, f3.n_firms firms_after3,
               f0.min_price min_price_before, f3.min_price min_price_after3,
               pc.n_pre, pc.n_pre_ref, pc.n_pre_price_change, pc.n_pre_ref_price_change, ref.ref_price_chg,
               cl.mi + {hi} <= 48 post_window_complete,
               z.first_observed_month_out,
               (select count(*) from w join usage_c4_month u on u.c4 = w.c4 and u.month = w.wm and u.observed_out
                 where w.c4 = cl.c4 and w.month = cl.month and w.rel < 0) n_obs_before,
               (select count(*) from w where w.c4 = cl.c4 and w.month = cl.month and w.rel < 0) n_pre_months,
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
        left join cal_v5 km on km.list_month = cl.month
        left join cal_v5 k0 on k0.mi = km.mi - 1
        left join cal_v5 k3 on k3.mi = km.mi + {hi}
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
    return {"b2_cluster_dist": dist, "b2_selection": steps, "b2_clusters": cl.drop(columns=["ref_price_chg"]),
            "b2_scope": b2_scope(cl), "b2_pre_truncated": b2_pre_truncated(cl)}


def b2_pre_truncated(cl: pd.DataFrame) -> pd.DataFrame:
    """진입 전 창이 분석 시작(2022-01)에 잘린 군집 (사후 창 완결). 사례 조건(앞뒤 3개월 모두 관측)은 바꾸지 않고,
    있는 달만으로 보면 조건을 채우는지만 적는다 (사용자 결정 2026-11-16)"""
    t = cl[cl["post_window_complete"] & (cl["n_pre_months"] < 3)]
    out = t[["c4", "ingredient_name", "month", "n_entry_firms", "n_pre_months", "n_obs_before", "n_obs_after", "case_usable"]].copy()
    out["meets_with_available_months"] = (t["n_obs_before"] == t["n_pre_months"]) & (t["n_pre_months"] > 0) & (t["n_obs_after"] == 3)
    return out.reset_index(drop=True)


def b2_case_series(con, c4: str, month: str) -> pd.DataFrame:
    """사례 하나의 월별 업체 수·최저/최고가·원외 단가 (군집 관측월 −3 ~ +3)"""
    lo, hi = B2_WINDOW
    return con.execute(f"""with m0 as (select mi from cal_v5 where list_month = ?)
        select k.list_month as month, k.mi - m0.mi rel,
               count(distinct q.firm) firms_all, count(q.product_code) products_all,
               count(distinct q.firm) filter (where q.state = 'reimbursed') firms,
               count(*) filter (where q.state = 'reimbursed') products,
               min(q.price_listed) filter (where q.state = 'reimbursed') min_price,
               max(q.price_listed) filter (where q.state = 'reimbursed') max_price,
               round(any_value(u.up), 1) up, any_value(z.observed_out) observed_out
        from m0, cal_v5 k left join state_month_v5 q on q.c4 = ? and q.list_month = k.list_month
        left join _up u on u.c4 = ? and u.month = k.list_month
        left join usage_c4_month z on z.c4 = ? and z.month = k.list_month
        where k.mi between m0.mi + {lo} and m0.mi + {hi} group by 1, 2 order by 1""", [month, c4, c4, c4]).df()


# ---------------------------------------------------------------- C
def build_c(con, month: str = C_MONTH) -> dict[str, pd.DataFrame]:
    """대체조제 그룹 순위표. 그룹 = (sub_group_code, ingredient_code), 그 달 대체조제 목록에 있고 state = reimbursed,
    상한금액이 있는 제품. 성분 원외 금액은 성분4 단위라 같은 성분4의 여러 그룹이 같은 금액을 가진다."""
    # 표본 회귀 (PLAN F10): 대체조제 목록 원본 행 기준 (정지·산정불가 포함, 대체조제 목록 금액)
    f10 = con.execute("""with g as (select group_code, ingredient_code, count(*) n, min(price) mn, max(price) mx
                                    from stg_substitute where list_month = ? group by 1, 2)
        select count(*) groups, sum((n >= 2)::int) groups_ge2, round(median(case when n >= 2 then mx / mn end), 2) med_max_min,
               round(quantile_cont(case when n >= 2 then mx / mn end, 0.9), 2) p90_max_min from g""", [month]).df()
    year = month[:4]
    con.execute("""create or replace temp table _c as
        with p as (select sub_group_code grp, ingredient_code, c4, product_code, firm, price_listed price, sub_ref_drug ref
                   from state_month_v5 where list_month = ? and in_substitute_list and state = 'reimbursed' and price_listed > 0),
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
    total = con.execute("select sum(amt) from _up where substr(month, 1, 4) = ?", [year]).fetchone()[0]
    return {"c_f10_sample_check": f10, "c_groups_all": allg, "c_selection": sel, "c_rank": rank,
            **c_by_c4(rank, total)}


def c_by_c4(rank: pd.DataFrame, total_amt: float) -> dict[str, pd.DataFrame]:
    """C 본문: 성분4 단위 (사용자 결정 2026-11-16 [3]). 순위 대상 그룹(제품 2개 이상, 원외 12개월 관측)을 성분4로 묶는다.
    가격 차가 가장 큰 그룹 = 최고/최저 비율이 가장 큰 그룹 (같으면 제품 수가 많은 그룹)."""
    r = rank.sort_values(["c4", "max_min_ratio", "n_products"], ascending=[True, False, False])
    top = r.groupby("c4").head(1).set_index("c4")
    g = rank.groupby("c4").agg(c4_out_amt_year=("c4_out_amt_year", "first"), n_groups=("grp", "size"),
                               n_products=("n_products", "sum")).join(
        top[["grp", "ingredient_code", "ingredient_name", "n_products", "min_price", "max_price", "max_min_ratio"]]
        .rename(columns=lambda c: f"widest_{c}"))
    g = g.sort_values("c4_out_amt_year", ascending=False).reset_index()
    g.insert(0, "rank", range(1, len(g) + 1))
    share = pd.DataFrame([{"total_out_amt_year": total_amt, "c4s": len(g), "c4_unique_amt": g["c4_out_amt_year"].sum(),
                           "share_c4_unique": round(g["c4_out_amt_year"].sum() / total_amt, 4),
                           "group_row_amt_sum": rank["c4_out_amt_year"].sum(),
                           "share_group_row_sum": round(rank["c4_out_amt_year"].sum() / total_amt, 4)}])
    return {"c_rank_c4": g, "c_share": share}


def disclosure_check(con) -> pd.DataFrame:
    """목록의 성분×월 업체·제품 수(state = reimbursed)와 사용통계 원외 관측 여부.
    DATA.md의 비공개 규칙(품목 수 또는 업체 수 2개 이하 제외)을 목록 쪽 수로 대조한다. 규칙을 정하지 않고 건수만 낸다."""
    return con.execute("""with l as (select c4, list_month as month, count(distinct firm) nf, count(*) np
                                      from state_month_v5 where state = 'reimbursed' group by 1, 2)
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
    # 주 지표 = 유효일 달 일수 가중 (eff_daywt, 사용자 결정 2026-11-16). 관측월 기준(b1_slopes 등)은 민감도
    eff = b1_effective_pairs(con, pairs)
    sel_e, slo_e = [], []
    for key in ("eff_daywt", "eff_excl_mid"):
        p = eff.drop(columns=["stage", "dlog_list"]).rename(columns={f"stage_{key}": "stage", "dlog_list_eff": "dlog_list"})
        s1, s2 = b1_summary(p, seed)
        sel_e.append(s1.assign(basis=key))
        slo_e.append(s2.assign(basis=key))
    tabs["b1_pairs_effective"] = eff[["c4", "month", "n_basket", "n_changed", "n_changed_eff", "dlog_list", "dlog_list_eff",
                                      "dlog_up", "mid_month_change", "n_comp", "n_listing_delisting", "stage", "stage_eff_daywt", "stage_eff_excl_mid"]]
    tabs["b1_selection_effective"] = pd.concat(sel_e)
    tabs["b1_slopes_effective"] = pd.concat(slo_e)
    # 주 지표·대조군·제외 내역 (세 기준 나란히)
    mains, exs = [], []
    for key, stage_col, xcol in (("observed_month", "stage", "dlog_list"), ("eff_daywt", "stage_eff_daywt", "dlog_list_eff"),
                                 ("eff_excl_mid", "stage_eff_excl_mid", "dlog_list_eff")):
        d = eff.assign(stage=eff[stage_col], x=eff[xcol], y=eff["dlog_up"])
        m, e = b1_main(d, key)
        mains.append(m)
        exs.append(e)
        # 좁힌 기준선: 그 달 그 성분에 등재·삭제 이벤트가 없는 쌍 (대상·대조군 같은 조건)
        mains.append(b1_main(d[d["n_listing_delisting"] == 0], key, "no_listing_delisting")[0])
    tabs["b1_main"] = pd.concat(mains)
    tabs["b1_exclusion"] = pd.concat(exs)
    tabs.update(disclosure_detail(con))
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
