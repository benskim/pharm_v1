"""P1 S4: 분석 A (docs/PLAN_P1.md §5 A1~A6), v5 원장 (docs/RULES_2026-11-17.md, 2026-11-18 이전).

data/interim/pbd.duckdb의 v5 표(signal_v5, state_month_v5, ev_v5, cal_v5)에서 outputs/s4/*.csv와 검증 표본을 만든다.
v4 원장으로 만든 이전 산출은 outputs/v4_legacy/s4/ (읽기 전용).
- A1 지표 (RULES §7): 신호 유형 × 연도 × product_role. A 현재 시점 집행률 · B 최종 실현율 · 정시 실현율 · 집행정지율, Wilson 95%.
  삭제 예고(DELISTING_NOTICE)는 건수만.
- A2 시차: 최초 기재 달 1일 → 신호 날짜(lead_days) → 실현 유효일(realize_lag_days), 확인 스냅샷 → 실현 스냅샷 순서.
- A3 실현 안 된 신호: 결과값·사유별 건수와 사례 (미실현 / 판정 제외 / 미결).
- A4 사용통계 검증: 실현된 약가 예고(R2, 실현·변경 실현) 묶음(성분4 × 신호 날짜)에서 성분 원외 단가가 신호 날짜의 달과 실현 달 중 언제 움직였나.
- A5 원장 대 스냅샷 차이 방식 대조표 (연도 × 진입·퇴출·약가 변경).
- A6 검증 표본: sample_60은 고정 라벨 표본(규칙 개발용, scripts/v5_labels.py)이라 다시 만들지 않는다.
  새 검증 표본 holdout 30행 (RULES §11-8): 층 10개 × 3행, sample_60 제품 제외, 시드 고정, 라벨 파일에는 원장 판정값을 넣지 않는다.

product_role은 신호가 최초 기재된 달의 값(product_attr_month)이다. 대조약이 항상 오리지널은 아니다.
사용: PYTHONPATH=src python -m pbd.ledger.s4_analysis   (make p1-s4)
"""
from __future__ import annotations

import math

import pandas as pd

from pbd.common import ROOT, connect, load_yaml
from pbd.ledger.v5_rules import rates

