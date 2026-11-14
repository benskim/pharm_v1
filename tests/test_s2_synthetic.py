"""S2 합성 스냅샷 시험: S1 적재기 → 비고 파서 → S2 전체를 손으로 만든 스냅샷으로 돌린다.

기대값은 손으로 정했다 (docs/PLAN_P1.md §4 state·product_role, docs/NOTE_RULES.md R1~R7·R3b·§2,
docs/DECISIONS.md 2026-11-14 결정). 구현 출력에서 가져온 값이 없다.
월: 2024-01 ~ 2024-06 (기준일 매월 1일, 분석 끝 = 2024-06).
"""
import datetime as dt

import pandas as pd
import pytest

from pbd.common import connect
from pbd.ledger import s1_load as L
from pbd.ledger import s2_state as S2

D = dt.date
M = ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05", "2024-06"]


def bene(m, pc, note=None, price=100.0, firm="A", name="가", ing="101301ATB"):
    return dict(연번=1, 투여="내복", 분류=111.0, 주성분코드=ing, 제품코드=pc, 제품명=name, 업체명=firm, 규격="1", 단위="정",
                상한금액=price, 전일="전문", 비고=note, 주성분명="x", 소스파일명=f"b{m}.xlsx", 기준일자=f"{m}-01")


def subs(m, pc, note=None, ref=None, bioeq="○(1)", equiv="1", item=198500001.0):
    return {"연번(급여목록상)": 1, "투여": "내복", "분류": 111.0, "주성분코드": "101301ATB", "①주성분 및 투여제형코드": "101301ATB",
            "제품코드": pc, "②대표코드": 8806519003301.0, "목록정비전코드": None, "제품명": "가", "업체명": "A", "규격": "1",
            "단위": "정", "상한금액": 100.0, "전일": "전문", "정렬순서": 1.0, "②품목기준코드": item,
            "생동인정여부(공고번호)": bioeq, "생동대조약 여부": ref, "생동인정 및 생동대조약 적용일": None, "의약동등": equiv,
            "비고(급여정지)": note, "주성분명": "x", "기준일자": f"{m}-01", "소스파일명": f"s{m}.xlsx"}


def every(pc, notes=None, months=M, **kw):
    """months의 달마다 급여목록 행. notes: 달 → 비고"""
    notes = notes or {}
    return [bene(m, pc, notes.get(m), **kw) for m in months]


