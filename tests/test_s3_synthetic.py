"""S3 합성 시험: S1 적재 → 비고 파서 → S2 → S3를 손으로 만든 스냅샷으로 돌린다.

기대값은 손으로 정했다: docs/PLAN_P1.md §3(신호·실현 판정)·§4(유효일 규칙, F7 재분류),
docs/NOTE_RULES.md §1~3, docs/DECISIONS.md (7~10차). 구현 출력에서 가져온 값이 없다.
월: 2024-01 ~ 2024-06 (기준일 매월 1일, 분석 끝 2024-06). 제품마다 주성분코드를 따로 둬 F7 재분류가 우연히 맞지 않게 한다.
"""
import datetime as dt

import pandas as pd
import pytest

from pbd.common import connect
from pbd.ledger import s1_load as L
from pbd.ledger import s2_state as S2
from pbd.ledger import s3_ledger as S3

D = dt.date
M = ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05", "2024-06"]
_ING = {}


def ing(pc):
    return _ING.setdefault(pc, f"9{len(_ING):05d}ATB")


def bene(m, pc, note=None, price=100.0, firm="A", name=None, ingr=None):
    return dict(연번=1, 투여="내복", 분류=111.0, 주성분코드=ingr or ing(pc), 제품코드=pc, 제품명=name or f"제품{pc}_(1정)",
                업체명=firm, 규격="1", 단위="정", 상한금액=price, 전일="전문", 비고=note, 주성분명="x",
                소스파일명=f"b{m}.xlsx", 기준일자=f"{m}-01")


def subs(m, pc, note=None, item=198500001.0):
    return {"연번(급여목록상)": 1, "투여": "내복", "분류": 111.0, "주성분코드": ing(pc), "①주성분 및 투여제형코드": ing(pc),
            "제품코드": pc, "②대표코드": 8806519003301.0, "목록정비전코드": None, "제품명": "가", "업체명": "A", "규격": "1",
            "단위": "정", "상한금액": 100.0, "전일": "전문", "정렬순서": 1.0, "②품목기준코드": item,
            "생동인정여부(공고번호)": "○(1)", "생동대조약 여부": None, "생동인정 및 생동대조약 적용일": None, "의약동등": "1",
            "비고(급여정지)": note, "주성분명": "x", "기준일자": f"{m}-01", "소스파일명": f"s{m}.xlsx"}


def rows(pc, months=M, notes=None, prices=None, **kw):
    notes, prices = notes or {}, prices or {}
    return [bene(m, pc, notes.get(m), prices.get(m, 100.0), **kw) for m in months]


