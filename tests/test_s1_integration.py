"""S1 통합 시험: make p1-s1로 만든 data/interim/pbd.duckdb를 검사한다.

기대값 출처
- 원본 행 수: data/raw 파일을 이 시험에서 직접 센다 (적재기 코드를 쓰지 않음)
- F2 유형별 행 수: reference/precheck_v3_output.md
- 사용통계 재집계: data/sample/fact_cmpn_stat_* (S0 대조 범위 2023~2025)
"""
import re

import duckdb
import pytest

from pbd.common import DB_PATH, RAW, ROOT

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="pbd.duckdb 없음 (make p1-s1)")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(DB_PATH), read_only=True)
    c.execute("SET enable_progress_bar=false")
    yield c
    c.close()


def raw_count(sql_src):
    c = duckdb.connect()
    try:
        return c.execute(f"select count(*) from {sql_src}").fetchone()[0]
    finally:
        c.close()


@pytest.mark.parametrize("lst", ["benefit", "substitute"])
def test_raw_rows_equal_stg_rows(con, lst):
    src = f"read_parquet('{(RAW / lst).as_posix()}/{lst}_snapshot_table_*.parquet', union_by_name=true)"
    assert con.execute(f"select count(*) from stg_{lst}").fetchone()[0] == raw_count(src)


def test_usage_rows_equal_raw(con):
    src = f"read_csv('{(RAW / 'usage').as_posix()}/cmpn_stat_*/MEDICINE_*.csv', header=true, all_varchar=true)"
    assert con.execute("select count(*) from stg_usage").fetchone()[0] == raw_count(src)


@pytest.mark.parametrize("lst", ["benefit", "substitute"])
def test_codes_and_keys(con, lst):
    bad = con.execute(f"""select count(*) from stg_{lst} where row_kind = 'product_candidate'
                          and (not regexp_full_match(product_code, '[0-9]{{9}}') or length(c4) <> 4)""").fetchone()[0]
    dup = con.execute(f"select count(*) from (select product_code, list_month from stg_{lst} group by all having count(*) > 1)").fetchone()[0]
    months = con.execute(f"select count(distinct list_month) from stg_{lst}").fetchone()[0]
    assert (bad, dup, months) == (0, 0, 48)


@pytest.mark.parametrize("lst", ["benefit", "substitute"])
def test_row_kind_counts_match_raw(con, lst):
    """non_product 행 수 = 원본에서 숫자 8·9자리가 아닌 제품코드 행 수 (원본을 직접 센다)."""
    src = f"read_parquet('{(RAW / lst).as_posix()}/{lst}_snapshot_table_*.parquet', union_by_name=true)"
    want = raw_count(f"(select * from {src} where not regexp_full_match(coalesce(trim(제품코드), ''), '[0-9]{{8,9}}'))")
    got = con.execute(f"select count(*) from stg_{lst} where row_kind = 'non_product'").fetchone()[0]
    assert got == want


def test_note_token_excludes_non_product(con):
    n = con.execute("""select count(*) from note_token t join stg_benefit s using (src_file, src_row)
                       where t.source_list = 'benefit' and s.row_kind = 'non_product'""").fetchone()[0]
    assert n == 0


def test_every_note_row_has_token(con):
    for lst in ("benefit", "substitute"):
        n = con.execute(f"""select count(*) from stg_{lst} s where note is not null and row_kind = 'product_candidate' and not exists
            (select 1 from note_token t where t.source_list = '{lst}' and t.src_file = s.src_file and t.src_row = s.src_row)""").fetchone()[0]
        assert n == 0


def _reference_f2():
    text = (ROOT / "reference/precheck_v3_output.md").read_text(encoding="utf-8")
    block = text.split("## F2 비고 유형별 행·제품 수")[1].split("\n## ")[0]
    rows = {}
    for line in block.strip().splitlines()[1:]:
        k, n, _ = [x.strip() for x in line.split("|")]
        rows[k] = int(n)
    return rows


def test_sample_f2_row_counts(con):
    """표본 구간(2024-01~2025-12) 급여목록 비고 행의 유형별 수 = reference F2.

    F2 분류(precheck_v3.py)는 행 단위, 우선순위: 산정불가(단독) > 해제(R5·R6) > 집행정지+가격예고 > 집행정지 > 급여정지 > 등재일 > 가격예고.
    """
    got = dict(con.execute("""
        with r as (select src_file, src_row, any_value(note_raw) note, list(token_type) ts from note_token
                   where source_list = 'benefit' and list_month between '2024-01' and '2025-12' group by 1, 2)
        select case when note = '산정불가' then '산정불가'
                    when list_has_any(ts, ['R5', 'R6']) then '해제'
                    when list_contains(ts, 'R4') and list_contains(ts, 'R2') then '집행정지+가격예고'
                    when list_contains(ts, 'R4') then '집행정지'
                    when list_has_any(ts, ['R3', 'R3b']) then '급여정지'
                    when list_contains(ts, 'R1') then '등재일'
                    when list_contains(ts, 'R2') then '가격예고' else '기타' end k, count(*)
        from r group by 1""").fetchall())
    assert got == _reference_f2()


def test_no_unresolved_in_sample_period(con):
    n = con.execute("select count(*) from note_token where token_type = 'unresolved' and list_month >= '2024-01'").fetchone()[0]
    assert n == 0


def test_usage_reaggregate_equals_sample_fact(con):
    fact = f"read_parquet('{(ROOT / 'data/sample').as_posix()}/fact_cmpn_stat_*.parquet')"
    diff = con.execute(f"""
        with r as (select replace(month, '-', '') m, c4, in_out, sum(claim_cnt) cnt, sum(claim_amt) amt,
                          sum(cast(claim_qty as decimal(20,2))) qty
                   from stg_usage where month between '2023-01' and '2025-12' group by all),
             f as (select STD_YYYYMM m, WK_COMPN_4 c4, OUT_IN_HOS in_out, PRSCRPTN_TNDN_CNT cnt, PRSCRPTN_AMT amt,
                          cast(PRSCRPTN_TOT_USE as decimal(20,2)) qty from {fact})
        select count(*) filter (where r.m is null or f.m is null or r.cnt <> f.cnt or r.amt <> f.amt or r.qty <> f.qty)
        from r full join f using (m, c4, in_out)""").fetchone()[0]
    assert diff == 0


def test_sido_merged(con):
    assert con.execute("select count(*) from stg_usage where sido in ('51', '52')").fetchone()[0] == 0
    assert con.execute("select count(distinct sido) from stg_usage").fetchone()[0] == 17


def test_usage_obs_grid(con):
    n_c4, n = con.execute("select count(distinct c4), count(*) from usage_c4_month").fetchone()
    assert n == n_c4 * 96
    # 관측 격자와 stg_usage가 어긋나지 않는다
    assert con.execute("""select count(*) from usage_c4_month g where observed_any <>
        exists (select 1 from stg_usage u where u.c4 = g.c4 and u.month = g.month)""").fetchone()[0] == 0


def test_inventory_complete(con):
    rows = con.execute("select source_list, count(*), count(*) filter (where missing) from snapshot_inventory group by 1 order by 1").fetchall()
    assert rows == [("benefit", 48, 0), ("substitute", 48, 0)]
    # 2022-11 급여목록은 분류 대신 식약분류
    cols = con.execute("select columns_nonblank from snapshot_inventory where source_list='benefit' and list_month='2022-11'").fetchone()[0]
    assert "식약분류" in cols and "분류" not in cols
