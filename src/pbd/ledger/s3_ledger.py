"""P1 S3: 신호·이벤트 원장 (docs/PLAN_P1.md §3·§4, 완료 기준 §6 S3).

data/interim/pbd.duckdb의 L1·S2 표에서 다음을 만든다.
- s3_cal            : 스냅샷 달력 (기준일 = 그 달 1일, 사용자 결정 2026-11-15)
- s3_entry_exit     : 진입·퇴출 재분류 (§4 F7 7단계). 창을 인자로 받는다 (표본 구간 재현 시험)
- signal_ledger     : 신호. 비고의 미래 날짜 토큰, (유형, 제품, 신호 날짜)로 하나 (P4: 두 목록 합침, 여러 달에 보여도 하나)
- signal_outcome    : 신호마다 결과 하나. 확인 스냅샷 = 신호 날짜 이후 첫 스냅샷 (RESUME은 종료일 다음 날 이후)
- event_ledger      : 스냅샷 사이에서 관측된 변화. 신호만으로 만들지 않는다
- s3_compare_by_year: 스냅샷 차이 방식과의 대조표

원칙 (CLAUDE.md, PLAN §3·§4, NOTE_RULES)
- 신호는 그 스냅샷까지의 정보(state_asof)로만 만든다. 이벤트의 월말 판정은 state_eom.
- 유효일: ① 짝지은 신호가 realized면 신호 날짜(signal_confirmed) ② 이벤트 달 비고의 지난 날짜(note_benefit/note_substitute)
  ③ 관측월 1일(snapshot_first) ④ 삭제는 구간 lo~hi ⑥ 실현되지 않은 신호의 날짜는 쓰지 않는다
  ⑦ R4·R5 날짜는 STAY_START·STAY_END·stay_released 가격 변경의 유효일.
- 집행정지가 어느 처분을 막는지 비고로 연결하지 않는다 (NOTE_RULES §3): STAY_* 이벤트는 다른 처분 이벤트와 짝짓지 않는다.

사용: PYTHONPATH=src python -m pbd.ledger.s3_ledger   (make p1-s3)
"""
from __future__ import annotations

from pbd.common import ROOT, connect, load_yaml
from pbd.ledger.s1_load import write_csv

OUT = ROOT / "outputs/s3"
PARAMS = load_yaml("params.yaml")


# ---------------------------------------------------------------- 달력
def build_calendar(con) -> None:
    con.execute("""create or replace table s3_cal as
        select list_month, base_date, row_number() over (order by list_month) mi,
               lag(list_month) over (order by list_month) prev_month, lead(list_month) over (order by list_month) next_month,
               cast(list_month || '-01' as date) first_day
        from (select list_month, min(base_date) base_date from product_state_month group by 1) order by 1""")


# ---------------------------------------------------------------- 진입·퇴출 재분류 (F7)
def build_entry_exit(con, table: str = "s3_entry_exit", start: str | None = None, end: str | None = None,
                     per_disappearance: bool = True) -> None:
    """§4 진입·퇴출 재분류. 창 [start, end] 안의 첫·마지막 관측으로 진입·퇴출을 정한다 (좌측 절단: 창 첫 달의 제품은 진입 아님).
    진입 순서: 1 same_item_recode → 2 transfer_by_name → 3 same_firm_swap → 4 list_recode → 5 new_pack_existing_item
              → 6 unlinked → 7 new_item.  ±1개월은 '진입 달'과 '사라진 달(마지막 관측 다음 달)'의 차이.
    퇴출: recode_transfer(같은 품목 또는 같은 주성분코드+같은 업체/제품명의 진입이 ±1개월) → item_remains(같은 품목의
          다른 제품이 사라진 달에 있음) → exit.  품목 연결: 품목기준코드가 하나뿐인 제품만 (code_map ∪ ②품목기준코드).
    per_disappearance=True (원장): 다음 달에 없는 관측마다 퇴출 후보로 분류한다 (나중에 다시 나타나도 그때는 알 수 없으므로).
    다시 나타난 것은 LISTING reentry로 따로 둔다.  False (reference F7 정의): 창 안 마지막 관측만 퇴출."""
    start = start or con.execute("select min(list_month) from s3_cal").fetchone()[0]
    end = end or con.execute("select max(list_month) from s3_cal").fetchone()[0]
    ext_sql = ("select b.*, lk.item, b.mi + 1 gone from b left join lk using (pc), n where b.mi < n.n "
               "and not exists (select 1 from b b2 where b2.pc = b.pc and b2.mi = b.mi + 1)") if per_disappearance else \
              "select b.*, lk.item, l + 1 gone from b join life using (pc) left join lk using (pc), n where l < n.n and mi = l"
    con.execute(f"""create or replace table {table} as
        with w as (select c.list_month, row_number() over (order by c.list_month) mi from s3_cal c
                   where c.list_month between '{start}' and '{end}'),
             n as (select max(mi) n from w),
             b as (select p.product_code pc, w.mi, p.list_month, p.ingredient_code gc, p.firm, p.product_name nm
                   from product_state_month p join w using (list_month)),
             lk as (select product_code pc, min(item_code) item from bridge_product_item group by 1
                    having count(distinct item_code) = 1),
             life as (select pc, min(mi) f, max(mi) l from b group by 1),
             ent as (select b.*, lk.item from b join life using (pc) left join lk using (pc) where f > 1 and mi = f),
             ext as ({ext_sql}),
             rc as (select distinct new_code pc from bridge_code_recode where old_in_benefit),
             entc as (select e.*, case
                 when exists (select 1 from ext x where x.item = e.item and abs(x.gone - e.mi) <= 1) then 'same_item_recode'
                 when exists (select 1 from ext x where x.gc = e.gc and abs(x.gone - e.mi) <= 1 and x.firm <> e.firm
                              and split_part(x.nm, '_', 1) = split_part(e.nm, '_', 1)) then 'transfer_by_name'
                 when exists (select 1 from ext x where x.gc = e.gc and abs(x.gone - e.mi) <= 1 and x.firm = e.firm) then 'same_firm_swap'
                 when e.pc in (select pc from rc) then 'list_recode'
                 when exists (select 1 from b o join lk k on k.pc = o.pc where k.item = e.item and o.pc <> e.pc and o.mi < e.mi) then 'new_pack_existing_item'
                 when e.item is null then 'unlinked' else 'new_item' end kind from ent e),
             extc as (select x.*, case
                 when exists (select 1 from ent e where (e.item = x.item or (e.gc = x.gc and (e.firm = x.firm
                              or split_part(e.nm, '_', 1) = split_part(x.nm, '_', 1)))) and abs(x.gone - e.mi) <= 1) then 'recode_transfer'
                 when exists (select 1 from b o join lk k on k.pc = o.pc where k.item = x.item and o.pc <> x.pc and o.mi = x.gone) then 'item_remains'
                 else 'exit' end kind from ext x)
        select 'entry' side, e.pc product_code, e.list_month as month, e.kind, e.item item_code from entc e
        union all
        select 'exit', x.pc, (select list_month from w where w.mi = x.gone), x.kind, x.item from extc x
        order by 1, 2""")