B_ROWS = (
    rows("700000010")                                                                       # 배경
    # 가격 예고
    + rows("700000020", notes={"2024-02": "24.2.15., 90원"}, prices={"2024-03": 90.0, "2024-04": 90.0, "2024-05": 90.0, "2024-06": 90.0})  # 실현 (월중)
    + rows("700000030", notes={"2024-02": "24.2.15., 90원"}, prices={m: 95.0 for m in M[2:]})                # 금액 다름
    + rows("700000040", notes={"2024-02": "24.3.1., 90원"})                                                 # 비고 사라지고 가격 유지
    + rows("700000050", M[:2], notes={"2024-02": "24.3.1., 90원"})                                          # 예고 후 삭제
    + rows("700000060", notes={"2024-06": "24.7.1., 90원"})                                                 # 분석 끝 censored
    # 급여정지 예고
    + rows("700000070", notes={"2024-02": "급여정지(24.2.15.)", "2024-03": "집행정지(24.2.15.)", "2024-04": "집행정지(24.2.15.)"})  # 집행정지로 바뀜
    + rows("700000080", notes={"2024-02": "급여정지(24.2.15.)", **{m: "급여정지(24.2.16.)" for m in M[2:]}})  # 하루 밀려 실현
    + rows("700000090", notes={m: "급여정지(24.2.15.)" for m in M[1:]})                                     # 같은 날짜로 실현
    # 기간 명시 정지: 종료 예고 실현 / 새 정지로 미실현
    + rows("700000100", M[:4], notes={m: "급여정지(24.01.10.~24.02.20)" for m in M[:4]})
    + rows("700000110", notes={"2024-01": "급여정지(24.02.01.~24.02.20)", "2024-02": "급여정지(24.02.01.~24.02.20)",
                               **{m: "급여정지(24.3.1.)" for m in M[2:]}})
    # 삭제 예고: 삭제 / 코드 교체(F7)로 미실현
    + rows("700000120", M[:2], notes={"2024-02": "급여삭제(24.2.20.)"})
    + rows("700000130", M[:2], notes={"2024-02": "급여삭제(24.2.20.)"}) + rows("700000131", M[2:])
    # U6: 창 시작 전 집행정지 → 해제 예고(미래) → 다음 달 해제·금액 확인
    + [bene("2024-01", "700000140", "집행정지(21.9.1.)->집행정지해제(24.1.18., 176원)", 200.0),
       bene("2024-02", "700000140", "집행정지 해제 및 상한금액 인하(24.1.18. 고시)", 176.0)]
    # 두 목록 중복 신호 (P4)
    + rows("700000150", notes={"2024-02": "24.3.1., 90원"}, prices={m: 90.0 for m in M[2:]})
    # 등재 예정 (처음 관측)
    + rows("700000160", M[2:], notes={"2024-03": "'24.3.20 등재"})
    # F7: 양도양수(같은 주성분코드·제품명 앞부분, 다른 업체) / 같은 업체 교체 / 기등재 품목의 새 코드
    + rows("700000170", M[:2], firm="A", name="가나정_(1정)", ingr="800001ATB")
    + rows("700000171", M[2:], firm="B", name="가나정_(1정)", ingr="800001ATB")
    + rows("700000180", M[:2], firm="C", name="다라정_(1정)", ingr="800002ATB")
    + rows("700000181", M[2:], firm="C", name="마바정_(1정)", ingr="800002ATB")
    + rows("700000190") + rows("700000191", M[4:])
    # 창 시작 전 급여정지 → 창 안 복귀 (2개월 공백)
    + rows("700000200", notes={"2024-01": "급여정지(23.5.1.)", "2024-02": "급여정지(23.5.1.)"})
    # 전제 신호 미실현 → 복귀 예고 moot
    + rows("700000210", notes={"2024-01": "급여정지(24.02.01.~24.02.20)"})
    # 한 달 빠졌다 돌아옴 → 퇴출(exit_reversed) + reentry
    + rows("700000220", ["2024-01", "2024-02", "2024-04", "2024-05", "2024-06"])
    # 그 달 파일에만 그 달 안 날짜의 가격 비고, 금액 = 그 달 상한금액 (이미 적용된 가격, 사용자 결정 2026-11-15)
    + rows("700000230", notes={"2024-04": "24.4.10., 90원"}, prices={m: 90.0 for m in M[3:]})
    # 2월 파일의 4/10 예고가 3월 파일에 이미 금액으로 반영 (예고 내용이 처음 반영된 스냅샷 = 3월, 사용자 결정 2026-11-15)
    + rows("700000240", notes={"2024-02": "24.4.10., 90원"}, prices={m: 90.0 for m in M[2:]})
)
S_ROWS = [subs("2024-02", "700000150", None)] + [subs(m, "700000010") for m in M]
# 대체조제 비고에도 같은 예고 (P4). 비고(급여정지) 칸에 가격 예고는 드물지만 합치기 규칙 시험용
S_ROWS[0]["비고(급여정지)"] = "24.3.1., 90원"
ATTR_PRODUCT = [("700000130", "198500130"), ("700000131", "198500130"), ("700000160", "198500160"),
                ("700000190", "198500190"), ("700000191", "198500190")]
ATTR_ITEM = [("198500130", False, None, None), ("198500160", False, None, None), ("198500190", False, None, None)]


def build(tmp, b_rows, s_rows):
    c = connect(None)
    pb, ps = tmp / "b.parquet", tmp / "s.parquet"
    pd.DataFrame(b_rows).to_parquet(pb, index=False)
    pd.DataFrame(s_rows).to_parquet(ps, index=False)
    L.build_stg_snapshot(c, "benefit", [pb])
    L.build_stg_snapshot(c, "substitute", [ps])
    L.build_note_token(c)
    c.execute("create table attr_2026_product (product_code varchar, item_code varchar)")
    c.executemany("insert into attr_2026_product values (?, ?)", ATTR_PRODUCT)
    c.execute("create table attr_2026_item (item_code varchar, in_orangebook boolean, ob_ref boolean, ob_generic boolean)")
    c.executemany("insert into attr_2026_item values (?, ?, ?, ?)", ATTR_ITEM)
    S2.build_all(c)
    S3.build_all(c)
    return c


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    c = build(tmp_path_factory.mktemp("s3"), B_ROWS, S_ROWS)
    yield c
    c.close()


