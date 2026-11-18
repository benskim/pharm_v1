"""S6 월간 리포트 시험.

(a) 같은 입력 → 같은 파일 해시  (b) 2024-09 리포트에 2024-10 이후 목록·2024-04 이후 사용통계 정보 없음
    (뒤 자료를 지우고 MONTH−4 이후 사용통계를 바꾼 원본으로 만든 리포트와 바이트 동일)
(c) 스냅샷 없는 달 → "자료 없음"  (d) 리포트 코드가 사후 칸(posthoc.py)을 읽지 않음
F1 영향 규모·F2 주목할 것·F3 묶음·F4 대체 가능성은 손으로 만든 합성 자료로 계산을 확인한다.
내용 기대값: 2025-11 등재 예고 17 / 10 (PLAN_P1 §2 F11), 2024-09 시타글립틴 6942 가격 예고 149개·24.9.2 (reference F4 검증 표),
기준 기간 2024-02 ~ 2024-04, F2 1줄 시타글립틴·등재 줄 토파시티닙, 6272·6937 = tofacitinib, 6942·5011 = sitagliptin (사용자 완료 기준)
"""
import hashlib
import math
import re

import duckdb
import pandas as pd
import pytest

from pbd.common import DB_PATH, ROOT
from pbd.ledger import s6_report as R
from pbd.ledger.posthoc import find_posthoc

REF = (ROOT / "reference/precheck_v3_output.md").read_text(encoding="utf-8")
real = pytest.mark.skipif(not DB_PATH.exists(), reason="S1~S3 DB 필요")
COMMON = ["report_month", "list_ref_month", "usage_ref_from", "usage_ref_to"]


