"""P1 S4: 분석 A (docs/PLAN_P1.md §5 A1~A6).

data/interim/pbd.duckdb의 S2·S3 표에서 outputs/s4/*.csv와 outputs/validation/sample_60.csv를 만든다.
- A1 실현율: 신호 유형 × 연도 × product_role(대조약 / 생동인정 / 모름). 분모 = 전체 − censored − unverifiable − moot.
  실현 = realized + realized_partial (금액·날짜가 다른 실현 포함), 엄격 실현 = realized만. Wilson 95% 구간.
  not_realized는 reason_source(data / data+external / external)별 건수. DELISTING_SCHEDULED는 건수만.
- A2 시차: 발행 기준일 → 신호 날짜(lead_days) → 확인 스냅샷 기준일(confirm_lag_days).
- A3 실현 안 된 신호: 사유별 건수와 사례.
- A4 사용통계 검증: 실현된 가격 예고 묶음(성분4 × 신호 날짜)에서 성분 원외 단가가 신호 날짜의 달과 관측월 중 언제 움직였나.
  대상 선정 단계와 제외 건수(비공개로 빠진 성분 포함)를 먼저 낸다.
- A5 원장 대 스냅샷 차이 방식 대조표 (S3 s3_compare_by_year).
- A6 검증 표본 60건 (층화, 시드 고정, 라벨 비움). 정밀도는 scripts/s4_precision.py.

product_role은 신호가 발행된 달의 값(그 달 대체조제 목록 행, 없으면 오렌지북 공고대조약)이다. 대조약이 항상 오리지널은 아니다.
사용: PYTHONPATH=src python -m pbd.ledger.s4_analysis   (make p1-s4)
"""
from __future__ import annotations

import math

import pandas as pd

from pbd.common import ROOT, connect, load_yaml

OUT = ROOT / "outputs/s4"
VAL = ROOT / "outputs/validation"
PARAMS = load_yaml("params.yaml")
ROLE_KO = {"reference": "대조약", "bioequiv": "생동인정", None: "모름"}
LABEL_COLS = ("labeler", "label_source", "claude_label_type_correct", "claude_label_date_correct",
              "label_type_correct", "label_date_correct", "label_note")
MUST_INCLUDE = ["654700150"]                     # DECISIONS 2026-11-14 7차: S4 표본에 반드시 포함


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
    return con.execute("""select o.signal_id, o.signal_type, o.source_rule, substr(o.issued_month, 1, 4) as year, o.outcome,
               o.reason, o.reason_source, p.product_role role,
               case when p.product_role is null then null when p.in_substitute_list then 'substitute' else 'orangebook' end role_source
        from signal_outcome o left join product_state_month p on p.product_code = o.product_code and p.list_month = o.issued_month
        order by o.signal_id""").df()