def sig(con, pc, typ=None):
    q = """select o.signal_type, o.outcome, o.reason, o.amount_gap, o.date_gap_days from signal_outcome o where o.product_code = ?"""
    args = [pc]
    if typ:
        q += " and o.signal_type = ?"
        args.append(typ)
    return con.execute(q + " order by o.signal_date", args).fetchall()


def ev(con, pc, typ):
    return con.execute("""select observed_month, effective_date, date_source, kind, signal_id is not null,
                                 effective_date_lo, effective_date_hi
                          from event_ledger where product_code = ? and event_type = ? order by observed_month""", [pc, typ]).fetchall()


# ---------------------------------------------------------------- 가격 예고
def test_price_realized_mid_month(con):
    assert sig(con, "700000020") == [("PRICE_PENDING", "realized", None, None, None)]
    assert ev(con, "700000020", "PRICE_CHANGE") == [("2024-03", D(2024, 2, 15), "signal_confirmed", "signaled", True, None, None)]


def test_price_amount_differs(con):
    assert sig(con, "700000030") == [("PRICE_PENDING", "realized_partial", None, 5.0, None)]
    # realized_partial: 신호 날짜를 쓰지 않는다 → 관측월 1일
    assert ev(con, "700000030", "PRICE_CHANGE") == [("2024-03", D(2024, 3, 1), "snapshot_first", "signaled", True, None, None)]


def test_price_note_gone_price_kept(con):
    assert sig(con, "700000040") == [("PRICE_PENDING", "not_realized", "note_gone_price_kept", None, None)]
    assert ev(con, "700000040", "PRICE_CHANGE") == []


def test_price_then_delisted(con):
    assert sig(con, "700000050") == [("PRICE_PENDING", "not_realized", "delisted", None, None)]
    assert ev(con, "700000050", "DELISTING") == [("2024-03", None, "interval", "exit", False, D(2024, 2, 2), D(2024, 3, 1))]


def test_price_censored(con):
    assert sig(con, "700000060") == [("PRICE_PENDING", "censored", None, None, None)]


# ---------------------------------------------------------------- 급여정지 예고
def test_suspend_notice_became_stay(con):
    assert sig(con, "700000070") == [("SUSPEND_SCHEDULED", "not_realized", "stay", None, None)]
    assert ev(con, "700000070", "SUSPEND") == []
    # 집행정지는 다른 처분과 짝짓지 않는다 (NOTE_RULES §3): STAY_START는 신호 없이 비고 날짜
    assert ev(con, "700000070", "STAY_START") == [("2024-03", D(2024, 2, 15), "note_benefit", "in_window", False, None, None)]


def test_suspend_realized_one_day_late(con):
    assert sig(con, "700000080") == [("SUSPEND_SCHEDULED", "realized_partial", None, None, 1)]
    # 정지 이벤트 유효일 = 확인된 비고의 날짜 2/16. 예고 날짜 2/15는 쓰지 않는다
    assert ev(con, "700000080", "SUSPEND") == [("2024-03", D(2024, 2, 16), "note_benefit", "in_window", True, None, None)]


def test_suspend_realized_same_date(con):
    assert sig(con, "700000090") == [("SUSPEND_SCHEDULED", "realized", None, None, None)]   # 같은 날짜: date_gap_days 없음
    assert ev(con, "700000090", "SUSPEND") == [("2024-03", D(2024, 2, 15), "signal_confirmed", "in_window", True, None, None)]


def test_resume_scheduled_realized(con):
    assert [r[:2] for r in sig(con, "700000100")] == [("SUSPEND_SCHEDULED", "realized"), ("RESUME_SCHEDULED", "realized")]
    assert ev(con, "700000100", "RESUME") == [("2024-03", D(2024, 2, 21), "signal_confirmed", "R3b_end", True, None, None)]


def test_resume_scheduled_new_suspension(con):
    assert sig(con, "700000110", "RESUME_SCHEDULED") == [("RESUME_SCHEDULED", "not_realized", "new_suspension", None, None)]


# ---------------------------------------------------------------- 삭제 예고
def test_delisting_scheduled_realized(con):
    assert sig(con, "700000120") == [("DELISTING_SCHEDULED", "realized", None, None, None)]
    assert ev(con, "700000120", "DELISTING") == [("2024-03", D(2024, 2, 20), "signal_confirmed", "exit", True, D(2024, 2, 2), D(2024, 3, 1))]