def _sha(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def r2409():
    return R.render("2024-09")


@pytest.fixture(scope="module")
def r2511():
    return R.render("2025-11")


def test_d_report_code_does_not_read_posthoc_columns():
    src = (ROOT / "src/pbd/ledger/s6_report.py").read_text(encoding="utf-8")
    assert find_posthoc(src) == []
    assert not re.search(r"select\s+\*\s+from\s+(signal_outcome|signal_ledger|event_ledger|product_state_month)", src, re.I)
    # 사용통계 원본 표는 load_usage 한 곳에서만 읽는다
    body = src.split("def load_usage", 1)
    assert "src.stg_usage" not in body[0] and "src.stg_usage" not in body[1].split("\ndef ", 1)[1]
    assert "src.usage_c4_month" not in body[1].split("\ndef ", 1)[1]


@real
def test_a_same_input_same_hash(tmp_path):
    a = R.write("2024-09", md_dir=tmp_path / "a", csv_dir=tmp_path / "ac")
    b = R.write("2024-09", md_dir=tmp_path / "b", csv_dir=tmp_path / "bc")
    assert _sha(a) == _sha(b)
    fa, fb = sorted((tmp_path / "ac/2024-09").glob("*.csv")), sorted((tmp_path / "bc/2024-09").glob("*.csv"))
    assert [f.name for f in fa] == [f.name for f in fb] and len(fa) == 10   # + stay_pending.csv (RULES_2026-11-17 §7)
    assert [_sha(f) for f in fa] == [_sha(f) for f in fb]
    for f in fa:                                                     # F5: 모든 CSV에 기준 시점 칸
        assert set(COMMON) <= set(pd.read_csv(f, nrows=0).columns), f.name


@real
def test_b_no_information_after_month(tmp_path, r2409):
    """목록은 2024-09 이하만, 사용통계는 2024-04(= MONTH−5) 이하만 남기고, 2024-05 ~ 2024-09 사용통계는 10배로 바꿔 넣은 원본으로
    만든 리포트 = 전체 원본으로 만든 리포트 (F1·F5 시점 시험)"""
    month, ucut, start = "2024-09", "2024-04", "2023-01"
    db = tmp_path / "cut.duckdb"
    c = duckdb.connect(str(db))
    c.execute(f"attach '{DB_PATH.as_posix()}' as src (read_only)")
    assert c.execute("select count(*) from src.stg_benefit where list_month > ?", [month]).fetchone()[0] > 0
    for t in ("stg_benefit", "stg_substitute", "note_token"):
        c.execute(f"create table {t} as select * from src.{t} where list_month <= ?", [month])
    for t in ("attr_2026_product", "attr_2026_item"):
        c.execute(f"create table {t} as select * from src.{t}")
    c.execute("create table stg_usage as select * from src.stg_usage where month between ? and ?", [start, ucut])
    c.execute("""insert into stg_usage select * replace (claim_amt * 10 as claim_amt) from src.stg_usage
                 where month > ? and month <= ?""", [ucut, month])
    c.execute("create table usage_c4_month as select * from src.usage_c4_month where month between ? and ?", [start, ucut])
    c.execute("insert into usage_c4_month select * replace (true as observed_out) from src.usage_c4_month where month > ? and month <= ?",
              [ucut, month])
    c.close()
    md_cut, tabs_cut = R.render(month, db)
    md_full, tabs_full = r2409
    assert md_cut == md_full
    assert tabs_cut.keys() == tabs_full.keys()
    for k in tabs_full:
        assert tabs_cut[k].to_csv(index=False) == tabs_full[k].to_csv(index=False), k
    assert (tabs_full["signal_results"]["issued_month"] < month).all()


@real
def test_c_month_without_snapshot():
    for m in ("2021-12", "2026-01"):
        md, tabs = R.render(m)
        assert "**자료 없음**" in md and tabs == {}
        assert md == R.render(m)[0]


@real
def test_f11_listing_notices_2025_11(r2511):
    md, t = r2511
    s = t["new_signals"]
    g = s[s["signal_date"].astype(str) == "2025-11-23"].groupby("signal_type").size().to_dict()
    assert g == {"LISTING_NOTICE": 17, "LISTING_NOTE_EXISTING": 10}         # v5 이름 (RULES §6)       # F11: 처음 나타난 제품 17 + 이미 급여 중이던 제품 10
    assert "### 등재 예고 (새 제품) 17건" in md and "'25.11.23 등재" in md
    assert "이미 급여 중인 제품. 같은 날 같은 고시에 함께 실렸고 상태는 바뀌지 않는다." in md
    # F2: 등재 줄 = 토파시티닙 (가격 예고가 없어 가격 줄은 없다), F3: 6272·6937 = tofacitinib
    att = list(t["attention"]["text"])
    assert not any(a.startswith("**가격 인하 예고**") for a in att)
    assert [a for a in att if a.startswith("**등재 예고**")][0].startswith("**등재 예고** — tofacitinib ")
    gk = s[s["c4"].isin(["6272", "6937"])].groupby("c4")["group_key"].first().to_dict()
    assert gk == {"6272": "tofacitinib", "6937": "tofacitinib"}


@real
def test_sitagliptin_price_notice_2024_09(r2409):
    md, t = r2409
    row = [l for l in REF.split("## F4 검증", 1)[1].splitlines() if l.startswith("6942 |")][0].split(" | ")
    assert row[1:4] == ["2024-09", "24.9.2", "149"]
    s = t["new_signals"]
    p = s[(s["signal_type"] == "PRICE_NOTICE") & (s["c4"] == "6942")]
    assert len(p) == int(row[3]) and set(p["signal_date"].astype(str)) == {"2024-09-02"}
    assert re.search(r"\| 6942 \| sitagliptin [^|]+\| sitagliptin \| 2024-09-02 \| 149 / ", md)
    # F1: 기준 기간 = MONTH−7 ~ MONTH−5, n_noticed = 149, 중앙 인하율 = 표의 중앙값
    imp = t["impact"].set_index("c4").loc["6942"]
    assert (imp["usage_ref_from"], imp["usage_ref_to"], int(imp["n_noticed"])) == ("2024-02", "2024-04", 149)
    assert round(float(p["change_pct"].median()), 1) == round(float(imp["median_cut_noticed"]), 1)
    # F2 1줄 = 시타글립틴 인하 예고, F3: 6942·5011 = sitagliptin
    assert t["attention"]["text"].iloc[0].startswith("**가격 인하 예고** — sitagliptin ")
    gk = s[s["c4"].isin(["6942", "5011"])].groupby("c4")["group_key"].first().to_dict()
    assert gk == {"5011": "sitagliptin", "6942": "sitagliptin"}
    assert not (t["signal_results"]["c4"] == "6942").any()


@real
def test_wording(r2409, r2511):
    for md, _ in (r2409, r2511):
        assert "인과" not in md and "영향을 미" not in md
        assert "예고" in md and "## 6. 자료 한계" in md
        assert "영향 규모는 제품 수 비중으로 근사한 전국 성분 단위 추정이며, 기준 기간 이후 시장 변화는 반영되지 않는다." in md
        assert "초본과 수정본의 차이는 측정하지 못했다." in md and "<sub>발간일 " in md
    assert "11월은 미생산·미청구 약제 정기 정비 달" in r2511[0] and "11월은 미생산" not in r2409[0]


# ---------------------------------------------------------------- 합성 자료
def test_windows():
    assert (R.usage_window("2024-09", 0)[0], R.usage_window("2024-09", 0)[-1]) == ("2023-10", "2024-09")
    assert (R.usage_window("2024-09")[0], R.usage_window("2024-09")[-1]) == ("2023-05", "2024-04")   # 12개월 = M−16 ~ M−5
    assert R.base_window("2024-09") == ["2024-02", "2024-03", "2024-04"]                              # U = M−7 ~ M−5
    assert R.USAGE_LAG == 5


@pytest.fixture()
def usage_src(tmp_path):
    """src로 붙일 사용통계 표. A001: 2024-02·03·04 원외 6억·9억·12억 (+원내 100억) → 기준 9억. B002: U 3개월 모두 미관측.
    C003: 2024-03만 관측 3억 → 관측 1개월. 2024-05 이후 행은 F1이 읽으면 안 된다 (A001 1000억)"""
    db = tmp_path / "u.duckdb"
    c = duckdb.connect(str(db))
    c.execute("""create table stg_usage as select * from (values
        ('A001', '2024-02', 'OUT', 6e8), ('A001', '2024-03', 'OUT', 9e8), ('A001', '2024-04', 'OUT', 12e8), ('A001', '2024-04', 'IN', 100e8),
        ('A001', '2024-05', 'OUT', 1000e8), ('C003', '2024-03', 'OUT', 3e8)) t(c4, month, in_out, claim_amt)""")
    c.execute("""create table usage_c4_month as select * from (values
        ('A001', '2024-02', true), ('A001', '2024-03', true), ('A001', '2024-04', true), ('A001', '2024-05', true),
        ('B002', '2024-02', false), ('B002', '2024-03', false), ('B002', '2024-04', false),
        ('C003', '2024-02', false), ('C003', '2024-03', true), ('C003', '2024-04', false)) t(c4, month, observed_out)""")
    c.close()
    con = duckdb.connect()
    con.execute(f"attach '{db.as_posix()}' as src (read_only)")
    yield con
    con.close()


def _impact_con(con):
    """2024-09 발간. A001 reimbursed 4개, 예고 2개: 100 → 80 (r −0.2), 200 → 150 (r −0.25), 예고일 2024-09-11.
    B002 reimbursed 1개 예고 1개 (사용통계 미관측)"""
    con.execute("""create table state_month_v5 as select * from (values
        ('a1', '2024-09', 'A001', 'reimbursed', 100.0), ('a2', '2024-09', 'A001', 'reimbursed', 200.0),
        ('a3', '2024-09', 'A001', 'reimbursed', 300.0), ('a4', '2024-09', 'A001', 'reimbursed', 400.0),
        ('a5', '2024-09', 'A001', 'suspended', 500.0), ('b1', '2024-09', 'B002', 'reimbursed', 100.0))
        t(product_code, list_month, c4, state, price_listed)""")
    con.execute("""create table signal_v5 as select * from (values
        ('PRICE_NOTICE', 'R2', 'a1', 'A001', '2024-09', date '2024-09-11', 80.0),
        ('PRICE_NOTICE', 'R2', 'a2', 'A001', '2024-09', date '2024-09-11', 150.0),
        ('PRICE_NOTICE', 'R2', 'b1', 'B002', '2024-09', date '2024-10-01', 90.0))
        t(signal_type, source_rule, product_code, market_c4, first_month, signal_date, amount)""")


def test_f1_impact_hand_values(usage_src):
    """손 계산: A001 기준 9억/월 (원내·2024-05 제외), N = 4 (정지 제품 제외), Σr = −0.45
    → 상한금액 기준 9 × (−0.45) / 4 = −1.0125억 → −1.01, 전가율 적용 × 0.46 = −0.465750 → −0.47,
    이번 달 적용 = (30 − 11 + 1) / 30 = 0.6667. B002: 자료 없음 (0이 아님), 예고일이 다음 달이면 적용 0"""
    R.load_usage(usage_src, "2024-09")
    _impact_con(usage_src)
    t = R.impact_table(usage_src, "2024-09").set_index("c4")
    a = t.loc["A001"]
    assert (a["base_amt"], int(a["base_months_observed"]), int(a["n_products_c4"]), int(a["n_noticed"])) == (9.0, 3, 4, 2)
    assert math.isclose(a["base_amt_won"], 9e8) and math.isclose(a["sum_r"], -0.45)
    assert (a["impact_listed"], a["impact_pt"], a["exposure_this_month"]) == (-1.01, -0.47, 0.6667)
    assert (a["usage_ref_from"], a["usage_ref_to"]) == ("2024-02", "2024-04")
    b = t.loc["B002"]
    assert pd.isna(b["base_amt"]) and pd.isna(b["impact_listed"]) and int(b["base_months_observed"]) == 0
    assert b["exposure_this_month"] == 0.0
    assert R._amt(b["impact_listed"]) == "자료 없음"


def test_f1_reads_no_usage_after_lag(usage_src):
    """MONTH−4(2024-05) 이후 사용통계를 바꿔도 _u·_base가 같다. 관측 1개월만 있으면 그 달만 평균"""
    R.load_usage(usage_src, "2024-09")
    before = usage_src.execute("select * from _base order by c4").fetchall()
    assert usage_src.execute("select max(month) from _u").fetchone()[0] == "2024-04"
    assert dict((r[0], (r[1], r[2])) for r in before)["C003"] == (3e8, 1)


def test_f2_attention_deterministic_ties():
    """동점: 점수가 같으면 예고 제품 수, 그다음 묶음 이름 순. 등재는 새 업체 수 → 기준 금액 → 이름. 삭제는 대체 없는 성분4 기준 금액 합"""
    rates = pd.DataFrame({"signal_type": ["PRICE_NOTICE"], "rate": [0.5]})
    grp = pd.DataFrame({"c4": ["A1", "B1", "C1", "D1"], "group_key": ["beta", "alpha", "gamma", "delta"]})
    labels = {"beta": "beta", "alpha": "alpha", "gamma": "gamma", "delta": "delta"}
    sig = pd.DataFrame({"signal_type": ["PRICE_NOTICE"] * 4, "c4": ["A1", "A1", "B1", "B1"], "product_code": list("wxyz"),
                        "signal_date": pd.to_datetime(["2024-09-02"] * 4), "change_pct": [-10.0, -10.0, -20.0, -20.0]})
    imp = pd.DataFrame({"c4": ["A1", "B1"], "impact_listed": [-2.0, -2.0]})
    lstg = pd.DataFrame({"gk": ["gamma", "delta"], "notice_date": pd.to_datetime(["2024-09-10"] * 2), "firms_noticed": [3, 3],
                         "firms_now": [1, 2], "new_firms": [3, 3], "base_amt": [5.0, 5.0]})
    # 삭제: gamma(C1) 대체 없음 1건, 몫 7.0 / delta(D1) 대체 없음 2건(q, s), 몫 3.0 + 1.0, r은 대체 있음 → 건수가 먼저라 delta
    ev = pd.DataFrame({"event_type": ["DELISTING"] * 4, "kind": ["delisting"] * 4, "c4": ["C1", "D1", "D1", "D1"],
                       "product_code": ["p", "q", "r", "s"], "same_code_remaining": [0, 0, 2, 0],
                       "base_amt": [70.0, 30.0, 30.0, 30.0], "product_share": [7.0, 3.0, 3.0, 1.0]})
    out = [R.attention(sig, imp, lstg, ev, rates, grp, labels) for _ in range(2)]
    assert out[0] == out[1]
    assert out[0][0].startswith("**가격 인하 예고** — alpha 2개 제품")          # 점수·제품 수 같음 → 이름 순 alpha
    assert out[0][1] == "**등재 예고** — delta 3개 업체 2024-09-10 등재 예고 (업체 수 2 → 5)."
    assert out[0][2] == "**삭제·정지** — delta 3개 제품 삭제, 그중 같은 함량·제형 대체 제품이 없는 것 2개 (제품 몫 합 약 4.0억 원/월)."
    # 건수가 같으면 제품 몫 합이 큰 쪽 (gamma 7.0 > delta 3.0)
    ev2 = ev[ev["product_code"] != "s"]
    assert R.attention(sig, imp, lstg, ev2, rates, grp, labels)[2].startswith("**삭제·정지** — gamma 1개 제품 삭제")
    # 대체 없는 삭제가 없으면 줄을 쓰지 않는다
    ev3 = ev.assign(same_code_remaining=1)
    assert not any(x.startswith("**삭제·정지**") for x in R.attention(sig, imp, lstg, ev3, rates, grp, labels))


def test_small_amount_display():
    """영향 규모·제품 몫: 절댓값 0.05억 미만은 "0.1억 미만", 미관측은 자료 없음"""
    assert (R._small(-0.03), R._small(0.049, 2), R._small(0.05, 2), R._small(-1.05), R._small(None)) == \
        ("0.1억 미만", "0.1억 미만", "0.05", "-1.1", "자료 없음")


def test_f3_short_name():
    assert R.short_name("sitagliptin hydrochloride hydrate (as sitagliptin 0.1g)") == "sitagliptin"
    assert R.short_name("sitagliptin phosphate hydrate (as sitagliptin 0.1g)") == "sitagliptin"
    assert R.short_name("tofacitinib 5mg") == "tofacitinib"
    assert R.short_name("tofacitinib citrate (as tofacitinib 5mg)") == "tofacitinib"
    assert R.short_name("sodium hyaluronate 5mg(1mg/mL)") == "sodium hyaluronate"          # 첫 단어는 빼지 않는다
    assert R.short_name("omega-3-acid ethyl esters90 1g (as EPA ethylester 0.46g)") == "omega-3-acid ethyl esters90"
    assert R.short_name("telmisartan 40mg", 2) == "telmisartan 외 1종"


def test_f4_remaining_uses_month_snapshot_only():
    """2024-09 삭제 제품 d1(주성분코드 X, 2024-08에만 있음). 같은 X: r1 reimbursed(2024-09) → 1, s1 정지(2024-09) → 제외,
    n1은 2024-10에만 reimbursed → 제외. 같은 성분4 C001: r1, y1(주성분코드 Y) → 2. 다른 성분4 제품은 세지 않는다"""
    con = duckdb.connect()
    con.execute("""create table state_month_v5 as select * from (values
        ('d1', '2024-08', 'C001', 'X', 'reimbursed'), ('r1', '2024-09', 'C001', 'X', 'reimbursed'),
        ('s1', '2024-09', 'C001', 'X', 'suspended'), ('y1', '2024-09', 'C001', 'Y', 'reimbursed'),
        ('n1', '2024-10', 'C001', 'X', 'reimbursed'), ('z1', '2024-09', 'C002', 'X2', 'reimbursed'))
        t(product_code, list_month, c4, ingredient_code, state)""")
    con.execute("create table cal_v5 as select * from (values ('2024-08', null), ('2024-09', '2024-08'), ('2024-10', '2024-09')) t(list_month, prev_month)")
    con.execute("create table ev_v5 as select * from (values ('d1', 'C001', '2024-09')) t(product_code, market_c4, observed_month)")
    R.remaining_counts(con, "2024-09")
    assert con.execute("select ingredient_code, same_code_remaining, same_c4_remaining from _rem").fetchall() == [("X", 1, 2)]


def test_display_names_synthetic(tmp_path):
    """손으로 만든 표: 표시 이름 = MONTH 이하 가장 최근 스냅샷 이름 (급여목록 먼저), 복합제 "외 N종"
    AAA: 2024-01 'telmisartan 40mg', 2024-02 'amlodipine 5mg', 주성분개수 칸 없음, 약가정보 '텔미사르탄/암로디핀' 2개 → 외 1종
    BBB: 2024-02 주성분개수 1 (약가정보는 2라고 해도 스냅샷 칸이 먼저) → 접미사 없음
    CCC: 주성분개수 3 → 외 2종.  DDD: 같은 달 급여목록 'd-benefit', 대체조제 'd-subst' → 급여목록 이름"""
    c = duckdb.connect()
    c.execute("""create table stg_benefit as select * from (values
        ('AAA', '2024-01', 1, 'telmisartan   40mg', null, 'A001'), ('AAA', '2024-02', 1, 'amlodipine 5mg', null, 'A001'),
        ('BBB', '2024-02', 2, 'bbb 1mg', '1', 'B001'), ('CCC', '2024-02', 3, 'ccc 1mg', '3', 'C001'),
        ('DDD', '2024-02', 4, 'd-benefit', null, 'D001'))
        t(ingredient_code, list_month, row_no, ingredient_name, n_ingredients, c4)""")
    c.execute("""create table stg_substitute as select * from (values ('DDD', '2024-02', 1, 'd-subst', 'D001'))
        t(ingredient_code, list_month, row_no, ingredient_name, c4)""")
    c.execute("""create table state_month_v5 as select * from (values
        ('p1', '2024-01', 'A001', 'AAA'), ('p1', '2024-02', 'A001', 'AAA'), ('p2', '2024-02', 'B001', 'BBB'),
        ('p3', '2024-02', 'C001', 'CCC'), ('p4', '2024-02', 'D001', 'DDD'))
        t(product_code, list_month, c4, ingredient_code)""")
    c.execute("""create table s2_product_link as select * from (values ('p1', '101'), ('p2', '102'), ('p3', '103'), ('p4', '104'))
        t(product_code, item_code)""")
    di = tmp_path / "di.parquet"
    pd.DataFrame({"품목기준코드": [101, 102], "주성분": ["텔미사르탄/암로디핀", "비/비"]}).to_parquet(di)
    R._names(c, "2024-02", di)
    d = dict(c.execute("select ingredient_code, display_name from _ingname").fetchall())
    assert d == {"AAA": "amlodipine 5mg 외 1종", "BBB": "bbb 1mg", "CCC": "ccc 1mg 외 2종", "DDD": "d-benefit"}
    g = dict(c.execute("select c4, group_key from _c4grp").fetchall())
    assert g["A001"] == "c4:A001" and g["B001"] == "bbb"                     # 복합제는 묶지 않는다
    R._names(c, "2024-01", di)
    assert c.execute("select display_name from _ingname where ingredient_code = 'AAA'").fetchone()[0] == "telmisartan 40mg 외 1종"
    assert c.execute("select ingredient_name, ingredient_name_raw from _c4name where c4 = 'A001'").fetchone() == ("telmisartan 40mg 외 1종", "telmisartan   40mg")
    c.close()


def test_f3_group_overrides_split():
    """사용자 결정: calcium acetate / gluconate, human insulin 일반형 / N70·R30을 나눈다 (config group_overrides)"""
    c = duckdb.connect()
    c.execute("""create table _c4name as select * from (values ('1217', 'calcium acetate 0.71g', 1), ('1222', 'calcium gluconate 2g(0.1g/mL)', 1),
        ('1701', 'human insulin 1KI.U(100I.U/mL)', 1), ('1704', 'human insulin(N70/R30) 1KI.U(100I.U/mL)', 1)) t(c4, base_name, n_components)""")
    R._groups(c)
    g = dict(c.execute("select c4, group_key from _c4grp").fetchall())
    assert len({g["1217"], g["1222"]}) == 2 and len({g["1701"], g["1704"]}) == 2


@real
def test_f4_product_share(r2511):
    """제품 1개 몫 = 성분4 원외 금액 / 성분4 reimbursed 제품 수 (억 원/월)"""
    e = r2511[1]["events"]
    x = e[e["product_share"].notna()]
    assert len(x) > 0
    assert ((x["product_share"] - x["base_amt"] / x["n_products_c4"]).abs() < 0.01).all()


@real
def test_rate_note_and_blocked_amount_line(r2409):
    """사용자 지시 2026-11-18: §2에 '결과가 새로 나면 B·A가 달마다 바뀐다' 설명 줄, 그 달 새로 기록된 막힌 인하 처분(대체조제 금액 신호)은 한 줄.
    2024-10: 금액 차이 신호 48건 (RULES §1·§11-5, 2024-10 분), 2024-09: 없음"""
    md = r2409[0]
    assert "같은 유형이라도 리포트 달마다 B와 A가 달라진다." in md
    assert "막힌 인하 처분 (대체조제 목록 금액" not in md
    md10, _ = R.render("2024-10")
    assert re.search(r"이번 달 새로 기록된 막힌 인하 처분 \(대체조제 목록 금액, 급여목록에 새 집행정지\) 48건 · 성분4 \d+개", md10)
