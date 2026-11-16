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


def test_effective_month_prices_synthetic():
    """유효일 기준 일수 가중 목록가 (손 계산)
    q1: 100, 100, 90. 2024-03 관측, 유효일 2024-02-10 → 2월 = 100×9/29 + 90×20/29 (2/10~2/29 20일이 새 금액), 3월 90
    q2: 100, 80, 80. 2024-02 관측, 유효일 2024-02-01 → 관측월과 같음 (100, 80, 80)
    q3: 50, 50, 40. 2024-03 관측, 유효일 2024-03-11 → 3월 = 50×10/31 + 40×21/31"""
    con = duckdb.connect()
    con.execute("create table s3_cal as select * from (values ('2024-01', null, 1), ('2024-02', '2024-01', 2), ('2024-03', '2024-02', 3)) t(list_month, prev_month, mi)")
    con.execute("create table product_state_month (product_code varchar, list_month varchar, c4 varchar, state_asof varchar, price_listed double)")
    rows = []
    for pc, ps in (("q1", (100, 100, 90)), ("q2", (100, 80, 80)), ("q3", (50, 50, 40))):
        rows += [(pc, m, "X001", "reimbursed", p) for m, p in zip(("2024-01", "2024-02", "2024-03"), ps)]
    con.executemany("insert into product_state_month values (?, ?, ?, ?, ?)", rows)
    con.execute("""create table event_ledger as select * from (values
        ('q1', '2024-03', date '2024-02-10', 'PRICE_CHANGE'), ('q2', '2024-02', date '2024-02-01', 'PRICE_CHANGE'),
        ('q3', '2024-03', date '2024-03-11', 'PRICE_CHANGE')) t(product_code, observed_month, effective_date, event_type)""")
    p = S.effective_month_prices(con).set_index(["product_code", "list_month"])["p_eff"]
    assert math.isclose(p[("q1", "2024-02")], 100 * 9 / 29 + 90 * 20 / 29)
    assert (p[("q1", "2024-01")], p[("q1", "2024-03")]) == (100, 90)
    assert [p[("q2", m)] for m in ("2024-01", "2024-02", "2024-03")] == [100, 80, 80]
    assert math.isclose(p[("q3", "2024-03")], 50 * 10 / 31 + 40 * 21 / 31)
    assert p[("q3", "2024-02")] == 50
    con.close()


def test_weighted_quantile_hand():
    # 값 1,2,3 가중 1,1,2 → 누적 0.25, 0.5, 1.0 → 중앙값 2, 0.6분위 3
    v, w = np.array([3.0, 1.0, 2.0]), np.array([2.0, 1.0, 1.0])
    assert S.weighted_quantile(v, w, 0.5) == 2.0
    assert S.weighted_quantile(v, w, 0.6) == 3.0


def test_b1_main_synthetic():
    """대상 3쌍, 대조군 4쌍, 미관측 1쌍, 바구니 없음 1쌍 (손 계산)
    대상 x/y: (-0.10, -0.05), (-0.20, -0.30), (0.10, -0.02) → 같은 방향 2/3. 세 쌍 모두 |x|≥1% → 비율 0.5, 1.5, -0.2 → 중앙값 0.5
    대조군 y: -0.25, -0.05, 0.0, 0.15 → 오름 1/4, 내림 2/4, 그대로 1/4
    기준선(부호): x<0 두 쌍 0.5, x>0 한 쌍 0.25 → (0.5+0.5+0.25)/3
    |x_i| 이상: 0.10 → 오름 1/4 내림 1/4, 0.20 → 오름 0/4 내림 1/4, 0.10 → 1/4, 1/4
      x 방향 기준선 = (1/4 + 1/4 + 1/4)/3 = 0.25. 대상 x 방향 |x| 이상 = 1/3 (둘째 쌍만)
    금액 가중 같은 방향: 가중 1, 2, 7 → (1+2)/10"""
    df = pd.DataFrame({
        "c4": ["a", "b", "c", "d", "e", "f", "g", "h", "i"],
        "stage": ["target"] * 3 + ["3_no_list_change"] * 4 + ["2_usage_unobserved", "1_no_basket"],
        "x": [-0.10, -0.20, 0.10, 0, 0, 0, 0, np.nan, np.nan],
        "y": [-0.05, -0.30, -0.02, -0.25, -0.05, 0.0, 0.15, np.nan, 0.01],
        "amt1": [1.0, 2.0, 7.0, 1, 1, 1, 1, np.nan, 1],
        "obs0": [True] * 7 + [False, True], "obs1": [True] * 9,
        "n_basket": [1] * 8 + [0]})
    m, ex = S.b1_main(df, "t")
    v = m.set_index("metric")["value"]
    assert v["share_same_sign"] == round(2 / 3, 4)
    assert v["ratio_median"] == 0.5
    assert (v["ctl_share_up"], v["ctl_share_down"], v["ctl_share_flat"]) == (0.25, 0.5, 0.25)
    assert v["ctl_expected_same_sign"] == round(1.25 / 3, 4)
    assert v["ctl_expected_same_sign_ge_size"] == 0.25
    assert v["share_same_sign_ge_size"] == round(1 / 3, 4)
    assert v["w_share_same_sign"] == 0.3
    e = ex.set_index("category")["pairs"]
    assert e.to_dict() == {"usage_unobserved": 1, "no_basket": 1, "no_list_change": 4, "mid_month_change": 0, "target": 3}
    assert math.isclose(ex["share_pct"].sum(), 100, abs_tol=0.05)


