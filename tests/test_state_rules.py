"""state_rules.derive 단위 시험 (손으로 만든 입력).

기대값은 손으로 계산했다: docs/NOTE_RULES.md R1·R3·R3b·R6·§2, docs/PLAN_P1.md §4 state.
exposure_frac = 그 달 급여된 일수 / 그 달 일수.
"""
import datetime as dt

import pytest

from pbd.ledger.state_rules import Tok, derive

D = dt.date
MONTHS = ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05", "2024-06"]
BASE = {m: D(int(m[:4]), int(m[5:]), 1) for m in MONTHS}


def run(toks, months=MONTHS, last="2024-06"):
    return derive(months, BASE, {m: set(v) for m, v in toks.items()}, last)


def test_r3_future_confirmed_rewrites_signal_month():
    k = Tok("R3", D(2024, 2, 15))
    o = run({"2024-02": [k], "2024-03": [k]})
    assert (o["2024-02"].suspended, o["2024-02"].exposure_revised) == (True, True)
    assert o["2024-02"].exposure_frac == pytest.approx(14 / 29)          # 2/1~2/14 급여, 2024-02는 29일
    assert (o["2024-03"].suspended, o["2024-03"].exposure_frac, o["2024-03"].exposure_revised) == (True, 0.0, False)


def test_r3_future_not_confirmed_keeps_state():
    k = Tok("R3", D(2024, 2, 15))
    o = run({"2024-02": [k]})
    assert (o["2024-02"].suspended, o["2024-02"].exposure_frac, o["2024-02"].exposure_revised) == (False, 1.0, False)


def test_r3_gap_then_product_absent_stays_suspended():
    """1개월 공백 뒤 제품이 사라짐(분석 끝 아님): 정지 유지, resume_unconfirmed 아님"""
    k = Tok("R3", D(2023, 5, 1))
    o = run({"2024-01": [k], "2024-02": [k]}, months=MONTHS[:3])
    assert (o["2024-03"].suspended, o["2024-03"].resume_unconfirmed, o["2024-03"].note) == (True, False, "gap_then_absent")


def test_r3b_end_on_month_end():
    """종료일이 월말이면 그 달은 월말까지 정지, 다음 달부터 복귀"""
    k = Tok("R3b", D(2023, 12, 1), D(2024, 1, 31))
    o = run({"2024-01": [k], "2024-02": [k]})
    assert (o["2024-01"].suspended, o["2024-01"].exposure_frac) == (True, 0.0)
    assert (o["2024-02"].suspended, o["2024-02"].exposure_frac) == (False, 1.0)


def test_listing_at_analysis_end_not_confirmed():
    o = run({"2024-06": [Tok("R1", D(2024, 6, 20))]}, months=["2024-06"])
    assert (o["2024-06"].scheduled, o["2024-06"].exposure_frac, o["2024-06"].exposure_revised) == (True, 0.0, False)


def test_listing_only_for_first_observed_rows():
    o = run({"2024-03": [Tok("R1", D(2024, 3, 20))]})
    assert not o["2024-03"].scheduled                                        # 2024-01부터 있던 제품
