"""S1 적재기 시험 (손으로 만든 합성 데이터).

기대값은 손으로 정했다: schema_map.yaml의 별칭·결측 규칙, DATA.md의 코드 폭 규칙,
DECISIONS.md의 시도 통합(51→42, 52→45), NOTE_RULES.md §2의 is_future 정의.
"""
import datetime as dt

import pandas as pd
import pytest

from pbd.common import connect
from pbd.ledger import s1_load as L

D = dt.date


@pytest.fixture
def con():
    c = connect(None)
    yield c
    c.close()


def _parquet(tmp_path, name, rows):
    p = tmp_path / name
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


def test_benefit_columns_nulls_codes(con, tmp_path):
    a = _parquet(tmp_path, "benefit_a.parquet", [
        # 2022-11: 분류 없음, 식약분류에 값. 목록정비전코드 실수형 문자열. 비고 빈 문자열
        dict(연번=1, 투여="내복", 분류=None, 식약분류=214.0, 주성분코드="101301ATB", 제품코드="642100010", 제품명="가",
             업체명="A", 규격="1", 단위="정", 상한금액=100.0, 전일="전문", 비고="", 목록정비전코드="645101520.0",
             주성분명="x", 소스파일명="f1.xlsx", 기준일자="2022-11-01"),
    ])
    b = _parquet(tmp_path, "benefit_b.parquet", [
        # 2025-11: 8자리 제품코드, 'nan' 목록정비전코드, 비고 있음
        dict(연번=1, 투여="내복", 분류=396, 주성분코드="101301ATB", 제품코드="52300081", 제품명="나",
             업체명="B", 규격="1", 단위="정", 상한금액=None, 전일="전문", 비고=" 산정불가 ", 목록정비전코드="nan",
             주성분명="x", 소스파일명="f2.xlsx", 기준일자="2025-11-01", 주성분개수=1.0),
    ])
    L.build_stg_snapshot(con, "benefit", [a, b])
    rows = con.execute("""select list_month, base_date, product_code, product_code_raw, class_code, prev_code, note, c4, price
                          from stg_benefit order by list_month""").fetchall()
    assert rows[0] == ("2022-11", D(2022, 11, 1), "642100010", "642100010", 214, "645101520", None, "1013", 100.0)
    assert rows[1] == ("2025-11", D(2025, 11, 1), "052300081", "52300081", 396, None, "산정불가", "1013", None)


def test_substitute_column_aliases(con, tmp_path):
    base = dict(투여="내복", 분류=214, 주성분코드="101301ATB", 제품코드="642100010", 목록정비전코드="None",
                제품명="가", 업체명="A", 규격="1", 단위="정", 상한금액=100.0, 전일="전문", 정렬순서=1.0,
                **{"연번(급여목록상)": 1, "②대표코드": 8806519003301.0, "②품목기준코드": 198501814.0,
                   "생동인정여부(공고번호)": "○(8064)", "생동인정 및 생동대조약 적용일": None, "의약동등": "6",
                   "비고(급여정지)": None, "주성분명": "x", "소스파일명": "s.xlsx"})
    a = _parquet(tmp_path, "sub_a.parquet", [dict(base, 기준일자="2022-10-01",
                 **{"①주성분 및투여제형코드": "101301ATB", "생동대조약여부": "○"})])          # 띄어쓰기 없는 이름
    b = _parquet(tmp_path, "sub_b.parquet", [dict(base, 기준일자="2024-01-01",
                 **{"①주성분 및 투여제형코드": "101302ATB", "생동대조약 여부": None})])
    L.build_stg_snapshot(con, "substitute", [a, b])
    rows = con.execute("""select list_month, group_code, ref_drug, bioeq, item_code, rep_code, prev_code, equiv
                          from stg_substitute order by list_month""").fetchall()
    assert rows[0] == ("2022-10", "101301ATB", True, True, "198501814", "8806519003301", None, "6")
    assert rows[1] == ("2024-01", "101302ATB", False, True, "198501814", "8806519003301", None, "6")