B_ROWS = (
    every("600000010")                                                    # 배경 제품 (분석 끝까지)
    # 등재 예정: 처음 관측(2024-03) / 기존 제품 / 분석 끝 처음 관측
    + every("600000020", {"2024-03": "'24.3.20 등재"}, M[2:])
    + every("600000030", {"2024-03": "'24.3.20 등재"})
    + every("600000040", {"2024-06": "'24.6.20 등재"}, M[5:])
    # 월중 급여정지: 확인됨 / 확인 안 됨
    + every("600000050", {"2024-02": "급여정지(24.2.15.)", "2024-03": "급여정지(24.2.15.)"}, M[:3])
    + every("600000060", {"2024-02": "급여정지(24.2.15.)"}, M[:3])
    # 정지 중 삭제 (2024-04부터 없음)
    + every("600000070", {m: "급여정지(23.5.1.)" for m in M[:3]}, M[:3])
    # 집행정지 중 가격 변화
    + [bene("2024-01", "600000080", "집행정지(23.9.30.)", 100.0), bene("2024-02", "600000080", "집행정지(23.9.30.)", 90.0),
       bene("2024-03", "600000080", "집행정지(23.9.30.)", 90.0)]
    # 산정불가: 비고 / 금액 칸만 비어 있음
    + [bene("2024-01", "600000090", "산정불가", None), bene("2024-01", "600000100", None, None)]
    # 기간 명시 정지: 미래 시작일, 종료일 뒤에도 비고가 남음
    + every("600000110", {m: "급여정지(24.01.10.~24.02.20)" for m in M[:4]}, M[:4])
    # 종료일 없는 정지의 복귀
    + every("600000120", {m: "급여정지(23.5.1.)" for m in ["2024-01", "2024-02", "2024-04", "2024-05"]})  # 1개월 공백 후 재등장
    + every("600000130", {m: "급여정지(23.5.1.)" for m in M[:2]})                                        # 2개월 연속 공백
    + every("600000140", {m: "급여정지(23.5.1.)" for m in M[:2]})                                        # 대체조제에만 남음
    + every("600000150", {m: "급여정지(23.5.1.)" for m in M[:5]})                                        # 분석 끝 1개월 공백
    + every("600000160", {"2024-01": "급여정지(23.5.1.)", "2024-02": "급여정지(23.5.1.)",
                          "2024-03": "급여정지 해제(24.2.12)"}, M[:4])                                    # R6 해제
    # 창 시작 전 집행정지, U6 (해제 예정 → 다음 달 해제 확인)
    + every("600000170", {m: "집행정지(21.8.31.)" for m in M[:2]}, M[:2])
    + [bene("2024-01", "600000180", "집행정지(21.9.1.)->집행정지해제(24.1.18., 176원)"),
       bene("2024-02", "600000180", "집행정지 해제 및 상한금액 인하(24.1.18. 고시)", 176.0)]
    # product_role
    + every("600000200", months=M[:2]) + every("600000210", months=M[:1]) + every("600000220", months=M[:1])
    + every("600000230", months=M[:1]) + every("600000240", months=M[:1]) + every("600000250", months=M[:2])
    # non_product 행 (S1 row_kind)
    + [bene("2024-01", "243001ACS", None, None, firm=None, name="tretinoin 10mg")]
)
S_ROWS = (
    [subs(m, "600000140", "급여정지(23.5.1.)") for m in M[:4]] + [subs(m, "600000140") for m in M[4:]]
    + [subs(m, "600000200", ref="○", bioeq="○(2)", equiv="6", item=198500200.0) for m in M[:2]]   # 대조약 + 생동인정
    + [subs("2024-01", "600000220", ref="○", bioeq=None, equiv="4", item=198500220.0)]           # 오렌지북과 대조약 불일치
    + [subs("2024-01", "600000240", item=198500240.0)]                                           # 생동인정만
    + [subs("2024-01", "600000250", ref="○", bioeq=None, equiv="4", item=198500250.0),
       subs("2024-02", "600000250", ref="○", bioeq="○(3)", equiv="6", item=198500250.0)]         # 4 → 6
    + [subs("2024-01", "600000999")]                                                             # 대체조제에만 있음
)
ATTR_PRODUCT = [("600000210", "198500210"), ("600000230", "198500230")]
ATTR_ITEM = [  # item_code, in_orangebook, ob_ref, ob_generic
    ("198500210", True, False, True),    # 제네릭의약품 표시만
    ("198500220", True, False, False),   # 공고대조약 X (대체조제는 대조약 ○)
    ("198500230", True, True, False),    # 대체조제 목록에 없는 공고대조약
]


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("s2")
    c = connect(None)
    pb, ps = tmp / "benefit.parquet", tmp / "substitute.parquet"
    pd.DataFrame(B_ROWS).to_parquet(pb, index=False)
    pd.DataFrame(S_ROWS).to_parquet(ps, index=False)
    L.build_stg_snapshot(c, "benefit", [pb])
    L.build_stg_snapshot(c, "substitute", [ps])
    L.build_note_token(c)
    c.execute("create table attr_2026_product (product_code varchar, item_code varchar)")
    c.executemany("insert into attr_2026_product values (?, ?)", ATTR_PRODUCT)
    c.execute("create table attr_2026_item (item_code varchar, in_orangebook boolean, ob_ref boolean, ob_generic boolean)")
    c.executemany("insert into attr_2026_item values (?, ?, ?, ?)", ATTR_ITEM)
    S2.build_all(c)
    yield c
    c.close()


COLS = "state, exposure_frac, exposure_revised, state_revised, resume_unconfirmed"


def rows(con, pc, cols=COLS):
    return {r[0]: tuple(r[1:]) for r in con.execute(
        f"select list_month, {cols} from product_state_month where product_code = ? order by 1", [pc]).fetchall()}


def test_row_count_excludes_non_product(con):
    n = con.execute("select count(*) from product_state_month").fetchone()[0]
    assert n == len(B_ROWS) - 1
    assert con.execute("select count(*) from product_state_month where product_code = '243001ACS'").fetchone()[0] == 0


def test_scheduled_listing_first_observed(con):
    r = rows(con, "600000020")
    assert r["2024-03"] == ("scheduled_listing", pytest.approx(12 / 31), True, False, False)   # 3/20~3/31 급여
    assert r["2024-04"] == ("reimbursed", 1.0, False, False, False)


def test_scheduled_listing_existing_product_stays_reimbursed(con):
    assert rows(con, "600000030")["2024-03"] == ("reimbursed", 1.0, False, False, False)


def test_scheduled_listing_at_analysis_end(con):
    assert rows(con, "600000040")["2024-06"] == ("scheduled_listing", 0.0, False, False, False)


def test_mid_month_suspension_confirmed(con):
    r = rows(con, "600000050")
    assert r["2024-01"] == ("reimbursed", 1.0, False, False, False)
    assert r["2024-02"] == ("suspended", pytest.approx(14 / 29), True, False, False)
    assert r["2024-03"] == ("suspended", 0.0, False, False, False)


def test_mid_month_suspension_not_confirmed(con):
    r = rows(con, "600000060")
    assert r["2024-02"] == ("reimbursed", 1.0, False, False, False)
    assert r["2024-03"] == ("reimbursed", 1.0, False, False, False)