# ---------------------------------------------------------------- 신호
def build_signal_ledger(con) -> None:
    con.execute("""create or replace temp table _sig_raw as
        select case token_type when 'R2' then 'PRICE_PENDING' when 'R1' then 'LISTING' when 'R5' then 'STAY_RELEASE_PRICE'
                    when 'R9' then 'DELISTING_SCHEDULED' else 'SUSPEND_SCHEDULED' end signal_type,
               token_type source_rule, source_list, list_month, base_date, product_code, token_date signal_date,
               case when token_type = 'R3b' then token_date end period_start, token_date_end period_end, amount, raw, in_month_dated
        from note_token where is_future and token_type in ('R1', 'R2', 'R3', 'R3b', 'R5', 'R9')
        union all
        select 'RESUME_SCHEDULED', 'R3b', source_list, list_month, base_date, product_code, token_date_end,
               token_date, token_date_end, null, raw, in_month_dated
        from note_token where token_type = 'R3b' and is_future_end""")
    con.execute("""create or replace table signal_ledger as
        with g as (select signal_type, product_code, signal_date,
                          min(list_month) issued_month, max(list_month) last_seen_month, count(distinct list_month) n_months_seen,
                          list_sort(list(distinct source_list)) source_list, list_sort(list(distinct source_rule)) source_rules,
                          arg_min(source_rule, (source_list <> 'benefit', list_month)) source_rule,
                          arg_min(amount, (source_list <> 'benefit', list_month)) amount,
                          count(distinct amount) n_amounts,
                          arg_min(period_start, (source_list <> 'benefit', list_month)) period_start,
                          arg_min(period_end, (source_list <> 'benefit', list_month)) period_end,
                          arg_min(raw, (source_list <> 'benefit', list_month)) raw,
                          -- 발행 달 토큰의 날짜가 그 달 2일~말일: 월중 수정으로 들어왔을 수 있음 (P2 누수 민감도용, 판정에 쓰지 않음)
                          bool_or(in_month_dated) filter (where list_month = first_month) in_month_dated
                   from (select *, min(list_month) over (partition by signal_type, product_code, signal_date) first_month from _sig_raw)
                   group by 1, 2, 3),
             -- LISTING: 발행 달의 state_asof가 scheduled_listing이면 LISTING_SCHEDULED (그 스냅샷까지의 정보만)
             t as (select g.*, p.state_asof asof_at_issue,
                          case when g.signal_type <> 'LISTING' then g.signal_type
                               when p.state_asof = 'scheduled_listing' then 'LISTING_SCHEDULED'
                               when p.product_code is null and not exists (select 1 from product_state_month q
                                    where q.product_code = g.product_code and q.list_month < g.issued_month) then 'LISTING_SCHEDULED'
                               else 'LISTING_NOTE_EXISTING' end signal_type2,
                          coalesce(p.c4, (select min(c4) from stg_substitute s where s.product_code = g.product_code
                                          and s.list_month = g.issued_month)) market_c4
                   from g left join product_state_month p on p.product_code = g.product_code and p.list_month = g.issued_month)
        select signal_type2 || ':' || product_code || ':' || strftime(signal_date, '%Y-%m-%d') signal_id,
               signal_type2 signal_type, source_rule, source_rules, product_code, market_c4, issued_month,
               (select base_date from s3_cal c where c.list_month = t.issued_month) issued_base_date,
               signal_date, period_start, period_end, amount, n_amounts, source_list, len(source_list) = 2 merged_two_lists,
               last_seen_month, n_months_seen, asof_at_issue, raw, coalesce(in_month_dated, false) in_month_dated,
               -- 전제 신호: RESUME_SCHEDULED의 전제는 같은 제품·같은 시작일의 SUSPEND_SCHEDULED (시작일이 발행 시점에 미래였던 것)
               case when signal_type2 = 'RESUME_SCHEDULED' and exists (select 1 from t t2 where t2.signal_type2 = 'SUSPEND_SCHEDULED'
                         and t2.product_code = t.product_code and t2.signal_date = t.period_start)
                    then 'SUSPEND_SCHEDULED:' || product_code || ':' || strftime(period_start, '%Y-%m-%d') end parent_signal_id
        from t order by issued_month, signal_type, product_code, signal_date""")


