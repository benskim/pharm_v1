"""S5 시험.

기대값 출처: B1 탈락 단계·변화량은 손으로 만든 합성 표(아래 주석에 계산), OLS는 손 계산,
B2 군집 분포·6282 사례와 C 그룹 가격 분산은 reference/precheck_v3_output.md의 F8·F10 (2024-01~2025-12 표본).
"""
import math
import re

import duckdb
import numpy as np
import pandas as pd
import pytest

from pbd.common import DB_PATH, ROOT
from pbd.ledger import s5_market as S

REF = (ROOT / "reference/precheck_v3_output.md").read_text(encoding="utf-8")


def ref_table(title: str) -> list[list[str]]:
    block = REF.split(f"## {title}\n", 1)[1].split("\n\n", 1)[0].strip().splitlines()
    return [[c.strip() for c in line.split("|")] for line in block[1:]]


def test_ols_hand_values():
    b, a = S.ols_slope(np.array([0.0, 1.0, 2.0]), np.array([1.0, 3.0, 5.0]))
    assert (round(b, 9), round(a, 9)) == (2.0, 1.0)


def test_boot_deterministic():
    df = pd.DataFrame({"c4": list("aabbccdd"), "x": [-.1, -.2, -.05, -.3, -.1, 0, -.2, -.15], "y": [-.08, -.1, -.06, -.2, -.02, .01, -.1, -.1]})
    assert S.boot_slope(df, 7, 200) == S.boot_slope(df, 7, 200)


@pytest.fixture()
def syn():
    """손으로 만든 2개월 합성 자료 (2024-01 → 2024-02)
    A001: p1 100→90, p2 200→200, p3 reimbursed→suspended(바구니 밖). 목록 변화 = (ln 0.9 + 0)/2. 원외 100원→90원 → ln 0.9. 대상
    B002: p4 50→50 → 3_no_list_change
    C003: p5 100→80, 2024-02 원외 미관측 → 2_usage_unobserved (1월은 관측 → 일부 미관측)
    D004: p6 100→90, PRICE_CHANGE 유효일 2024-02-15 → 4_mid_month_change
    E005: p7 unpriced 두 달 → 1_no_basket
    원내(IN) 행은 단가에 넣지 않는다 (A001 IN 1000원/1개)"""
    con = duckdb.connect()
    con.execute("create table s3_cal as select * from (values ('2024-01', null, 1), ('2024-02', '2024-01', 2)) t(list_month, prev_month, mi)")
    rows = [("p1", "A001", 100, 90), ("p2", "A001", 200, 200), ("p4", "B002", 50, 50), ("p5", "C003", 100, 80), ("p6", "D004", 100, 90)]
    psm = []
    for pc, c4, a, b in rows:
        psm += [(pc, "2024-01", c4, "reimbursed", a), (pc, "2024-02", c4, "reimbursed", b)]
    psm += [("p3", "2024-01", "A001", "reimbursed", 300), ("p3", "2024-02", "A001", "suspended", 300),
            ("p7", "2024-01", "E005", "unpriced", None), ("p7", "2024-02", "E005", "unpriced", None)]
    con.execute("create table product_state_month (product_code varchar, list_month varchar, c4 varchar, state_asof varchar, price_listed double)")
    con.executemany("insert into product_state_month values (?, ?, ?, ?, ?)", psm)
    con.execute("""create table event_ledger as select * from (values ('D004', date '2024-02-15', 'PRICE_CHANGE', '2024-03'),
                   ('A001', date '2024-02-01', 'PRICE_CHANGE', '2024-02')) t(market_c4, effective_date, event_type, observed_month)""")
    obs = [(c4, m, not (c4 == "C003" and m == "2024-02")) for c4 in ("A001", "B002", "C003", "D004", "E005") for m in ("2024-01", "2024-02")]
    con.execute("create table usage_c4_month (c4 varchar, month varchar, observed_out boolean)")
    con.executemany("insert into usage_c4_month values (?, ?, ?)", obs)
    con.execute("""create table stg_usage as select * from (values
        ('A001', '2024-01', 'OUT', 600.0, 6.0), ('A001', '2024-01', 'OUT', 400.0, 4.0), ('A001', '2024-01', 'IN', 1000.0, 1.0),
        ('A001', '2024-02', 'OUT', 900.0, 10.0), ('A001', '2024-02', 'IN', 1000.0, 1.0),
        ('B002', '2024-01', 'OUT', 500.0, 10.0), ('B002', '2024-02', 'OUT', 500.0, 10.0),
        ('C003', '2024-01', 'OUT', 500.0, 5.0),
        ('D004', '2024-01', 'OUT', 500.0, 5.0), ('D004', '2024-02', 'OUT', 450.0, 5.0),
        ('E005', '2024-01', 'OUT', 500.0, 5.0), ('E005', '2024-02', 'OUT', 500.0, 5.0)) t(c4, month, in_out, claim_amt, claim_qty)""")
    yield con
    con.close()


