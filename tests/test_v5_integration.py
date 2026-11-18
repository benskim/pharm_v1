"""v5 원장 통합 시험 (data/interim/pbd.duckdb, make p1-v5 뒤). 기대값은 docs/RULES_2026-11-17.md 예시·§9 사용자 판정에서 온다."""
from __future__ import annotations

import datetime as dt

import pytest

from pbd.common import DB_PATH, connect

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="pbd.duckdb 없음")


@pytest.fixture(scope="module")
def con():
    c = connect(read_only=True)
    if not c.execute("select count(*) from information_schema.tables where table_name = 'event_ledger_v5'").fetchone()[0]:
        pytest.skip("v5 표 없음 (make p1-v5)")
    yield c
    c.close()


def D(s):
    return dt.date.fromisoformat(s)


def ev(con, sql, *args):
    return con.execute(sql, list(args)).fetchall()


def test_sample17_gap_transfer(con):
    """한국신텍스 몬테루칸스츄정(~2022-04) → 3개월 공백 → 씨엠지(2022-08~), 같은 허가 201905521 = 양도양수(gap 3)"""
    r = ev(con, """select kind, link_basis, gap_months, counterpart from event_ledger_v5
                   where event_type = 'SUCCESSION' and product_code = '648604350' and observed_month = '2022-08'""")
    assert r == [("transfer", "same_item", 3, "623005440")]
    # 옛 코드가 사라진 달 as_known 급여삭제 → revised 양도양수, 정정 이력
    st = ev(con, """select list_month, state_as_known, state_revised from product_state_month_v5
                    where product_code = '623005440' and list_month between '2022-05' and '2022-08' order by 1""")
    assert st == [("2022-05", "delisted", "transferred_out"), ("2022-06", "delisted", "transferred_out"),
                  ("2022-07", "delisted", "transferred_out"), ("2022-08", "transferred_out", "transferred_out")]
    d = ev(con, """select kind, known_month, retracted_month from event_ledger_v5 where event_type = 'DELISTING' and product_code = '623005440'""")
    assert d == [("delisting", "2022-05", "2022-08")]


def test_sample18_delisting_stay(con):
    """가나칸정: 2024-11 급여삭제 → 집행정지 공고 2025-01-31 → 급여 유지 2025-02-01 → 2025-03 삭제 확정, 하나의 사건"""
    r = {(t, k): (m, e, lo, hi, c) for t, k, m, e, lo, hi, c in ev(con, """
        select event_type, kind, observed_month, effective_date, effective_lo, effective_hi, delisting_case_id from event_ledger_v5
        where product_code = '644900310' and event_type in ('DELISTING', 'DELISTING_STAY', 'STAY_START', 'LISTING')""")}
    assert r[("DELISTING", "delisting")][:1] == ("2024-11",)
    assert r[("DELISTING_STAY", "delisting_stay")][:2] == ("2025-02", D("2025-02-01"))
    assert r[("STAY_START", "delisting_target")][1] == D("2025-01-31")
    assert r[("DELISTING", "confirmed_after_stay")][0] == "2025-03"
    assert len({v[4] for k, v in r.items() if k[0] != "STAY_START" or True}) == 1
    assert ("LISTING", "relisting") not in r


def test_sample42_external_lift(con):
    """로코탄정: 중지해제, 급여목록 기준 2024-01-01 → 외부 확인 2024-01-04로 덮어씀. subst_lag 유지"""
    r = ev(con, """select effective_date, date_source from event_ledger_v5 where event_type = 'SUSPEND_LIFT' and product_code = '654700150'""")
    assert r == [(D("2024-01-04"), "external")]
    assert ev(con, "select subst_lag from product_state_month_v5 where product_code = '654700150' and list_month = '2024-01'") == [(True,)]


@pytest.mark.parametrize("code,month,kind,basis,old", [
    ("670608790", "2022-02", "swap", "same_firm_ingredient", "670607300"),     # sample 58 휴메리나 → 휴다트
    ("650701341", "2023-07", "transfer", "same_name", "652105571"),            # sample 60 스트렌식주 한독 → 아스트라제네카
    ("643904070", "2024-11", "transfer", "same_item", "668101450"),            # sample 59 산도스 → 삼일
    ("641608020", "2024-08", "transfer", "same_item", "670609030"),            # sample 12 휴듀오 → 포시로멧
])
def test_succession_examples(con, code, month, kind, basis, old):
    r = ev(con, """select kind, link_basis, counterpart from event_ledger_v5 where event_type = 'SUCCESSION' and product_code = ? and observed_month = ?""",
           code, month)
    assert r == [(kind, basis, old)]


def test_sample12_one_to_one(con):
    """휴시가듀오서방정(휴온스, 허가 202300360)은 휴듀오와 잇지 않는다"""
    r = ev(con, "select new from succession_v5 where old = '670609030'")
    assert r == [("641608020",)]


def test_sample11_spec_delisting(con):
    r = ev(con, """select kind, link_basis from event_ledger_v5 where event_type = 'DELISTING' and product_code = '649508011' and observed_month = '2022-11'""")
    assert r == [("delisting_spec", "item_remains")]


