"""S1 비고 파서 시험.

기대값 출처
- docs/NOTE_RULES.md §1의 문형 예시 (R1~R7)
- reports/S0_result.md §6의 U1~U9 원문과 사용자 결정 (DECISIONS.md 2026-11-14)
구현 출력으로 기대값을 만들지 않는다.
"""
import datetime as dt

import pytest

from pbd.ledger.notes import parse_note, is_future

D = dt.date


def one(note):
    toks = parse_note(note)
    assert len(toks) == 1, toks
    return toks[0]


# ---------------------------------------------------------------- R1~R7 (NOTE_RULES §1 예시)
@pytest.mark.parametrize("note, typ, date, amount", [
    ("25.11.23 등재", "R1", D(2025, 11, 23), None),
    ("24.9.2., 453원", "R2", D(2024, 9, 2), 453),
    ("급여정지(22.10.20.)", "R3", D(2022, 10, 20), None),
    ("집행정지(24.9.30.)", "R4", D(2024, 9, 30), None),
    ("집행정지 해제 및 상한금액 인하(25.11.20. 고시)", "R5", D(2025, 11, 20), None),
    ("급여정지 해제(25.11.12)", "R6", D(2025, 11, 12), None),
    ("산정불가", "R7", None, None),
])
def test_rules(note, typ, date, amount):
    t = one(note)
    assert (t["type"], t["date"], t["amount"]) == (typ, date, amount)
    assert t["variant"] is None


# ---------------------------------------------------------------- P2 표기 변형
@pytest.mark.parametrize("note, typ, date, amount", [
    ("'25.11.23 등재", "R1", D(2025, 11, 23), None),     # 앞 따옴표
    ("2024.3.1., 131원", "R2", D(2024, 3, 1), 131),       # 4자리 연도
    ("25.6.9. 402원", "R2", D(2025, 6, 9), 402),          # 쉼표 없는 가격
    ("급여정지(22.1.11)", "R3", D(2022, 1, 11), None),     # 끝 마침표 없음
    ("'2023.1.9., 240원", "R2", D(2023, 1, 9), 240),
])
def test_p2_variants(note, typ, date, amount):
    t = one(note)
    assert (t["type"], t["date"], t["amount"]) == (typ, date, amount)


# ---------------------------------------------------------------- P1 복합 비고
def test_p1_compound():
    toks = parse_note("집행정지(22.5.3.) / 집행정지(22.5.9.)")
    assert [(t["type"], t["date"]) for t in toks] == [("R4", D(2022, 5, 3)), ("R4", D(2022, 5, 9))]
    toks = parse_note("집행정지(18.4.1.), 급여정지(19.9.26.)")
    assert [(t["type"], t["date"]) for t in toks] == [("R4", D(2018, 4, 1)), ("R3", D(2019, 9, 26))]
    toks = parse_note("집행정지(21.11.30.), 2024.3.1., 179원")
    assert [(t["type"], t["amount"]) for t in toks] == [("R4", None), ("R2", 179)]
    assert [t["seq"] for t in toks] == [1, 2]


# ---------------------------------------------------------------- S0 U1~U9 (사용자 결정)
def test_u1_reeval_stay_is_r4():
    t = one("허가변경 약제 재평가 집행정지(21.8.31.)")
    assert (t["type"], t["date"], t["variant"]) == ("R4", D(2021, 8, 31), "reeval")
    assert t["raw"] == "허가변경 약제 재평가 집행정지(21.8.31.)"    # 원문 보존


@pytest.mark.parametrize("note, start, end", [
    ("급여정지(22.04.01.~22.12.31)", D(2022, 4, 1), D(2022, 12, 31)),   # U2
    ("급여정지(22.8.1.~22.8.31)", D(2022, 8, 1), D(2022, 8, 31)),       # U3
])
def test_u2_u3_period_suspension_is_r3b(note, start, end):
    t = one(note)
    assert (t["type"], t["date"], t["date_end"]) == ("R3b", start, end)


@pytest.mark.parametrize("note, amount", [("('22.7.15., 1280원)", 1280), ("('22.7.15., 10560원)", 10560)])
def test_u4_u5_paren_price_is_r2(note, amount):
    t = one(note)
    assert (t["type"], t["date"], t["amount"], t["variant"]) == ("R2", D(2022, 7, 15), amount, "paren")


def test_u6_arrow_is_r4_then_r5():
    toks = parse_note("집행정지(21.9.1.)->집행정지해제(22.1.18., 176원)")
    assert [(t["type"], t["date"], t["amount"], t["variant"]) for t in toks] == [
        ("R4", D(2021, 9, 1), None, "arrow"),
        ("R5", D(2022, 1, 18), 176, "arrow"),
    ]


def test_u7_delisting_is_r9():
    t = one("급여삭제(23.6.12.)")
    assert (t["type"], t["date"]) == ("R9", D(2023, 6, 12))


def test_u9_shinseol_is_r1():
    t = one("'22.3.5.신설")
    assert (t["type"], t["date"], t["variant"]) == ("R1", D(2022, 3, 5), "shinseol")


# ---------------------------------------------------------------- 해제를 정지로 읽지 않음
def test_release_not_suspension():
    assert one("급여정지 해제(25.11.12)")["type"] == "R6"
    assert one("집행정지 해제 및 상한금액 인하(25.11.20. 고시)")["type"] == "R5"


# ---------------------------------------------------------------- 모르는 문형은 unresolved (추측 금지)
@pytest.mark.parametrize("note", ["약가유연계약 적용 약제", "급여정지(22.4.1.) 기타문구", "집행정지(99.99.99.)"])
def test_unknown_is_unresolved(note):
    toks = parse_note(note)
    assert [t["type"] for t in toks] == ["unresolved"]
    assert toks[0]["raw"] == note.strip()


# ---------------------------------------------------------------- is_future: 실제 기준일과 비교
def test_is_future_uses_actual_base_date():
    base = D(2025, 3, 4)                       # 기준일이 1일이 아닌 달
    assert is_future(D(2025, 3, 3), base) is False
    assert is_future(D(2025, 3, 4), base) is False   # 같은 날은 미래 아님
    assert is_future(D(2025, 3, 5), base) is True
    assert is_future(D(2025, 3, 2), D(2025, 3, 1)) is True
    assert is_future(None, base) is None
