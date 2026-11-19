"""RULES_2026-11-17 (v5) 순수 함수 시험. 기대값은 docs/RULES_2026-11-17.md의 예시와 날짜·금액에서 가져온다 (구현 출력으로 만들지 않음).

§8 목록: 각 결과값, 코드 이어받기 3종과 1:1, 같은 허가의 공백 있는 양도양수(as_known 급여삭제 → revised 양도양수), 규격 단위 급여삭제,
삭제 집행정지, 기간 중지 = 신호 하나, 소급 정정, 한 달 누락, 집행정지 중 별도 일괄 인하, 대체조제 목록의 막힌 처분 금액,
외부 날짜 덮어쓰기, 전이표 0건 칸.
"""
from __future__ import annotations

import datetime as dt

import pytest

from pbd.ledger.v5_rules import (
    Life, Obs, Tok, check_transitions, derive_signals, derive_status, derive_stays, exit_reentry_events, flag_batch,
    link_codes, omission_rate, price_events, price_type, rates, state_of, stay_records, wilson,
)


# ---------------------------------------------------------------- 도우미
def cal(a: str, b: str) -> list[str]:
    y, m = int(a[:4]), int(a[5:])
    out = []
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def D(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def B(price=100.0, *toks, in_s=True, price_s="same", unit="정") -> Obs:
    """급여목록에 있는 달. 토큰 src 기본 B."""
    return Obs(in_b=True, in_s=in_s, price_b=price, price_s=price if price_s == "same" else price_s, unit=unit, toks=tuple(toks))


def R2(d, amt, src="B"):
    return Tok("R2", D(d), amount=amt, src=src)


def R3(d, src="B"):
    return Tok("R3", D(d), src=src)


def R3b(a, b, src="B"):
    return Tok("R3b", D(a), D(b), src=src)


def R4(d):
    return Tok("R4", D(d), src="B")


def run(months, obs, **kw):
    st = derive_status(months, obs, external_lift=kw.pop("external_lift", None))
    stays = derive_stays(months, obs)
    sigs = derive_signals("P", months, obs, st, stays, **kw)
    return st, stays, {(s.signal_type, s.signal_date): s for s in sigs}


# ================================================================ 상태 (§1·§2·§4)
def test_status_benefit_only_substitute_mark_is_subst_lag():
    """§1: 상태는 급여목록만. 대체조제에만 남은 급여정지 표시 → 급여 + subst_lag"""
    m = cal("2023-11", "2024-02")
    obs = {"2023-11": B(100, R3("2022-11-01")), "2023-12": B(100, R3("2022-11-01")),
           "2024-01": B(100, R3("2022-11-01", src="S")), "2024-02": B(100)}
    st = derive_status(m, obs)
    assert state_of(st.rows["2023-12"], "revised") == "suspended"
    assert state_of(st.rows["2024-01"], "as_known") == "reimbursed"
    assert st.rows["2024-01"].subst_lag is True


def test_two_month_lift_effective_first_gap_month():
    """§4.1: 급여목록에서 두 달 연속 없으면 중지해제, 유효일 = 처음 없어진 달 1일 (근거 list_only)"""
    m = cal("2023-11", "2024-03")
    obs = {"2023-11": B(100, R3("2022-11-01")), "2023-12": B(100, R3("2022-11-01")),
           "2024-01": B(100), "2024-02": B(100), "2024-03": B(100)}
    st = derive_status(m, obs)
    lifts = [e for e in st.events if e.event_type == "SUSPEND_LIFT"]
    assert len(lifts) == 1
    assert lifts[0].effective_date == D("2024-01-01") and lifts[0].kind == "list_only"
    assert lifts[0].known_month == "2024-02"
    assert state_of(st.rows["2024-01"], "as_known") == state_of(st.rows["2024-01"], "revised") == "reimbursed"


def test_external_lift_date_overrides(  ):
    """§4.4 sample 42 로코탄정: 급여목록 기준 2024-01-01 → 외부 확인 2024-01-04로 덮어씀. subst_lag 유지 (대체조제는 2024-01까지 표시)"""
    m = cal("2023-11", "2024-03")
    mark = "2022-11-01"
    obs = {"2023-11": B(100, R3(mark), R3(mark, "S")), "2023-12": B(100, R3(mark), R3(mark, "S")),
           "2024-01": B(100, R3(mark, "S")), "2024-02": B(100), "2024-03": B(100)}
    st = derive_status(m, obs, external_lift={D(mark): D("2024-01-04")})
    lift = [e for e in st.events if e.event_type == "SUSPEND_LIFT"][0]
    assert lift.effective_date == D("2024-01-04") and lift.date_source == "external"
    assert st.rows["2024-01"].subst_lag is True
    # 1월 1일에는 아직 급여중지 (외부 해제일 1/4): as_known은 급여, revised는 급여중지 → 정정 이력
    assert state_of(st.rows["2024-01"], "as_known") == "reimbursed"
    assert state_of(st.rows["2024-01"], "revised") == "suspended"
    assert any(r.month == "2024-01" and r.reason == "external_date" for r in st.revisions)


def test_one_month_omission_keeps_suspension():
    """§4.2 한 달 누락 (S0 시험 사례 644914120: 2022-11 1개월 공백 뒤 같은 정지일로 재등장) → 급여중지 유지, 이벤트 없음"""
    m = cal("2022-09", "2023-01")
    obs = {"2022-09": B(100, R3("2022-03-01")), "2022-10": B(100, R3("2022-03-01")), "2022-11": B(100),
           "2022-12": B(100, R3("2022-03-01")), "2023-01": B(100, R3("2022-03-01"))}
    st = derive_status(m, obs)
    assert state_of(st.rows["2022-11"], "as_known") == "reimbursed"       # 그 시점에는 표시 없음
    assert state_of(st.rows["2022-11"], "revised") == "suspended"         # 소급 정정
    assert [r.reason for r in st.revisions if r.month == "2022-11"] == ["list_omission"]
    assert not [e for e in st.events if e.event_type in ("SUSPEND_LIFT", "SUSPEND")]


def test_mark_gone_then_delisted_is_retro_suspended():
    """§4.3 사라진 다음 달에 삭제되면 지난달을 급여중지로 소급 (현재 데이터 0건, 합성 시험)"""
    m = cal("2023-01", "2023-04")
    obs = {"2023-01": B(100, R3("2022-03-01")), "2023-02": B(100), "2023-03": Obs(), "2023-04": Obs()}
    st = derive_status(m, obs)
    assert state_of(st.rows["2023-02"], "as_known") == "reimbursed"
    assert state_of(st.rows["2023-02"], "revised") == "suspended"
    assert [r.reason for r in st.revisions if r.month == "2023-02"] == ["delisted_after_mark_gone"]
    assert not [e for e in st.events if e.event_type == "SUSPEND_LIFT"]


def test_period_suspension_status_by_dates():
    """기간 중지는 비고 유무가 아니라 날짜로: 종료일 뒤 비고가 남아도 급여 (NOTE_RULES R3b 예: 643702980 2023-02까지 남음)"""
    m = cal("2022-08", "2022-11")
    p = R3b("2022-08-01", "2022-08-31")
    obs = {"2022-08": B(100, p), "2022-09": B(100, p), "2022-10": B(100, p), "2022-11": B(100)}
    st = derive_status(m, obs)
    assert state_of(st.rows["2022-08"], "revised") == "suspended"
    assert state_of(st.rows["2022-09"], "revised") == "reimbursed"
    lift = [e for e in st.events if e.event_type == "SUSPEND_LIFT"][0]
    assert lift.effective_date == D("2022-09-01") and lift.kind == "period_end"


def test_scheduled_listing_new_vs_existing():
    """R1 예: 2025-11 `25.11.23 등재` — 처음 관측된 제품은 등재 예정, 이미 급여 중인 제품은 급여 유지 (LISTING_NOTE_EXISTING)"""
    m = cal("2025-10", "2025-12")
    r1 = Tok("R1", D("2025-11-23"), src="S")
    new = {"2025-10": Obs(), "2025-11": B(100, r1), "2025-12": B(100)}
    old = {"2025-10": B(100), "2025-11": B(100, r1), "2025-12": B(100)}
    st_new = derive_status(m, new)
    st_old = derive_status(m, old)
    assert state_of(st_new.rows["2025-11"], "as_known") == "scheduled_listing"
    assert state_of(st_old.rows["2025-11"], "as_known") == "reimbursed"
    _, _, s_new = run(m, new)
    _, _, s_old = run(m, old)
    assert s_new[("LISTING_NOTICE", D("2025-11-23"))].outcome == "REALIZED"
    assert ("LISTING_NOTE_EXISTING", D("2025-11-23")) in s_old


# ================================================================ 약가 유형 (§2, §10 Q1)
@pytest.mark.parametrize("unit,price,toks,expect", [
    ("mCi", 100.0, (), ("minimal_unit", "unit_bare_measure", False)),
    ("mCi/kit", 100.0, (), ("minimal_unit", "unit_bare_measure", False)),   # RULES §2-1
    ("mL/병", 100.0, (), ("production_unit", "default_majority", False)),   # 용기 표기 → 제외
    ("mL", 100.0, (), ("minimal_unit", "unit_bare_measure", False)),
    ("회", 100.0, (), ("minimal_unit", "unit_bare_measure", False)),
    ("정", 100.0, (), ("production_unit", "default_majority", False)),
    ("I.U/병", 100.0, (), ("production_unit", "default_majority", True)),
    ("L/통", 100.0, (), ("production_unit", "default_majority", True)),
    ("g/백", 100.0, (), ("production_unit", "default_majority", True)),
    ("정", None, (Tok("R7", None),), ("unpriced", "r7", False)),
])
def test_price_type(unit, price, toks, expect):
    assert price_type(Obs(in_b=True, price_b=price, unit=unit, toks=toks)) == expect


# ================================================================ 약가 축 (§3)
def test_no_price_event_during_suspension_and_lift_carries_prices():
    """§3: 급여중지 중 금액 변화는 이벤트 아님. 중지해제에 중지 전 금액 → 해제 후 금액 (656700050: 정지 중 523→520, 520으로 복귀)"""
    m = cal("2024-05", "2024-10")
    s = R3("2022-10-20")
    obs = {"2024-05": B(523), "2024-06": B(523, s), "2024-07": B(520, s), "2024-08": B(520, s),
           "2024-09": B(520), "2024-10": B(520)}
    st, stays, sigs = run(m, obs)
    pe = price_events("P", m, obs, st, list(sigs.values()))
    assert pe == []
    lift = [e for e in st.events if e.event_type == "SUSPEND_LIFT"][0]
    assert (lift.price_before, lift.price_after) == (523, 520)


def test_unobserved_notice_change():
    """비고 미기재 약가 변경(비고의 예고와 연결되지 않은 약가 변경) (sample 28: 2024-10 893 → 2024-11 858, 비고 없음, 유효일 2024-11-01)"""
    m = cal("2024-09", "2024-11")
    obs = {"2024-09": B(893), "2024-10": B(893), "2024-11": B(858)}
    st, stays, sigs = run(m, obs)
    pe = price_events("P", m, obs, st, list(sigs.values()))
    assert [(e.kind, e.observed_month, e.effective_date) for e in pe] == [("unobserved_notice", "2024-11", D("2024-11-01"))]


def test_batch_flag_and_stored_counts():
    """§3 ★·§11-2: 한 달 안에 비고 미기재 약가 변경이 걸친 성분이 50개 이상이면 그달의 비고 미기재 약가 변경을 batch. 판정에 쓰지 않음.
    달마다 n_products·n_ingredients 저장 (2024-11: 287건이지만 성분 13개 → batch 아님)"""
    rows = [dict(key=i, month="2023-09", c4=f"{i:04d}", kind="unobserved_notice") for i in range(50)]
    rows += [dict(key=60, month="2023-09", c4="9999", kind="signaled")]
    rows += [dict(key=100 + i, month="2024-11", c4=f"{i % 13:04d}", kind="unobserved_notice") for i in range(287)]
    flagged, stats = flag_batch(rows, min_c4=50, min_products=0)
    assert flagged == set(range(50))                      # 예고 실현(60)은 표시하지 않음
    assert stats["2023-09"] == dict(n_products=50, n_ingredients=50, batch=True)
    assert stats["2024-11"] == dict(n_products=287, n_ingredients=13, batch=False)


# ================================================================ 신호 결과값 (§6)
def test_outcome_realized_price():
    """R2 예 `24.9.2., 453원`: 다음 스냅샷 금액 = 예고 금액 → 실현, 유효일 = 예정일"""
    m = cal("2024-08", "2024-11")
    obs = {"2024-08": B(589), "2024-09": B(589, R2("2024-09-02", 453)), "2024-10": B(453), "2024-11": B(453)}
    st, stays, sigs = run(m, obs)
    s = sigs[("PRICE_NOTICE", D("2024-09-02"))]
    assert (s.outcome, s.check_month, s.realized_month, s.effective_date) == ("REALIZED", "2024-10", "2024-10", D("2024-09-02"))
    pe = price_events("P", m, obs, st, list(sigs.values()))
    assert [(e.kind, e.signal_id, e.effective_date) for e in pe] == [("signaled", s.signal_id, D("2024-09-02"))]


def test_outcome_changed_price():
    """변경 실현: 금액이 다르게 실현. 유효일은 최초 예고 비고의 날짜"""
    m = cal("2024-08", "2024-10")
    obs = {"2024-08": B(589), "2024-09": B(589, R2("2024-09-02", 453)), "2024-10": B(460)}
    _, _, sigs = run(m, obs)
    s = sigs[("PRICE_NOTICE", D("2024-09-02"))]
    # §6.4 (★2026-11-18 개정): 유효일은 실현된 금액을 알린 비고의 날짜, 없으면 실현 달 1일 (최초 예고 날짜가 아님)
    assert (s.outcome, s.effective_date, s.amount_gap) == ("REALIZED_CHANGED", D("2024-10-01"), 7)


def test_changed_realization_date_from_announcing_note():
    """§6.4 ★: 실현 달(또는 직전 달) 비고가 실현된 금액을 알리면 그 날짜"""
    m = cal("2024-08", "2024-10")
    obs = {"2024-08": B(589), "2024-09": B(589, R2("2024-09-02", 453)), "2024-10": B(460, R2("2024-09-20", 460))}
    _, _, sigs = run(m, obs)
    s = sigs[("PRICE_NOTICE", D("2024-09-02"))]
    assert (s.outcome, s.effective_date) == ("REALIZED_CHANGED", D("2024-09-20"))


def test_outcome_delayed_suspension_date_later():
    """지연 실현: 확인 비고의 날짜가 늦음 (sample 52: 2022-09 `급여정지(22.9.8.)` → 2022-10 `급여정지(22.9.9.)`)"""
    m = cal("2022-08", "2022-11")
    obs = {"2022-08": B(100), "2022-09": B(100, R3("2022-09-08")), "2022-10": B(100, R3("2022-09-09")),
           "2022-11": B(100, R3("2022-09-09"))}
    st, _, sigs = run(m, obs)
    s = sigs[("SUSPEND_NOTICE", D("2022-09-08"))]
    assert (s.outcome, s.check_month, s.date_gap_days, s.effective_date) == ("REALIZED_LATE", "2022-10", 1, D("2022-09-09"))
    susp = [e for e in st.events if e.event_type == "SUSPEND"]
    assert [e.effective_date for e in susp] == [D("2022-09-09")]


def test_outcome_delayed_after_check_month_without_stay():
    """지연 실현: 확인 스냅샷보다 뒤 달에 실현 (비고가 남아 있는 동안 분석 끝까지 추적, §10 Q4)"""
    m = cal("2024-08", "2024-12")
    n = R2("2024-09-02", 453)
    obs = {"2024-08": B(589), "2024-09": B(589, n), "2024-10": B(589, n), "2024-11": B(453), "2024-12": B(453)}
    _, _, sigs = run(m, obs)
    s = sigs[("PRICE_NOTICE", D("2024-09-02"))]
    assert (s.outcome, s.realized_month, s.stay_blocked) == ("REALIZED_LATE", "2024-11", False)


def test_outcome_realized_by_delisting():
    """sample 50: 급여정지(22.1.11) 예고 뒤 2022-02 급여삭제 → 삭제로 실현"""
    m = cal("2022-01", "2022-03")
    obs = {"2022-01": B(100, R3("2022-01-11")), "2022-02": Obs(), "2022-03": Obs()}
    _, _, sigs = run(m, obs)
    s = sigs[("SUSPEND_NOTICE", D("2022-01-11"))]
    assert (s.outcome, s.check_month) == ("REALIZED_BY_DELISTING", "2022-02")


def test_outcome_not_realized_note_gone():
    """미실현: 변화 없이 비고만 사라짐"""
    m = cal("2025-05", "2025-08")
    obs = {"2025-05": B(100), "2025-06": B(100, R3("2025-06-24")), "2025-07": B(100), "2025-08": B(100)}
    _, _, sigs = run(m, obs)
    s = sigs[("SUSPEND_NOTICE", D("2025-06-24"))]
    assert (s.outcome, s.reason, s.stay_blocked) == ("NOT_REALIZED", "note_gone", False)


def test_outcome_listed_during_suspension():
    """sample 32: 급여정지(22.12.28.) 중 기재된 인하 예고 → 판정 불가(중지 중 기재). 신호일 2023-01-09, 확인 달 2023-02"""
    m = cal("2022-11", "2023-03")
    obs = {"2022-11": B(267), "2022-12": B(267, R3("2022-12-28")),
           "2023-01": B(267, R3("2022-12-28"), R2("2023-01-09", 240)), "2023-02": Obs(), "2023-03": Obs()}
    _, _, sigs = run(m, obs)
    s = sigs[("PRICE_NOTICE", D("2023-01-09"))]
    assert (s.outcome, s.reason, s.check_month) == ("EXCLUDED", "listed_during_suspension", "2023-02")
    assert sigs[("SUSPEND_NOTICE", D("2022-12-28"))].outcome == "REALIZED"


def test_outcome_applied_during_suspension_via_parent():
    """§6.1 중지 예고에 딸린 약가 예고(가격일 ≥ 중지일): 부모 중지가 실현돼 가격일에 급여중지 중이면 판정 불가(중지 중 적용)
    (697100360 비고 형식 `2024.3.1., 94원, 급여정지(24.2.6.)`)"""
    m = cal("2024-01", "2024-04")
    obs = {"2024-01": B(110), "2024-02": B(110, R2("2024-03-01", 94), R3("2024-02-06")),
           "2024-03": B(94, R3("2024-02-06")), "2024-04": B(94, R3("2024-02-06"))}
    _, _, sigs = run(m, obs)
    p = sigs[("PRICE_NOTICE", D("2024-03-01"))]
    parent = sigs[("SUSPEND_NOTICE", D("2024-02-06"))]
    assert p.parent_signal_id == parent.signal_id
    assert parent.outcome == "REALIZED"
    assert (p.outcome, p.reason) == ("EXCLUDED", "suspended_before_price_date")     # 판정 제외 사유: 인하일 전 급여중지 (2026-11-18)


def test_parent_blocked_child_judged_normally():
    """부모 중지가 집행정지로 막혀 급여가 유지되면 딸린 약가 예고는 정상 판정"""
    m = cal("2024-01", "2024-04")
    obs = {"2024-01": B(110), "2024-02": B(110, R2("2024-03-01", 94), R3("2024-02-06")),
           "2024-03": B(94, R4("2024-02-20")), "2024-04": B(94, R4("2024-02-20"))}
    _, _, sigs = run(m, obs)
    parent = sigs[("SUSPEND_NOTICE", D("2024-02-06"))]
    assert parent.stay_blocked is True
    assert sigs[("PRICE_NOTICE", D("2024-03-01"))].outcome == "REALIZED"


def test_outcome_ended_by_delisting():
    """§6.2: 확인 시점에 제품 없음, 이어지는 코드 없음 → 약가 예고는 판정 불가(삭제로 종료)"""
    m = cal("2024-08", "2024-10")
    obs = {"2024-08": B(589), "2024-09": B(589, R2("2024-09-02", 453)), "2024-10": Obs()}
    _, _, sigs = run(m, obs)
    s = sigs[("PRICE_NOTICE", D("2024-09-02"))]
    # §6.2·§11-9: 판정 제외(대상 소멸) — 파로마정·심바스트씨알정·유나신정375mg·뉴트리플렉스48주사 유형
    assert (s.outcome, s.reason) == ("EXCLUDED", "target_gone")


def test_successor_code_is_followed():
    """§6.2: 확인 시점에 제품 없음 → 코드 이어받기 추적 → 새 코드로 판정"""
    m = cal("2024-08", "2024-10")
    obs = {"2024-08": B(589), "2024-09": B(589, R2("2024-09-02", 453)), "2024-10": Obs()}
    succ_obs = {"2024-08": Obs(), "2024-09": Obs(), "2024-10": B(453)}
    succ = {"2024-10": ("Q", succ_obs, derive_status(m, succ_obs))}
    _, _, sigs = run(m, obs, successors=succ)
    s = sigs[("PRICE_NOTICE", D("2024-09-02"))]
    assert (s.outcome, s.successor_code) == ("REALIZED", "Q")


def test_outcome_stay_ongoing_and_insufficient():
    m = cal("2025-09", "2025-12")
    blocked = {"2025-09": B(500), "2025-10": B(500, R2("2025-11-01", 450)), "2025-11": B(500, R4("2025-10-30")),
               "2025-12": B(500, R4("2025-10-30"))}
    _, _, sigs = run(m, blocked)
    s = sigs[("PRICE_NOTICE", D("2025-11-01"))]
    assert (s.outcome, s.current_status, s.stay_blocked) == ("CENSORED", "ONGOING_STAY", True)
    late = {"2025-09": B(500), "2025-10": B(500), "2025-11": B(500), "2025-12": B(500, R2("2026-01-01", 450))}
    _, _, sigs = run(m, late)
    s = sigs[("PRICE_NOTICE", D("2026-01-01"))]
    assert (s.outcome, s.current_status) == ("CENSORED", "AWAITING")


def test_outcome_not_realized_after_stay_end():
    """§6.4 미실현: 급여중지 처분이 집행정지 종료 뒤에도 시행되지 않은 경우, 사유 '집행정지 종료 후 미시행'"""
    m = cal("2023-01", "2023-06")
    obs = {"2023-01": B(100), "2023-02": B(100, R3("2023-03-01")), "2023-03": B(100, R4("2023-02-25")),
           "2023-04": B(100, R4("2023-02-25")), "2023-05": B(100), "2023-06": B(100)}
    _, stays, sigs = run(m, obs)
    s = sigs[("SUSPEND_NOTICE", D("2023-03-01"))]
    assert (s.outcome, s.reason, s.stay_blocked, s.stay_end_month) == ("NOT_REALIZED", "stay_ended_not_executed", True, "2023-05")


def test_preceding_stay_does_not_block():
    """§5 대상 추론: 최초 기재 이전부터 있던 집행정지는 그 처분을 막지 않는다"""
    m = cal("2024-04", "2024-07")
    st0 = R4("2018-10-06")
    obs = {"2024-04": B(500, st0), "2024-05": B(500, st0, R2("2024-06-01", 450)), "2024-06": B(500, st0), "2024-07": B(500, st0)}
    _, _, sigs = run(m, obs)
    s = sigs[("PRICE_NOTICE", D("2024-06-01"))]
    assert (s.outcome, s.reason, s.stay_blocked) == ("NOT_REALIZED", "note_gone", False)


def test_separate_batch_cut_during_stay_gabapentin():
    """§5 예: 동아가바펜틴캡슐300mg — A `22.5.4., 368원`(대체조제) 집행정지 → B `24.7.1., 392원` 일괄 인하 2024-07 실현
    → 집행정지 종료 후 A 2024-08 지연 실현. 22.5.3. 집행정지는 A의 최초 기재 달에 새로 붙음, 18.10.6.은 앞선 집행정지"""
    m = cal("2022-04", "2024-09")
    a = R2("2022-05-04", 368, src="S")
    old, new = R4("2018-10-06"), R4("2022-05-03")
    obs = {}
    for mo in m:
        if mo == "2022-04":
            obs[mo] = B(393, old)
        elif mo == "2022-05":
            obs[mo] = B(393, new, a, price_s=393)
        elif mo < "2024-06":
            obs[mo] = B(393, new)
        elif mo == "2024-06":
            obs[mo] = B(393, new, R2("2024-07-01", 392))
        elif mo == "2024-07":
            obs[mo] = B(392, new)
        else:
            obs[mo] = B(368)
    st, stays, sigs = run(m, obs)
    sa, sb = sigs[("PRICE_NOTICE", D("2022-05-04"))], sigs[("PRICE_NOTICE", D("2024-07-01"))]
    assert (sa.stay_blocked, sa.blocking_stays, sa.outcome, sa.realized_month) == (True, (D("2022-05-03"),), "REALIZED_LATE", "2024-08")
    assert (sb.stay_blocked, sb.outcome, sb.realized_month, sb.effective_date) == (False, "REALIZED", "2024-07", D("2024-07-01"))
    pe = price_events("P", m, obs, st, list(sigs.values()))
    assert [(e.observed_month, e.signal_id) for e in pe] == [("2024-07", sb.signal_id), ("2024-08", sa.signal_id)]
    ends = {s.date: s.end_month for s in stays}
    assert ends[D("2022-05-03")] == "2024-08"


def test_external_stay_target_overrides_timing():
    """sample 61: 집행정지(22.5.9.)가 8월 급여중지 처분을 막음 (외부 확인). 22.5.3.이 먼저 끝나도 22.5.9.가 남으면 막힘 유지"""
    m = cal("2022-04", "2022-12")
    p = R3b("2022-08-01", "2022-08-31")
    a, b = R4("2022-05-03"), R4("2022-05-09")
    obs = {"2022-04": B(100), "2022-05": B(100, a, p), "2022-06": B(100, a, b), "2022-07": B(100, a, b),
           "2022-08": B(100, b), "2022-09": B(100, b), "2022-10": B(100, b), "2022-11": B(100, b), "2022-12": B(100, b)}
    _, _, sigs = run(m, obs, external_targets={("SUSPEND_NOTICE", D("2022-08-01")): (D("2022-05-09"),)})
    s = sigs[("SUSPEND_NOTICE", D("2022-08-01"))]
    assert (s.stay_blocked, s.blocking_stays, s.stay_target_source) == (True, (D("2022-05-09"),), "external")
    assert (s.outcome, s.current_status) == ("CENSORED", "ONGOING_STAY")


def test_substitute_blocked_amount_signal():
    """§1: 같은 달 대체조제 금액 ≠ 급여목록 금액 + 급여목록에 새 집행정지 → 대체조제 금액을 막힌 인하 처분 금액으로 신호 (최초 기재월 = 그 달)"""
    m = cal("2024-09", "2024-12")
    st = R4("2024-09-27")
    obs = {"2024-09": B(1000), "2024-10": B(1000, st, price_s=900), "2024-11": B(1000, st), "2024-12": B(1000, st)}
    _, _, sigs = run(m, obs)
    blocked = [s for s in sigs.values() if s.source_rule == "S_AMT"]
    assert len(blocked) == 1
    s = blocked[0]
    assert (s.signal_type, s.first_month, s.amount, s.stay_blocked) == ("PRICE_NOTICE", "2024-10", 900, True)


def test_two_lists_merge_into_one_signal():
    """P4: 같은 제품·유형·날짜가 두 목록에 함께 나오면 신호 하나"""
    m = cal("2024-08", "2024-10")
    obs = {"2024-08": B(589), "2024-09": B(589, R2("2024-09-02", 453), R2("2024-09-02", 453, src="S")), "2024-10": B(453)}
    _, _, sigs = run(m, obs)
    s = sigs[("PRICE_NOTICE", D("2024-09-02"))]
    assert s.source_list == ("benefit", "substitute") and len(sigs) == 1


# ================================================================ 기간 중지 = 신호 하나 (§6.1)
def test_period_suspension_single_signal_realized():
    """기간 중지는 신호 하나. 실현되면 급여중지(시작일)와 중지해제(종료일 다음 날) 이벤트 2개"""
    m = cal("2022-05", "2022-10")
    p = R3b("2022-08-01", "2022-08-31")
    obs = {"2022-05": B(100, p), "2022-06": B(100, p), "2022-07": B(100, p), "2022-08": B(100, p),
           "2022-09": B(100), "2022-10": B(100)}
    st, _, sigs = run(m, obs)
    assert list(sigs) == [("SUSPEND_NOTICE", D("2022-08-01"))]
    assert sigs[("SUSPEND_NOTICE", D("2022-08-01"))].outcome == "REALIZED"
    ev = sorted((e.event_type, e.effective_date) for e in st.events)
    assert ev == [("SUSPEND", D("2022-08-01")), ("SUSPEND_LIFT", D("2022-09-01"))]


def test_period_suspension_single_signal_blocked():
    """sample 37 리피논정: 8월 급여중지 처분이 집행정지로 막힘 → 신호 전체가 막힘. 종료일을 따로 신호로 판정하지 않음"""
    m = cal("2022-04", "2022-10")
    p = R3b("2022-08-01", "2022-08-31")
    a, b = R4("2022-05-03"), R4("2022-05-09")
    obs = {"2022-04": B(100), "2022-05": B(100, a, p), "2022-06": B(100, a, b), "2022-07": B(100, a, b),
           "2022-08": B(100, a, b), "2022-09": B(100, a, b), "2022-10": B(100, a, b)}
    st, _, sigs = run(m, obs)
    assert [k[0] for k in sigs] == ["SUSPEND_NOTICE"]
    s = sigs[("SUSPEND_NOTICE", D("2022-08-01"))]
    assert s.stay_blocked is True and (s.outcome, s.current_status) == ("CENSORED", "ONGOING_STAY")
    assert not [e for e in st.events if e.event_type in ("SUSPEND", "SUSPEND_LIFT")]


# ================================================================ 코드 이어받기 (§3-1)
def life(months, firm, name, ing, item, prev=()):
    return Life(months=tuple(months), firm_first=firm, firm_last=firm, name_first=name, name_last=name,
                ing_first=ing, ing_last=ing, item=item, prev_codes=frozenset(prev))


def test_link_transfer_same_item_sample59():
    """sample 59 산도스에스시탈로프람 한국산도스 → 삼일제약 (같은 허가) = 양도양수"""
    m = cal("2024-08", "2025-02")
    lives = {"668101450": life(cal("2024-08", "2024-10"), "한국산도스(주)", "산도스에스시탈로프람정5밀리그램", "474801ATB", "201501051"),
             "643904070": life(cal("2024-11", "2025-02"), "삼일제약(주)", "산도스에스시탈로프람정5밀리그램", "474801ATB", "201501051")}
    r = link_codes(m, lives)
    assert [(p.old, p.new, p.kind, p.link_basis, p.gap_months) for p in r.pairs] == [("668101450", "643904070", "transfer", "same_item", 0)]
    assert r.exits[("668101450", "2024-11")].kind == "succession"


def test_link_recode_same_firm_same_item():
    m = cal("2024-01", "2024-04")
    lives = {"A": life(cal("2024-01", "2024-02"), "갑", "가정", "111101ATB", "I1"),
             "B": life(cal("2024-03", "2024-04"), "갑", "가정", "111101ATB", "I1")}
    r = link_codes(m, lives)
    assert [(p.kind, p.link_basis) for p in r.pairs] == [("recode", "same_item")]


def test_link_swap_sample58():
    """sample 58 휴메리나 → 휴다트 (휴온스, 같은 성분·함량·금액, 다른 허가, 다음 달 이어짐) = 교체 등재"""
    m = cal("2022-01", "2022-03")
    lives = {"670607300": life(["2022-01"], "(주)휴온스", "휴메리나연질캡슐0.5밀리그램(두타스테리드)", "458801ACS", "201506729"),
             "670608790": life(cal("2022-02", "2022-03"), "(주)휴온스", "휴다트연질캡슐0.5밀리그램(두타스테리드)", "458801ACS", "202106074")}
    r = link_codes(m, lives)
    assert [(p.old, p.new, p.kind, p.link_basis) for p in r.pairs] == [("670607300", "670608790", "swap", "same_firm_ingredient")]


def test_link_transfer_by_name_sample60():
    """sample 60 스트렌식주 한독 → 아스트라제네카 (같은 제품명·성분, 기간 빈틈없이 이어짐, 한독 코드는 품목기준코드 연결 없음) = 양도양수"""
    m = cal("2023-05", "2023-08")
    nm = "스트렌식주40mg/mL(아스포타제알파)"
    lives = {"652105571": life(cal("2023-05", "2023-06"), "(주)한독", nm, "650402BIJ", None),
             "650701341": life(cal("2023-07", "2023-08"), "한국아스트라제네카(주)", nm, "650402BIJ", "201600826")}
    r = link_codes(m, lives)
    assert [(p.old, p.new, p.kind, p.link_basis) for p in r.pairs] == [("652105571", "650701341", "transfer", "same_name")]


def test_link_one_to_one_sample12():
    """sample 12: 휴듀오서방정(휴온스, 허가 202300356) → 같은 허가를 이은 대웅제약 포시로멧서방정 = 양도양수 (순서 1).
    같은 시기 휴온스 휴시가듀오서방정(허가 202300360)은 별개 허가 품목이라 휴듀오와 잇지 않는다"""
    m = cal("2024-05", "2024-09")
    ing = "639800ATR"
    lives = {"670402400": life(cal("2024-05", "2024-06"), "(주)휴온스메디텍", "휴시가듀오서방정10/1000밀리그램", ing, "202300360"),
             "670609030": life(cal("2024-05", "2024-07"), "(주)휴온스", "휴듀오서방정10/1000밀리그램", ing, "202300356"),
             "670609310": life(cal("2024-07", "2024-09"), "(주)휴온스", "휴시가듀오서방정10/1000밀리그램", ing, "202300360"),
             "641608020": life(cal("2024-08", "2024-09"), "(주)대웅제약", "포시로멧서방정10/1000밀리그램", ing, "202300356")}
    r = link_codes(m, lives)
    got = sorted((p.old, p.new, p.kind, p.order) for p in r.pairs)
    assert got == [("670402400", "670609310", "transfer", 1), ("670609030", "641608020", "transfer", 1)]


def test_link_same_item_gap_transfer_sample17():
    """sample 17 몬테루카스트 4.16mg 츄정: 한국신텍스(~2022-04) → 3개월 공백 → 씨엠지(2022-08~), 같은 허가 201905521 = 양도양수(gap 3).
    옛 코드가 사라진 달의 as_known은 급여삭제, 새 코드가 나타나면 revised를 양도양수로 고친다"""
    m = cal("2022-03", "2022-09")
    lives = {"623005440": life(cal("2022-03", "2022-04"), "한국신텍스제약(주)", "몬테루칸스츄정4밀리그램(몬테루카스트나트륨)", "374603ATB", "201905521"),
             "648604350": life(cal("2022-08", "2022-09"), "(주)씨엠지제약", "씨엠지몬테루카츄정4밀리그램(몬테루카스트나트륨)", "374603ATB", "201905521")}
    r = link_codes(m, lives)
    p = r.pairs[0]
    assert (p.kind, p.link_basis, p.gap_months, p.gone_month, p.entry_month, p.known_month) == \
        ("transfer", "same_item", 3, "2022-05", "2022-08", "2022-08")
    x = r.exits[("623005440", "2022-05")]
    assert (x.kind, x.as_known_kind, x.known_month, x.retracted_month) == ("succession", "delisting", "2022-05", "2022-08")


def test_link_added_listing_needs_code_in_same_month():
    """추가 등재 = 같은 허가의 다른 코드가 그 달 목록에 있음 (sample 17: 원장의 추가 등재는 그 달 같은 허가 코드가 없어 전제가 깨짐)"""
    m = cal("2022-01", "2022-06")
    lives = {"X": life(cal("2022-01", "2022-02"), "갑", "가정", "111101ATB", "I1"),          # 다른 함량 → 순서 1 아님
             "Y": life(cal("2022-05", "2022-06"), "을", "나정", "111102ATB", "I1")}
    r = link_codes(m, lives)
    assert r.pairs == [] and r.entries[("Y", "2022-05")].kind == "new_listing"
    lives["Z"] = life(cal("2022-01", "2022-06"), "을", "나정", "111103ATB", "I1")
    r = link_codes(m, lives)
    assert r.entries[("Y", "2022-05")].kind == "added_listing"


def test_link_same_item_requires_same_ingredient_q3():
    """§10 Q3: 순서 1은 같은 주성분코드일 때만 (예: 670304833 ← 642706481, 공백 3개월, 규격 다름 → 이어받기 아님)"""
    m = cal("2022-09", "2023-03")
    lives = {"642706481": life(cal("2022-09", "2022-10"), "동화약품(주)", "동화플루엔현탁용분말6mg/mL", "358907ASS", "201705168"),
             "670304833": life(cal("2023-02", "2023-03"), "코오롱제약(주)", "코미플루현탁용분말6mg/mL", "358909ASS", "201705168")}
    r = link_codes(m, lives)
    assert r.pairs == []
    assert r.exits[("642706481", "2022-11")].kind == "delisting"


def test_link_spec_delisting_sample11():
    """sample 11 디어큐어점안액 15mg/0.5mL: 같은 허가의 다른 규격이 남아 있어도 이은 코드가 없으면 급여삭제(규격), link_basis = 허가 잔존"""
    m = cal("2022-09", "2022-12")
    lives = {"649508011": life(cal("2022-09", "2022-10"), "유니메드제약(주)", "디어큐어점안액", "617333COS", "202003200"),
             "649508001": life(cal("2022-09", "2022-12"), "유니메드제약(주)", "디어큐어점안액", "617335COS", "202003200")}
    r = link_codes(m, lives)
    x = r.exits[("649508011", "2022-11")]
    assert (x.kind, x.link_basis) == ("delisting_spec", "item_remains")


def test_link_list_recode_order4():
    m = cal("2024-01", "2024-04")
    lives = {"A": life(cal("2024-01", "2024-02"), "갑", "가정", "111101ATB", None),
             "B": life(cal("2024-03", "2024-04"), "을", "다른정", "222201ATB", None, prev=("A",))}
    r = link_codes(m, lives)
    assert [(p.kind, p.link_basis, p.order) for p in r.pairs] == [("transfer", "list_recode", 4)]


# ================================================================ 삭제 집행정지·재등재 (§3)
def test_delisting_stay_sample18():
    """sample 18 가나칸정: 2024-11 급여삭제 → 집행정지 공고 2025-01-31 → 급여 유지 2025-02-01 → 2025-03 삭제 확정.
    급여 유지 유효일 = 다시 실린 달 1일, 집행정지 유효일 = 비고 날짜, 두 사라짐은 하나의 급여삭제 사건"""
    m = cal("2024-09", "2025-04")
    obs = {mo: Obs() for mo in m}
    obs["2024-09"], obs["2024-10"] = B(103), B(103)
    obs["2025-02"] = B(103, R4("2025-01-31"))
    lives = {"644900310": life(["2024-09", "2024-10", "2025-02"], "제이더블유중외제약(주)", "가나칸정", "179001ATB", "199600267")}
    lk = link_codes(m, lives)
    ev = exit_reentry_events("644900310", m, obs, lk)
    by = {(e.event_type, e.kind): e for e in ev}
    first = by[("DELISTING", "delisting")]
    assert (first.observed_month, first.effective_lo, first.effective_hi) == ("2024-11", D("2024-10-02"), D("2024-11-01"))
    keep = by[("DELISTING_STAY", "delisting_stay")]
    assert keep.effective_date == D("2025-02-01")
    stay = by[("STAY_START", "delisting_target")]
    assert stay.effective_date == D("2025-01-31") and stay.stay_target == "delisting"
    final = by[("DELISTING", "confirmed_after_stay")]
    assert final.observed_month == "2025-03"
    assert first.delisting_case_id == keep.delisting_case_id == final.delisting_case_id
    assert ("LISTING", "relisting") not in by


def test_relisting_same_code():
    """재등재: 같은 코드가 나중에 다시 나타나면 삭제는 고치지 않고 재등재 이벤트"""
    m = cal("2024-01", "2024-05")
    obs = {"2024-01": B(100), "2024-02": Obs(), "2024-03": Obs(), "2024-04": B(100), "2024-05": B(100)}
    lives = {"A": life(["2024-01", "2024-04", "2024-05"], "갑", "가정", "111101ATB", "I1")}
    ev = exit_reentry_events("A", m, obs, link_codes(m, lives))
    assert sorted((e.event_type, e.kind, e.observed_month) for e in ev) == \
        [("DELISTING", "delisting", "2024-02"), ("LISTING", "relisting", "2024-04")]


# ================================================================ 지표 (§7)
def test_rates_formulas():
    """§7 (2026-11-18 개정): A = 실현 계열 ÷ (전체 − 판정 제외 − 미결 AWAITING), 미결 ONGOING_STAY는 미실현으로 셈.
    B = 실현 계열 ÷ (전체 − 판정 제외 − 미결 전체). 정시 = 실현 ÷ B 분모. 집행정지율 = 막힌 신호 ÷ (전체 − 판정 제외)"""
    def r(o, reason=None, blocked=False, cs=None):
        return dict(outcome=o, reason=reason, stay_blocked=blocked, current_status=cs)
    rows = ([r("REALIZED")] * 3 + [r("REALIZED_CHANGED")] + [r("REALIZED_LATE"), r("REALIZED_LATE", blocked=True)]
            + [r("REALIZED_BY_DELISTING")] + [r("NOT_REALIZED", "note_gone"), r("NOT_REALIZED", "stay_ended_not_executed", True)]
            + [r("EXCLUDED", "listed_during_suspension"), r("CENSORED", blocked=True, cs="ONGOING_STAY"), r("CENSORED", cs="AWAITING")])
    x = rates(rows)
    assert x["A"][:2] == (7, 10)
    assert x["B"][:2] == (7, 9)
    assert x["on_time"][:2] == (3, 9)
    assert x["stay"][:2] == (3, 11)
    lo, hi = wilson(3, 9)
    assert lo == pytest.approx(0.1206, abs=1e-3) and hi == pytest.approx(0.6458, abs=1e-3)
    assert omission_rate(1, 2) == pytest.approx(2 / 3)


def test_stay_then_delisted_suspension_and_price():
    """§6.3 (2026-11-18): 집행정지 중 대상 삭제 → 미실현, 사유 stay_then_delisted (급여중지 예고·약가 예고 모두.
    급여중지 예고 12건: 동아에스티 기간 중지 11 + 아노핀정 급여정지(22.3.18.) 1, 모두 집행정지 표시가 유지된 채 삭제)"""
    m = cal("2022-04", "2022-12")
    p = R3b("2022-08-01", "2022-08-31")
    b = R4("2022-05-09")
    obs = {mo: Obs() for mo in m}
    obs["2022-04"] = B(100)
    obs["2022-05"] = B(100, p, R2("2022-05-20", 90))
    for mo in cal("2022-06", "2022-10"):
        obs[mo] = B(100, b)
    _, _, sigs = run(m, obs)
    for key in (("SUSPEND_NOTICE", D("2022-08-01")), ("PRICE_NOTICE", D("2022-05-20"))):
        s = sigs[key]
        assert (s.outcome, s.reason, s.stay_blocked) == ("NOT_REALIZED", "stay_then_delisted", True), key


# ================================================================ 전이표 (§8 전이표 0건 칸)
def test_transitions_possible_rare_cells_synthetic():
    """관측 0건이던 칸 중 가능한 칸: 등재 예정 → 급여중지, 등재 예정 → 급여삭제, 급여삭제 → 급여중지(중지 표시와 함께 다시 실림)"""
    m = cal("2025-01", "2025-04")
    a = {"2025-01": Obs(), "2025-02": B(100, Tok("R1", D("2025-03-05"))), "2025-03": B(100, R3("2025-03-01")), "2025-04": B(100, R3("2025-03-01"))}
    st = derive_status(m, a)
    seq = [state_of(st.rows[mo], "revised") for mo in m if mo in st.rows]
    assert seq == ["scheduled_listing", "suspended", "suspended"]
    check_transitions(seq)
    b = {"2025-01": Obs(), "2025-02": B(100, Tok("R1", D("2025-03-05"))), "2025-03": Obs(), "2025-04": Obs()}
    seq = [state_of(r, "revised") for r in derive_status(m, b).rows.values()]
    assert seq[:2] == ["scheduled_listing", "delisted"]
    check_transitions(seq)
    c = {"2025-01": B(100), "2025-02": Obs(), "2025-03": B(100, R3("2025-02-15")), "2025-04": B(100, R3("2025-02-15"))}
    seq = [state_of(r, "revised") for r in derive_status(m, c).rows.values()]
    assert seq == ["reimbursed", "delisted", "suspended", "suspended"]
    check_transitions(seq)


@pytest.mark.parametrize("seq", [["reimbursed", "scheduled_listing"], ["delisted", "transferred_out"],
                                 ["transferred_out", "delisted"], ["suspended", "scheduled_listing"]])
def test_transitions_impossible_cells_fail(seq):
    with pytest.raises(ValueError):
        check_transitions(seq)


def test_blocked_signal_realized_while_other_stay_remains():
    """§3 동아에스티 43개 제품: 2024-07 `집행정지(22.5.3.) / 집행정지(22.5.9.)` → 2024-08 `집행정지(22.5.9.)`, 상한금액 인하.
    막힌 인하 처분이 그 금액으로 시행되면 지연 실현 (다른 집행정지가 남아 있어도)"""
    m = cal("2022-04", "2024-09")
    a, b = R4("2022-05-03"), R4("2022-05-09")
    obs = {}
    for mo in m:
        if mo == "2022-04":
            obs[mo] = B(500)
        elif mo == "2022-05":
            obs[mo] = B(500, a, R2("2022-05-04", 450, src="S"), R3b("2022-08-01", "2022-08-31"))
        elif mo < "2024-08":
            obs[mo] = B(500, a, b)
        else:
            obs[mo] = B(450, b)
    _, _, sigs = run(m, obs)
    s = sigs[("PRICE_NOTICE", D("2022-05-04"))]
    assert (s.stay_blocked, s.outcome, s.realized_month) == (True, "REALIZED_LATE", "2024-08")


def test_late_realization_date_from_note_sample25():
    """sample 25 (663300880): 2023-09 대체조제 금액 989원 = 막힌 인하 처분 (§1). 2025-12 비고 `집행정지 해제 및 상한금액 인하(25.11.20. 고시)`,
    상한금액 1164 → 989 → 지연 실현, 유효일 = 확인 비고의 날짜 2025-11-20 (외부 확인: 고시 제2025-187호 시행일 2025.11.20.)"""
    m = cal("2023-08", "2025-12")
    st = R4("2023-09-05")
    obs = {mo: B(1164, st) for mo in m}
    obs["2023-08"] = B(1164)
    obs["2023-09"] = B(1164, st, price_s=841)            # 실제 2023-09 대체조제 금액
    obs["2025-12"] = B(989, Tok("R5", D("2025-11-20"), src="B"))
    stt, _, sigs = run(m, obs)
    s = [x for x in sigs.values() if x.source_rule == "S_AMT"][0]
    # §11-1: 변경 실현, 유효일 = 실현 금액을 알린 비고의 날짜 2025-11-20
    assert (s.outcome, s.realized_month, s.effective_date) == ("REALIZED_CHANGED", "2025-12", D("2025-11-20"))
    pe = price_events("P", m, obs, stt, list(sigs.values()))
    assert [(e.kind, e.effective_date) for e in pe] == [("signaled", D("2025-11-20"))]



def test_substitute_higher_amount_is_not_signal():
    """§1: 대체조제 금액이 급여목록 금액보다 **낮고** 새 집행정지가 붙었을 때만 신호. 더 높으면 신호 아님 (696300550 유형)"""
    m = cal("2024-09", "2024-11")
    st = R4("2024-09-27")
    obs = {"2024-09": B(56576), "2024-10": B(56576, st, price_s=68000), "2024-11": B(56576, st)}
    _, _, sigs = run(m, obs)
    assert not [s for s in sigs.values() if s.source_rule == "S_AMT"]


def test_link_recode_same_name_same_firm_azeria():
    """§3-1 ★: (주)한국파마 아제리아정16밀리그램 670105380(~2023-02, 364원) → 653006820(2023-03~),
    아제리아정8밀리그램 670105260 → 653006840(210원). 같은 업체·제품명·규격·금액 → 둘 다 코드 변경 (옛 코드는 허가 연결 없음)"""
    m = cal("2023-01", "2023-04")
    f = "(주)한국파마"
    lives = {"670105380": life(["2023-02"], f, "아제리아정16밀리그램(아젤니디핀)", "128402ATB", None),
             "670105260": life(["2023-02"], f, "아제리아정8밀리그램(아젤니디핀)", "128401ATB", None),
             "653006820": life(cal("2023-03", "2023-04"), f, "아제리아정16밀리그램(아젤니디핀)", "128402ATB", "202202475"),
             "653006840": life(cal("2023-03", "2023-04"), f, "아제리아정8밀리그램(아젤니디핀)", "128401ATB", "202108119")}
    r = link_codes(m, lives)
    assert sorted((p.old, p.new, p.kind, p.link_basis) for p in r.pairs) == \
        [("670105260", "653006840", "recode", "same_name"), ("670105380", "653006820", "recode", "same_name")]


def test_swap_requires_name_change():
    """교체 등재 = 같은 업체가 이름·허가만 바꿔 이어서 실음 (sample 58 휴메리나 → 휴다트는 그대로 교체 등재)"""
    m = cal("2022-01", "2022-02")
    lives = {"A": life(["2022-01"], "갑", "가정", "111101ATB", "I1"), "B": life(["2022-02"], "갑", "나정", "111101ATB", "I2")}
    assert [(p.kind, p.link_basis) for p in link_codes(m, lives).pairs] == [("swap", "same_firm_ingredient")]


def test_stay_records_fields():
    """§5: 집행정지 기록 칸 (stay_date, stay_end_date, target_type, target_signal_id, target_detail, target_source)과 신호의 blocking_stay_id.
    동아가바펜틴: 22.5.3.은 A `22.5.4., 368원` 인하를 막음 (timing), 18.10.6.은 앞선 집행정지라 대상 미상"""
    m = cal("2022-04", "2024-09")
    a = R2("2022-05-04", 368, src="S")
    old, new = R4("2018-10-06"), R4("2022-05-03")
    obs = {mo: (B(393, old) if mo == "2022-04" else B(393, new, a) if mo == "2022-05" else B(393, new) if mo < "2024-08" else B(368)) for mo in m}
    st, stays, sigs = run(m, obs)
    rec = {r["stay_date"]: r for r in stay_records("P", stays, list(sigs.values()))}
    r = rec[D("2022-05-03")]
    assert (r["stay_end_date"], r["stay_end_reason"], r["target_type"], r["target_signal_id"], r["target_detail"], r["target_source"]) == \
        (D("2024-08-01"), None, "price_cut", "PRICE_NOTICE:P:2022-05-04", "22.5.4. 368원 인하", "timing")
    assert (rec[D("2018-10-06")]["target_type"], rec[D("2018-10-06")]["target_signal_id"]) == ("unknown", None)
    assert sigs[("PRICE_NOTICE", D("2022-05-04"))].blocking_stay_id == "STAY:P:2022-05-03"



def test_excluded_suspended_before_price_date_then_delisted():
    """판정 제외 사유 '인하일 전 급여중지' (사용자 결정 2026-11-18): 예고 때는 급여였는데 인하 예정일 전에 급여중지가 시작됨.
    신플랙스정 697100360: 2024-02 비고 `2024.3.1., 94원, 급여정지(24.2.6.)`, 2024-03 삭제 → '대상 소멸'이 아니다"""
    m = cal("2024-01", "2024-04")
    obs = {"2024-01": B(110), "2024-02": B(110, R2("2024-03-01", 94), R3("2024-02-06")), "2024-03": Obs(), "2024-04": Obs()}
    _, _, sigs = run(m, obs)
    assert sigs[("SUSPEND_NOTICE", D("2024-02-06"))].outcome == "REALIZED_BY_DELISTING"
    p = sigs[("PRICE_NOTICE", D("2024-03-01"))]
    assert (p.outcome, p.reason) == ("EXCLUDED", "suspended_before_price_date")


def test_excluded_target_gone_without_suspension():
    """'대상 소멸' = 처분과 무관한 사유로 예정일 전에 사라짐 (급여중지 예고 없음, 집행정지 없음). 파로마정 등 4건"""
    m = cal("2024-01", "2024-04")
    obs = {"2024-01": B(110), "2024-02": B(110, R2("2024-03-01", 94)), "2024-03": Obs(), "2024-04": Obs()}
    _, _, sigs = run(m, obs)
    assert (sigs[("PRICE_NOTICE", D("2024-03-01"))].outcome, sigs[("PRICE_NOTICE", D("2024-03-01"))].reason) == ("EXCLUDED", "target_gone")


def test_link_name_ignores_ingredient_parentheses_nesina():
    """§3-1 순서 2 (2026-11-18 holdout 반영): 괄호 안 성분 표기를 지우고 비교. holdout 7 셀트리온제약 네시나액트정25/15
    693903640 `네시나액트정25/15밀리그램`(~2024-11) → 693903820 `네시나액트정25/15밀리그램(알로글립틴벤조산염,피오글리타존염산염)`(2025-01~) = 코드 변경"""
    m = cal("2024-10", "2025-02")
    f = "(주)셀트리온제약"
    lives = {"693903640": life(cal("2024-10", "2024-11"), f, "네시나액트정25/15밀리그램", "630500ATB", None),
             "693903820": life(cal("2025-01", "2025-02"), f, "네시나액트정25/15밀리그램(알로글립틴벤조산염,피오글리타존염산염)", "630500ATB", "202303455")}
    r = link_codes(m, lives)
    assert [(p.old, p.new, p.kind, p.link_basis, p.gap_months) for p in r.pairs] == [("693903640", "693903820", "recode", "same_name", 1)]


def test_external_stay_blocks_suspension():
    """외부 집행정지 경로 (2026-11-18): 급여목록에 집행정지 비고가 없어도 config의 외부 집행정지로 막힘 (target_source = external).
    경보제약 급여정지(25.6.24.): 대전지법 2025-06-18 임시, 2025-07-09 본 집행정지 → 2025-12 기준 미결(집행정지 중).
    집행정지 중 삭제되면 미실현 stay_then_delisted"""
    from pbd.ledger.v5_rules import Stay
    m = cal("2025-05", "2025-12")
    ext = {("SUSPEND_NOTICE", D("2025-06-24")): (Stay(D("2025-06-18"), "2025-06", None, False),)}
    obs = {mo: B(100) for mo in m}
    obs["2025-06"] = B(100, R3("2025-06-24"))
    _, _, sigs = run(m, obs, external_stays=ext)
    s = sigs[("SUSPEND_NOTICE", D("2025-06-24"))]
    assert (s.outcome, s.current_status, s.stay_blocked, s.stay_target_source, s.blocking_stays) == \
        ("CENSORED", "ONGOING_STAY", True, "external", (D("2025-06-18"),))
    gone = dict(obs)
    for mo in cal("2025-09", "2025-12"):
        gone[mo] = Obs()
    _, _, sigs = run(m, gone, external_stays=ext)
    s = sigs[("SUSPEND_NOTICE", D("2025-06-24"))]
    assert (s.outcome, s.reason, s.stay_blocked) == ("NOT_REALIZED", "stay_then_delisted", True)
    # 외부 집행정지가 없으면 기존대로 비고 사라짐 → 미실현
    _, _, sigs = run(m, obs)
    assert sigs[("SUSPEND_NOTICE", D("2025-06-24"))].reason == "note_gone"


def test_name_keeps_form_parentheses():
    """제품명 정규화 (2026-11-18 v5.3 마무리): 공백 제거는 유지. 괄호 안에 1회용·수출명·무보존제·프리필드·펜·바이알·앰플·백이 있으면
    그 괄호는 지우지 않는다. 성분 괄호는 지운다"""
    from pbd.ledger.v5_rules import name_key
    assert name_key("레스타시스점안액0.05%(사이클로스포린)") == name_key("레스타시스점안액0.05%")
    assert name_key("레스타시스점안액0.05%(사이클로스포린)") != name_key("레스타시스점안액0.05%(사이클로스포린)(1회용)")
    assert name_key("조터나흡입용캡슐 110/50마이크로그램") == name_key("조터나흡입용캡슐110/50마이크로그램")
    for w in ("1회용", "수출명 : X", "무보존제", "프리필드", "펜", "바이알", "앰플", "백"):
        assert name_key(f"가주({w})") != name_key("가주"), w
    m = cal("2024-01", "2024-02")
    lives = {"A": life(["2024-01"], "갑", "가점안액(성분)", "111101COS", None),
             "B": life(["2024-02"], "을", "가점안액(성분)(1회용)", "111101COS", None)}
    assert link_codes(m, lives).pairs == []          # 이름이 달라 순서 2 아님, 업체가 달라 순서 3도 아님