def a1_table(base: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    for k, g in base.groupby(keys, dropna=False, sort=True):
        k = k if isinstance(k, tuple) else (k,)
        cnt = g["outcome"].value_counts()
        n = len(g)
        re, rp = int(cnt.get("realized", 0)), int(cnt.get("realized_partial", 0))
        cen, unv, moot = int(cnt.get("censored", 0)), int(cnt.get("unverifiable", 0)), int(cnt.get("moot", 0))
        nr = int(cnt.get("not_realized", 0))
        denom = n - cen - unv - moot
        lo, hi = wilson(re + rp, denom)
        slo, shi = wilson(re, denom)
        src = g.loc[g["outcome"] == "not_realized", "reason_source"].value_counts()
        rec = dict(zip(keys, [("모름" if (c == "role" and (v is None or (isinstance(v, float) and math.isnan(v)))) else
                               ROLE_KO.get(v, v) if c == "role" else v) for c, v in zip(keys, k)]))
        rec.update(n=n, realized=re, realized_partial=rp, not_realized=nr, moot=moot, unverifiable=unv, censored=cen,
                   denominator=denom,
                   rate=None if denom == 0 else round((re + rp) / denom, 4),
                   rate_lo=None if lo is None else round(lo, 4), rate_hi=None if hi is None else round(hi, 4),
                   rate_strict=None if denom == 0 else round(re / denom, 4),
                   strict_lo=None if slo is None else round(slo, 4), strict_hi=None if shi is None else round(shi, 4),
                   nr_data=int(src.get("data", 0)), nr_data_external=int(src.get("data+external", 0)),
                   nr_external=int(src.get("external", 0)))
        if rec.get("signal_type") == "DELISTING_SCHEDULED":           # PLAN §5 A1: 건수로만
            for c in ("rate", "rate_lo", "rate_hi", "rate_strict", "strict_lo", "strict_hi"):
                rec[c] = None
        rows.append(rec)
    return pd.DataFrame(rows)


def build_a1(con) -> dict[str, pd.DataFrame]:
    base = a1_base(con)
    out = {
        "a1_by_type": a1_table(base, ["signal_type"]),
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
# A2 (사용자 결정 2026-11-15): 확인 스냅샷이 신호 날짜보다 앞선 경우(confirm_lag_days < 0, 예고 금액이 신호 날짜가 속한 달
# 파일에 이미 실림)는 "같은 달 반영"으로 따로 센다. 일수 분포는 같은 달 반영 건을 빼고 낸다.
SAME_MONTH = "confirm_lag_days < 0"


def build_a2(con) -> pd.DataFrame:
    return con.execute(f"""select signal_type, count(*) filter (where not ({SAME_MONTH})) n,
               count(*) filter (where {SAME_MONTH}) same_month_reflected,
               min(lead_days) filter (where not ({SAME_MONTH})) lead_min,
               quantile_cont(lead_days, 0.1) filter (where not ({SAME_MONTH})) lead_p10,
               median(lead_days) filter (where not ({SAME_MONTH})) lead_median,
               quantile_cont(lead_days, 0.9) filter (where not ({SAME_MONTH})) lead_p90,
               max(lead_days) filter (where not ({SAME_MONTH})) lead_max,
               min(confirm_lag_days) filter (where not ({SAME_MONTH})) confirm_min,
               median(confirm_lag_days) filter (where not ({SAME_MONTH})) confirm_median,
               max(confirm_lag_days) filter (where not ({SAME_MONTH})) confirm_max,
               count(*) filter (where not ({SAME_MONTH}) and check_month = (select c.next_month from s3_cal c where c.list_month = o.issued_month)) checked_next_snapshot
        from signal_outcome o where outcome in ('realized', 'realized_partial') group by 1 order by 1""").df()


def build_a2_buckets(con) -> pd.DataFrame:
    """일수 구간 (같은 달 반영 건 제외)"""
    return con.execute(f"""select signal_type, case when lead_days <= 7 then '0-7' when lead_days <= 14 then '8-14' when lead_days <= 31 then '15-31'
                    when lead_days <= 62 then '32-62' else '63+' end lead_bucket,
               case when confirm_lag_days <= 7 then '0-7' when confirm_lag_days <= 31 then '8-31' else '32+' end confirm_bucket, count(*) n
        from signal_outcome where outcome in ('realized', 'realized_partial') and not ({SAME_MONTH}) group by all order by 1, 2, 3""").df()


def build_a2_snapshot_order(con) -> pd.DataFrame:
    """확인 스냅샷 순서: 같은 달 / 다음 스냅샷 / 2번째 / 3번째 … (발행 스냅샷 기준)"""
    df = con.execute(f"""select o.signal_type, ({SAME_MONTH}) same_month, ci.mi - ii.mi k, count(*) n
        from signal_outcome o join s3_cal ii on ii.list_month = o.issued_month join s3_cal ci on ci.list_month = o.check_month
        where o.outcome in ('realized', 'realized_partial') group by all""").df()
    df["confirm_snapshot"] = [("같은 달" if sm else "다음 스냅샷" if k == 1 else f"{k}번째") for sm, k in zip(df["same_month"], df["k"])]
    df["sort_key"] = [-1 if sm else k for sm, k in zip(df["same_month"], df["k"])]
    df = df.groupby(["signal_type", "confirm_snapshot", "sort_key"], as_index=False)["n"].sum()
    return df.sort_values(["signal_type", "sort_key"]).reset_index(drop=True)[["signal_type", "confirm_snapshot", "n"]]


def build_a3(con) -> pd.DataFrame:
    return con.execute("""select o.signal_type, o.source_rule, o.outcome, coalesce(o.reason, '-') reason, coalesce(o.reason_source, '-') reason_source,
               count(*) n, min(o.issued_month) first_issued, max(o.issued_month) last_issued,
               array_to_string(list(o.product_code || ' ' || o.issued_month || ' ' || l.raw order by o.issued_month, o.product_code)[1:3], ' ; ') examples,
               count(o.realized_after_followup) later_reached_notice_amount
        from signal_outcome o join signal_ledger l using (signal_id)
        where o.outcome in ('not_realized', 'moot') group by all order by 1, 2, 3, 4, 5""").df()


# ---------------------------------------------------------------- A4
A4_STATES = ("reimbursed",)                     # 사용자 결정 2026-11-15: 산정불가는 상한금액이 없어 성분 단가에 기여하지 않는다
A4_STATES_SENS = ("reimbursed", "unpriced")      # 민감도: 이전 기준 (산정불가 포함)


def build_a4(con, states: tuple[str, ...] = A4_STATES) -> dict[str, pd.DataFrame]:
    """states: "절반 이상"의 분모에 넣는 발행 달 state_asof 값"""
    st = ", ".join(f"'{x}'" for x in states)
    con.execute("""create or replace temp table _up as
        select month, c4, sum(claim_amt) amt, sum(claim_qty) qty, sum(claim_amt) / nullif(sum(claim_qty), 0) up
        from stg_usage where in_out = 'OUT' group by 1, 2""")
    con.execute(f"""create or replace temp table _a4 as
        with s as (select o.signal_id, o.product_code, l.market_c4 c4, l.signal_date, o.check_month, o.issued_month,
                          (select q.price_listed from product_state_month q where q.product_code = o.product_code and q.list_month = o.issued_month) p0,
                          (select q.price_listed from product_state_month q where q.product_code = o.product_code and q.list_month = o.check_month) p1
                   from signal_outcome o join signal_ledger l using (signal_id)
                   where o.signal_type = 'PRICE_PENDING' and o.outcome in ('realized', 'realized_partial')),
             g as (select c4, signal_date, check_month, min(issued_month) issued_month, count(*) n_sig,
                          avg(p1 / p0 - 1) list_chg from s group by 1, 2, 3),
             np as (select c4, list_month, count(*) n_prod from product_state_month
                    where state_asof in ({st}) group by 1, 2)
        select g.*, strftime(g.signal_date, '%Y-%m') d_month, np.n_prod, g.n_sig * 1.0 / np.n_prod share,
               (select prev_month from s3_cal c where c.list_month = strftime(g.signal_date, '%Y-%m')) d_prev
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
    """주 기준(reimbursed)과 민감도(reimbursed + unpriced)를 함께 내고, 분모·대상 여부가 다른 묶음을 비교한다."""
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


# ---------------------------------------------------------------- A6
def build_sample(con, n_total: int = 60, seed: int | None = None, frozen: pd.DataFrame | None = None) -> pd.DataFrame:
    """층화 표본. 층 = (record_type, 유형, kind 또는 outcome, date_source). 층마다 1건, 남는 수는 층 크기에 비례.
    층 안에서는 연도를 돌아가며 고르고, 같은 연도 안의 순서는 md5(seed || id)로 고정한다.
    frozen(sample_id, record_id): 라벨을 단 표본을 고정한다. 같은 기록·같은 sample_id로 설명 칸·층·층 크기만 지금 원장으로 다시 채운다
    (원장 규칙이 바뀌어 층 배분이 달라져도 라벨 단 기록을 바꾸지 않는다, DECISIONS 2026-11-15)."""
    seed = PARAMS["seed"] if seed is None else seed
    con.execute("""create or replace temp table _cand as
        select 'event' record_type, event_id record_id, product_code, event_type || '|' || kind || '|' || date_source stratum,
               observed_month as month, substr(observed_month, 1, 4) as year from event_ledger
        union all
        select 'signal', signal_id, product_code, signal_type || '|' || outcome || '|' || coalesce(reason, '-'),
               issued_month, substr(issued_month, 1, 4) from signal_outcome""")
    cand = con.execute(f"""select *, md5('{seed}' || record_id) h from _cand order by stratum, h""").df()
    sizes = cand.groupby("stratum").size().sort_values(ascending=False)
    must = cand[cand["product_code"].isin(MUST_INCLUDE) & (cand["record_type"] == "event")]
    alloc = {s: 1 for s in sizes.index}
    rest = n_total - len(alloc) - len(must)
    if rest < 0:                                    # 층이 너무 많으면 큰 층부터 1건씩
        alloc = {s: 1 for s in sizes.index[: n_total - len(must)]}
        rest = 0
    total = sizes[list(alloc)].sum()
    extra = {s: int(rest * sizes[s] / total) for s in alloc}
    for s in sizes.index:                           # 반올림으로 남은 수를 큰 층부터
        if sum(extra.values()) >= rest:
            break
        if s in alloc:
            extra[s] += 1
    picked = list(must.itertuples(index=False))
    taken = {r.record_id for r in picked}
    all_years = sorted(cand["year"].unique())
    for si, (s, k) in enumerate(alloc.items()):
        k += extra.get(s, 0)
        g = cand[(cand["stratum"] == s) & ~cand["record_id"].isin(taken)]
        # 연도 분산: 층마다 시작 연도를 돌려 가며 연도를 번갈아 고른다
        years = sorted(g["year"].unique())
        start = all_years[si % len(all_years)]
        years = [y for y in years if y >= start] + [y for y in years if y < start]
        by_year = {y: list(g[g["year"] == y].itertuples(index=False)) for y in years}
        while k > 0 and any(by_year.values()):
            for y in years:
                if k > 0 and by_year[y]:
                    r = by_year[y].pop(0)
                    picked.append(r)
                    taken.add(r.record_id)
                    k -= 1
    df = pd.DataFrame(picked)[["record_type", "record_id", "product_code", "stratum", "month", "year"]]
    if frozen is not None:
        df = cand[cand["record_id"].isin(frozen["record_id"])][["record_type", "record_id", "product_code", "stratum", "month", "year"]]
        missing = set(frozen["record_id"]) - set(df["record_id"])
        assert not missing, f"고정 표본의 기록이 원장에 없다: {sorted(missing)}"
    con.register("_pick", df)
    out = con.execute("""
        with p as (select * from _pick),
             d as (select p.*, e.event_type, e.kind, e.effective_date, e.effective_date_lo, e.effective_date_hi, e.date_source, e.evidence,
                          o.signal_type, o.outcome, o.reason, o.check_month, l.signal_date, l.raw signal_raw
                   from p left join event_ledger e on p.record_type = 'event' and e.event_id = p.record_id
                   left join signal_outcome o on p.record_type = 'signal' and o.signal_id = p.record_id
                   left join signal_ledger l on l.signal_id = o.signal_id),
             m as (select d.*, c.prev_month,
                          case when d.record_type = 'signal' then coalesce(d.check_month, d.month) else d.month end look_month
                   from d join s3_cal c on c.list_month = d.month)
        select row_number() over (order by m.stratum, m.record_id) sample_id, m.record_type, m.record_id, m.stratum,
               m.product_code, (select min(product_name) from stg_benefit b where b.product_code = m.product_code and b.list_month = m.month) product_name,
               m.prev_month, m.month, m.look_month,
               case when m.record_type = 'event' then m.event_type || ' ' || m.kind || ' 유효일 ' || coalesce(cast(m.effective_date as varchar),
                         cast(m.effective_date_lo as varchar) || '~' || cast(m.effective_date_hi as varchar)) || ' (' || m.date_source || ')'
                    else m.signal_type || ' ' || cast(m.signal_date as varchar) || ' → ' || m.outcome || coalesce(' / ' || m.reason, '') end ledger_claim,
               coalesce(m.signal_raw, '') signal_note_raw,
               -- 구간으로 기록된 행(삭제)의 구간 길이(일). 라벨 기준: 실제 삭제일이 구간 안이면 Y (LABELING.md)
               case when m.effective_date_lo is not null then datediff('day', m.effective_date_lo, m.effective_date_hi) + 1 end interval_days,
               (select count(*) from _cand c where c.stratum = m.stratum) stratum_size,
               (select note from stg_benefit b where b.product_code = m.product_code and b.list_month = m.prev_month and b.row_kind = 'product_candidate') benefit_note_prev,
               (select note from stg_benefit b where b.product_code = m.product_code and b.list_month = m.month and b.row_kind = 'product_candidate') benefit_note_month,
               (select note from stg_benefit b where b.product_code = m.product_code and b.list_month = m.look_month and b.row_kind = 'product_candidate') benefit_note_look,
               (select note from stg_substitute s where s.product_code = m.product_code and s.list_month = m.month) substitute_note_month,
               (select price from stg_benefit b where b.product_code = m.product_code and b.list_month = m.prev_month and b.row_kind = 'product_candidate') price_prev,
               (select price from stg_benefit b where b.product_code = m.product_code and b.list_month = m.month and b.row_kind = 'product_candidate') price_month,
               (select price from stg_benefit b where b.product_code = m.product_code and b.list_month = m.look_month and b.row_kind = 'product_candidate') price_look,
               coalesce(m.evidence, '') ledger_evidence,
               -- 원자료에서 찾기 쉽게: 확인 달까지 3개월의 비고 원문(B 급여목록, S 대체조제)과 상한금액
               (select string_agg(c2.list_month || ' B:' || coalesce((select b.note from stg_benefit b where b.product_code = m.product_code
                         and b.list_month = c2.list_month and b.row_kind = 'product_candidate'), '-')
                       || ' S:' || coalesce((select s.note from stg_substitute s where s.product_code = m.product_code
                         and s.list_month = c2.list_month), '-'), ' | ' order by c2.list_month)
                from s3_cal c2 where c2.mi between (select mi from s3_cal where list_month = m.look_month) - 2
                                                  and (select mi from s3_cal where list_month = m.look_month)) notes_3m,
               (select string_agg(c2.list_month || ':' || coalesce(cast((select b.price from stg_benefit b where b.product_code = m.product_code
                         and b.list_month = c2.list_month and b.row_kind = 'product_candidate') as varchar), '없음'), ' | ' order by c2.list_month)
                from s3_cal c2 where c2.mi between (select mi from s3_cal where list_month = m.look_month) - 2
                                                  and (select mi from s3_cal where list_month = m.look_month)) prices_3m,
               -- 라벨 칸 (LABELING.md): labeler = claude(1차) / user_reviewed(사용자 검토). claude_label_* = 1차 라벨 보존용
               '' labeler, '' label_source, '' claude_label_type_correct, '' claude_label_date_correct,
               '' label_type_correct, '' label_date_correct, '' label_note
        from m order by sample_id""").df()
    con.unregister("_pick")
    if frozen is not None:
        sid = dict(zip(frozen["record_id"], frozen["sample_id"].astype(int)))
        out["sample_id"] = out["record_id"].map(sid)
        out = out.sort_values("sample_id").reset_index(drop=True)
    return out


# 고정 표본에 더한 기록 (DECISIONS 2026-11-15): 층, 고른 규칙 = 층 안에서 이미 뽑힌 기록을 빼고 md5(seed || record_id)가 가장 작은 것
EXTRA_DRAWS = [{"sample_id": 61, "stratum": "SUSPEND_SCHEDULED|not_realized|stay"}]


def extra_draw(con, stratum: str, exclude: set[str], seed: int | None = None) -> str:
    """층에서 고정 시드로 1건. build_sample의 층 안 순서(md5(seed || id))와 같은 규칙"""
    seed = PARAMS["seed"] if seed is None else seed
    con.execute("""create or replace temp table _cand2 as
        select event_id record_id, event_type || '|' || kind || '|' || date_source stratum from event_ledger
        union all select signal_id, signal_type || '|' || outcome || '|' || coalesce(reason, '-') from signal_outcome""")
    ids = [r[0] for r in con.execute(f"select record_id from _cand2 where stratum = ? order by md5('{seed}' || record_id)", [stratum]).fetchall()]
    ids = [i for i in ids if i not in exclude]
    assert ids, f"층 {stratum}에 뽑을 기록이 없다"
    return ids[0]


def write_all(con) -> dict[str, pd.DataFrame]:
    OUT.mkdir(parents=True, exist_ok=True)
    tabs = build_a1(con)
    tabs["a2_lag"] = build_a2(con)
    tabs["a2_lag_buckets"] = build_a2_buckets(con)
    tabs["a2_snapshot_order"] = build_a2_snapshot_order(con)
    tabs["a3_not_realized"] = build_a3(con)
    tabs.update(build_a4_with_sensitivity(con))
    tabs["a5_compare_by_year"] = con.execute("select * exclude (n_check) from s3_compare_by_year order by year, event_type").df()
    for name, df in tabs.items():
        _dump(df, OUT / f"{name}.csv")
    target = VAL / "sample_60.csv"
    sample = None
    if target.exists():                             # 라벨을 채운 파일은 덮어쓰지 않는다
        old = pd.read_csv(target, dtype=str)
        labs = [c for c in LABEL_COLS if c in old.columns]
        if labs and old[labs].notna().any().any():
            # 같은 기록으로 고정해 다시 채우고 라벨을 옮겨 붙인다. 원장 주장이 바뀐 행은 claim_changed (다시 판정)
            fr = old[["sample_id", "record_id"]].copy()
            for x in EXTRA_DRAWS:                    # 고정 표본에 더하는 기록 (이미 있으면 그대로)
                if str(x["sample_id"]) not in set(fr["sample_id"]):
                    rid = extra_draw(con, x["stratum"], set(fr["record_id"]))
                    fr = pd.concat([fr, pd.DataFrame([{"sample_id": str(x["sample_id"]), "record_id": rid}])], ignore_index=True)
            sample = build_sample(con, frozen=fr)
            keep = old.set_index("record_id")
            for c in LABEL_COLS:
                if c in keep.columns:
                    sample[c] = sample["record_id"].map(keep[c]).fillna("")
            sample["claim_changed"] = sample["record_id"].map(keep["ledger_claim"]).fillna("(새 기록)") != sample["ledger_claim"]
            target = VAL / "sample_60.new.csv"
            print(f"sample_60.csv에 라벨이 있어 같은 기록으로 다시 채워 {target.name}에 썼다 (원장 주장이 바뀐 행 {int(sample['claim_changed'].sum())})")
    if sample is None:
        sample = build_sample(con)
    _dump(sample, target)
    tabs["sample_60"] = sample
    return tabs


def main() -> None:
    con = connect(read_only=False)
    tabs = write_all(con)
    print(f"A1 유형 {len(tabs['a1_by_type'])}, A4 대상 묶음 {len(tabs['a4_targets'])}, 표본 {len(tabs['sample_60'])}행")
    con.close()


if __name__ == "__main__":
    main()