def build_signal_outcome(con) -> None:
    con.execute("""create or replace table signal_outcome as
        -- 확인 스냅샷 (사용자 결정 2026-11-15): 예고 내용이 처음 반영된 스냅샷. 날짜는 비교하지 않는다.
        --   가격 예고·집행정지 해제(금액): 발행 뒤 상한금액이 예고 금액이 된 첫 스냅샷 / 삭제 예고: 발행 뒤 제품이 없는 첫 스냅샷.
        --   반영되지 않았으면 신호 날짜 이후 첫 스냅샷(check_month_by_date)에서 결과를 판정한다.
        --   정지·복귀·등재 예고는 예고 내용이 날짜라 반영을 날짜 없이 볼 수 없다: check_month_by_date 그대로.
        with s0 as (select l.*,
                          (select min(list_month) from s3_cal c where case when l.signal_type = 'RESUME_SCHEDULED'
                                then c.base_date > l.signal_date else c.base_date >= l.signal_date end) check_month_by_date
                   from signal_ledger l),
             s as (select s0.*, coalesce(
                          case when s0.signal_type in ('PRICE_PENDING', 'STAY_RELEASE_PRICE') and s0.amount is not null then
                                    (select min(p.list_month) from product_state_month p where p.product_code = s0.product_code
                                       and p.list_month > s0.issued_month and p.list_month <= coalesce(s0.check_month_by_date, '9999')
                                       and p.price_listed = s0.amount)
                               when s0.signal_type = 'DELISTING_SCHEDULED' then
                                    (select min(c.list_month) from s3_cal c where c.list_month > s0.issued_month
                                       and c.list_month <= coalesce(s0.check_month_by_date, '9999')
                                       and not exists (select 1 from product_state_month p where p.product_code = s0.product_code
                                                       and p.list_month = c.list_month)) end,
                          s0.check_month_by_date) check_month
                   from s0),
             j0 as (select s.*, c.base_date check_base, p.product_code is not null in_check, p.price_listed check_price,
                          p.state_asof check_asof, p.stay_flag check_stay,
                          (select q.price_listed from product_state_month q where q.product_code = s.product_code
                             and q.list_month < s.check_month order by q.list_month desc limit 1) prev_price,
                          exists (select 1 from note_token t where t.product_code = s.product_code and t.list_month = s.check_month
                                  and t.token_type = 'R2' and t.token_date = s.signal_date) note_in_check,
                          exists (select 1 from note_token t where t.product_code = s.product_code and t.list_month = s.check_month
                                  and t.token_type in ('R3', 'R3b') and t.token_date is distinct from s.period_start) new_susp_in_check,
                          k.outcome k_outcome, k.reason k_reason, k.reason_source k_reason_source, k.evidence k_evidence, k.reason_external k_reason_external,
                          k.date_gap_days k_gap, k.event_effective_date k_event_date, k.event_date_source k_event_source,
                          x.kind exit_kind
                   from s left join s3_cal c on c.list_month = s.check_month
                   left join product_state_month p on p.product_code = s.product_code and p.list_month = s.check_month
                   left join (select *, row_number() over (partition by product_code, token_date order by signal_month) rn
                              from s2_suspend_signal_check) k
                          on s.signal_type = 'SUSPEND_SCHEDULED' and k.product_code = s.product_code and k.token_date = s.signal_date and k.rn = 1
                   left join s3_entry_exit x on x.side = 'exit' and x.product_code = s.product_code and x.month = s.check_month),
             j as (select j0.*, (select k2.outcome from s2_suspend_signal_check k2 where 'SUSPEND_SCHEDULED:' || k2.product_code || ':'
                                   || strftime(k2.token_date, '%Y-%m-%d') = j0.parent_signal_id order by k2.signal_month limit 1) parent_outcome
                   from j0 j0),
             o as (select j.*,
                   case
                     when j.signal_type = 'SUSPEND_SCHEDULED' then j.k_outcome
                     when j.check_month is null then 'censored'
                     -- 전제 신호가 실현되지 않으면 moot (실현율 분모에서 뺀다). 전제가 unverifiable·censored면 같은 값
                     when j.parent_signal_id is not null and j.parent_outcome not in ('realized', 'realized_partial')
                          then case j.parent_outcome when 'not_realized' then 'moot' else j.parent_outcome end
                     when j.signal_type = 'DELISTING_SCHEDULED' then
                          case when j.in_check then 'not_realized' when j.exit_kind = 'recode_transfer' then 'not_realized' else 'realized' end
                     when not j.in_check then 'not_realized'
                     when j.signal_type = 'PRICE_PENDING' then
                          case when j.check_price = j.amount then 'realized'
                               when j.check_price is not null and j.prev_price is not null and j.check_price <> j.prev_price then 'realized_partial'
                               else 'not_realized' end
                     when j.signal_type in ('LISTING_SCHEDULED', 'LISTING_NOTE_EXISTING') then
                          case when j.check_asof in ('reimbursed', 'unpriced') then 'realized' else 'not_realized' end
                     when j.signal_type = 'STAY_RELEASE_PRICE' then
                          case when j.check_stay then 'not_realized' when j.amount is null or j.check_price = j.amount then 'realized'
                               else 'realized_partial' end
                     when j.signal_type = 'RESUME_SCHEDULED' then
                          case when j.new_susp_in_check then 'not_realized' when j.check_asof = 'suspended' then 'not_realized'
                               else 'realized' end
                   end outcome
                   from j)
        select signal_id, signal_type, source_rule, product_code, issued_month, signal_date, outcome, parent_signal_id,
               check_month, check_month_by_date, case when outcome in ('realized', 'realized_partial') then check_month end realized_month,
               datediff('day', issued_base_date, signal_date) lead_days,
               case when check_month is not null then datediff('day', signal_date, check_base) end confirm_lag_days,
               case when signal_type in ('PRICE_PENDING', 'STAY_RELEASE_PRICE') and outcome = 'realized_partial'
                    then check_price - amount end amount_gap,
               case when signal_type = 'SUSPEND_SCHEDULED' then k_gap end date_gap_days,
               case when outcome <> 'not_realized' then null
                    when signal_type = 'SUSPEND_SCHEDULED' then k_reason
                    when signal_type = 'DELISTING_SCHEDULED' then case when in_check then 'still_listed' else 'recode_transfer' end
                    when not in_check then 'delisted'
                    when signal_type = 'PRICE_PENDING' then
                         -- 집행정지 비고가 있다는 관측만 적는다. 집행정지가 이 예고를 막았다고 연결하지 않는다 (NOTE_RULES §3)
                         case when check_asof = 'suspended' then 'suspended' when check_stay then 'price_kept_stay_note'
                              when check_price is null then 'unpriced' when note_in_check then 'price_kept_note_remains'
                              else 'note_gone_price_kept' end
                    when signal_type in ('LISTING_SCHEDULED', 'LISTING_NOTE_EXISTING') then
                         case when check_asof = 'scheduled_listing' then 'still_scheduled' else check_asof end
                    when signal_type = 'STAY_RELEASE_PRICE' then 'stay_continues'
                    when signal_type = 'RESUME_SCHEDULED' then case when new_susp_in_check then 'new_suspension' else 'still_suspended' end
               end reason,
               case when outcome <> 'not_realized' then null when signal_type = 'SUSPEND_SCHEDULED' then k_reason_source else 'data' end reason_source,
               -- 외부 자료로 확인한 원인 (reason은 원본에서 나온 사유 그대로, 사용자 결정 2026-11-15)
               case when signal_type = 'SUSPEND_SCHEDULED' then k_reason_external end reason_external,
               case when signal_type = 'SUSPEND_SCHEDULED' then k_evidence
                    when check_month is null then null
                    when not in_check then check_month || ' 급여목록에 제품 없음'
                    when signal_type in ('PRICE_PENDING', 'STAY_RELEASE_PRICE')
                         then check_month || ' 상한금액 ' || coalesce(cast(check_price as varchar), 'NULL') || ' (예고 ' || coalesce(cast(amount as varchar), '-') || ', 직전 ' || coalesce(cast(prev_price as varchar), 'NULL') || ')'
                    else check_month || ' state_asof ' || check_asof end evidence,
               case when signal_type = 'SUSPEND_SCHEDULED' then k_event_date end event_effective_date,
               case when signal_type = 'SUSPEND_SCHEDULED' then k_event_source end event_date_source,
               -- 참고(판정에 쓰지 않음): 미실현 가격 예고가 확인 스냅샷 뒤에 예고 금액으로 나타난 첫 달
               case when signal_type = 'PRICE_PENDING' and outcome = 'not_realized' then
                    (select min(q.list_month) from product_state_month q where q.product_code = o.product_code
                       and q.list_month > o.check_month and q.price_listed = o.amount) end realized_after_followup
        from o order by issued_month, signal_type, product_code, signal_date""")
    apply_external_price(con)


