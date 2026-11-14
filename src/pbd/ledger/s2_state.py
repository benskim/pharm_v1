"""P1 S2: 상태·속성·연결 (docs/PLAN_P1.md §4, 완료 기준 §6 S2).

data/interim/pbd.duckdb의 L1 테이블에서 다음을 만든다.
- product_state_month : 급여목록 제품×월 (row_kind = product_candidate 행만). state_asof(기준일 시점)·state_eom(월말),
                        exposure_frac(월말 기준), 고쳐 쓰기 표시,
                        stay_flag, price_pending, 대체조제 목록 여부, product_role, 출처별 원본 값
- product_attr_scd2   : 업체명·제품명·주성분코드의 유효 구간 (급여목록 관측 달 기준)
- bridge_product_item : 제품코드 → 품목기준코드 (경로 code_map / substitute)
- bridge_code_recode  : 목록정비전코드(옛 코드) → 제품코드(새 코드)

- 날짜 규칙(등재 예정·급여정지·복귀)은 pbd.ledger.state_rules.derive (순수 함수)
- state_asof는 그 스냅샷까지의 정보만, state_eom은 이후 스냅샷으로 확인된 것까지 쓴다 (state_rules).
- state 우선순위 (두 컬럼 모두): scheduled_listing > suspended > unpriced > reimbursed. delisted는 행으로 만들지 않는다
  (행 = 관측된 스냅샷 행. 삭제는 absent_next_month로 표시하고 이벤트는 S3)
- 버린 행: non_product 행 → outputs/audit/dropped_s2_non_product.csv.
  대체조제 목록에만 있고 같은 달 급여목록에 없는 행 → outputs/audit/s2_substitute_only_rows.csv

사용: PYTHONPATH=src python -m pbd.ledger.s2_state   (make p1-s2)
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from pbd.common import AUDIT, ROOT, connect, load_yaml
from pbd.ledger.s1_load import write_csv
from pbd.ledger.state_rules import Tok, derive

OUT = ROOT / "outputs/s2"


# ---------------------------------------------------------------- 날짜 규칙 (Python)
def build_state_override(con) -> None:
    """등재 예정·급여정지 토큰이 있는 제품만 state_rules.derive로 판정해 s2_state_override에 둔다."""
    cal = dict(con.execute("select list_month, min(base_date) from stg_benefit group by 1").fetchall())
    last_month = max(cal)
    tok_rows = con.execute("""
        select product_code, list_month, token_type, token_date, token_date_end from note_token
        where token_type in ('R1', 'R3', 'R3b', 'R6') and token_date is not null
        order by 1, 2""").fetchall()
    toks: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for pc, m, typ, d, d2 in tok_rows:
        toks[pc][m].add(Tok(typ, d, d2 if typ == "R3b" else None))
    con.execute("create or replace temp table _tp (product_code varchar)")
    con.executemany("insert into _tp values (?)", [(p,) for p in sorted(toks)])
    months_by = defaultdict(list)
    for pc, m in con.execute("""select b.product_code, b.list_month from stg_benefit b join _tp using (product_code)
                                where b.row_kind = 'product_candidate' order by 1, 2""").fetchall():
        months_by[pc].append(m)
    rows = []
    for pc in sorted(months_by):
        res = derive(months_by[pc], cal, toks[pc], last_month)
        for m, o in res.items():
            rows.append((pc, m, o.scheduled, o.suspended, o.exposure_frac, o.exposure_revised, o.state_revised,
                         o.late_note_revised, o.resume_unconfirmed, o.listing_date, o.suspend_date, o.note,
                         o.asof_scheduled, o.asof_suspended, o.asof_suspend_date))
    df = pd.DataFrame(rows, columns=["product_code", "list_month", "scheduled", "suspended", "exposure_frac",
                                     "exposure_revised", "state_revised", "late_note_revised", "resume_unconfirmed",
                                     "listing_date", "suspend_date", "rule_note",
                                     "asof_scheduled", "asof_suspended", "asof_suspend_date"])
    for c in ("listing_date", "suspend_date", "asof_suspend_date"):
        df[c] = pd.to_datetime(df[c])
    con.register("_ov", df)
    con.execute("""create or replace table s2_state_override as
        select product_code, list_month, scheduled, suspended, cast(exposure_frac as double) exposure_frac,
               exposure_revised, state_revised, late_note_revised, resume_unconfirmed, cast(listing_date as date) listing_date,
               cast(suspend_date as date) suspend_date, rule_note, asof_scheduled, asof_suspended,
               cast(asof_suspend_date as date) asof_suspend_date from _ov order by 1, 2""")
    con.unregister("_ov")


# ---------------------------------------------------------------- 연결
def build_bridges(con) -> None:
    con.execute("""create or replace table bridge_product_item as
        with prods as (select distinct product_code from stg_benefit where row_kind = 'product_candidate'
                       union select distinct product_code from stg_substitute where row_kind = 'product_candidate')
        select a.product_code, a.item_code, 'code_map' as path,
               cast(null as varchar) first_month, cast(null as varchar) last_month, cast(null as bigint) n_months
        from attr_2026_product a join prods using (product_code)
        union all
        select product_code, item_code, 'substitute', min(list_month), max(list_month), count(*)
        from stg_substitute where row_kind = 'product_candidate' and item_code is not null group by 1, 2
        order by 1, 3, 2""")
    # 제품 단위 오렌지북 연결: 경로 1(code_map) 품목이 오렌지북에 있으면 경로 1, 아니면 경로 2(②품목기준코드)
    con.execute("""create or replace table s2_product_link as
        with l as (select b.product_code, b.path, b.item_code, i.in_orangebook, i.ob_ref, i.ob_generic
                   from bridge_product_item b left join attr_2026_item i using (item_code)),
             p as (select product_code,
                          bool_or(path = 'code_map') item_code_map, bool_or(path = 'substitute') item_substitute,
                          coalesce(bool_or(in_orangebook) filter (where path = 'code_map'), false) ob_code_map,
                          coalesce(bool_or(in_orangebook) filter (where path = 'substitute'), false) ob_substitute,
                          bool_or(ob_ref) filter (where path = 'code_map' and in_orangebook) ref1,
                          bool_or(ob_generic) filter (where path = 'code_map' and in_orangebook) gen1,
                          bool_or(ob_ref) filter (where path = 'substitute' and in_orangebook) ref2,
                          bool_or(ob_generic) filter (where path = 'substitute' and in_orangebook) gen2,
                          min(item_code) filter (where path = 'code_map') item1,
                          min(item_code) filter (where path = 'substitute') item2
                   from l group by 1)
        select product_code, coalesce(item1, item2) item_code,
               case when item1 is not null then 'code_map' when item2 is not null then 'substitute' end item_link_path,
               item_code_map, item_substitute, ob_code_map, ob_substitute,
               case when ob_code_map then 'code_map' when ob_substitute then 'substitute' end ob_link_path,
               case when ob_code_map then ref1 when ob_substitute then ref2 end ob_ref,
               case when ob_code_map then gen1 when ob_substitute then gen2 end ob_generic
        from p order by 1""")
    con.execute("""create or replace table bridge_code_recode as
        with r as (select prev_code old_code, product_code new_code, 'benefit' src, list_month from stg_benefit
                   where prev_code is not null and row_kind = 'product_candidate' and prev_code <> product_code
                   union all
                   select prev_code, product_code, 'substitute', list_month from stg_substitute
                   where prev_code is not null and row_kind = 'product_candidate' and prev_code <> product_code),
             life as (select product_code, min(list_month) f, max(list_month) l from stg_benefit
                      where row_kind = 'product_candidate' group by 1),
             g as (select old_code, new_code, list_sort(list(distinct src)) source_lists, min(list_month) first_month,
                          max(list_month) last_month, count(distinct list_month) n_months from r group by 1, 2)
        select g.*, lo.product_code is not null old_in_benefit, lo.f old_first_month, lo.l old_last_month,
               count(*) over (partition by old_code) n_new_for_old
        from g left join life lo on lo.product_code = g.old_code order by 1, 2""")


# ---------------------------------------------------------------- 속성 유효 구간
def build_attr_scd2(con) -> None:
    con.execute("""create or replace table product_attr_scd2 as
        with b as (select product_code, list_month, firm, product_name, ingredient_code from stg_benefit
                   where row_kind = 'product_candidate'),
             c as (select *, case when lag(list_month) over w is null
                                    or lag(firm) over w is distinct from firm
                                    or lag(product_name) over w is distinct from product_name
                                    or lag(ingredient_code) over w is distinct from ingredient_code then 1 else 0 end chg
                   from b window w as (partition by product_code order by list_month)),
             g as (select *, sum(chg) over (partition by product_code order by list_month) version from c),
             v as (select product_code, cast(version as int) version, firm, product_name, ingredient_code,
                          min(list_month) valid_from_month, max(list_month) valid_to_month, count(*) n_months
                   from g group by product_code, version, firm, product_name, ingredient_code)
        select v.*, lag(firm) over w is distinct from firm and version > 1 changed_firm,
               lag(product_name) over w is distinct from product_name and version > 1 changed_product_name,
               lag(ingredient_code) over w is distinct from ingredient_code and version > 1 changed_ingredient_code,
               version = max(version) over (partition by product_code) is_last
        from v window w as (partition by product_code order by version) order by product_code, version""")


# ---------------------------------------------------------------- 제품×월
def build_product_state_month(con) -> None:
    con.execute("""create or replace temp table _psm as
        with cal as (select list_month, min(base_date) base_date,
                            lead(list_month) over (order by list_month) next_month from stg_benefit group by 1),
             b as (select * from stg_benefit where row_kind = 'product_candidate'),
             -- 다음 달 급여목록에 같은 제품이 있는가 (list_month = 이번 달)
             bn as (select b.product_code, c.list_month, true has_next from b join cal c on c.next_month = b.list_month),
             s as (select product_code, list_month, ref_drug, bioeq, bioeq_raw, equiv, item_code sub_item_code,
                          group_code, price sub_price from stg_substitute where row_kind = 'product_candidate'),
             tk as (select product_code, list_month,
                           bool_or(token_type = 'R7') has_r7,
                           bool_or(token_type = 'R4') has_r4,
                           min(token_date) filter (where token_type = 'R4') stay_date,
                           coalesce(bool_or(token_type = 'R5' and not is_future), false) r5_past
                    from note_token group by 1, 2),
             pp as (select product_code, list_month,
                           arg_min(amount, case when source_list = 'benefit' then 0 else 1 end) price_pending,
                           arg_min(token_date, case when source_list = 'benefit' then 0 else 1 end) pending_date,
                           arg_min(source_list, case when source_list = 'benefit' then 0 else 1 end) pending_source,
                           count(distinct (token_date, amount)) n_pending_values
                    from note_token where token_type = 'R2' and is_future group by 1, 2)
        select b.product_code, b.list_month, b.base_date, b.c4, b.ingredient_code, b.firm, b.product_name, b.class_code,
               case when ov.asof_scheduled then 'scheduled_listing'
                    when ov.asof_suspended then 'suspended'
                    when coalesce(tk.has_r7, false) or b.price is null then 'unpriced'
                    else 'reimbursed' end state_asof,
               case when ov.scheduled then 'scheduled_listing'
                    when ov.suspended then 'suspended'
                    when coalesce(tk.has_r7, false) or b.price is null then 'unpriced'
                    else 'reimbursed' end state_eom,
               b.price price_listed, pp.price_pending, pp.pending_date, pp.pending_source, pp.n_pending_values,
               coalesce(tk.has_r4, false) and not coalesce(tk.r5_past, false) stay_flag,
               case when coalesce(tk.has_r4, false) and not coalesce(tk.r5_past, false) then tk.stay_date end stay_date,
               coalesce(ov.exposure_frac, 1.0) exposure_frac,
               coalesce(ov.exposure_revised, false) exposure_revised,
               coalesce(ov.state_revised, false) state_revised,
               coalesce(ov.late_note_revised, false) late_note_revised,
               coalesce(ov.resume_unconfirmed, false) resume_unconfirmed,
               ov.listing_date, case when ov.suspended then ov.suspend_date end suspend_date,
               case when ov.asof_suspended then ov.asof_suspend_date end asof_suspend_date,
               case when coalesce(tk.has_r7, false) and b.price is null then 'note+price_null'
                    when coalesce(tk.has_r7, false) then 'note' when b.price is null then 'price_null' end unpriced_evidence,
               case when cal.next_month is not null then bn.has_next is null end absent_next_month,
               s.product_code is not null in_substitute_list,
               -- product_role: 그 달 대체조제 목록 행 → 없으면 오렌지북 공고대조약 (제품×월 판정)
               case when s.product_code is not null then
                         case when s.ref_drug then 'reference' when s.bioeq then 'bioequiv' end
                    when lk.ob_ref then 'reference' end product_role,
               case when s.product_code is not null then
                         case when s.ref_drug then 'reference' when s.bioeq then 'bioequiv' end end sub_role,
               case when s.product_code is not null then s.bioeq end is_bioequiv,
               case when s.product_code is not null and lk.ob_link_path is not null then s.ref_drug <> lk.ob_ref end role_conflict,
               -- 출처별 원본 값 (판정 컬럼과 별도)
               s.ref_drug sub_ref_drug, s.bioeq sub_bioeq, s.bioeq_raw sub_bioeq_raw, s.equiv sub_equiv,
               s.group_code sub_group_code, s.sub_item_code, s.sub_price,
               lk.item_code, lk.item_link_path, lk.ob_link_path, lk.ob_ref, lk.ob_generic,
               b.src_file, b.src_row
        from b join cal using (list_month)
        left join bn using (product_code, list_month)
        left join s using (product_code, list_month)
        left join tk using (product_code, list_month)
        left join pp using (product_code, list_month)
        left join s2_state_override ov using (product_code, list_month)
        left join s2_product_link lk using (product_code)""")
    # product_role_carried: 그 달 대체조제 값 → 없으면 과거 달의 대체조제 값(앞으로만 이어 씀) → 오렌지북 공고대조약
    con.execute("""create or replace table product_state_month as
        with c as (select *, last_value(sub_role ignore nulls) over (partition by product_code order by list_month
                                 rows between unbounded preceding and 1 preceding) prev_sub_role from _psm)
        select * exclude (sub_role, prev_sub_role, product_role_carried, role_source, ob_link_path, ob_ref, ob_generic,
                          item_code, item_link_path, src_file, src_row),
               product_role_carried, role_source,
               item_code, item_link_path, ob_link_path, ob_ref, ob_generic, src_file, src_row
        from (select *,
                     case when in_substitute_list then sub_role
                          when prev_sub_role is not null then prev_sub_role
                          when ob_ref then 'reference' end product_role_carried,
                     case when in_substitute_list and sub_role is not null then 'observed'
                          when not in_substitute_list and prev_sub_role is not null then 'carried'
                          when not in_substitute_list and ob_ref then 'orangebook' end role_source
              from c)
        order by list_month, product_code""")
    con.execute("drop table _psm")


# ---------------------------------------------------------------- 신호 확인 근거 (S3 입력)
def build_signal_checks(con) -> None:
    """미래 날짜 정지 토큰(R3, R3b 시작일)마다 확인 스냅샷과 결과·사유를 남긴다 (S3 SUSPEND_SCHEDULED signal_outcome의 근거).
    확인 스냅샷 = 신호 날짜 이후 첫 스냅샷. 실현 판정: 확인 스냅샷에 그 제품의 정지 비고(R3·R3b)가 있음 (사용자 결정 2026-11-14 8차).
    confirm_status (→ S3 outcome)
      confirmed            같은 토큰(같은 정지일)            → realized
      confirmed_other_date 정지 비고는 있으나 날짜가 다름   → realized_partial (date_gap_days)
      unverifiable         R3b 종료일 < 확인 스냅샷 기준일  → unverifiable (실현율 분모에서 뺌)
      not_confirmed        reason: stay(집행정지 비고) / delisted(제품 없음) / note_gone(비고 사라짐) → not_realized
      censored             확인 스냅샷 없음
    """
    con.execute("""create or replace table s2_suspend_signal_check as
        with cal as (select list_month, min(base_date) base from stg_benefit group by 1),
             f as (select product_code, list_month signal_month, token_type, token_date, token_date_end,
                          list_sort(list(distinct source_list)) source_lists, min(raw) raw
                   from note_token where token_type in ('R3', 'R3b') and is_future group by 1, 2, 3, 4, 5),
             j as (select f.*, (select min(list_month) from cal where base >= f.token_date) check_month from f),
             k as (select j.*, c.base check_base,
                          exists (select 1 from note_token t where t.product_code = j.product_code and t.list_month = j.check_month
                                  and t.token_type = j.token_type and t.token_date = j.token_date
                                  and t.token_date_end is not distinct from j.token_date_end) token_in_check,
                          (select arg_min(datediff('day', j.token_date, t.token_date), abs(datediff('day', j.token_date, t.token_date)))
                           from note_token t where t.product_code = j.product_code and t.list_month = j.check_month
                             and t.token_type in ('R3', 'R3b')) susp_gap_in_check,
                          (select arg_min(t.token_date, abs(datediff('day', j.token_date, t.token_date)))
                           from note_token t where t.product_code = j.product_code and t.list_month = j.check_month
                             and t.token_type in ('R3', 'R3b')) susp_date_in_check,
                          (select min(t.source_list) from note_token t where t.product_code = j.product_code and t.list_month = j.check_month
                             and t.token_type in ('R3', 'R3b')) susp_list_in_check,
                          (select string_agg(distinct t.raw, ' ; ') from note_token t where t.product_code = j.product_code
                             and t.list_month = j.check_month and t.token_type in ('R3', 'R3b')) susp_raw_in_check,
                          exists (select 1 from stg_benefit b where b.product_code = j.product_code and b.list_month = j.check_month
                                  and b.row_kind = 'product_candidate') product_in_check,
                          exists (select 1 from note_token t where t.product_code = j.product_code and t.list_month = j.check_month
                                  and t.token_type = 'R4') stay_in_check,
                          exists (select 1 from note_token t where t.product_code = j.product_code and t.list_month = j.signal_month
                                  and t.token_type = 'R4') stay_in_signal_month
                   from j left join cal c on c.list_month = j.check_month)
        select 'SUSPEND_SCHEDULED' signal_type, token_type source_rule, *,
               case when check_month is null then 'censored'
                    when token_in_check then 'confirmed'
                    when susp_gap_in_check is not null then 'confirmed_other_date'
                    when token_type = 'R3b' and token_date_end < check_base then 'unverifiable'
                    else 'not_confirmed' end confirm_status,
               case when not token_in_check and susp_gap_in_check is not null then susp_gap_in_check end date_gap_days,
               case when check_month is null or token_in_check or susp_gap_in_check is not null
                         or (token_type = 'R3b' and token_date_end < check_base) then null
                    when not product_in_check then 'delisted'
                    when stay_in_check then 'stay' else 'note_gone' end reason
        from k order by signal_month, product_code, token_type, token_date""")
    # 확인 스냅샷 기준 outcome (S3 signal_outcome으로 옮긴다)과 이벤트 유효일. 예고 날짜는 실현(같은 날짜)일 때만 쓴다
    con.execute("""create or replace table s2_suspend_signal_check as select *,
            case confirm_status when 'confirmed' then 'realized' when 'confirmed_other_date' then 'realized_partial'
                 when 'not_confirmed' then 'not_realized' else confirm_status end outcome,
            case when confirm_status = 'confirmed' then token_date
                 when confirm_status = 'confirmed_other_date' then susp_date_in_check end event_effective_date,
            case when confirm_status = 'confirmed' then 'signal_confirmed'
                 when confirm_status = 'confirmed_other_date' then 'note_' || susp_list_in_check end event_date_source,
            case when reason is not null then 'data' end reason_source,
            case when confirm_status in ('confirmed', 'confirmed_other_date') then check_month || ' ' || susp_raw_in_check
                 when reason = 'stay' then check_month || ' 집행정지 비고, 정지 비고 없음'
                 when reason = 'delisted' then check_month || ' 급여목록에 제품 없음'
                 when reason = 'note_gone' then check_month || ' 제품 있음, 정지·집행정지 비고 없음' end evidence
        from s2_suspend_signal_check""")
    apply_external_evidence(con)


def apply_external_evidence(con) -> None:
    """config/external_evidence.yaml: 외부 자료로 정하거나 확인한 사유 (사용자 결정 9·10차).
    reason_source = external(외부 자료로만) / data+external(데이터와 외부 자료). keep_data_evidence면 데이터 근거 뒤에 링크를 붙인다."""
    path = ROOT / "config/external_evidence.yaml"
    if not path.exists():
        return
    for e in load_yaml("external_evidence.yaml").get("suspend_signal", []):
        m = e["match"]
        cond = "signal_month = ? and token_type = ? and token_date = cast(? as date) and outcome = 'not_realized'"
        args = [m["signal_month"], m["token_type"], m["token_date"]]
        n = con.execute(f"select count(*) from s2_suspend_signal_check where {cond}", args).fetchone()[0]
        if n == 0:
            continue                                    # 합성 데이터 등 대상이 없는 실행
        assert n == e["expected_count"], (e["id"], n, e["expected_count"])
        src = e.get("reason_source", "external")
        ext = e["note"] + " | " + " ; ".join(e["evidence"])
        ev = "'데이터: ' || evidence || ' / 외부: ' || ?" if e.get("keep_data_evidence") else "?"
        con.execute(f"""update s2_suspend_signal_check set reason = ?, reason_source = ?, evidence = {ev}
                        where {cond}""", [e["reason"], src, ext, *args])


COMMENTS = {
    "state_asof": "명목 기준일 기준: 스냅샷 기준일(파일명의 명목 날짜) 시점의 상태. 그 스냅샷까지의 정보만 쓴다 (뒤의 스냅샷을 지워도 같다). 스냅샷 자체에 명목 기준일 뒤의 정보가 들어 있을 수 있다",
    "state_eom": "월말 상태. 이후 스냅샷으로 확인된 것(미래 신호 확인, 2개월 공백 복귀, 늦게 보인 지난 날짜)만 반영",
    "exposure_frac": "그 달 급여된 일수 / 그 달 일수. state_eom 기준",
    "stay_flag": "기준일 시점 값(명목 기준일 기준): 그 달 비고에 집행정지(R4)가 있고 명목 기준일 이전 해제(R5)가 없음. 늦게 보인 R4·R5 날짜로 고쳐 쓰지 않는다 (날짜는 S3 이벤트 유효일)",
    "late_note_revised": "다음 스냅샷에서 처음 보인 지난 날짜(R6 해제일·R3 정지일·R3b 시작일, 이 달 안)로 state_eom·exposure_frac을 고쳐 씀",
    "product_role": "그 달 관측: 대체조제 행의 역할, 목록에 없으면 오렌지북 공고대조약",
    "product_role_carried": "그 달 대체조제 값, 없으면 과거 달 대체조제 값(앞으로만 이어 씀), 없으면 오렌지북 공고대조약",
    "role_source": "product_role_carried의 출처: observed / carried / orangebook",
}


def comment_columns(con) -> None:
    for col, text in COMMENTS.items():
        esc = text.replace("'", "''")
        con.execute(f"comment on column product_state_month.{col} is '{esc}'")


# ---------------------------------------------------------------- 보고용 표
def write_tables(con) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    def dump(name, sql):
        r = con.execute(sql)
        cols = [d[0] for d in r.description]
        write_csv(OUT / f"{name}.csv", cols, r.fetchall())

    life = """life as (select product_code, max(list_month) last_month from product_state_month group by 1)"""
    for col in ("state_asof", "state_eom"):
        dump(f"{col}_by_year", f"""select substr(list_month, 1, 4) as year, {col} state, count(*) n_rows, count(distinct product_code) n_products
            from product_state_month group by all order by 1, 2""")
    dump("state_asof_vs_eom", """select state_asof, state_eom, exposure_revised, state_revised, late_note_revised,
               count(*) n_rows, count(distinct product_code) n_products, min(product_code || ' ' || list_month) example
        from product_state_month where state_asof <> state_eom group by all order by 6 desc""")
    dump("link_rate", f"""with {life},
        py as (select distinct substr(list_month, 1, 4) as year, product_code from product_state_month)
        select year, case when l.last_month < '2025-12' then 'deleted' else 'alive_2025_12' end deleted, count(*) n_products,
               count(*) filter (where k.item_code_map) item_code_map, count(*) filter (where k.item_substitute) item_substitute,
               count(*) filter (where k.item_code is not null) item_any,
               count(*) filter (where k.ob_code_map) ob_code_map, count(*) filter (where k.ob_substitute) ob_substitute,
               count(*) filter (where k.ob_link_path is not null) ob_any,
               round(100.0 * count(*) filter (where k.item_code is not null) / count(*), 1) item_any_pct,
               round(100.0 * count(*) filter (where k.ob_link_path is not null) / count(*), 1) ob_any_pct
        from py join life l using (product_code) left join s2_product_link k using (product_code)
        group by rollup (year, 2) order by 1 nulls last, 2 nulls last""")
    # 역할: 그 해 마지막 관측월의 값. product_role(그 달 관측) / product_role_carried(이어 씀)
    for name, rc, sc in (("role_by_year", "product_role",
                          "case when in_substitute_list then 'substitute' when product_role is not null then 'orangebook' end"),
                         ("role_carried_by_year", "product_role_carried", "role_source")):
        dump(name, f"""with {life},
            ly as (select product_code, substr(list_month, 1, 4) as year, arg_max({rc}, list_month) role,
                          arg_max({sc}, list_month) src from product_state_month group by 1, 2)
            select *, round(100.0 * n_products / sum(n_products) over (partition by year, deleted), 1) pct_in_year_deleted
            from (select year, case when l.last_month < '2025-12' then 'deleted' else 'alive_2025_12' end deleted,
                         coalesce(role, 'NULL') role, coalesce(src, '-') source, count(*) n_products
                  from ly join life l using (product_code) group by all) order by 1, 2, 3, 4""")
    dump("role_conflict_cases", """select product_code, min(list_month) first_month, max(list_month) last_month, count(*) n_months,
               any_value(sub_ref_drug) sub_ref_drug, any_value(ob_ref) ob_ref, any_value(product_role) product_role,
               any_value(ob_link_path) ob_link_path, any_value(product_name) product_name
        from product_state_month where role_conflict group by 1 order by 1""")
    dump("role_changes", """with r as (select product_code, list_month, product_role, is_bioequiv, sub_equiv,
                      lag(product_role) over w prev_role, lag(is_bioequiv) over w prev_bioeq, lag(sub_equiv) over w prev_equiv,
                      row_number() over w rn
                   from product_state_month window w as (partition by product_code order by list_month))
        select product_code, list_month, prev_role, product_role, prev_bioeq, is_bioequiv, prev_equiv, sub_equiv from r
        where rn > 1 and (prev_role is distinct from product_role or prev_bioeq is distinct from is_bioequiv
                          or prev_equiv is distinct from sub_equiv)
        order by 1, 2""")
    dump("suspend_signal_check", """select * from s2_suspend_signal_check""")
    dump("suspend_signal_outcome_by_source", """select source_rule, outcome, coalesce(reason, '-') reason, coalesce(reason_source, '-') reason_source,
               count(*) n, min(date_gap_days) min_gap, max(date_gap_days) max_gap
        from s2_suspend_signal_check group by all order by 1, 2, 3, 4""")
    # 명목 기준일 뒤의 정보 (판정은 바꾸지 않는다. 보고용)
    dump("post_base_info_by_month", """with t as (select n.source_list, n.list_month, n.base_date, i.filename_revision_date rev, n.token_type, n.token_date, n.product_code, n.raw
                   from note_token n join snapshot_inventory i using (source_list, list_month) where n.is_future)
        select source_list, list_month, base_date, rev filename_revision_date, token_type, count(*) n,
               count(*) filter (where rev is not null and token_date <= rev) n_on_or_before_revision,
               min(token_date) min_date, max(token_date) max_date,
               list(product_code || ' ' || raw order by product_code)[1:3] examples
        from t group by all order by 1, 2, 5""")
    dump("suspend_signal_unconfirmed_by_reason", """select token_type, substr(signal_month, 1, 4) as year, confirm_status,
               coalesce(reason, '-') reason, count(*) n, count(*) filter (where stay_in_signal_month) stay_in_signal_month,
               min(date_gap_days) min_gap_days, max(date_gap_days) max_gap_days,
               min(product_code || ' ' || signal_month || ' ' || raw) example
        from s2_suspend_signal_check group by all order by 1, 2, 3, 4""")
    # 늦게 보인 지난 날짜: 다음 스냅샷에서 처음 보인 토큰의 날짜가 직전 스냅샷의 달 안 (그 달 행이 있는 제품)
    dump("late_past_dated_tokens", """with cal as (select list_month, lag(list_month) over (order by list_month) prev_month
                           from (select distinct list_month from product_state_month)),
             t as (select distinct product_code, list_month, token_type, token_date, token_date_end, 'date' as which, token_date d
                   from note_token where token_date is not null and token_type <> 'unresolved'
                   union all
                   select distinct product_code, list_month, token_type, token_date, token_date_end, 'date_end', token_date_end
                   from note_token where token_type = 'R3b'),
             q as (select t.*, c.prev_month from t join cal c using (list_month) where c.prev_month is not null
                     and exists (select 1 from product_state_month p where p.product_code = t.product_code and p.list_month = c.prev_month)
                     and not exists (select 1 from note_token u where u.product_code = t.product_code and u.list_month = c.prev_month
                                     and u.token_type = t.token_type and u.token_date is not distinct from t.token_date
                                     and u.token_date_end is not distinct from t.token_date_end))
        select token_type, which,
               case when d between cast(prev_month || '-01' as date) and last_day(cast(prev_month || '-01' as date)) then 'in_prev_month'
                    when d < cast(prev_month || '-01' as date) then 'before_prev_month' else 'after_prev_month' end when_,
               token_type in ('R3', 'R3b', 'R6') and which = 'date' state_rule, count(*) n, count(distinct product_code) n_products,
               min(product_code || ' ' || list_month || ' ' || cast(d as varchar)) example
        from q group by all order by 1, 2, 3""")


def write_audit(con) -> None:
    rows = con.execute("""select 'benefit', list_month, src_file, src_row, product_code_raw, product_name, firm,
                                 'non_product: 0 채움 후 숫자 9자리가 아닌 제품코드 (S1 row_kind). product_state_month에서 제외'
                          from stg_benefit where row_kind = 'non_product'
                          union all
                          select 'substitute', list_month, src_file, src_row, product_code_raw, product_name, firm,
                                 'non_product: 0 채움 후 숫자 9자리가 아닌 제품코드 (S1 row_kind). product_state_month에서 제외'
                          from stg_substitute where row_kind = 'non_product' order by 1, 2, 3, 4""").fetchall()
    write_csv(AUDIT / "dropped_s2_non_product.csv",
              ["source_list", "list_month", "src_file", "src_row", "product_code_raw", "product_name", "firm", "reason"], rows)
    rows = con.execute("""select s.list_month, s.product_code, s.product_name, s.firm, s.src_file, s.src_row,
                                 '대체조제 목록에만 있고 같은 달 급여목록에 없음. product_state_month 행이 없다 (행 기준 = 급여목록)'
                          from stg_substitute s where s.row_kind = 'product_candidate' and not exists
                          (select 1 from stg_benefit b where b.product_code = s.product_code and b.list_month = s.list_month
                           and b.row_kind = 'product_candidate') order by 1, 2""").fetchall()
    write_csv(AUDIT / "s2_substitute_only_rows.csv",
              ["list_month", "product_code", "product_name", "firm", "src_file", "src_row", "reason"], rows)


def build_all(con) -> None:
    build_state_override(con)
    build_bridges(con)
    build_attr_scd2(con)
    build_product_state_month(con)
    build_signal_checks(con)
    comment_columns(con)


def main() -> None:
    con = connect()
    con.execute("SET threads=8")
    build_all(con)
    for t in ("product_state_month", "product_attr_scd2", "bridge_product_item", "bridge_code_recode"):
        print(f"{t}: {con.execute(f'select count(*) from {t}').fetchone()[0]:,}행")
    write_audit(con)
    write_tables(con)
    con.execute("checkpoint")
    con.close()


if __name__ == "__main__":
    main()