def test_note_token_is_future_uses_first_of_month(con, tmp_path):
    """적용 기준일 = 그 달 1일 (사용자 결정 2026-11-15). 원본 기준일자 2025-03-04는 base_date_raw로만 남는다.
    3/3 가격 예고와 3/5 정지는 3/1 기준 미래. 둘 다 날짜가 그 달 2일~말일이라 in_month_dated"""
    p = _parquet(tmp_path, "benefit.parquet", [
        dict(연번=1, 투여="내복", 분류=214, 주성분코드="101301ATB", 제품코드="642100010", 제품명="가", 업체명="A",
             규격="1", 단위="정", 상한금액=100.0, 전일="전문", 비고="25.3.3., 90원 / 급여정지(25.3.5.)",
             목록정비전코드="nan", 주성분명="x", 소스파일명="f.xlsx", 기준일자="2025-03-04"),
        dict(연번=2, 투여="내복", 분류=214, 주성분코드="101301ATB", 제품코드="642100020", 제품명="나", 업체명="A",
             규격="1", 단위="정", 상한금액=100.0, 전일="전문", 비고="급여정지(25.2.1.~25.4.30)",
             목록정비전코드="nan", 주성분명="x", 소스파일명="f.xlsx", 기준일자="2025-03-04"),
    ])
    L.build_stg_snapshot(con, "benefit", [p])
    L.create_empty_stg(con, "substitute")
    L.build_note_token(con)
    got = con.execute("""select product_code, seq, token_type, token_date, is_future, token_date_end, is_future_end, amount, in_month_dated
                         from note_token order by product_code, seq""").fetchall()
    assert got == [
        ("642100010", 1, "R2", D(2025, 3, 3), True, None, None, 90, True),
        ("642100010", 2, "R3", D(2025, 3, 5), True, None, None, None, True),
        ("642100020", 1, "R3b", D(2025, 2, 1), False, D(2025, 4, 30), True, None, False),
    ]
    assert con.execute("select distinct base_date, base_date_raw from stg_benefit").fetchall() == [(D(2025, 3, 1), D(2025, 3, 4))]


def test_usage_sido_merge_and_observed(con, tmp_path):
    d = tmp_path / "usage" / "cmpn_stat_2024(2026.10.28.)"
    d.mkdir(parents=True)
    hdr = "STD_YYYYMM,WK_COMPN_4,AGE_G,SEX_TYPE,SIDO,YOYANG_CLSFC_CD_ADJ,OUT_IN_HOS,PRSCRPTN_TNDN_CNT,PRSCRPTN_AMT,PATIENT_CNT,PRSCRPTN_TOT_USE\n"
    (d / "MEDICINE_202401 (2026.10.28.).csv").write_text(
        hdr + "202401,0101,60,2,51,3,OUT,2,1000,2,10.50\n202401,0202,60,1,11,1,IN,1,0,1,0.00\n", encoding="utf-8")
    (d / "MEDICINE_202402 (2026.10.28.).csv").write_text(
        hdr + "202402,0101,60,2,42,3,OUT,1,500,1,5.00\n", encoding="utf-8")
    L.build_stg_usage(con, str(tmp_path / "usage" / "cmpn_stat_*" / "MEDICINE_*.csv"), {"51": "42", "52": "45"})
    rows = con.execute("select month, c4, sido_raw, sido, claim_amt, claim_qty, observed from stg_usage order by month, c4").fetchall()
    assert rows == [("2024-01", "0101", "51", "42", 1000, 10.5, True),
                    ("2024-01", "0202", "11", "11", 0, 0.0, True),      # 0 값 행은 그대로 둔다
                    ("2024-02", "0101", "42", "42", 500, 5.0, True)]
    L.build_usage_obs(con, "2024-01", "2024-02")
    obs = con.execute("select c4, month, observed_any, observed_out, first_observed_month from usage_c4_month order by c4, month").fetchall()
    assert obs == [("0101", "2024-01", True, True, "2024-01"), ("0101", "2024-02", True, True, "2024-01"),
                   ("0202", "2024-01", True, False, "2024-01"), ("0202", "2024-02", False, False, "2024-01")]   # 미관측 ≠ 0


def test_row_kind_product_code_rule(con, tmp_path):
    """숫자 8자리만 0을 채운다. 7자리 이하는 채우지 않는다. 0 채움 후 숫자 9자리가 아니면 non_product.
    non_product 행은 stg에 남고 note_token에서 빠진다. (사용자 결정 2026-11-14)"""
    rows = []
    for i, code in enumerate(["642100010", "52300081", "1234567", "243001ACS"], 1):
        rows.append(dict(연번=i, 투여="내복", 분류=214, 주성분코드="101301ATB", 제품코드=code, 제품명="가", 업체명="A",
                         규격="1", 단위="정", 상한금액=100.0, 전일="전문", 비고="급여정지(22.9.5.)",
                         목록정비전코드="nan", 주성분명="x", 소스파일명="f.xlsx", 기준일자="2022-09-01"))
    p = _parquet(tmp_path, "benefit.parquet", rows)
    L.build_stg_snapshot(con, "benefit", [p])
    L.create_empty_stg(con, "substitute")
    got = con.execute("select row_no, product_code, row_kind from stg_benefit order by row_no").fetchall()
    assert got == [(1, "642100010", "product_candidate"), (2, "052300081", "product_candidate"),
                   (3, "1234567", "non_product"), (4, "243001ACS", "non_product")]
    L.build_note_token(con)
    assert con.execute("select product_code from note_token order by 1").fetchall() == [("052300081",), ("642100010",)]