def apply_external_price(con) -> None:
    """config/external_evidence.yaml price_signal: 가격 예고 미실현의 외부 원인을 reason_external에 둔다. reason·evidence는 그대로 (사용자 결정 2026-11-15)"""
    path = ROOT / "config/external_evidence.yaml"
    if not path.exists():
        return
    for e in load_yaml("external_evidence.yaml").get("price_signal", []):
        m = e["match"]
        cond = "signal_type = 'PRICE_PENDING' and outcome = 'not_realized' and issued_month = ? and signal_date = cast(? as date) and reason = ?"
        args = [m["issued_month"], m["signal_date"], m["reason"]]
        if e.get("product_codes"):
            cond += " and product_code in (" + ", ".join("?" for _ in e["product_codes"]) + ")"
            args += list(e["product_codes"])
        n = con.execute(f"select count(*) from signal_outcome where {cond}", args).fetchone()[0]
        if n == 0:
            continue                                    # 합성 데이터 등 대상이 없는 실행
        assert n == e["expected_count"], (e["id"], n, e["expected_count"])
        ext = e["cause"] + ": " + e["note"] + " | " + " ; ".join(e["evidence"])
        con.execute(f"""update signal_outcome set reason_source = 'data+external', reason_external = ?
                        where {cond}""", [ext, *args])


