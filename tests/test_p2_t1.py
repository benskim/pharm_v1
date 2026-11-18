"""P2 T1 예측 마트 시험. 기대값은 손으로 만든 합성 데이터와 원본 CSV에서 온다 (구현 출력으로 만들지 않는다)."""
from __future__ import annotations

import pytest

from pbd.common import DB_PATH, RAW, connect
from pbd.forecast import t1_panel as T1


def _syn(con):
    """성분 A: 2018-01부터 계속 관측(자료 시작월 첫 관측). 성분 B: 2018-02 비공개, 2018-03에 처음 나타남, 2018-04 비공개"""
    con.execute("""create table stg_usage as select * from (values
        ('A', '2018-01', 'OUT', 10.0, 1.0, 100.0), ('A', '2018-01', 'OUT', 5.0, 1.0, 50.0), ('A', '2018-01', 'IN', 99.0, 9.0, 1.0),
        ('A', '2018-02', 'OUT', 7.0, 2.0, 70.0), ('A', '2018-03', 'OUT', 8.0, 2.0, 80.0), ('A', '2018-04', 'OUT', 9.0, 2.0, 90.0),
        ('B', '2018-03', 'OUT', 3.0, 1.0, 30.0), ('B', '2018-02', 'IN', 4.0, 1.0, 40.0))
        t(c4, month, in_out, claim_qty, claim_cnt, claim_amt)""")
    con.execute("""create table usage_c4_month as select * from (values
        ('A', '2018-01', true, true, '2018-01', '2018-01'), ('A', '2018-02', true, true, '2018-01', '2018-01'),
        ('A', '2018-03', true, true, '2018-01', '2018-01'), ('A', '2018-04', true, true, '2018-01', '2018-01'),
        ('B', '2018-01', false, false, '2018-02', '2018-03'), ('B', '2018-02', true, false, '2018-02', '2018-03'),
        ('B', '2018-03', true, true, '2018-02', '2018-03'), ('B', '2018-04', false, false, '2018-02', '2018-03'))
        t(c4, month, observed_any, observed_out, first_observed_month, first_observed_month_out)""")


def test_unobserved_is_null_not_zero_and_first_month_excluded():
    con = connect(None)
    _syn(con)
    T1.build_panel(con, "2018-01", "2018-04")
    r = {(c, m): (o, q, u) for c, m, o, q, u in
         con.execute("select c4, month, observed, claim_qty, usable from fc_panel").fetchall()}
    assert r[("A", "2018-01")] == (True, 15.0, True)        # 원외 두 행 합, 원내 제외. 자료 시작월 첫 관측은 쓴다
    assert r[("B", "2018-02")] == (False, None, False)      # 원내만 있는 달 = 원외 미관측 (0이 아님)
    assert r[("B", "2018-03")] == (True, 3.0, False)        # 첫 관측월(등장)은 학습·평가에서 뺀다
    assert r[("B", "2018-04")] == (False, None, False)
    assert r[("A", "2018-04")] == (True, 9.0, True)


@pytest.mark.parametrize("month,observed,first,expected", [
    ("2018-01", True, "2018-01", True), ("2019-05", True, "2019-05", False), ("2019-06", True, "2019-05", True),
    ("2019-06", False, "2019-05", False)])
def test_usable_flag(month, observed, first, expected):
    assert T1.usable_flag(month, observed, first, "2018-01") is expected


def test_raw_reaggregate_synthetic(tmp_path):
    """손으로 만든 원본 형식 CSV 2개: 성분코드 0 채움, 원외만, 수량·건수 합"""
    head = "STD_YYYYMM,WK_COMPN_4,AGE_G,SEX_TYPE,SIDO,YOYANG_CLSFC_CD_ADJ,OUT_IN_HOS,PRSCRPTN_TNDN_CNT,PRSCRPTN_AMT,PATIENT_CNT,PRSCRPTN_TOT_USE\n"
    (tmp_path / "MEDICINE_201801.csv").write_text(head + "201801,0123,10,1,11,3,OUT,2,100,2,10.5\n"
                                                  "201801,0123,20,2,26,1,OUT,1,50,1,4.5\n201801,0123,20,2,26,1,IN,9,9,9,99\n",
                                                  encoding="utf-8")
    (tmp_path / "MEDICINE_201802.csv").write_text(head + "201802,0123,10,1,11,3,OUT,3,1,1,7\n", encoding="utf-8")
    con = connect(None)
    r = T1.raw_reaggregate(con, str(tmp_path / "MEDICINE_*.csv")).sort_values("month")
    assert r[["c4", "month", "qty", "cnt"]].values.tolist() == [["0123", "2018-01", 15.0, 3.0], ["0123", "2018-02", 7.0, 3.0]]


@pytest.fixture(scope="module")
def db():
    if not DB_PATH.exists():
        pytest.skip("DB 없음")
    con = connect(DB_PATH, read_only=True)
    if not con.execute("select count(*) from duckdb_tables() where table_name = 'fc_panel'").fetchone()[0]:
        pytest.skip("fc_panel 없음 (make p2-t1)")
    return con


def test_mart_equals_raw_two_files(db):
    """원본 CSV 2개(2018-01, 2025-12)를 다시 읽은 합 = 마트 합 (전 기간 검사는 make p2-t1이 한다)"""
    files = sorted((RAW / "usage").glob("cmpn_stat_*/MEDICINE_201801*.csv")) + sorted((RAW / "usage").glob("cmpn_stat_*/MEDICINE_202512*.csv"))
    assert len(files) == 2
    con = connect(None)
    con.execute(f"attach '{DB_PATH.as_posix()}' as src (read_only)")
    for f in files:
        raw = T1.raw_reaggregate(con, str(f))
        m = raw["month"].iloc[0]
        mart = con.execute("select c4, claim_qty, claim_cnt from src.fc_panel where month = ? and observed", [m]).df()
        j = raw.merge(mart, on="c4", how="outer", indicator=True)
        assert (j["_merge"] == "both").all(), j[j["_merge"] != "both"].head()
        assert (j["qty"] - j["claim_qty"]).abs().max() < 1e-6 and (j["cnt"] - j["claim_cnt"]).abs().max() < 1e-6


def test_target_selection(db):
    t = db.execute("select * from fc_target").df()
    assert set(t["cls"]) <= set(T1.MAIN_CLASSES) and len(t) > 0
    n = db.execute("""select count(*) from fc_panel p join fc_target t using (c4) where not p.observed""").fetchone()[0]
    assert n == 0                                            # 대상은 96개월 모두 관측
    assert (t["qty_rank"] <= T1.P["top_n"]).all()
