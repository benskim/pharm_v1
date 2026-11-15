"""S3 통합 시험: make p1-s3로 만든 data/interim/pbd.duckdb를 검사한다.

기대값 출처
- F4 가격 예고, F7 진입·퇴출 재분류: reference/precheck_v3_output.md (표본 구간 2024-01~2025-12)
- 2022-09 정지일 9/8 → 9/9, 경보제약·동아에스티 사유: DECISIONS 9·10차 (사용자 결정)
"""
import datetime as dt

import duckdb
import pytest

from pbd.common import DB_PATH, ROOT

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="pbd.duckdb 없음 (make p1-s1, p1-s2, p1-s3)")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(DB_PATH), read_only=True)
    if not c.execute("select count(*) from information_schema.tables where table_name = 'event_ledger'").fetchone()[0]:
        pytest.skip("event_ledger 없음 (make p1-s3)")
    yield c
    c.close()


def _ref(title):
    text = (ROOT / "reference/precheck_v3_output.md").read_text(encoding="utf-8")
    block = text.split(f"## {title}\n")[1].split("\n## ")[0].strip().splitlines()
    return [tuple(x.strip() for x in line.split("|")) for line in block[1:]]


def test_f4_price_notice_reproduced(con):
    """F4: 비고월별 가격 예고 행, 익월 금액 일치, 익월 부재, 당월 일치 (급여목록, 표본 구간)"""
    want = [(m, d, int(n), int(a), int(b), int(c)) for m, d, n, a, b, c in _ref("F4 가격 예고: 비고 날짜와 익월 가격 일치")]
    got = con.execute("""with t as (select n.list_month, n.product_code, n.token_date, n.amount from note_token n
                             where n.source_list = 'benefit' and n.token_type = 'R2' and n.list_month between '2024-01' and '2025-12')
        select t.list_month, min(t.token_date), count(*), count(*) filter (where nx.price_listed = t.amount),
               count(*) filter (where nx.product_code is null), count(*) filter (where cu.price_listed = t.amount)
        from t join s3_cal c using (list_month)
        left join product_state_month nx on nx.product_code = t.product_code and nx.list_month = c.next_month
        left join product_state_month cu on cu.product_code = t.product_code and cu.list_month = t.list_month
        group by 1 order by 1""").fetchall()
    # reference의 적용일 표기(2024.3.1, 24.4.8)는 비고 원문이라 날짜로 바꿔 비교한다
    def d(s):
        y, m, dd = s.split(".")
        return dt.date(int(y) + (2000 if len(y) == 2 else 0), int(m), int(dd))
    assert got == [(m, d(x), n, a, b, c) for m, x, n, a, b, c in want]


def test_f4_signal_outcomes_sample(con):
    """F4를 신호 결과로: 표본 구간 급여목록 가격 예고 4,565 = 다음 스냅샷 일치 4,552 + 없음 6 + 불일치 7 (PLAN_P1 F4)"""
    r = con.execute("""select count(*), count(*) filter (where o.outcome = 'realized'), count(*) filter (where o.reason = 'delisted'),
               count(*) filter (where o.outcome = 'realized_partial' or (o.outcome = 'not_realized' and o.reason <> 'delisted'))
        from signal_outcome o join signal_ledger l using (signal_id)
        where l.signal_type = 'PRICE_PENDING' and l.issued_month between '2024-01' and '2025-12' and list_contains(l.source_list, 'benefit')""").fetchone()
    assert r == (4565, 4552, 6, 7)


KIND_ENTRY = {"1 동일 품목 코드 교체": "same_item_recode", "2 양도양수 추정": "transfer_by_name", "3 동일 업체 교체": "same_firm_swap",
              "4 기등재 품목의 새 코드": "new_pack_existing_item", "5 품목 연결 불가": "unlinked", "6 실질 신규 품목": "new_item"}
KIND_EXIT = {"실질 퇴출": "exit", "코드 교체·양도양수": "recode_transfer", "품목 잔존": "item_remains"}