# ---------------------------------------------------------------- 이벤트
def build_event_ledger(con) -> None:
    con.execute("""create or replace temp table _rows as
        select p.*, c.mi, c.base_date cal_base, c.first_day,
               lag(p.list_month) over w prev_row_month, lag(p.price_listed) over w prev_price,
               lag(p.state_eom) over w prev_eom, lag(p.stay_flag) over w prev_stay,
               lead(p.list_month) over w next_row_month,
               (lag(p.list_month) over w) = c.prev_month adj_prev,
               (select next_month from s3_cal n where n.list_month = p.list_month) cal_next
        from product_state_month p join s3_cal c using (list_month)
        window w as (partition by p.product_code order by p.list_month)""")
    con.execute("""create or replace temp table _tok as
        select product_code, list_month, token_type, token_date, token_date_end, amount, is_future,
               bool_or(source_list = 'benefit') in_benefit from note_token group by all""")
    first_m, last_m = con.execute("select min(list_month), max(list_month) from s3_cal").fetchone()
    con.execute(f"""create or replace table event_ledger as
        with
        -- 진입·퇴출 (F7), 관측 공백 뒤 재진입·공백 앞 퇴출
        lst as (select e.product_code, e.month observed_month, e.kind,
                       case when e.kind in ('same_item_recode', 'transfer_by_name', 'same_firm_swap', 'list_recode') then 'TRANSFER' else 'LISTING' end event_type
                from s3_entry_exit e where e.side = 'entry'
                union all
                select product_code, list_month, 'reentry', 'LISTING' from _rows where prev_row_month is not null and not adj_prev),
        lsig as (select product_code, check_month, signal_id, signal_date from signal_outcome
                 where signal_type = 'LISTING_SCHEDULED' and outcome = 'realized'),
        ev_listing(event_type, product_code, observed_month, kind, effective_date, date_source, signal_id, lo, hi, evidence) as (
            select l.event_type, l.product_code, l.observed_month, l.kind,
                   coalesce(s.signal_date, nt.token_date, c.first_day) effective_date,
                   case when s.signal_id is not null then 'signal_confirmed'
                        when nt.token_date is not null then case when nt.in_benefit then 'note_benefit' else 'note_substitute' end
                        else 'snapshot_first' end date_source,
                   s.signal_id, null::date lo, null::date hi, null::varchar evidence
            from lst l join s3_cal c on c.list_month = l.observed_month
            left join lsig s on s.product_code = l.product_code and s.check_month = c.next_month
            left join (select product_code, list_month, min(token_date) token_date, bool_or(in_benefit) in_benefit from _tok
                       where token_type = 'R1' and not is_future group by 1, 2) nt
                   on nt.product_code = l.product_code and nt.list_month = l.observed_month),
        dex as (select x.product_code, x.month observed_month, x.kind from s3_entry_exit x where x.side = 'exit'),
        dsig as (select product_code, check_month, signal_id, signal_date from signal_outcome
                 where signal_type = 'DELISTING_SCHEDULED' and outcome = 'realized'),
        ev_delisting(event_type, product_code, observed_month, kind, effective_date, date_source, signal_id, lo, hi, evidence) as (
            select 'DELISTING', d.product_code, d.observed_month, d.kind,
                   s.signal_date, case when s.signal_id is not null then 'signal_confirmed' else 'interval' end,
                   s.signal_id, r.cal_base + 1, c.base_date,
                   case when r.state_eom = 'suspended' then '정지 상태로 삭제: 시장 이탈 시점 = 정지일 ' || cast(r.suspend_date as varchar) end
            from dex d join s3_cal c on c.list_month = d.observed_month
            join _rows r on r.product_code = d.product_code and r.list_month = c.prev_month
            left join dsig s on s.product_code = d.product_code and s.check_month = d.observed_month),
        -- 집행정지 (stay_flag = 기준일 시점 값). 창 첫 달에 이미 집행정지면 STAY_START 없음
        sr as (select product_code, check_month, signal_id, signal_date from signal_outcome
               where signal_type = 'STAY_RELEASE_PRICE' and outcome in ('realized', 'realized_partial')),
        r5 as (select product_code, list_month, min(token_date) d, bool_or(in_benefit) b from _tok
               where token_type = 'R5' and not is_future group by 1, 2),
        r4 as (select product_code, list_month, token_date d, bool_or(in_benefit) b from _tok where token_type = 'R4' group by 1, 2, 3),
        -- 집행정지 비고가 사라진 파일에 함께 처음 나타난 가격 예고가 실현됨 → STAY_END 유효일 = 예고일 (사용자 결정 2026-11-15, 전체 14건)
        sp as (select o.product_code, l.issued_month, min(o.signal_id) signal_id, min(o.signal_date) signal_date from signal_outcome o
               join signal_ledger l using (signal_id) where o.signal_type = 'PRICE_PENDING' and o.outcome = 'realized' group by 1, 2),
        ev_stay(event_type, product_code, observed_month, kind, effective_date, date_source, signal_id, lo, hi, evidence) as (
            select 'STAY_START', r.product_code, r.list_month, case when r.list_month = '{first_m}' then 'window_first_month' when r.prev_row_month is null then 'at_entry' else 'in_window' end,
                   r.stay_date, case when r4.b then 'note_benefit' else 'note_substitute' end, null, null, null, null
            from _rows r left join r4 on r4.product_code = r.product_code and r4.list_month = r.list_month and r4.d = r.stay_date
            where r.stay_flag and not (r.list_month = '{first_m}' and r.stay_date < r.first_day)   -- 창 시작 전 집행정지: stay_flag만
              and (r.prev_row_month is null or not r.adj_prev or not r.prev_stay)
            union all
            select 'STAY_END', r.product_code, r.list_month, case when s.signal_id is not null or r5.d is not null then 'released' else 'note_gone' end,
                   coalesce(s.signal_date, r5.d, sp.signal_date, r.first_day),
                   case when s.signal_id is not null then 'signal_confirmed' when r5.d is not null then case when r5.b then 'note_benefit' else 'note_substitute' end
                        when sp.signal_id is not null then 'signal_confirmed' else 'snapshot_first' end, coalesce(s.signal_id, sp.signal_id), null, null, null
            from _rows r left join sr s on s.product_code = r.product_code and s.check_month = r.list_month
            left join r5 on r5.product_code = r.product_code and r5.list_month = r.list_month
            left join sp on sp.product_code = r.product_code and sp.issued_month = r.list_month
            where r.adj_prev and r.prev_stay and not r.stay_flag),
        -- 급여정지 (state_eom). 창 첫 달에 이미 정지면 SUSPEND 없음
        ss as (select product_code, signal_id, signal_date, outcome, event_effective_date, event_date_source from signal_outcome
               where signal_type = 'SUSPEND_SCHEDULED' and outcome in ('realized', 'realized_partial')),
        susp_tok as (select product_code, token_date d, bool_or(in_benefit) b from _tok where token_type in ('R3', 'R3b') group by 1, 2),
        ev_susp(event_type, product_code, observed_month, kind, effective_date, date_source, signal_id, lo, hi, evidence) as (
            select 'SUSPEND', r.product_code,
                   case when r.exposure_revised or r.late_note_revised then r.cal_next else r.list_month end,
                   case when r.list_month = '{first_m}' then 'window_first_month' when r.prev_row_month is null then 'at_entry' else 'in_window' end,
                   r.suspend_date,
                   case when s.outcome = 'realized' then 'signal_confirmed' when s.outcome = 'realized_partial' then s.event_date_source
                        when t.d is not null then case when t.b then 'note_benefit' else 'note_substitute' end else 'snapshot_first' end,
                   s.signal_id, null, null, null
            from _rows r
            left join ss s on s.product_code = r.product_code and coalesce(s.event_effective_date, s.signal_date) = r.suspend_date
            left join susp_tok t on t.product_code = r.product_code and t.d = r.suspend_date
            where r.state_eom = 'suspended' and not (r.list_month = '{first_m}' and r.suspend_date < r.first_day)   -- 창 시작 전 정지
              and (r.prev_row_month is null or not r.adj_prev or r.prev_eom <> 'suspended')),
        rs as (select product_code, signal_id, signal_date from signal_outcome where signal_type = 'RESUME_SCHEDULED' and outcome = 'realized'),
        r6 as (select product_code, token_date d, bool_or(in_benefit) b from _tok where token_type = 'R6' group by 1, 2),
        r3b as (select product_code, token_date_end + 1 d, bool_or(in_benefit) b from _tok where token_type = 'R3b' group by 1, 2),
        ev_resume(event_type, product_code, observed_month, kind, effective_date, date_source, signal_id, lo, hi, evidence) as (
            select 'RESUME', r.product_code,
                   case when r.exposure_revised or r.late_note_revised or r.state_revised then r.cal_next else r.list_month end,
                   case when r6.d is not null then 'R6' when r3b.d is not null then 'R3b_end' when r.state_revised then 'two_month_gap' else 'other' end,
                   r.resume_date,
                   case when r6.d is not null then case when r6.b then 'note_benefit' else 'note_substitute' end
                        when rs.signal_id is not null then 'signal_confirmed'
                        when r3b.d is not null then case when r3b.b then 'note_benefit' else 'note_substitute' end
                        else 'snapshot_first' end,
                   case when r6.d is null then rs.signal_id end, null, null, null
            from _rows r
            left join r6 on r6.product_code = r.product_code and r6.d = r.resume_date
            left join r3b on r3b.product_code = r.product_code and r3b.d = r.resume_date
            left join rs on rs.product_code = r.product_code and rs.signal_date + 1 = r.resume_date
            where r.resume_date is not null),
        -- 가격 변경
        stay_end as (select product_code, observed_month from ev_stay where event_type = 'STAY_END'),
        ps as (select product_code, check_month, signal_id, signal_date, outcome from signal_outcome
               where signal_type = 'PRICE_PENDING' and outcome in ('realized', 'realized_partial')),
        r2p as (select t.product_code, t.list_month, t.token_date d, t.amount, t.in_benefit b from _tok t
                where t.token_type = 'R2' and not t.is_future),
        ev_price(event_type, product_code, observed_month, kind, effective_date, date_source, signal_id, lo, hi, evidence) as (
            select 'PRICE_CHANGE', r.product_code, r.list_month,
                   case when se.product_code is not null then 'stay_released' when ps.signal_id is not null then 'signaled' else 'unsignaled' end,
                   case when se.product_code is not null then coalesce(sr.signal_date, r5.d, r.first_day)
                        when ps.outcome = 'realized' then ps.signal_date
                        when ps.signal_id is null then coalesce(np.d, r.first_day) else r.first_day end,
                   case when se.product_code is not null then
                             case when sr.signal_id is not null then 'signal_confirmed' when r5.d is not null then case when r5.b then 'note_benefit' else 'note_substitute' end
                                  else 'snapshot_first' end
                        when ps.outcome = 'realized' then 'signal_confirmed'
                        when ps.signal_id is null and np.d is not null then case when np.b then 'note_benefit' else 'note_substitute' end
                        else 'snapshot_first' end,
                   case when se.product_code is not null then sr.signal_id else ps.signal_id end, null, null,
                   cast(r.prev_price as varchar) || ' → ' || cast(r.price_listed as varchar)
            from _rows r
            left join stay_end se on se.product_code = r.product_code and se.observed_month = r.list_month
            left join sr on sr.product_code = r.product_code and sr.check_month = r.list_month
            left join r5 on r5.product_code = r.product_code and r5.list_month = r.list_month
            left join ps on ps.product_code = r.product_code and ps.check_month = r.list_month
            left join (select r2p.product_code, r2p.list_month, min(r2p.d) d, bool_or(r2p.b) b from r2p
                       join _rows q on q.product_code = r2p.product_code and q.list_month = r2p.list_month and q.price_listed = r2p.amount
                       join s3_cal c on c.list_month = r2p.list_month
                       where r2p.d > (select base_date from s3_cal p where p.list_month = c.prev_month) group by 1, 2) np
                   on np.product_code = r.product_code and np.list_month = r.list_month
            where r.adj_prev and r.prev_price is not null and r.price_listed is not null and r.prev_price <> r.price_listed),
        ev_attr(event_type, product_code, observed_month, kind, effective_date, date_source, signal_id, lo, hi, evidence) as (
            select 'ATTR_CHANGE', a.product_code, a.valid_from_month,
                   concat_ws('+', case when a.changed_firm then 'firm' end, case when a.changed_product_name then 'product_name' end,
                             case when a.changed_ingredient_code then 'ingredient_code' end),
                   c.first_day, 'snapshot_first', null, null, null, null
            from product_attr_scd2 a join s3_cal c on c.list_month = a.valid_from_month where a.version > 1),
        allv as (select * from ev_listing union all select * from ev_delisting union all select * from ev_stay
                 union all select * from ev_susp union all select * from ev_resume union all select * from ev_price union all select * from ev_attr)
        select a.event_type || ':' || a.product_code || ':' || a.observed_month event_id, a.event_type, a.product_code,
               (select p.c4 from product_state_month p where p.product_code = a.product_code and p.list_month <= a.observed_month
                order by p.list_month desc limit 1) market_c4,
               a.observed_month, a.effective_date, a.lo effective_date_lo, a.hi effective_date_hi, a.date_source, a.kind,
               a.signal_id, a.evidence,
               -- 참고(판정에 쓰지 않음): 짝지은 신호의 예고 날짜
               (select l.signal_date from signal_ledger l where l.signal_id = a.signal_id) notice_date,
               -- 사후 분석용(판정에 쓰지 않음): 퇴출 뒤 같은 제품코드로 다시 나타남. 뒤의 스냅샷에 따라 바뀐다
               case when a.event_type = 'DELISTING' then exists (select 1 from product_state_month q where q.product_code = a.product_code
                         and q.list_month >= a.observed_month) end exit_reversed,
               -- 사후 참고(판정·증분 시험에 쓰지 않음): 이 가격 변경이 추적 기간 뒤에 예고 금액으로 나타난 미실현 가격 예고의 신호
               case when a.event_type = 'PRICE_CHANGE' then (select min(o.signal_id) from signal_outcome o
                         where o.signal_type = 'PRICE_PENDING' and o.product_code = a.product_code
                           and o.realized_after_followup = a.observed_month) end late_realization_of,
               cast(null as varchar) reason_source, cast(null as varchar) reason_external
        from allv a
        order by observed_month, event_type, product_code""")


