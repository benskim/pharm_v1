"""S6 월간 리포트 시험.

(a) 같은 입력 → 같은 파일 해시  (b) 2024-09 리포트에 2024-10 이후 정보 없음 (뒤 자료를 지운 원본으로 만든 리포트와 바이트 동일)
(c) 스냅샷 없는 달 → "자료 없음"  (d) 리포트 코드가 사후 칸(posthoc.py)을 읽지 않음
내용 기대값: 2025-11 등재 예고 17 / 10 (PLAN_P1 §2 F11), 2024-09 시타글립틴 6942 가격 예고 149개·24.9.2 (reference F4 검증 표)
"""
import hashlib
import re

import duckdb
import pytest

from pbd.common import DB_PATH, ROOT
from pbd.ledger import s6_report as R
from pbd.ledger.posthoc import find_posthoc

REF = (ROOT / "reference/precheck_v3_output.md").read_text(encoding="utf-8")
pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="S1~S3 DB 필요")


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


def test_a_same_input_same_hash(tmp_path):
    a = R.write("2024-09", md_dir=tmp_path / "a", csv_dir=tmp_path / "ac")
    b = R.write("2024-09", md_dir=tmp_path / "b", csv_dir=tmp_path / "bc")
    assert _sha(a) == _sha(b)
    fa, fb = sorted((tmp_path / "ac/2024-09").glob("*.csv")), sorted((tmp_path / "bc/2024-09").glob("*.csv"))
    assert [f.name for f in fa] == [f.name for f in fb] and len(fa) == 6
    assert [_sha(f) for f in fa] == [_sha(f) for f in fb]


def test_b_no_information_after_month(tmp_path, r2409):
    """2024-09 이하만 남긴 원본(급여목록·대체조제·비고 토큰·사용통계)으로 만든 리포트 = 전체 원본으로 만든 리포트"""
    month, start = "2024-09", "2023-10"            # 사용통계는 12개월 창만 있으면 된다
    db = tmp_path / "cut.duckdb"
    c = duckdb.connect(str(db))
    c.execute(f"attach '{DB_PATH.as_posix()}' as src (read_only)")
    assert c.execute("select count(*) from src.stg_benefit where list_month > ?", [month]).fetchone()[0] > 0
    for t in ("stg_benefit", "stg_substitute", "note_token"):
        c.execute(f"create table {t} as select * from src.{t} where list_month <= ?", [month])
    for t in ("attr_2026_product", "attr_2026_item"):
        c.execute(f"create table {t} as select * from src.{t}")
    c.execute("create table stg_usage as select * from src.stg_usage where month between ? and ?", [start, month])
    c.execute("create table usage_c4_month as select * from src.usage_c4_month where month between ? and ?", [start, month])
    c.close()
    md_cut, tabs_cut = R.render(month, db)
    md_full, tabs_full = r2409
    assert md_cut == md_full
    assert tabs_cut.keys() == tabs_full.keys()
    for k in tabs_full:
        assert tabs_cut[k].to_csv(index=False) == tabs_full[k].to_csv(index=False), k
    # 이번 달 뒤에 관측된 행이 없다 (신호 날짜는 예고 내용이라 미래일 수 있다)
    assert (tabs_full["new_signals"]["signal_id"].str.len() > 0).all()
    assert (tabs_full["signal_results"]["issued_month"] < month).all()


def test_c_month_without_snapshot():
    for m in ("2021-12", "2026-01"):
        md, tabs = R.render(m)
        assert "**자료 없음**" in md and tabs == {}
        assert md == R.render(m)[0]


def test_f11_listing_notices_2025_11(r2511):
    md, t = r2511
    s = t["new_signals"]
    g = s[s["signal_date"].astype(str) == "2025-11-23"].groupby("signal_type").size().to_dict()
    assert g == {"LISTING_SCHEDULED": 17, "LISTING_NOTE_EXISTING": 10}       # F11: 처음 나타난 제품 17 + 이미 급여 중이던 제품 10
    assert "### 등재 예고 (새 제품) 17건" in md and "'25.11.23 등재" in md