def test_delisting_scheduled_code_swap(con):
    assert sig(con, "700000130") == [("DELISTING_SCHEDULED", "not_realized", "recode_transfer", None, None)]
    assert ev(con, "700000131", "TRANSFER") == [("2024-03", D(2024, 3, 1), "snapshot_first", "same_item_recode", False, None, None)]
    assert ev(con, "700000130", "DELISTING")[0][3] == "recode_transfer"


# ---------------------------------------------------------------- U6, P4, 등재 예정
def test_u6_stay_release(con):
    assert sig(con, "700000140") == [("STAY_RELEASE_PRICE", "realized", None, None, None)]
    # 신호 달(2024-01)에는 이벤트가 없다. 창 시작 전 집행정지라 STAY_START도 없다
    assert con.execute("select count(*) from event_ledger where product_code = '700000140' and observed_month = '2024-01'").fetchone()[0] == 0
    assert ev(con, "700000140", "STAY_END") == [("2024-02", D(2024, 1, 18), "signal_confirmed", "released", True, None, None)]
    assert ev(con, "700000140", "PRICE_CHANGE") == [("2024-02", D(2024, 1, 18), "signal_confirmed", "stay_released", True, None, None)]


def test_p4_duplicate_signal_merged(con):
    r = con.execute("""select l.source_list, l.merged_two_lists, count(*) over () n from signal_ledger l
                       where l.product_code = '700000150'""").fetchall()
    assert r == [(["benefit", "substitute"], True, 1)]
    # 실현율 분모에 한 번만
    assert con.execute("select count(*) from signal_outcome where product_code = '700000150'").fetchone()[0] == 1


def test_listing_scheduled(con):
    assert sig(con, "700000160") == [("LISTING_SCHEDULED", "realized", None, None, None)]
    assert ev(con, "700000160", "LISTING") == [("2024-03", D(2024, 3, 20), "signal_confirmed", "new_item", True, None, None)]


# ---------------------------------------------------------------- F7, 창 시작 전 정지
def test_f7_transfer_kinds(con):
    assert ev(con, "700000171", "TRANSFER")[0][3] == "transfer_by_name"
    assert ev(con, "700000181", "TRANSFER")[0][3] == "same_firm_swap"
    assert ev(con, "700000191", "LISTING")[0][3] == "new_pack_existing_item"
    assert ev(con, "700000170", "DELISTING")[0][3] == "recode_transfer"


def test_pre_window_suspension_resume(con):
    assert ev(con, "700000200", "SUSPEND") == []                                  # 창 시작 전 정지: 이벤트 없음
    assert ev(con, "700000200", "RESUME") == [("2024-04", D(2024, 3, 1), "snapshot_first", "two_month_gap", False, None, None)]


# ---------------------------------------------------------------- 공통 성질
def test_every_signal_one_outcome(con):
    assert con.execute("""select count(*) from signal_ledger l full join signal_outcome o using (signal_id)
                          where l.signal_id is null or o.signal_id is null or o.outcome is null""").fetchone()[0] == 0


def test_unrealized_signal_dates_not_used(con):
    bad = con.execute("""select count(*) from event_ledger e join signal_outcome o using (signal_id)
                         where o.outcome not in ('realized', 'realized_partial')
                            or (e.date_source = 'signal_confirmed' and o.outcome <> 'realized')""").fetchone()[0]
    assert bad == 0
    # 미실현·censored 신호의 날짜를 쓰는 이벤트가 없다 (같은 제품에서 그 날짜가 다른 근거로 나온 경우 제외)
    bad = con.execute("""select count(*) from signal_outcome o join event_ledger e on e.product_code = o.product_code
                         where o.outcome not in ('realized', 'realized_partial') and e.effective_date = o.signal_date
                           and e.date_source = 'signal_confirmed'""").fetchone()[0]
    assert bad == 0


def _final_parts(c, cut, last):
    """cut 시점에 확정된 행: 상태는 cut 전 달까지, 이벤트는 cut 전 달 관측까지, 신호 결과는 확인 달이 cut 이하"""
    psm = c.execute("""select * exclude (src_file, src_row) from product_state_month where list_month < ? order by product_code, list_month""",
                    [cut]).fetchall() if cut != last else c.execute(
        "select * exclude (src_file, src_row) from product_state_month order by product_code, list_month").fetchall()
    evq = ("select * exclude (exit_reversed, late_realization_of) from event_ledger where observed_month < ? order by event_id" if cut != last
           else "select * exclude (exit_reversed, late_realization_of) from event_ledger order by event_id")     # exit_reversed: 사후 분석용
    sgq = ("select * exclude (realized_after_followup, check_month_by_date) from signal_outcome where check_month <= ? order by signal_id" if cut != last
           else "select * exclude (realized_after_followup, check_month_by_date) from signal_outcome order by signal_id")
    return psm, c.execute(evq, [cut] if cut != last else []).fetchall(), c.execute(sgq, [cut] if cut != last else []).fetchall()