# ---------------------------------------------------------------- 대조표: 스냅샷 차이 방식 vs 원장
def apply_external_event(con) -> None:
    """config/external_evidence.yaml event: 이벤트에 외부 근거를 붙인다. 유효일·분류는 바꾸지 않는다 (사용자 결정 2026-11-15)"""
    path = ROOT / "config/external_evidence.yaml"
    if not path.exists():
        return
    for e in load_yaml("external_evidence.yaml").get("event", []):
        m = e["match"]
        cond, args = "event_type = ? and observed_month = ?", [m["event_type"], m["observed_month"]]
        if "late_realization_issued_month" in m:
            cond += " and late_realization_of in (select signal_id from signal_ledger where issued_month = ?)"
            args.append(m["late_realization_issued_month"])
        if e.get("product_codes"):
            cond += " and product_code in (" + ", ".join("?" for _ in e["product_codes"]) + ")"
            args += list(e["product_codes"])
        n = con.execute(f"select count(*) from event_ledger where {cond}", args).fetchone()[0]
        if n == 0:
            continue
        assert n == e["expected_count"], (e["id"], n, e["expected_count"])
        ext = e["note"] + " | " + " ; ".join(e["evidence"])
        if "cause" in e:        # 외부 원인은 reason_external (evidence는 데이터 근거 그대로, 2026-11-15 방식)
            con.execute(f"""update event_ledger set reason_source = 'data+external', reason_external = ? where {cond}""",
                        [e["cause"] + ": " + ext, *args])
        else:
            con.execute(f"""update event_ledger set reason_source = ?, evidence = '데이터: ' || coalesce(evidence, '') || ' / 외부: ' || ?
                            where {cond}""", [e["reason_source"], ext, *args])