OUT = ROOT / "outputs/s4"
VAL = ROOT / "outputs/validation"
PARAMS = load_yaml("params.yaml")
ROLE_KO = {"reference": "대조약", "bioequiv": "생동인정", None: "모름"}
OUTCOMES = ("REALIZED", "REALIZED_CHANGED", "REALIZED_LATE", "REALIZED_BY_DELISTING", "NOT_REALIZED", "EXCLUDED", "CENSORED")


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    """Wilson 95% 구간. n = 0이면 (None, None)."""
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def _dump(df: pd.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n")


# ---------------------------------------------------------------- A1
def a1_base(con) -> pd.DataFrame:
    return con.execute("""select s.signal_id, s.signal_type, s.source_rule, substr(s.first_month, 1, 4) as year, s.outcome,
               s.reason, s.current_status, s.stay_blocked, a.product_role role,
               case when a.product_role is null then null when a.in_substitute_list then 'substitute' else 'orangebook' end role_source
        from signal_v5 s left join product_attr_month a on a.product_code = s.product_code and a.list_month = s.first_month
        order by s.signal_id""").df()


def a1_table(base: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    for k, g in base.groupby(keys, dropna=False, sort=True):
        k = k if isinstance(k, tuple) else (k,)
        rec = dict(zip(keys, [("모름" if (c == "role" and (v is None or (isinstance(v, float) and math.isnan(v)))) else
                               ROLE_KO.get(v, v) if c == "role" else v) for c, v in zip(keys, k)]))
        cnt = g["outcome"].value_counts()
        rec["n"] = len(g)
        for o in OUTCOMES:
            rec[o.lower()] = int(cnt.get(o, 0))
        rec["censored_ongoing_stay"] = int(((g["outcome"] == "CENSORED") & (g["current_status"] == "ONGOING_STAY")).sum())
        rec["censored_awaiting"] = int(((g["outcome"] == "CENSORED") & (g["current_status"] == "AWAITING")).sum())
        x = rates(g[["outcome", "reason", "stay_blocked", "current_status"]].to_dict("records"))
        for m in ("A", "B", "on_time", "stay"):
            a, b, p, lo, hi = x[m]
            count_only = rec.get("signal_type") == "DELISTING_NOTICE"            # PLAN §5 A1: 건수로만
            rec.update({f"{m}_k": a, f"{m}_n": b, f"{m}_rate": None if (p is None or count_only) else round(p, 4),
                        f"{m}_lo": None if (lo is None or count_only) else round(lo, 4),
                        f"{m}_hi": None if (hi is None or count_only) else round(hi, 4)})
        rows.append(rec)
    return pd.DataFrame(rows)


def build_a1(con) -> dict[str, pd.DataFrame]:
    base = a1_base(con)
    out = {
        "a1_by_type": a1_table(base, ["signal_type"]),
        "a1_by_type_rule": a1_table(base, ["signal_type", "source_rule"]),
        "a1_by_type_year": a1_table(base, ["signal_type", "year"]),
        "a1_by_type_role": a1_table(base, ["signal_type", "role"]),
        "a1_by_type_year_role": a1_table(base, ["signal_type", "year", "role"]),
    }
    rp = base.assign(role=base["role"].map(lambda v: ROLE_KO.get(v, v) if isinstance(v, str) else "모름"),
                     role_source=base["role_source"].fillna("-"))
    out["a1_role_preface"] = (rp.groupby(["year", "role", "role_source"]).size().rename("n_signals").reset_index()
                              .assign(pct_in_year=lambda d: (100 * d["n_signals"] / d.groupby("year")["n_signals"].transform("sum")).round(1)))
    return out


# ---------------------------------------------------------------- A2·A3
REALIZED_SQL = "outcome in ('REALIZED', 'REALIZED_CHANGED', 'REALIZED_LATE', 'REALIZED_BY_DELISTING')"


def build_a2(con) -> pd.DataFrame:
    """lead_days = 신호 날짜 − 최초 기재 달 1일, realize_lag_days = 실현 유효일 − 신호 날짜 (실현 계열)"""
    return con.execute(f"""with s as (select signal_type, outcome, datediff('day', cast(first_month || '-01' as date), signal_date) lead_days,
                                       datediff('day', signal_date, effective_date) realize_lag_days from signal_v5 where {REALIZED_SQL})
        select signal_type, outcome, count(*) n, min(lead_days) lead_min, quantile_cont(lead_days, 0.1) lead_p10, median(lead_days) lead_median,
               quantile_cont(lead_days, 0.9) lead_p90, max(lead_days) lead_max, min(realize_lag_days) realize_lag_min,
               median(realize_lag_days) realize_lag_median, max(realize_lag_days) realize_lag_max
        from s group by 1, 2 order by 1, 2""").df()


def build_a2_buckets(con) -> pd.DataFrame:
    return con.execute(f"""with s as (select signal_type, datediff('day', cast(first_month || '-01' as date), signal_date) lead_days,
                                       datediff('day', signal_date, effective_date) lag from signal_v5 where {REALIZED_SQL})
        select signal_type, case when lead_days <= 7 then '0-7' when lead_days <= 14 then '8-14' when lead_days <= 31 then '15-31'
                    when lead_days <= 62 then '32-62' else '63+' end lead_bucket,
               case when lag < 0 then '<0' when lag = 0 then '0' when lag <= 31 then '1-31' when lag <= 365 then '32-365' else '366+' end realize_lag_bucket,
               count(*) n from s group by all order by 1, 2, 3""").df()


def build_a2_snapshot_order(con) -> pd.DataFrame:
    """실현 스냅샷 순서 (최초 기재 스냅샷 기준): 같은 달 / 다음 스냅샷 / 2번째 …"""
    df = con.execute(f"""select o.signal_type, ci.mi - ii.mi k, count(*) n
        from signal_v5 o join cal_v5 ii on ii.list_month = o.first_month join cal_v5 ci on ci.list_month = o.realized_month
        where o.{REALIZED_SQL} group by all""").df()
    df["confirm_snapshot"] = [("같은 달" if k == 0 else "다음 스냅샷" if k == 1 else f"{k}번째") for k in df["k"]]
    return df.sort_values(["signal_type", "k"]).reset_index(drop=True)[["signal_type", "confirm_snapshot", "n"]]


def build_a3(con) -> pd.DataFrame:
    return con.execute("""select signal_type, source_rule, outcome, coalesce(reason, current_status, '-') reason, stay_blocked,
               coalesce(exclude_reason, '-') exclude_reason, count(*) n, min(first_month) first_listed, max(first_month) last_listed,
               array_to_string(list(product_code || ' ' || first_month || ' ' || signal_date order by first_month, product_code)[1:3], ' ; ') examples,
               count(reason_external) n_reason_external
        from signal_v5 where outcome in ('NOT_REALIZED', 'EXCLUDED', 'CENSORED') group by all order by 1, 2, 3, 4, 5""").df()


# ---------------------------------------------------------------- A4
# v5: 산정불가는 상태가 아니라 약가 유형 (RULES §2-1). 주 기준 = 급여 상태이고 산정불가가 아님 (v4 reimbursed와 같은 뜻),
# 민감도 = 급여 상태 전체 (v4 reimbursed + unpriced와 같은 뜻). 사용자 결정 2026-11-15
A4_STATES = "state = 'reimbursed' and coalesce(price_type, '') <> 'unpriced'"
A4_STATES_SENS = "state = 'reimbursed'"


def build_a4(con, states: str = A4_STATES) -> dict[str, pd.DataFrame]:
    """states: "절반 이상"의 분모에 넣는 최초 기재 달 제품 조건 (state_month_v5)"""
    con.execute("""create or replace temp table _up as
        select month, c4, sum(claim_amt) amt, sum(claim_qty) qty, sum(claim_amt) / nullif(sum(claim_qty), 0) up
        from stg_usage where in_out = 'OUT' group by 1, 2""")
    con.execute(f"""create or replace temp table _a4 as
        with s as (select o.signal_id, o.product_code, o.market_c4 c4, o.signal_date, o.realized_month check_month, o.first_month issued_month,
                          (select q.price_listed from state_month_v5 q where q.product_code = o.product_code and q.list_month = o.first_month) p0,
                          (select q.price_listed from state_month_v5 q where q.product_code = o.product_code and q.list_month = o.realized_month) p1
                   from signal_v5 o
                   where o.signal_type = 'PRICE_NOTICE' and o.source_rule = 'R2' and o.outcome in ('REALIZED', 'REALIZED_CHANGED')),
             g as (select c4, signal_date, check_month, min(issued_month) issued_month, count(*) n_sig,
                          avg(p1 / p0 - 1) list_chg from s group by 1, 2, 3),
             np as (select c4, list_month, count(*) n_prod from state_month_v5
                    where {states} group by 1, 2)
        select g.*, strftime(g.signal_date, '%Y-%m') d_month, np.n_prod, g.n_sig * 1.0 / np.n_prod share,
               (select prev_month from cal_v5 c where c.list_month = strftime(g.signal_date, '%Y-%m')) d_prev
        from g left join np on np.c4 = g.c4 and np.list_month = g.issued_month""")
    con.execute("""create or replace temp table _a4s as
        select a.*,
               case when a.share < 0.5 then '1_share_lt_half'
                    when coalesce(u0.observed_out, false) = false or coalesce(u1.observed_out, false) = false
                         or coalesce(u2.observed_out, false) = false then '2_usage_unobserved'
                    else 'target' end stage,
               p0.up up_before, p1.up up_signal_month, p2.up up_observed_month,
               a.d_month = a.check_month same_month,
               (day(last_day(a.signal_date)) - day(a.signal_date) + 1) * 1.0 / day(last_day(a.signal_date)) frac_new_in_signal_month
        from _a4 a
        left join usage_c4_month u0 on u0.c4 = a.c4 and u0.month = a.d_prev
        left join usage_c4_month u1 on u1.c4 = a.c4 and u1.month = a.d_month
        left join usage_c4_month u2 on u2.c4 = a.c4 and u2.month = a.check_month
        left join _up p0 on p0.c4 = a.c4 and p0.month = a.d_prev
        left join _up p1 on p1.c4 = a.c4 and p1.month = a.d_month
        left join _up p2 on p2.c4 = a.c4 and p2.month = a.check_month""")
    steps = con.execute("""select stage, count(*) groups, sum(n_sig) signals, count(distinct c4) c4s from _a4s group by 1 order by 1""").df()
    tgt = con.execute("""select c4, signal_date, d_month, check_month, n_sig, n_prod, round(share, 2) share, round(list_chg * 100, 1) list_chg_pct,
               round(frac_new_in_signal_month, 3) frac_new, same_month,
               round((up_signal_month / up_before - 1) * 100, 1) up_chg_signal_month_pct,
               round((up_observed_month / up_signal_month - 1) * 100, 1) up_chg_observed_month_pct,
               -- 원장 기대값: 신호 달에 frac_new 비율만큼, 나머지는 다음 달. 스냅샷 차이 방식 기대값: 관측월에 전부
               round(list_chg * frac_new_in_signal_month * 100, 1) ledger_expected_signal_month_pct
        from _a4s where stage = 'target' order by signal_date, c4""").df()
    t = tgt[~tgt["same_month"]].copy()
    if len(t):
        le_d = t["ledger_expected_signal_month_pct"]
        le_o = ((1 + t["list_chg_pct"] / 100) / (1 + le_d / 100) - 1) * 100
        err_ledger = (t["up_chg_signal_month_pct"] - le_d).abs() + (t["up_chg_observed_month_pct"] - le_o).abs()
        err_snap = t["up_chg_signal_month_pct"].abs() + (t["up_chg_observed_month_pct"] - t["list_chg_pct"]).abs()
        t["ledger_closer"] = err_ledger < err_snap
        t["moved_more_in"] = (t["up_chg_signal_month_pct"].abs() > t["up_chg_observed_month_pct"].abs()).map(
            {True: "signal_month", False: "observed_month"})
        tgt = tgt.merge(t[["c4", "signal_date", "ledger_closer", "moved_more_in"]], on=["c4", "signal_date"], how="left")
    groups = con.execute("""select c4, signal_date, check_month, n_sig, n_prod, share, stage from _a4s""").df()
    return {"a4_selection": steps, "a4_targets": tgt, "_groups": groups}


def build_a4_with_sensitivity(con) -> dict[str, pd.DataFrame]:
    """주 기준(급여·산정불가 아님)과 민감도(급여 전체, 산정불가 포함)를 함께 내고, 분모·대상 여부가 다른 묶음을 비교한다."""
    main = build_a4(con, A4_STATES)
    sens = build_a4(con, A4_STATES_SENS)
    key = ["c4", "signal_date", "check_month"]
    cmp = main.pop("_groups").merge(sens.pop("_groups"), on=key + ["n_sig"], suffixes=("_main", "_sens"), how="outer")
    diff = cmp[(cmp["n_prod_main"].fillna(-1) != cmp["n_prod_sens"].fillna(-1)) | (cmp["stage_main"] != cmp["stage_sens"])].copy()
    diff["stage_changed"] = diff["stage_main"] != diff["stage_sens"]
    diff["share_main"] = diff["share_main"].round(3)
    diff["share_sens"] = diff["share_sens"].round(3)
    diff = diff.sort_values(key).reset_index(drop=True)
    return {"a4_selection": main["a4_selection"], "a4_targets": main["a4_targets"],
            "a4_selection_sens_unpriced": sens["a4_selection"], "a4_targets_sens_unpriced": sens["a4_targets"],
            "a4_denominator_diff": diff}


# ---------------------------------------------------------------- A5
def build_a5(con) -> pd.DataFrame:
    """원장(v5) 대 스냅샷 차이 방식: 연도 × 구분. 스냅샷 차이 = 급여목록에 새로 나타난 코드 / 사라진 코드 / 상한금액이 바뀐 연속 두 달.
    원장 = 진입(신규·추가·재등재·이어받기·삭제 집행정지) / 퇴출(급여삭제, 이어받기로 넘겨준 쪽) / 약가 변경 (전후 두 달 급여 상태만)"""
    return con.execute("""with b as (select product_code, list_month, price_listed, state from state_month_v5),
             c as (select list_month, mi, prev_month from cal_v5),
             naive as (
                select substr(c.list_month, 1, 4) yr, 'entry' side, count(*) n from b join c using (list_month)
                where c.prev_month is not null and not exists (select 1 from b b0 where b0.product_code = b.product_code and b0.list_month = c.prev_month) group by 1
                union all
                select substr(c.list_month, 1, 4), 'exit', count(*) from b b0 join c on c.prev_month = b0.list_month
                where not exists (select 1 from b where b.product_code = b0.product_code and b.list_month = c.list_month) group by 1
                union all
                select substr(c.list_month, 1, 4), 'price_change', count(*) from b join c using (list_month)
                join b b0 on b0.product_code = b.product_code and b0.list_month = c.prev_month
                where b.price_listed is not null and b0.price_listed is not null and b.price_listed <> b0.price_listed group by 1),
             led as (
                select substr(observed_month, 1, 4) yr, 'entry' side, count(*) n from ev_v5
                where event_type in ('LISTING', 'SUCCESSION', 'DELISTING_STAY') group by 1
                union all
                select substr(observed_month, 1, 4), 'exit', count(*) from ev_v5 where event_type = 'DELISTING' group by 1
                union all
                select substr(gone_month, 1, 4), 'exit_succession', count(*) from succession_v5 group by 1
                union all
                select substr(observed_month, 1, 4), 'price_change', count(*) from ev_v5 where event_type = 'PRICE_CHANGE' group by 1),
             ex as (select substr(list_month, 1, 4) yr, count(*) filter (where state = 'scheduled_listing') scheduled_rows,
                           count(*) filter (where state = 'suspended') suspended_rows from b group by 1)
        select coalesce(n.yr, l.yr) "year", coalesce(n.side, l.side) side, coalesce(n.n, 0) naive_n, coalesce(l.n, 0) ledger_n,
               ex.scheduled_rows, ex.suspended_rows
        from naive n full join led l on l.yr = n.yr and l.side = n.side left join ex on ex.yr = coalesce(n.yr, l.yr)
        order by 1, 2""").df()


# ---------------------------------------------------------------- A6 holdout (RULES §11-8)
HOLDOUT_STRATA = [   # (층 이름, 기록 종류, 조건)
    ("양도양수", "event", "event_type = 'SUCCESSION' and kind = 'transfer'"),
    ("코드 변경", "event", "event_type = 'SUCCESSION' and kind = 'recode'"),
    ("교체 등재", "event", "event_type = 'SUCCESSION' and kind = 'swap'"),
    ("급여삭제", "event", "event_type = 'DELISTING' and kind = 'delisting'"),
    ("중지해제", "event", "event_type = 'SUSPEND_LIFT'"),
    ("지연 실현", "signal", "outcome = 'REALIZED_LATE'"),
    ("변경 실현", "signal", "outcome = 'REALIZED_CHANGED'"),
    ("미실현", "signal", "outcome = 'NOT_REALIZED'"),
    ("미결", "signal", "outcome = 'CENSORED'"),
    ("예고 미관측 변경", "event", "event_type = 'PRICE_CHANGE' and kind = 'unobserved_notice'"),
]
HOLDOUT_PER = 3
BLIND_NAME, KEY_NAME = "holdout_30.blind.csv", "holdout_30.key.csv"
TRUTH_COLS = ("truth_type", "truth_date", "truth_note")


def holdout_pick(con, seed: int | None = None, exclude_products: set[str] | None = None) -> pd.DataFrame:
    """층마다 md5(seed || record_id) 순으로 3행. sample_60 제품(그리고 이어받기 상대 코드가 sample_60 제품인 기록)은 뺀다.
    한 제품은 표본에 한 번만"""
    seed = PARAMS["seed"] if seed is None else seed
    ex = set(exclude_products or ())
    picked, used = [], set()
    for name, rtype, cond in HOLDOUT_STRATA:
        if rtype == "event":
            q = f"""select event_id record_id, product_code, counterpart, observed_month ref_month from ev_v5 where {cond}
                    order by md5('{seed}' || event_id)"""
        else:
            q = f"""select signal_id record_id, product_code, null counterpart, first_month ref_month from signal_v5 where {cond}
                    order by md5('{seed}' || signal_id)"""
        k = 0
        for rid, pc, cp, ref in con.execute(q).fetchall():
            if pc in ex or (cp and cp in ex) or pc in used or (cp and cp in used):
                continue
            picked.append(dict(stratum=name, record_type=rtype, record_id=rid, product_code=pc, counterpart=cp, ref_month=ref))
            used |= {pc} | ({cp} if cp else set())
            k += 1
            if k == HOLDOUT_PER:
                break
    df = pd.DataFrame(picked)
    df.insert(0, "holdout_id", range(1, len(df) + 1))
    return df


def _window(con, pc: str, ref: str) -> tuple[str, str, str]:
    """기준월 전후 2개월의 두 목록 원문 (업체명 / 제품명 / 규격 / 금액 / 비고), 급여목록 관측 기간"""
    ms = [r[0] for r in con.execute("""select list_month from cal_v5 where mi between (select mi from cal_v5 where list_month = ?) - 2
                                       and (select mi from cal_v5 where list_month = ?) + 2 order by 1""", [ref, ref]).fetchall()]
    def fmt(table, note):
        out = []
        for m in ms:
            r = con.execute(f"""select firm, product_name, spec, unit, price, {note} from {table} where product_code = ? and list_month = ?
                                and row_kind = 'product_candidate' limit 1""", [pc, m]).fetchone()
            out.append(f"{m}: " + ("(없음)" if r is None else " / ".join("" if v is None else str(v) for v in
                                     (r[0], r[1], f"{r[2] or ''}{r[3] or ''}", r[4], r[5] or "-"))))
        return " | ".join(out)
    life = con.execute("select min(list_month), max(list_month) from stg_benefit where product_code = ? and row_kind = 'product_candidate'", [pc]).fetchone()
    return fmt("stg_benefit", "note"), fmt("stg_substitute", "note"), f"{life[0]}~{life[1]}" if life[0] else "(급여목록에 없음)"


def _candidates(con, pc: str, ref: str) -> str:
    """후보 코드 (원문 칸만): 같은 품목기준코드의 다른 코드는 기간 제한 없이 모두 (2026-11-18 holdout 반영),
    같은 주성분코드(9자리)의 다른 코드는 기준월 전후 2개월 안에 처음 실리거나 마지막으로 실린 것"""
    rows = con.execute("""with me as (select any_value(ingredient_code) ing from stg_benefit where product_code = ? and row_kind = 'product_candidate'),
             it as (select item_code from bridge_product_item where product_code = ?),
             w as (select list_month from cal_v5 where mi between (select mi from cal_v5 where list_month = ?) - 2
                   and (select mi from cal_v5 where list_month = ?) + 2),
             life as (select b.product_code, min(b.list_month) f, max(b.list_month) l, arg_max(b.firm, b.list_month) firm,
                             arg_max(b.product_name, b.list_month) nm, arg_max(b.price, b.list_month) price
                      from stg_benefit b where b.row_kind = 'product_candidate' group by 1)
        select l.product_code, l.firm, l.nm, l.price, l.f, l.l,
               exists (select 1 from bridge_product_item i where i.product_code = l.product_code and i.item_code in (select item_code from it)) same_item
        from life l where l.product_code <> ?
          and (l.product_code in (select product_code from bridge_product_item where item_code in (select item_code from it))
               or l.product_code in (select product_code from stg_benefit where ingredient_code = (select ing from me)))
          and (l.product_code in (select product_code from bridge_product_item where item_code in (select item_code from it))
               or l.f in (select list_month from w) or l.l in (select list_month from w))
        order by same_item desc, l.product_code limit 12""", [pc, pc, ref, ref, pc]).fetchall()
    return " ; ".join(f"{c}({'같은 허가' if si else '같은 성분'}, {f} / {n} / {p}, {a}~{b})" for c, f, n, p, a, b, si in rows) or "(없음)"


def build_holdout(con, seed: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(blind, key). blind = 라벨용 (원장 판정값 없음). key = holdout_id ↔ 원장 기록 (라벨을 마친 뒤 대조할 때만 연다).
    라벨 파일(holdout_30.labeled.csv)이 있으면 표본은 고정한다: 기존 key의 기록을 그대로 쓰고 라벨용 칸만 다시 채운다"""
    s60 = pd.read_csv(VAL / "sample_60.user_reviewed.csv", dtype=str)
    if (VAL / "holdout_30.labeled.csv").exists() and (VAL / KEY_NAME).exists():
        pick = pd.read_csv(VAL / KEY_NAME, dtype=str)
        pick["holdout_id"] = pick["holdout_id"].astype(int)
        pick["counterpart"] = None
    else:
        pick = holdout_pick(con, seed, set(s60["product_code"]))
    blind = []
    for r in pick.itertuples():
        wb, ws, life = _window(con, r.product_code, r.ref_month)
        note = ""
        if r.record_type == "signal":           # 판정할 비고 (원문 토큰)
            note = con.execute("""select string_agg(distinct t.raw, ' ; ') from note_token t
                                  join signal_v5 s on s.product_code = t.product_code and t.list_month = s.first_month
                                  where s.signal_id = ? and t.token_date = s.signal_date""", [r.record_id]).fetchone()[0] or ""
        blind.append(dict(holdout_id=r.holdout_id, record_kind="비고 예고" if r.record_type == "signal" else "목록 변화",
                          product_code=r.product_code, ref_month=r.ref_month, target_note=note, benefit_listed=life,
                          benefit_window=wb, substitute_window=ws, candidate_codes=_candidates(con, r.product_code, r.ref_month),
                          truth_type="", truth_date="", truth_note="", labeler="", label_source=""))
    key = pick.drop(columns=["counterpart"])
    return pd.DataFrame(blind), key


def write_all(con) -> dict[str, pd.DataFrame]:
    OUT.mkdir(parents=True, exist_ok=True)
    tabs = build_a1(con)
    tabs["a2_lag"] = build_a2(con)
    tabs["a2_lag_buckets"] = build_a2_buckets(con)
    tabs["a2_snapshot_order"] = build_a2_snapshot_order(con)
    tabs["a3_not_realized"] = build_a3(con)
    tabs.update(build_a4_with_sensitivity(con))
    tabs["a5_compare_by_year"] = build_a5(con)
    for name, df in tabs.items():
        _dump(df, OUT / f"{name}.csv")
    blind, key = build_holdout(con)
    target = VAL / BLIND_NAME
    if target.exists():                        # 라벨을 채운 파일은 덮어쓰지 않는다
        old = pd.read_csv(target, dtype=str).fillna("")
        if old[[c for c in TRUTH_COLS if c in old.columns]].ne("").any().any():
            target = VAL / "holdout_30.new.csv"
            print(f"{BLIND_NAME}에 정답이 있어 {target.name}에 썼다")
    _dump(blind, target)
    _dump(key, VAL / KEY_NAME)
    tabs["holdout"] = blind
    tabs["holdout_key"] = key
    return tabs


def main() -> None:
    con = connect(read_only=True)
    tabs = write_all(con)
    print(f"A1 유형 {len(tabs['a1_by_type'])}, A4 대상 묶음 {len(tabs['a4_targets'])}, holdout {len(tabs['holdout'])}행")
    con.close()


if __name__ == "__main__":
    main()