def test_f7_entry_reproduced(con):
    want = {KIND_ENTRY[k]: int(n) for k, n, _ in _ref("F7 진입 재분류")}
    got = dict(con.execute("select kind, count(*) from s3_entry_exit_sample where side = 'entry' group by 1").fetchall())
    assert got == want


def test_f7_exit_reproduced(con):
    want = {(KIND_EXIT[k], m == "True"): int(n) for k, m, n in _ref("F7 퇴출 재분류")}
    got = {(k, bool(m)): n for k, m, n in con.execute("""select kind, month in ('2024-11', '2025-11'), count(*)
        from s3_entry_exit_sample where side = 'exit' group by 1, 2""").fetchall()}
    assert got == want


def test_every_signal_one_outcome(con):
    n_l, n_o, n_join = con.execute("""select (select count(*) from signal_ledger), (select count(*) from signal_outcome),
        (select count(*) from signal_ledger l join signal_outcome o using (signal_id))""").fetchone()
    assert n_l == n_o == n_join
    bad = con.execute("""select count(*) from signal_outcome where outcome is null
        or outcome not in ('realized', 'realized_partial', 'not_realized', 'unverifiable', 'censored', 'moot')
        or (outcome = 'moot') <> (parent_signal_id is not null and outcome = 'moot')
        or (outcome = 'not_realized') <> (reason is not null) or (outcome = 'not_realized') <> (reason_source is not null)""").fetchone()[0]
    assert bad == 0


def test_ids_deterministic_and_unique(con):
    assert con.execute("select count(*) - count(distinct signal_id) from signal_ledger").fetchone()[0] == 0
    assert con.execute("select count(*) - count(distinct event_id) from event_ledger").fetchone()[0] == 0
    bad = con.execute("""select count(*) from signal_ledger where signal_id <> signal_type || ':' || product_code || ':' || strftime(signal_date, '%Y-%m-%d')""").fetchone()[0]
    assert bad == 0
    bad = con.execute("""select count(*) from event_ledger where event_id <> event_type || ':' || product_code || ':' || observed_month""").fetchone()[0]
    assert bad == 0


def test_unrealized_signal_dates_not_used(con):
    """실현되지 않은 신호(not_realized·censored·unverifiable)는 이벤트와 짝짓지 않고, 그 날짜를 signal_confirmed로 쓰지 않는다"""
    assert con.execute("""select count(*) from event_ledger e join signal_outcome o using (signal_id)
                          where o.outcome not in ('realized', 'realized_partial')""").fetchone()[0] == 0
    assert con.execute("""select count(*) from event_ledger e join signal_outcome o using (signal_id)
                          where e.date_source = 'signal_confirmed' and o.outcome <> 'realized'""").fetchone()[0] == 0
    # signal_confirmed 날짜 = 짝지은 신호의 날짜 (RESUME는 종료일 다음 날)
    assert con.execute("""select count(*) from event_ledger e join signal_ledger l using (signal_id)
                          where e.date_source = 'signal_confirmed'
                            and e.effective_date <> case when l.signal_type = 'RESUME_SCHEDULED' then l.signal_date + 1 else l.signal_date end""").fetchone()[0] == 0
    assert con.execute("select count(*) from event_ledger where date_source = 'signal_confirmed' and signal_id is null").fetchone()[0] == 0


def test_shifted_suspension_2022_09(con):
    """2022-09 예고 9/8 → 비고 9/9: SUSPEND 이벤트 39개 모두 9/9, 9/8을 쓰는 정지 이벤트 없음"""
    r = con.execute("""select e.effective_date, e.date_source, count(*) from event_ledger e join signal_outcome o using (signal_id)
                       where e.event_type = 'SUSPEND' and o.signal_date = '2022-09-08' group by 1, 2""").fetchall()
    assert r == [(dt.date(2022, 9, 9), "note_benefit", 39)]
    assert con.execute("select count(*) from event_ledger where event_type = 'SUSPEND' and effective_date = '2022-09-08'").fetchone()[0] == 0