def build_compare(con) -> None:
    con.execute("""create or replace table s3_compare_by_year as
        with naive as (  -- 스냅샷 차이: 처음 나타남 / 사라짐 / 가격 변화, 날짜는 관측월 1일, 비고·재분류 없음
                 select 'LISTING' t, product_code, observed_month from event_ledger where event_type in ('LISTING', 'TRANSFER')
                 union all select 'DELISTING', product_code, observed_month from event_ledger where event_type = 'DELISTING'
                 union all select 'PRICE_CHANGE', product_code, observed_month from event_ledger where event_type = 'PRICE_CHANGE'),
             led as (select event_type, kind, product_code, observed_month, effective_date, effective_date_lo, date_source, exit_reversed,
                            late_realization_of from event_ledger),
             yrs as (select distinct substr(list_month, 1, 4) as year from s3_cal),
             act as (select substr(list_month, 1, 4) as year,
                            count(*) filter (where state_asof = 'scheduled_listing') asof_scheduled,
                            count(*) filter (where state_asof = 'suspended') asof_suspended,
                            count(*) filter (where state_eom = 'suspended') eom_suspended,
                            count(distinct product_code) filter (where state_eom = 'suspended' or state_asof = 'scheduled_listing') products_excluded
                     from product_state_month group by 1)
        select y.year, t.event_type,
               -- 스냅샷 차이 방식의 건수. LISTING 행 = 처음 나타남 전체 (원장의 LISTING + TRANSFER)
               case when t.event_type in ('LISTING', 'DELISTING', 'PRICE_CHANGE') then
                    (select count(*) from naive n where substr(n.observed_month, 1, 4) = y.year and n.t = t.event_type) end naive_n,
               (select count(*) from led l where l.event_type = t.event_type and substr(l.observed_month, 1, 4) = y.year) ledger_n,
               case when t.event_type = 'DELISTING' then (select count(*) from led l where l.event_type = 'DELISTING'
                    and substr(l.observed_month, 1, 4) = y.year and not l.exit_reversed) end ledger_n_excl_exit_reversed,
               case when t.event_type = 'PRICE_CHANGE' then (select count(*) from led l where l.event_type = 'PRICE_CHANGE' and l.kind = 'unsignaled'
                    and substr(l.observed_month, 1, 4) = y.year) end n_unsignaled,
               case when t.event_type = 'PRICE_CHANGE' then (select count(*) from led l where l.event_type = 'PRICE_CHANGE' and l.kind = 'unsignaled'
                    and substr(l.observed_month, 1, 4) = y.year and l.late_realization_of is null) end n_unsignaled_excl_late_realization,
               (select count(*) from led l where l.event_type = t.event_type and substr(l.observed_month, 1, 4) = y.year
                  and l.effective_date is not null and l.effective_date <> cast(l.observed_month || '-01' as date)) n_date_differs,
               (select count(*) from led l where l.event_type = t.event_type and substr(l.observed_month, 1, 4) = y.year
                  and ((l.event_type = 'LISTING' and l.kind <> 'new_item') or l.event_type = 'TRANSFER'
                       or (l.event_type = 'DELISTING' and l.kind <> 'exit'))) n_kind_differs,
               (select count(*) from led l where l.event_type = t.event_type and substr(l.observed_month, 1, 4) = y.year
                  and l.observed_month < '9999') n_check,
               a.asof_scheduled, a.asof_suspended, a.eom_suspended, a.products_excluded
        from yrs y cross join (select distinct event_type from event_ledger) t left join act a using (year)
        order by 1, 2""")