def test_b1_pairs_synthetic(syn):
    S._usage(syn)
    p = S.build_b1_pairs(syn).set_index("c4")
    assert p["stage"].to_dict() == {"A001": "target", "B002": "3_no_list_change", "C003": "2_usage_unobserved",
                                    "D004": "4_mid_month_change", "E005": "1_no_basket"}
    a = p.loc["A001"]
    assert (a["n_basket"], a["n_changed"]) == (2, 1)
    assert math.isclose(a["dlog_list"], math.log(0.9) / 2)
    assert math.isclose(a["dlog_up"], math.log(0.9))
    assert not p.loc["C003", "usage_never_observed"]
    st, _ = S.b1_summary(p.reset_index(), seed=1)
    assert dict(zip(st["stage"], st["pairs"]))["0_all_pairs"] == 5


db = pytest.mark.skipif(not DB_PATH.exists(), reason="pbd.duckdb 없음")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(DB_PATH), read_only=True)
    if not c.execute("select count(*) from information_schema.tables where table_name = 'event_ledger'").fetchone()[0]:
        pytest.skip("event_ledger 없음")
    S._usage(c)
    yield c
    c.close()


@db
def test_f8_cluster_distribution_sample(con):
    """reference F8: 표본 구간(2024-02~2025-12)의 실질 신규 품목을 낸 업체 수 분포. reference 정의의 진입 재분류 표(s3_entry_exit_sample)로 센다"""
    got = dict(con.execute("""with e as (select x.month, q.c4, q.firm from s3_entry_exit_sample x
            join product_state_month q on q.product_code = x.product_code and q.list_month = x.month
            where x.side = 'entry' and x.kind = 'new_item'),
          g as (select c4, month, count(distinct firm) nf from e group by 1, 2)
        select case when nf >= 10 then '10+' when nf >= 5 then '5~9' when nf >= 2 then '2~4' else '1' end, count(*) from g group by 1""").fetchall())
    want = {r[0]: int(r[1]) for r in ref_table("F8 실질 신규의 성분×월 군집")}
    assert got == want


@db
def test_f8_case_6282(con):
    """reference F8 사례: 엠파글리플로진(6282) 2025-08~12 제품·업체·최저/최고가·원외 단가 (목록 원본 행 기준)"""
    s = S.b2_case_series(con, "6282", "2025-10").set_index("month")
    for ym, n, nf, mn, mx, up in ref_table("F8 사례: empagliflozin(6282) 목록 vs 사용정보"):
        r = s.loc[ym]
        assert (r["products_all"], r["firms_all"], r["up"]) == (int(n), int(nf), float(up))
    # 최저·최고가는 reimbursed 제품 기준이라 시행 전 제품이 있는 달은 다를 수 있다: 8·9·12월은 같다
    for ym, n, nf, mn, mx, up in ref_table("F8 사례: empagliflozin(6282) 목록 vs 사용정보"):
        if ym in ("2025-08", "2025-09", "2025-12"):
            assert (s.loc[ym, "min_price"], s.loc[ym, "max_price"]) == (float(mn), float(mx))


@db
def test_f10_group_dispersion_sample(con):
    t = S.build_c(con, "2025-12")["c_f10_sample_check"].iloc[0]
    (g, g2, med, p90), = ref_table("F10 대체조제 그룹(①코드×주성분코드) 가격 분산, 2025-12")
    assert (int(t["groups"]), int(t["groups_ge2"]), float(t["med_max_min"]), float(t["p90_max_min"])) == (int(g), int(g2), float(med), float(p90))


@db
def test_c_rank_rules(con):
    c = S.build_c(con, "2025-12")
    r = c["c_rank"]
    assert (r["n_products"] >= 2).all() and (r["c4_obs_months"] == 12).all()
    assert not any(re.search("saving|절감", col) for col in r.columns)       # 절감액은 계산하지 않는다
    # 한 그룹을 직접 센 값과 대조
    top = r.iloc[0]
    n, mn, mx = con.execute("""select count(*), min(price_listed), max(price_listed) from product_state_month
        where list_month = '2025-12' and in_substitute_list and state_asof = 'reimbursed' and price_listed > 0
          and sub_group_code = ? and ingredient_code = ?""", [top["grp"], top["ingredient_code"]]).fetchone()
    assert (top["n_products"], top["min_price"], top["max_price"]) == (n, mn, mx)


@db
def test_b1_outputs_consistent():
    p = pd.read_csv(ROOT / "outputs/s5/b1_pairs.csv", dtype={"c4": str})
    st = pd.read_csv(ROOT / "outputs/s5/b1_selection.csv").set_index("stage")["pairs"]
    assert st["0_all_pairs"] == len(p)
    assert st[[s for s in st.index if s[0].isdigit() and s[1] == "_" and s != "0_all_pairs"]].sum() + st["target"] == len(p)
    assert st["2a_usage_never_observed"] + st["2b_usage_partly_unobserved"] == st["2_usage_unobserved"]