def test_external_reason_kept(con):
    """사용자 결정 2026-11-15: reason은 원본 사유. 경보제약 23건은 note_gone(원본) + reason_external "집행정지".
    동아에스티 72건은 원본 사유 stay + reason_external. 642302580 1건은 data만"""
    r = dict(con.execute("""select reason_source, count(*) from signal_outcome where signal_type = 'SUSPEND_SCHEDULED'
                            and outcome = 'not_realized' and reason = 'stay' group by 1""").fetchall())
    assert r == {"data": 1, "data+external": 72}
    k = con.execute("""select reason, reason_source, reason_external from signal_outcome where signal_type = 'SUSPEND_SCHEDULED'
                       and issued_month = '2025-06' and signal_date = '2025-06-24'""").fetchall()
    assert len(k) == 23 and all(x[:2] == ("note_gone", "data+external") and x[2].startswith("집행정지: ") for x in k)
    assert con.execute("""select count(*) from signal_outcome where reason_source = 'external'""").fetchone()[0] == 0
    assert con.execute("""select count(*) from signal_outcome where (reason_source = 'data+external') <> (reason_external is not null)""").fetchone()[0] == 0


def test_unverifiable_separate_from_censored(con):
    """unverifiable은 censored와 다른 값이다 (실현율 분모에서 뺀다). S2 근거 표와 같은 수"""
    a = con.execute("select count(*) from signal_outcome where outcome = 'unverifiable'").fetchone()[0]
    b = con.execute("""select count(*) from (select distinct product_code, token_date from s2_suspend_signal_check
                       where confirm_status = 'unverifiable')""").fetchone()[0]
    assert a == b


def test_stay_events_not_linked_to_other_dispositions(con):
    """집행정지 이벤트는 STAY_RELEASE_PRICE 신호만 짝짓는다 (NOTE_RULES §3: 어느 처분을 막는지 비고로 연결하지 않음)"""
    assert con.execute("""select count(*) from event_ledger e join signal_ledger l using (signal_id)
                          where e.event_type in ('STAY_START', 'STAY_END') and l.signal_type <> 'STAY_RELEASE_PRICE'""").fetchone()[0] == 0


def test_pre_window_states_have_no_start_events(con):
    """창 시작 전 집행정지·급여정지는 STAY_START·SUSPEND 없이 상태만 (PLAN §4)"""
    assert con.execute("""select count(*) from event_ledger e where e.event_type in ('STAY_START', 'SUSPEND')
                          and e.effective_date < '2022-01-01'""").fetchone()[0] == 0


def test_delisting_interval(con):
    bad = con.execute("""select count(*) from event_ledger where event_type = 'DELISTING' and (effective_date_lo is null or effective_date_hi is null
                         or effective_date_lo > effective_date_hi or (effective_date is null) <> (date_source = 'interval'))""").fetchone()[0]
    assert bad == 0


@pytest.mark.parametrize("cut", ["2022-06", "2023-12", "2025-06"])
def test_incremental_equals_batch_real(con, cut):
    """cut까지의 스냅샷으로 S2·S3를 다시 만들면, cut 시점에 확정된 부분(상태는 cut 전 달까지, 이벤트는 cut 전 달 관측까지,
    신호 결과는 확인 달이 cut 이하)이 전체로 만든 결과와 같다. 고쳐 쓰기는 다음 달 안에서 끝난다"""
    from pbd.common import connect
    from pbd.ledger import s2_state as S2
    from pbd.ledger import s3_ledger as S3
    c = connect(None)
    try:
        c.execute(f"attach '{DB_PATH.as_posix()}' as src (read_only)")
        for t in ("stg_benefit", "stg_substitute", "note_token"):
            c.execute(f"create table {t} as select * from src.{t} where list_month <= '{cut}'")
        for t in ("attr_2026_product", "attr_2026_item"):
            c.execute(f"create table {t} as select * from src.{t}")
        S2.build_all(c)
        S3.build_all(c)
        for q, args in (
            ("select * exclude (src_file, src_row) from {db}product_state_month where list_month < ? order by product_code, list_month", [cut]),
            # exit_reversed는 사후 분석용(뒤의 스냅샷에 따라 바뀜, 판정에 쓰지 않음)이라 비교에서 뺀다
            ("select * exclude (exit_reversed, late_realization_of) from {db}event_ledger where observed_month < ? order by event_id", [cut]),
            # realized_after_followup도 사후 참고 컬럼 (뒤의 스냅샷에 따라 바뀜)
            ("select * exclude (realized_after_followup) from {db}signal_outcome where check_month <= ? order by signal_id", [cut]),
        ):
            assert c.execute(q.format(db=""), args).fetchall() == c.execute(q.format(db="src."), args).fetchall(), q[:60]
    finally:
        c.close()


