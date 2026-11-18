"""급여목록·대체조제 목록 제품의 상태전이 전수조사 (2026-11-17, 판정 로직 점검용).

읽기 전용: data/interim/pbd.duckdb (S1~S3 결과). 파이프라인 테이블을 고치지 않는다.
출력: outputs/state_census/*.csv, 화면에 요약.
실행: python -I scripts/state_transition_census.py

축 (MECE)
- 행(제품×월): 목록 존재(B+S / B / S) × 급여 상태(state_asof, 우선순위 scheduled_listing > suspended > unpriced > reimbursed)
  × 비고 토큰 조합(두 목록 합침, 미래 날짜 여부 구분)
- 전이(t → t+1, 48개월 연속, 누락 월 0): 상태쌍(absent·S_only 포함) × 상한금액 변화(same/down/up/null/n.a.)
- 신호: 신호 유형 × 발행 달 상태(+stay) × 확인 달 상태(+stay) × outcome·reason
"""
from __future__ import annotations

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/state_census"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute(f"attach '{ROOT / 'data/interim/pbd.duckdb'}' as src (read_only)")
    c.execute("create table cal as select list_month, row_number() over (order by list_month) mi "
              "from (select distinct list_month from src.stg_benefit)")
    c.execute("""create table tk as
     select product_code, list_month,
      bool_or(token_type='R1' and is_future) r1f, bool_or(token_type='R1' and not is_future) r1p,
      bool_or(token_type='R2') r2,
      bool_or(token_type='R3' and not is_future) r3p, bool_or(token_type='R3' and is_future) r3f,
      bool_or(token_type='R3b' and not is_future) r3bs_p, bool_or(token_type='R3b' and is_future) r3bs_f,
      bool_or(token_type='R4' and not is_future) r4p, bool_or(token_type='R4' and is_future) r4f,
      bool_or(token_type='R5') r5, bool_or(token_type='R6') r6, bool_or(token_type='R7') r7, bool_or(token_type='R9') r9,
      bool_or(token_type='unresolved') unres,
      bool_or(token_type in ('R3','R3b') and source_list='benefit') susp_in_b,
      bool_or(token_type in ('R3','R3b') and source_list='substitute') susp_in_s
     from src.note_token group by 1, 2""")
    c.execute("""create table u as
     with keys as (select distinct product_code, list_month from src.stg_benefit where row_kind='product_candidate'
                   union select distinct product_code, list_month from src.stg_substitute where row_kind='product_candidate'),
          s as (select distinct product_code, list_month from src.stg_substitute)
     select k.product_code, k.list_month, cal.mi, p.product_code is not null in_b, s.product_code is not null in_s,
            p.state_asof, p.state_eom, p.price_listed, coalesce(p.stay_flag, false) stay_flag,
            t.* exclude (product_code, list_month)
     from keys k join cal using (list_month)
     left join src.product_state_month p using (product_code, list_month)
     left join s using (product_code, list_month)
     left join tk t using (product_code, list_month)""")
    flags = ["r1f", "r1p", "r2", "r3p", "r3f", "r3bs_p", "r3bs_f", "r4p", "r4f", "r5", "r6", "r7", "r9", "unres", "susp_in_b", "susp_in_s"]
    c.execute("update u set " + ", ".join(f"{f} = coalesce({f}, false)" for f in flags))
    lab = dict(r1f="R1f", r1p="R1", r2="R2f", r3p="R3", r3f="R3f", r3bs_p="R3b", r3bs_f="R3bf", r4p="R4", r4f="R4f",
               r5="R5", r6="R6", r7="R7", r9="R9f", unres="UNRES")
    sig = "concat_ws('+', " + ", ".join(f"case when {k} then '{v}' end" for k, v in lab.items()) + ")"

    def dump(name: str, sql: str):
        df = c.execute(sql).df()
        df.to_csv(OUT / f"{name}.csv", index=False)
        print(f"\n== {name} ({len(df)}행)")
        print(df.head(40).to_string(index=False))
        return df

    # 1. 행 단위: 목록 존재 × 상태 × 비고 조합
    dump("row_state_note_combo", f"""
        select case when in_b and in_s then 'B+S' when in_b then 'B' else 'S' end presence,
               coalesce(state_asof, '-') state_asof, coalesce(nullif({sig}, ''), '(없음)') note_combo,
               stay_flag, count(*) n_rows, count(distinct product_code) n_products, min(list_month) first_month, max(list_month) last_month
        from u group by all order by state_asof, presence, n_rows desc""")

    # 2. 전이: 상태쌍 × 가격 변화 (asof·eom 각각)
    c.execute("""create table tr as
     with p as (select distinct product_code from u),
     g as (select p.product_code, a.list_month m0, a.mi from p cross join cal a where a.mi < (select max(mi) from cal)),
     x as (select g.product_code, g.m0, b.list_month m1,
       coalesce(u0.state_asof, case when u0.in_s then 'S_only' end, 'absent') a0,
       coalesce(u1.state_asof, case when u1.in_s then 'S_only' end, 'absent') a1,
       coalesce(u0.state_eom, case when u0.in_s then 'S_only' end, 'absent') e0,
       coalesce(u1.state_eom, case when u1.in_s then 'S_only' end, 'absent') e1,
       u0.price_listed p0, u1.price_listed p1, coalesce(u0.stay_flag, false) stay0, coalesce(u1.stay_flag, false) stay1,
       coalesce(u0.r2, false) r2_0
      from g join cal b on b.mi = g.mi + 1
      left join u u0 on u0.product_code = g.product_code and u0.list_month = g.m0
      left join u u1 on u1.product_code = g.product_code and u1.list_month = b.list_month)
     select *, case when a0 in ('absent','S_only') or a1 in ('absent','S_only') then 'n/a'
                    when p0 is null or p1 is null then 'null' when p1 < p0 then 'down' when p1 > p0 then 'up' else 'same' end dp
     from x where not (a0 = 'absent' and a1 = 'absent')""")
    for k, nm in (("e", "eom"), ("a", "asof")):
        dump(f"transition_{nm}_x_price", f"""
            select {k}0 state_from, {k}1 state_to,
                   count(*) filter (where dp='same') same, count(*) filter (where dp='down') down, count(*) filter (where dp='up') up,
                   count(*) filter (where dp='null') price_null, count(*) filter (where dp='n/a') na, count(*) total
            from tr group by all order by 1, 2""")

    # 3. 정지(월말) 중 상한금액 변화: 이벤트 종류·신호 결과
    c.execute("""create table sp as
     select t.*, e.kind ev_kind, e.signal_id, o.outcome sig_outcome
     from tr t left join src.event_ledger e on e.product_code = t.product_code and e.observed_month = t.m1 and e.event_type = 'PRICE_CHANGE'
     left join src.signal_outcome o on o.signal_id = e.signal_id
     where t.e0 = 'suspended' and t.dp in ('down', 'up')""")
    dump("price_change_during_suspension", """
        select e0 || '→' || e1 transition, m1 observed_month, dp, r2_0 price_note_prev_month, stay0, stay1,
               coalesce(ev_kind, '(없음)') event_kind, coalesce(sig_outcome, '-') signal_outcome,
               count(*) n, count(distinct product_code) n_products
        from sp group by all order by observed_month""")
    dump("price_change_during_suspension_detail", """
        select sp.product_code, sp.m0, sp.m1, sp.p0, sp.p1, sp.ev_kind, sp.sig_outcome, b0.firm, b0.note note_m0, b1.note note_m1
        from sp join src.stg_benefit b0 on b0.product_code = sp.product_code and b0.list_month = sp.m0
        join src.stg_benefit b1 on b1.product_code = sp.product_code and b1.list_month = sp.m1 order by sp.m1, sp.product_code""")
    dump("same_month_all_price_cuts", """
        select m1 observed_month, count(*) filter (where dp = 'down') n_down_all,
               count(*) filter (where dp = 'down' and e0 = 'suspended') n_down_suspended
        from tr where m1 in (select distinct m1 from sp) group by 1 order by 1""")
    dump("suspension_price_change_fate", """
        with p as (select distinct product_code from sp)
        select case when exists (select 1 from tr where tr.product_code = p.product_code and e0 = 'suspended' and e1 = 'reimbursed') then 'resumed'
                    when exists (select 1 from tr where tr.product_code = p.product_code and e0 = 'suspended' and e1 = 'absent') then 'delisted_while_suspended'
                    else 'suspended_to_end' end fate, count(*) n_products from p group by 1 order by 2 desc""")

    # 4. 가격 예고와 정지가 한 제품·한 달에 겹친 행
    dump("price_note_with_suspension", """
        select u.product_code, u.list_month, u.state_asof, u.state_eom, u.stay_flag, b.firm, b.note note_benefit, s.note note_substitute,
               t.token_date price_date, o.outcome price_outcome, o.reason price_reason, o.check_month
        from u join src.stg_benefit b using (product_code, list_month)
        left join src.stg_substitute s using (product_code, list_month)
        join (select distinct product_code, list_month, token_date from src.note_token where token_type = 'R2') t
             on t.product_code = u.product_code and t.list_month = u.list_month
        left join src.signal_outcome o on o.signal_type = 'PRICE_PENDING' and o.product_code = u.product_code and o.signal_date = t.token_date
        where u.r2 and (u.state_asof = 'suspended' or u.state_eom = 'suspended' or u.r3f or u.r3bs_f)
        order by u.list_month, u.product_code""")

    # 5. 정지와 집행정지가 한 칸에 겹친 제품: 집행정지일 < 정지일 이면 그 정지를 멈춘 집행정지가 아님 (날짜상)
    dump("suspension_with_stay", """
        with a as (select product_code, list_month, token_type, token_date from src.note_token where token_type in ('R3','R3b','R4'))
        select s.product_code, min(s.list_month) first_month, max(s.list_month) last_month, max(s.token_date) suspend_date,
               string_agg(distinct cast(r.token_date as varchar), ',') stay_dates, bool_and(r.token_date < s.token_date) all_stay_before_suspend
        from a s join a r on r.product_code = s.product_code and r.list_month = s.list_month and r.token_type = 'R4'
        join u on u.product_code = s.product_code and u.list_month = s.list_month and u.state_asof = 'suspended'
        where s.token_type in ('R3','R3b') group by 1 order by 2, 1""")

    # 6. 신호 × 발행 달 상태 × 확인 달 상태 × 결과
    dump("signal_state_outcome", """
        select o.signal_type, coalesce(pi.state_eom, 'absent') || case when pi.stay_flag then '+stay' else '' end at_issue,
               coalesce(pc.state_eom, 'absent') || case when pc.stay_flag then '+stay' else '' end at_check,
               o.outcome, coalesce(o.reason, '-') reason, count(*) n
        from src.signal_outcome o join src.signal_ledger l using (signal_id)
        left join src.product_state_month pi on pi.product_code = l.product_code and pi.list_month = l.issued_month
        left join src.product_state_month pc on pc.product_code = l.product_code and pc.list_month = o.check_month
        group by all order by 1, 2, 3, n desc""")

    # 7. 급여→급여 가격 변화의 이벤트 종류
    dump("reimbursed_price_change_kind", """
        select t.dp, case when t.stay0 and t.stay1 then 'stay_kept' when t.stay0 then 'stay_released' when t.stay1 then 'stay_started' else '-' end stay,
               coalesce(e.kind, '(없음)') event_kind, count(*) n
        from tr t left join src.event_ledger e on e.product_code = t.product_code and e.observed_month = t.m1 and e.event_type = 'PRICE_CHANGE'
        where t.e0 = 'reimbursed' and t.e1 = 'reimbursed' and t.dp in ('down', 'up') group by all order by 1, 2, n desc""")

    # 8. 상태 경계 이벤트의 종류 (진입·퇴출·정지·복귀)
    dump("boundary_event_kind", """
        select 'SUSPEND/RESUME' grp, event_type, kind, date_source, count(*) n from src.event_ledger
         where event_type in ('SUSPEND', 'RESUME') group by all
        union all select 'ENTRY/EXIT', side, kind, null, count(*) from src.s3_entry_exit group by all
        order by 1, 2, n desc""")

    # 9. 기타 칸
    dump("misc_checks", """
        select 'stay_flag of R4 future-dated rows' item, cast(count(*) filter (where stay_flag) as varchar) || ' / ' || count(*) v from u where r4f
        union all select 'suspended rows without suspension note', cast(count(*) as varchar) from u
          where state_asof = 'suspended' and not (r3p or r3bs_p or r3f or r3bs_f)
        union all select 'suspended rows, note only in substitute list', cast(count(*) as varchar) from u where susp_in_s and not susp_in_b and state_asof = 'suspended'
        union all select 'unresolved tokens', cast(count(*) as varchar) from src.note_token where token_type = 'unresolved'
        union all select 'S_only rows', cast(count(*) as varchar) || ' (' || min(list_month) || '~' || max(list_month) || ')' from u where not in_b
        union all select 'missing months', cast(count(*) as varchar) from src.snapshot_inventory where missing
        union all select 'left-censored 2022-01 suspended', cast(count(*) as varchar) from u where list_month = '2022-01' and state_asof = 'suspended'""")


if __name__ == "__main__":
    main()