def test_b2_scope_synthetic():
    cl = pd.DataFrame({"c4": list("abcde"), "post_window_complete": [True, True, True, True, False],
                       "n_obs_before": [3, 3, 0, 2, 3], "n_obs_after": [3, 1, 3, 0, 1]})
    s = S.b2_scope(cl).set_index("scope")["clusters"].to_dict()
    assert s == {"0_post_window_incomplete": 1, "1_both": 1, "2_before_only": 1, "3_after_only": 1, "4_neither": 1}


def test_c_by_c4_synthetic():
    """성분4 X: 그룹 2개(비율 1.2 제품 3, 비율 1.5 제품 2) 금액 100, 성분4 Y: 그룹 1개 금액 300, 전체 500
    → Y 1위, X의 가장 넓은 그룹 = 비율 1.5, 성분4 단위 비중 400/500, 그룹 행 합 500/500"""
    rank = pd.DataFrame({"c4": ["X", "X", "Y"], "grp": ["g1", "g2", "g3"], "ingredient_code": ["i1", "i2", "i3"],
                         "ingredient_name": ["n1", "n2", "n3"], "n_products": [3, 2, 4], "min_price": [10, 10, 5],
                         "max_price": [12, 15, 6], "max_min_ratio": [1.2, 1.5, 1.2], "c4_out_amt_year": [100.0, 100.0, 300.0]})
    t = S.c_by_c4(rank, 500.0)
    g = t["c_rank_c4"].set_index("c4")
    assert list(t["c_rank_c4"]["c4"]) == ["Y", "X"]
    assert (g.loc["X", "n_groups"], g.loc["X", "widest_grp"], g.loc["X", "widest_max_min_ratio"]) == (2, "g2", 1.5)
    assert (t["c_share"].loc[0, "share_c4_unique"], t["c_share"].loc[0, "share_group_row_sum"]) == (0.8, 1.0)


def test_b2_pre_truncated_synthetic():
    """진입 전 창이 잘린 군집만 (사후 창 완결). 있는 달이 모두 관측되고 사후 3개월 관측이면 meets = True
    a: 있는 달 1, 관측 1, 사후 3 → True / b: 있는 달 2, 관측 0 → False / c: 창 3개월 → 목록 밖 / d: 사후 창 미완결 → 목록 밖"""
    cl = pd.DataFrame({"c4": list("abcd"), "ingredient_name": list("abcd"), "month": ["2022-02", "2022-03", "2023-01", "2025-11"],
                       "n_entry_firms": [5] * 4, "n_pre_months": [1, 2, 3, 3], "n_obs_before": [1, 0, 3, 3],
                       "n_obs_after": [3, 3, 3, 1], "case_usable": [False, False, True, False],
                       "post_window_complete": [True, True, True, False]})
    t = S.b2_pre_truncated(cl).set_index("c4")
    assert list(t.index) == ["a", "b"]
    assert t["meets_with_available_months"].to_dict() == {"a": True, "b": False}


def test_b1_main_subset_label():
    """좁힌 기준선은 같은 함수에 거른 표를 넣는다: 대상·대조군 모두 같은 조건으로 줄어든다
    대상 2쌍 (같은 방향 1개, 그중 등재·삭제 있는 쌍은 같은 방향), 대조군 2쌍 (내림 1, 등재·삭제 있는 쌍)"""
    df = pd.DataFrame({"c4": list("abcd"), "stage": ["target", "target", "3_no_list_change", "3_no_list_change"],
                       "x": [-0.1, -0.1, 0, 0], "y": [-0.05, 0.02, -0.01, 0.03], "amt1": [1.0] * 4,
                       "obs0": [True] * 4, "obs1": [True] * 4, "n_basket": [1] * 4, "n_listing_delisting": [1, 0, 1, 0]})
    full = S.b1_main(df, "t")[0].set_index("metric")["value"]
    sub_df = df[df["n_listing_delisting"] == 0]
    m = S.b1_main(sub_df.assign(x=sub_df["x"]), "t", "no_listing_delisting")[0]
    v = m.set_index("metric")["value"]
    assert (full["share_same_sign"], full["ctl_expected_same_sign"]) == (0.5, 0.5)
    assert (v["pairs"], v["share_same_sign"], v["ctl_pairs"], v["ctl_expected_same_sign"]) == (1, 0.0, 1, 0.0)
    assert set(m["subset"]) == {"no_listing_delisting"}