def test_no_resume_signal_type(con):
    """sample 37·38: 기간 중지 종료일을 따로 신호로 판정하지 않음"""
    assert ev(con, "select count(*) from signal_v5 where signal_type like 'RESUME%'") == [(0,)]


@pytest.mark.parametrize("sid,outcome,reason,blocked", [
    ("PRICE_NOTICE:649702730:2023-01-09", "EXCLUDED", "listed_during_suspension", False),   # sample 32
    ("SUSPEND_NOTICE:644502610:2022-01-11", "REALIZED_BY_DELISTING", None, False),               # sample 50
])
def test_signal_examples(con, sid, outcome, reason, blocked):
    r = ev(con, "select outcome, reason, stay_blocked from signal_v5 where signal_id = ?", sid)
    assert r == [(outcome, reason, blocked)]


def test_sample32_check_month(con):
    assert ev(con, "select check_month from signal_v5 where signal_id = 'PRICE_NOTICE:649702730:2023-01-09'") == [("2023-02",)]


def test_sample33_blocked_price(con):
    """대체조제 목록의 22.5.4. 550원을 하루 전 집행정지(22.5.3.)가 막음"""
    r = ev(con, "select stay_blocked, blocking_stays from signal_v5 where signal_id = 'PRICE_NOTICE:642506850:2022-05-04'")
    assert r == [(True, "2022-05-03")]


def test_sample61_blocked_suspension_external(con):
    """집행정지(22.5.9.)가 8월 급여중지 처분을 막음 (외부 확인)"""
    r = ev(con, "select stay_blocked, blocking_stays, stay_target_source from signal_v5 where signal_id = 'SUSPEND_NOTICE:642502811:2022-08-01'")
    assert r == [(True, "2022-05-09", "external")]


def test_gabapentin_two_signals(con):
    """동아가바펜틴캡슐300mg: A 22.5.4. 368원 막힘 → 2024-08 지연 실현, B 24.7.1. 392원 2024-07 실현"""
    r = dict((s, (o, m, b)) for s, o, m, b in ev(con, """select signal_id, outcome, realized_month, stay_blocked from signal_v5
             where product_code = '642500360' and signal_type = 'PRICE_NOTICE'"""))
    assert r["PRICE_NOTICE:642500360:2022-05-04"] == ("REALIZED_LATE", "2024-08", True)
    assert r["PRICE_NOTICE:642500360:2024-07-01"] == ("REALIZED", "2024-07", False)


def test_no_price_change_unless_both_months_reimbursed(con):
    r = ev(con, """select count(*) from event_ledger_v5 e
                   join product_state_month_v5 a on a.product_code = e.product_code and a.list_month = e.observed_month
                   where e.event_type = 'PRICE_CHANGE' and a.state_revised <> 'reimbursed'""")
    assert r == [(0,)]
    # 2024-07 정지 제품 5개의 가격 예고 실현 사례(삼진제약 647801211 등)는 약가 변경 이벤트가 아니다
    assert ev(con, "select count(*) from event_ledger_v5 where event_type = 'PRICE_CHANGE' and product_code = '647801211' and observed_month = '2024-07'") == [(0,)]


def test_scheduled_listing_2025_11(con):
    """F11: 2025-11 `'25.11.23 등재` 27행 = 처음 나타난 제품 17 + 이미 급여 중이던 제품 10"""
    r = dict(ev(con, """select state_as_known, count(*) from product_state_month_v5 p
                       where list_month = '2025-11' and exists (select 1 from note_token t where t.product_code = p.product_code
                             and t.list_month = '2025-11' and t.token_type = 'R1' and t.token_date = '2025-11-23') group by 1"""))
    assert r == {"scheduled_listing": 17, "reimbursed": 10}


def test_transitions_all_allowed(con):
    from pbd.ledger.v5_rules import check_transitions
    rows = ev(con, "select product_code, state_revised from product_state_month_v5 order by product_code, list_month")
    seq, cur = [], None
    for pc, s in rows:
        if pc != cur:
            if seq:
                check_transitions(seq)
            seq, cur = [], pc
        seq.append(s)
    check_transitions(seq)


def test_one_to_one_pairs(con):
    assert ev(con, "select count(*) - count(distinct old), count(*) - count(distinct new) from succession_v5") == [(0, 0)]


def test_as_known_never_uses_later_revision(con):
    """as_known과 revised가 다른 행은 모두 정정 이력에 있다"""
    r = ev(con, """select count(*) from product_state_month_v5 p where revised_differs and not exists
                   (select 1 from state_revision_log_v5 l where l.product_code = p.product_code and l.list_month = p.list_month)""")
    assert r == [(0,)]