def test_resume_moot_2022_05(con):
    """동아에스티 72건의 복귀 예고: 전제 정지 신호가 실현되지 않아 moot, parent_signal_id로 연결 (사용자 결정 2026-11-15)"""
    r = con.execute("""select o.outcome, p.outcome, count(*) from signal_outcome o join signal_outcome p on p.signal_id = o.parent_signal_id
                       where o.signal_type = 'RESUME_SCHEDULED' group by 1, 2 order by 1""").fetchall()
    assert r == [("moot", "not_realized", 72), ("realized", "realized", 11)]


def test_price_external_evidence(con):
    """가격 예고 미실현 77건: 사유 그대로, 동아에스티 74·마더스제약 3은 data+external, 외부 원인·링크는 reason_external (사용자 결정 2026-11-15)"""
    rows = dict(con.execute("""select issued_month, count(*) from signal_outcome where signal_type = 'PRICE_PENDING'
                               and reason = 'price_kept_stay_note' and reason_source = 'data+external' group by 1""").fetchall())
    assert rows == {"2022-05": 74, "2024-02": 3}
    ev, ext = con.execute("""select evidence, reason_external from signal_outcome where product_code = '622802320' and signal_type = 'PRICE_PENDING'""").fetchone()
    assert ev.startswith("2024-03 상한금액 116") and "https://dailypharm.com/user/news/19265" in ext and "342239" in ext
    assert con.execute("""select count(*) from signal_outcome where signal_type = 'PRICE_PENDING' and reason = 'price_kept_stay_note'
                          and reason_source = 'data'""").fetchone()[0] == 0


def test_exit_reversed_reference_only(con):
    """exit_reversed = 퇴출 뒤 같은 제품코드가 다시 나타남 (LISTING reentry와 같은 수)"""
    a = con.execute("select count(*) from event_ledger where event_type = 'DELISTING' and exit_reversed").fetchone()[0]
    b = con.execute("select count(*) from event_ledger where event_type = 'LISTING' and kind = 'reentry'").fetchone()[0]
    assert a == b
    assert con.execute("select count(*) from event_ledger where event_type <> 'DELISTING' and exit_reversed is not null").fetchone()[0] == 0


def test_donga_2024_08_late_realization(con):
    """동아에스티 2022-05 가격 예고 62건의 2024-08 가격 변경: 유효일 2024-08-01, 분류 unsignaled 43 / stay_released 19 그대로,
    late_realization_of로만 연결, reason_source = data+external (사용자 결정 2026-11-15)"""
    r = con.execute("""select e.kind, e.effective_date, e.date_source, e.signal_id is null, e.reason_source, count(*)
                       from event_ledger e join signal_ledger l on l.signal_id = e.late_realization_of
                       where l.issued_month = '2022-05' group by all order by 1""").fetchall()
    assert r == [("stay_released", dt.date(2024, 8, 1), "snapshot_first", True, "data+external", 19),
                 ("unsignaled", dt.date(2024, 8, 1), "snapshot_first", True, "data+external", 43)]
    ev = con.execute("""select min(evidence) from event_ledger where late_realization_of like 'PRICE_PENDING:%:2022-05-04'""").fetchone()[0]
    assert ev.startswith("데이터: ") and "https://www.dailypharm.com/user/news/14837" in ev
    # 2022년 예고의 실현으로 연결하지 않는다: 그 신호의 outcome은 그대로
    assert con.execute("""select count(*) from signal_outcome where signal_id in (select late_realization_of from event_ledger)
                          and outcome <> 'not_realized'""").fetchone()[0] == 0