def build_all(con) -> None:
    build_calendar(con)
    build_entry_exit(con)
    # 표본 구간 재현 시험용 (reference F7): 같은 규칙을 표본 창에만 적용
    build_entry_exit(con, "s3_entry_exit_sample", PARAMS["p1"]["sample_start"], PARAMS["p1"]["sample_end"], per_disappearance=False)
    build_signal_ledger(con)
    build_signal_outcome(con)
    build_event_ledger(con)
    apply_external_event(con)
    build_compare(con)


def write_tables(con) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    def dump(name, sql):
        r = con.execute(sql)
        write_csv(OUT / f"{name}.csv", [d[0] for d in r.description], r.fetchall())

    dump("signal_outcome_by_type", """select signal_type, source_rule, outcome, coalesce(reason, '-') reason, coalesce(reason_source, '-') reason_source,
               count(*) n from signal_outcome group by all order by 1, 2, 3, 4, 5""")
    dump("signal_outcome_by_year", """select signal_type, substr(issued_month, 1, 4) as year, count(*) n,
               count(*) filter (where outcome = 'realized') realized, count(*) filter (where outcome = 'realized_partial') realized_partial,
               count(*) filter (where outcome = 'not_realized') not_realized, count(*) filter (where outcome = 'unverifiable') unverifiable,
               count(*) filter (where outcome = 'censored') censored
        from signal_outcome group by all order by 1, 2""")
    dump("event_by_year", """select event_type, kind, substr(observed_month, 1, 4) as year, date_source, count(*) n
        from event_ledger group by all order by 1, 2, 3, 4""")
    dump("compare_by_year", "select * exclude (n_check) from s3_compare_by_year")
    dump("entry_exit_kinds", "select side, kind, substr(month, 1, 4) as year, count(*) n from s3_entry_exit group by all order by 1, 2, 3")


def main() -> None:
    con = connect()
    con.execute("SET threads=8")
    build_all(con)
    for t in ("signal_ledger", "signal_outcome", "event_ledger", "s3_entry_exit"):
        print(f"{t}: {con.execute(f'select count(*) from {t}').fetchone()[0]:,}행")
    write_tables(con)
    con.execute("checkpoint")
    con.close()


if __name__ == "__main__":
    main()