def test_stay_then_delisted_12_suspension(con):
    """RULES §6.3: 급여중지 예고 12건 = 동아에스티 기간 중지 11 + 아노핀정 급여정지(22.3.18.) 1, 미실현 stay_then_delisted"""
    r = ev(con, """select count(*), count(*) filter (where reason_external is not null) from signal_v5
                   where signal_type = 'SUSPEND_NOTICE' and outcome = 'NOT_REALIZED' and reason = 'stay_then_delisted'""")
    assert r == [(12, 11)]
    assert ev(con, "select reason from signal_v5 where signal_id = 'SUSPEND_NOTICE:642302580:2022-03-18'") == [("stay_then_delisted",)]


def test_excluded_target_gone_only_unblocked(con):
    """§6.2·§11-9: '대상 소멸'(판정 제외)은 집행정지 없이 예정일 전에 삭제된 경우에만.
    파로마정·심바스트씨알정·유나신정375mg·뉴트리플렉스48주사 4건: 이벤트는 급여삭제, reason_external = 허가 자진취하"""
    assert ev(con, "select count(*) from signal_v5 where reason = 'target_gone' and stay_blocked") == [(0,)]
    r = ev(con, """select count(*) from signal_v5 where outcome = 'EXCLUDED' and reason = 'target_gone' and exclude_reason = '대상 소멸'
                   and reason_external like '허가 자진취하%' and product_code in ('052700390', '643501450', '648900640', '667400020')""")
    assert r == [(4,)]
    d = ev(con, """select count(distinct product_code) from event_ledger_v5 where event_type = 'DELISTING' and retracted_month is null
                   and product_code in ('052700390', '643501450', '648900640', '667400020')""")
    assert d == [(4,)]


@pytest.mark.parametrize("old,new", [("670105380", "653006820"), ("670105260", "653006840")])
def test_azeria_recode(con, old, new):
    """§3-1 ★: (주)한국파마 아제리아정16·8밀리그램, 같은 허가·업체·제품명·규격·금액 → 코드 변경"""
    r = ev(con, "select kind from event_ledger_v5 where event_type = 'SUCCESSION' and product_code = ? and counterpart = ?", new, old)
    assert r == [("recode",)]


def test_sample25_changed_date(con):
    """§11-1: 변경 실현 유효일 = 실현 금액을 알린 비고의 날짜 (sample 25 → 2025-11-20)"""
    r = ev(con, """select outcome, effective_date from signal_v5 where signal_id = 'PRICE_NOTICE:663300880:2023-09-01:S_AMT'""")
    assert r == [("REALIZED_CHANGED", D("2025-11-20"))]
    assert ev(con, "select effective_date from event_ledger_v5 where event_type = 'PRICE_CHANGE' and product_code = '663300880'") == [(D("2025-11-20"),)]


def test_batch_month_counts_stored(con):
    """§3 ★: 달마다 n_products·n_ingredients 저장. 2024-11은 287건이지만 성분 13개 → batch 아님"""
    r = ev(con, "select n_products, n_ingredients, batch from batch_month_v5 where observed_month = '2024-11'")
    assert r == [(287, 13, False)]
    assert ev(con, "select count(*) from batch_month_v5 where batch <> (n_ingredients >= min_c4)") == [(0,)]
    # batch는 예고 미관측 변경에만
    assert ev(con, "select count(*) from event_ledger_v5 where event_type = 'PRICE_CHANGE' and kind = 'signaled' and batch") == [(0,)]


def test_substitute_higher_not_signal_696300550(con):
    """§1: 696300550 (2024-10)은 대체조제 금액이 더 높고 새 집행정지가 없어 신호가 아니다. 금액 차이 신호는 74건"""
    assert ev(con, "select count(*) from signal_v5 where product_code = '696300550' and source_rule = 'S_AMT'") == [(0,)]
    assert ev(con, "select count(*) from signal_v5 where source_rule = 'S_AMT'") == [(74,)]


def test_stay_record_fields(con):
    """§5: sample 33 — 집행정지(22.5.3.)의 대상은 대체조제 목록의 22.5.4. 550원 인하 (target_source = external 설정)"""
    r = ev(con, """select target_type, target_source from stay_v5 where product_code = '642506850' and stay_date = '2022-05-03'""")
    assert r == [("price_cut", "external")]
    assert ev(con, "select blocking_stay_id from signal_v5 where signal_id = 'PRICE_NOTICE:642506850:2022-05-04'") == [("STAY:642506850:2022-05-03",)]
    assert ev(con, "select count(*) from stay_v5 where target_type is null") == [(0,)]



def test_excluded_reasons_three(con):
    """판정 제외 사유 3종 (사용자 결정 2026-11-18): 급여중지 중 예고 6 / 인하일 전 급여중지 = 신플랙스정 / 대상 소멸 = 파로마정 등 4건"""
    r = dict(ev(con, "select exclude_reason, count(*) from signal_v5 where outcome = 'EXCLUDED' group by 1"))
    assert r == {"급여중지 중 예고": 6, "인하일 전 급여중지": 1, "대상 소멸": 4}
    assert ev(con, "select reason from signal_v5 where signal_id = 'PRICE_NOTICE:697100360:2024-03-01'") == [("suspended_before_price_date",)]