def test_suspended_then_delisted(con):
    r = rows(con, "600000070", "state, absent_next_month")
    assert r == {"2024-01": ("suspended", False), "2024-02": ("suspended", False), "2024-03": ("suspended", True)}


def test_stay_with_price_change(con):
    r = rows(con, "600000080", "state, stay_flag, price_listed, exposure_frac")
    assert r == {"2024-01": ("reimbursed", True, 100.0, 1.0), "2024-02": ("reimbursed", True, 90.0, 1.0),
                 "2024-03": ("reimbursed", True, 90.0, 1.0)}


def test_unpriced_two_places(con):
    assert rows(con, "600000090", "state, unpriced_evidence")["2024-01"] == ("unpriced", "note+price_null")
    assert rows(con, "600000100", "state, unpriced_evidence")["2024-01"] == ("unpriced", "price_null")


def test_dated_suspension_resumes_even_if_note_remains(con):
    r = rows(con, "600000110")
    assert r["2024-01"] == ("suspended", pytest.approx(9 / 31), True, False, False)   # 1/1~1/9 급여 (시작일 미래 → 2월에 확인)
    assert r["2024-02"] == ("reimbursed", pytest.approx(9 / 29), True, False, False)  # 2/21~2/29 급여 (종료일 미래 → 3월에 확인)
    assert r["2024-03"] == ("reimbursed", 1.0, False, False, False)                   # 비고가 남아 있어도 복귀
    assert r["2024-04"] == ("reimbursed", 1.0, False, False, False)


def test_resume_one_month_gap_no_event(con):
    r = rows(con, "600000120")
    assert r["2024-03"] == ("suspended", 0.0, False, False, False)
    assert r["2024-06"] == ("suspended", 0.0, False, False, True)    # 분석 끝 1개월 공백도 함께 확인


def test_resume_two_month_gap_rewrites_first_month(con):
    r = rows(con, "600000130")
    assert r["2024-02"] == ("suspended", 0.0, False, False, False)
    assert r["2024-03"] == ("reimbursed", 1.0, False, True, False)   # 첫 공백 달 1일 복귀, 고쳐 쓴 행
    assert r["2024-04"] == ("reimbursed", 1.0, False, False, False)


def test_token_left_in_one_list_is_not_resume(con):
    r = rows(con, "600000140")
    assert [r[m][0] for m in M] == ["suspended"] * 4 + ["reimbursed"] * 2
    assert r["2024-05"][3] is True                                   # 두 목록 모두 2개월 공백 → 5월 1일 복귀


def test_resume_unconfirmed_at_analysis_end(con):
    assert rows(con, "600000150")["2024-06"] == ("suspended", 0.0, False, False, True)


def test_r6_release(con):
    r = rows(con, "600000160")
    assert r["2024-02"] == ("suspended", 0.0, False, False, False)   # 지난 달은 고쳐 쓰지 않는다 (해제일 2/12)
    assert r["2024-03"] == ("reimbursed", 1.0, False, False, False)


def test_stay_before_window_flag_only(con):
    r = rows(con, "600000170", "state, stay_flag, stay_date")
    assert r["2024-01"] == ("reimbursed", True, D(2021, 8, 31))


def test_u6_stay_release(con):
    r = rows(con, "600000180", "stay_flag")
    assert r == {"2024-01": (True,), "2024-02": (False,)}            # 해제일 미래 → 유지, 다음 달 해제 확인


ROLE = "product_role, role_source, is_bioequiv, role_conflict, sub_equiv, ob_ref, ob_generic, ob_link_path"


def test_role_reference_and_bioequiv(con):
    assert rows(con, "600000200", ROLE)["2024-01"] == ("reference", "substitute", True, None, "6", None, None, None)


def test_role_orangebook_generic_mark_only_is_null(con):
    assert rows(con, "600000210", ROLE)["2024-01"] == (None, None, None, None, None, False, True, "code_map")


def test_role_conflict_adopts_substitute(con):
    assert rows(con, "600000220", ROLE)["2024-01"] == ("reference", "substitute", False, True, "4", False, False, "substitute")


def test_role_orangebook_reference_outside_substitute(con):
    assert rows(con, "600000230", ROLE)["2024-01"] == ("reference", "orangebook", None, None, None, True, False, "code_map")


def test_role_bioequiv(con):
    assert rows(con, "600000240", ROLE)["2024-01"][:3] == ("bioequiv", "substitute", True)


def test_role_monthly(con):
    r = rows(con, "600000250", "product_role, is_bioequiv, sub_equiv")
    assert r == {"2024-01": ("reference", False, "4"), "2024-02": ("reference", True, "6")}


def test_substitute_only_row_has_no_state_row(con):
    assert con.execute("select count(*) from product_state_month where product_code = '600000999'").fetchone()[0] == 0