def test_incremental_equals_batch(con, tmp_path):
    """한 달씩 추가: cut마다 그때 확정된 행만 쌓고, 마지막 달까지 쌓은 결과 = 한 번에 만든 결과
    (exposure_frac·복귀 첫 달 state_eom 고쳐 쓰기, F7 재분류는 다음 달에 확정된다)"""
    store_p, store_e, store_s = {}, {}, {}
    for cut in M:
        b = [r for r in B_ROWS if r["기준일자"][:7] <= cut]
        s = [r for r in S_ROWS if r["기준일자"][:7] <= cut]
        c = build(tmp_path / cut, b, s) if (tmp_path / cut).mkdir() is None else None
        try:
            p, e, g = _final_parts(c, cut, M[-1])
        finally:
            c.close()
        for r in p:
            store_p.setdefault((r[0], r[1]), r)                    # (product_code, list_month): 처음 확정된 값
        for r in e:
            store_e.setdefault(r[0], r)
        for r in g:
            store_s.setdefault(r[0], r)
    bp, be, bs = _final_parts(con, M[-1], M[-1])
    assert sorted(store_p.values()) == sorted(bp)
    assert sorted(store_e.values()) == sorted(be)
    assert sorted(store_s.values()) == sorted(bs)


def test_resume_moot_when_parent_not_realized(con):
    r = con.execute("""select signal_type, outcome, reason, parent_signal_id from signal_outcome where product_code = '700000210'
                       order by signal_type desc""").fetchall()
    assert r == [("SUSPEND_SCHEDULED", "not_realized", "note_gone", None),
                 ("RESUME_SCHEDULED", "moot", None, "SUSPEND_SCHEDULED:700000210:2024-02-01")]


def test_exit_reversed_and_reentry(con):
    d = con.execute("""select observed_month, kind, exit_reversed from event_ledger where product_code = '700000220'
                       and event_type = 'DELISTING'""").fetchall()
    assert d == [("2024-03", "exit", True)]
    assert ev(con, "700000220", "LISTING")[0][:4] == ("2024-04", D(2024, 4, 1), "snapshot_first", "reentry")


def test_notice_date_on_partial_price(con):
    r = con.execute("""select effective_date, notice_date from event_ledger where product_code = '700000030' and event_type = 'PRICE_CHANGE'""").fetchall()
    assert r == [(D(2024, 3, 1), D(2024, 2, 15))]      # 유효일은 관측월 1일, 예고 날짜는 참고 컬럼



def test_in_month_applied_price_note(con):
    """그 달 파일의 가격 비고에 그 달 안 날짜(4/10)가 있고 금액이 그 달 상한금액(90)과 같으면: 신호를 만들지 않고,
    가격 변경은 kind 그대로(unsignaled), 유효일 = 비고 날짜, date_source = note_benefit (사용자 결정 2026-11-15)"""
    assert sig(con, "700000230") == []
    assert ev(con, "700000230", "PRICE_CHANGE") == [("2024-04", D(2024, 4, 10), "note_benefit", "unsignaled", False, None, None)]
    assert con.execute("select in_month_dated from note_token where product_code = '700000230'").fetchall() == [(True,)]


def test_signal_confirmed_at_first_reflected_snapshot(con):
    """확인 스냅샷 = 예고 내용이 처음 반영된 스냅샷 (날짜 비교 없음, 사용자 결정 2026-11-15).
    2월 발행 4/10·90원 예고, 3월 파일 금액 90 → 확인 3월, realized, 가격 변경 3월 signaled 유효일 4/10 (signal_confirmed)"""
    r = con.execute("""select check_month, check_month_by_date, outcome from signal_outcome where product_code = '700000240'""").fetchall()
    assert r == [("2024-03", "2024-05", "realized")]
    assert ev(con, "700000240", "PRICE_CHANGE") == [("2024-03", D(2024, 4, 10), "signal_confirmed", "signaled", True, None, None)]