def test_sitagliptin_price_notice_2024_09(r2409):
    md, t = r2409
    row = [l for l in REF.split("## F4 검증", 1)[1].splitlines() if l.startswith("6942 |")][0].split(" | ")
    assert row[1:4] == ["2024-09", "24.9.2", "149"]
    s = t["new_signals"]
    p = s[(s["signal_type"] == "PRICE_PENDING") & (s["c4"] == "6942")]
    assert len(p) == int(row[3]) and set(p["signal_date"].astype(str)) == {"2024-09-02"}
    assert re.search(r"\| 6942 \| sitagliptin [^|]+\| 2024-09-02 \| 149 \|", md)
    # 예고는 이번 달 결과 표에 들어가지 않는다 (확인 스냅샷은 그 뒤)
    assert not (t["signal_results"]["c4"] == "6942").any()


def test_wording(r2409, r2511):
    for md, _ in (r2409, r2511):
        assert "인과" not in md and "영향을 미" not in md
        assert "예고" in md and "## 6. 자료 한계" in md


def test_usage_window_lag():
    assert (R.usage_window("2024-09", 0)[0], R.usage_window("2024-09", 0)[-1]) == ("2023-10", "2024-09")
    assert (R.usage_window("2024-09", 3)[0], R.usage_window("2024-09", 3)[-1]) == ("2023-07", "2024-06")
    assert R.usage_window("2024-09", None) == R.usage_window("2024-09", 0) and len(R.usage_window("2024-09", 2)) == 12


def test_display_names_synthetic(tmp_path):
    """손으로 만든 표: 표시 이름 = MONTH 이하 가장 최근 스냅샷 이름 (급여목록 먼저), 복합제 "외 N종"
    AAA: 2024-01 'telmisartan 40mg', 2024-02 'amlodipine 5mg', 주성분개수 칸 없음, 약가정보 '텔미사르탄/암로디핀' 2개 → 외 1종
    BBB: 2024-02 주성분개수 1 (약가정보는 2라고 해도 스냅샷 칸이 먼저) → 접미사 없음
    CCC: 주성분개수 3 → 외 2종.  DDD: 같은 달 급여목록 'd-benefit', 대체조제 'd-subst' → 급여목록 이름"""
    import pandas as pd
    c = duckdb.connect()
    c.execute("""create table stg_benefit as select * from (values
        ('AAA', '2024-01', 1, 'telmisartan   40mg', null, 'A001'), ('AAA', '2024-02', 1, 'amlodipine 5mg', null, 'A001'),
        ('BBB', '2024-02', 2, 'bbb 1mg', '1', 'B001'), ('CCC', '2024-02', 3, 'ccc 1mg', '3', 'C001'),
        ('DDD', '2024-02', 4, 'd-benefit', null, 'D001'))
        t(ingredient_code, list_month, row_no, ingredient_name, n_ingredients, c4)""")
    c.execute("""create table stg_substitute as select * from (values ('DDD', '2024-02', 1, 'd-subst', 'D001'))
        t(ingredient_code, list_month, row_no, ingredient_name, c4)""")
    c.execute("""create table product_state_month as select * from (values
        ('p1', '2024-01', 'A001', 'AAA', '101'), ('p1', '2024-02', 'A001', 'AAA', '101'), ('p2', '2024-02', 'B001', 'BBB', '102'),
        ('p3', '2024-02', 'C001', 'CCC', '103'), ('p4', '2024-02', 'D001', 'DDD', '104'))
        t(product_code, list_month, c4, ingredient_code, item_code)""")
    di = tmp_path / "di.parquet"
    pd.DataFrame({"품목기준코드": [101, 102], "주성분": ["텔미사르탄/암로디핀", "비/비"]}).to_parquet(di)
    R._names(c, "2024-02", di)
    d = dict(c.execute("select ingredient_code, display_name from _ingname").fetchall())
    assert d == {"AAA": "amlodipine 5mg 외 1종", "BBB": "bbb 1mg", "CCC": "ccc 1mg 외 2종", "DDD": "d-benefit"}
    R._names(c, "2024-01", di)
    assert c.execute("select display_name from _ingname where ingredient_code = 'AAA'").fetchone()[0] == "telmisartan 40mg 외 1종"
    assert c.execute("select ingredient_name, ingredient_name_raw from _c4name where c4 = 'A001'").fetchone() == ("telmisartan 40mg 외 1종", "telmisartan   40mg")
    c.close()
