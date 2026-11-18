"""S4 시험.

기대값 출처: Wilson 구간은 손으로 계산한 값(공식: Wilson 1927), A1 분모는 signal_outcome을 이 시험에서 직접 센 값,
표본 규칙은 PLAN_P1 §5 A6·DECISIONS 7차(654700150 포함), 정밀도 스크립트는 손으로 만든 라벨 CSV.
"""
import csv
import importlib.util
from pathlib import Path

import duckdb
import pytest

from pbd.common import DB_PATH, ROOT
from pbd.ledger.s4_analysis import wilson

spec = importlib.util.spec_from_file_location("s4_precision", ROOT / "scripts/s4_precision.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def test_wilson_hand_values():
    # k=5, n=10: p=0.5 → (0.2366, 0.7634);  k=0, n=10 → (0, 0.2775);  k=10, n=10 → (0.7225, 1)
    lo, hi = wilson(5, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.2366, 0.7634)
    lo, hi = wilson(0, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.0, 0.2775)
    lo, hi = wilson(10, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.7225, 1.0)
    assert wilson(0, 0) == (None, None)
    assert P.wilson(5, 10) == wilson(5, 10)


def test_precision_script_on_hand_labels(tmp_path):
    rows = [dict(sample_id=str(i), record_type="event", stratum=f"PRICE_CHANGE|signaled|{'signal_confirmed' if i < 4 else 'snapshot_first'}",
                 label_type_correct="Y" if i != 2 else "N", label_date_correct="Y" if i not in (2, 3) else "N", label_note="")
            for i in range(1, 6)]
    p = tmp_path / "s.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    assert P.main(p) == 0
    with (tmp_path / "precision.csv").open(encoding="utf-8") as f:
        res = {(r["group"], r["key"], r["label"]): r for r in csv.DictReader(f)}
    assert (res[("all", "all", "label_type_correct")]["n"], res[("all", "all", "label_type_correct")]["correct"]) == ("5", "4")
    assert res[("all", "all", "label_date_correct")]["precision"] == "0.6"
    assert res[("date_source", "signal_confirmed", "label_date_correct")]["correct"] == "1"   # 1·2·3 중 1만 Y


def test_precision_weighted_hand_values(tmp_path):
    """층 A(크기 900): Y, N → 0.5 / 층 B(크기 100): Y, Y → 1.0 / 모름 1행은 분모에서 뺀다.
    비가중 3/4 = 0.75. 가중 (900·0.5 + 100·1.0) / 1000 = 0.55. 가중치 450, 450, 50, 50 → n_eff = 1000² / 410000 = 2.439"""
    rows = [("1", "A", "900", "Y"), ("2", "A", "900", "N"), ("3", "B", "100", "Y"), ("4", "B", "100", "Y"), ("5", "B", "100", "모름")]
    p = tmp_path / "s.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sample_id", "record_type", "stratum", "stratum_size", "label_type_correct", "label_date_correct", "label_note"])
        for sid, st, size, lab in rows:
            w.writerow([sid, "signal", st, size, lab, "", ""])
    assert P.main(p) == 0
    with (tmp_path / "precision.csv").open(encoding="utf-8") as f:
        r = {(x["group"], x["label"]): x for x in csv.DictReader(f)}[("all", "label_type_correct")]
    assert (r["n"], r["correct"], r["unknown"], r["precision"], r["precision_weighted"], r["n_eff"]) == ("4", "3", "1", "0.75", "0.55", "2.439")
    lo, hi = P.wilson_p(0.55, 1000 ** 2 / 410000)
    assert (r["weighted_lo"], r["weighted_hi"]) == (str(round(lo, 4)), str(round(hi, 4)))


def _write(p, header, rows):
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


H = ["sample_id", "record_id", "record_type", "stratum", "labeler", "claude_label_type_correct", "claude_label_date_correct",
     "label_type_correct", "label_date_correct", "label_note"]


def test_precision_scopes_and_review_diff(tmp_path):
    """손으로 만든 5행: 1~3 user_reviewed, 4~5 claude.
    1: 1차 Y/Y → 검토 Y/Y (같음) / 2: 1차 Y/Y → 검토 N/Y (type 다름) / 3: 1차 Y/N → 검토 Y/Y (date 다름)
    4: Y/Y / 5: N/(빈칸). 전체 type 3/5 (Y,N,Y,Y,N), user_reviewed type 2/3, 다른 행 2 (type 1, date 1)"""
    rows = [["1", "e1", "event", "A|x|s", "user_reviewed", "Y", "Y", "Y", "Y", "근거1"],
            ["2", "e2", "event", "A|x|s", "user_reviewed", "Y", "Y", "N", "Y", "kind가 다름"],
            ["3", "e3", "event", "A|x|s", "user_reviewed", "Y", "N", "Y", "Y", "날짜 맞음"],
            ["4", "e4", "event", "A|x|s", "claude", "Y", "Y", "Y", "Y", ""],
            ["5", "e5", "event", "A|x|s", "claude", "N", "", "N", "", ""]]
    p = tmp_path / "s.csv"
    _write(p, H, rows)
    assert P.main(p) == 0
    with (tmp_path / "precision.csv").open(encoding="utf-8") as f:
        res = {(r["scope"], r["group"], r["label"]): r for r in csv.DictReader(f) if r["group"] == "all"}
    assert (res[("all", "all", "label_type_correct")]["n"], res[("all", "all", "label_type_correct")]["correct"]) == ("5", "3")
    assert (res[("user_reviewed", "all", "label_type_correct")]["n"], res[("user_reviewed", "all", "label_type_correct")]["correct"]) == ("3", "2")
    assert (res[("user_reviewed", "all", "label_date_correct")]["n"], res[("user_reviewed", "all", "label_date_correct")]["correct"]) == ("3", "3")
    with (tmp_path / "label_review_diff.csv").open(encoding="utf-8") as f:
        d = list(csv.DictReader(f))
    assert [(r["sample_id"], r["changed"]) for r in d] == [("2", "label_type_correct"), ("3", "label_date_correct")]
    n_rev, n_diff, _ = P.review_diff([dict(zip(H, r)) for r in rows])
    assert (n_rev, n_diff) == (3, {"label_type_correct": 1, "label_date_correct": 1})


def test_precision_label_source_scopes(tmp_path):
    """손으로 만든 4행: 1·2 raw Y, 3 external Y(날짜 Y), 4 raw N. raw_only type 2/3, external_included 3/4"""
    h = H + ["label_source"]
    rows = [["1", "e1", "event", "A|x|s", "claude", "Y", "Y", "Y", "Y", "", "raw"],
            ["2", "e2", "event", "A|x|s", "claude", "Y", "Y", "Y", "Y", "", "raw"],
            ["3", "e3", "event", "A|x|s", "user_reviewed", "Y", "모름", "Y", "Y", "고시", "external"],
            ["4", "e4", "event", "A|x|s", "claude", "N", "Y", "N", "Y", "", "raw"]]
    p = tmp_path / "s.csv"
    _write(p, h, rows)
    assert P.main(p) == 0
    with (tmp_path / "precision.csv").open(encoding="utf-8") as f:
        res = {(r["scope"], r["label"]): r for r in csv.DictReader(f) if r["group"] == "all"}
    assert (res[("raw_only", "label_type_correct")]["n"], res[("raw_only", "label_type_correct")]["correct"]) == ("3", "2")
    assert (res[("external_included", "label_type_correct")]["n"], res[("external_included", "label_type_correct")]["correct"]) == ("4", "3")
    assert (res[("user_reviewed", "label_date_correct")]["n"], res[("user_reviewed", "label_date_correct")]["correct"]) == ("1", "1")
    _write(p, h, [["1", "e1", "event", "A|x|s", "claude", "Y", "Y", "Y", "Y", "", "web"]])
    assert P.main(p) == 1


def test_precision_refuses_bad_labeler(tmp_path):
    p = tmp_path / "s.csv"
    _write(p, H, [["1", "e1", "event", "A|x|s", "me", "Y", "Y", "Y", "Y", ""]])
    assert P.main(p) == 1
    _write(p, H, [["1", "e1", "event", "A|x|s", "", "Y", "Y", "Y", "Y", ""]])
    assert P.main(p) == 1


def test_precision_script_refuses_empty_labels(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("sample_id,record_type,stratum,label_type_correct,label_date_correct,label_note\n1,event,a|b|c,,,\n", encoding="utf-8")
    assert P.main(p) == 1


db = pytest.mark.skipif(not DB_PATH.exists(), reason="pbd.duckdb 없음")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(DB_PATH), read_only=True)
    if not c.execute("select count(*) from information_schema.tables where table_name = 'signal_v5'").fetchone()[0]:
        pytest.skip("signal_v5 없음 (make p1-v5)")
    yield c
    c.close()


@db
def test_a1_v5_rates_match_rules(con):
    """RULES §7 (2026-11-18) + 외부 집행정지 경로(경보제약 23건: 미결 22, 집행정지 중 삭제 1): 급여중지 예고 A = 109/203,
    B = 109/(144 − 22) = 109/122, 집행정지율 = (73 + 23)/203. 삭제 예고는 건수만"""
    import pandas as pd
    r = pd.read_csv(ROOT / "outputs/s4/a1_by_type.csv").set_index("signal_type")
    assert (r.loc["SUSPEND_NOTICE", "A_k"], r.loc["SUSPEND_NOTICE", "A_n"]) == (109, 203)
    assert (r.loc["SUSPEND_NOTICE", "B_k"], r.loc["SUSPEND_NOTICE", "B_n"]) == (109, 122)
    assert (r.loc["SUSPEND_NOTICE", "stay_k"], r.loc["SUSPEND_NOTICE", "stay_n"]) == (96, 203)
    assert pd.isna(r.loc["DELISTING_NOTICE", "A_rate"])
    want = dict(con.execute("select signal_type, count(*) from signal_v5 group by 1").fetchall())
    assert dict(zip(r.index, r["n"])) == want


@db
def test_sample_60(con):
    import pandas as pd
    s = pd.read_csv(ROOT / "outputs/validation/sample_60.user_reviewed.csv", dtype=str)   # 저장소에 있는 최종 라벨 (고정, v5_labels)
    assert len(s) == 61 and s["record_id"].is_unique          # 고정 60 + 추가 1 (sample_id 61, DECISIONS 2026-11-15)
    assert (s["product_code"] == "654700150").any()
    assert s["label_type_correct"].fillna("").isin(["", "Y", "N", "모름"]).all()
    assert s["labeler"].fillna("").isin(["", "claude", "user_reviewed", "rule_check"]).all()   # rule_check: 2026-11-18 사용자 지시
    assert s["label_source"].fillna("").isin(["", "raw", "external"]).all()
    assert s[["month", "product_code", "notes_3m", "stratum_size"]].notna().all().all()


@db
def test_holdout_30(con):
    """RULES §11-8 · 지시 7: 층 10개 × 3행, sample_60 제품 제외, 시드 고정, 라벨용 파일에 원장 판정값 없음, 정답 칸 비움"""
    import pandas as pd
    from pbd.ledger import s4_analysis as S4
    s60 = set(pd.read_csv(ROOT / "outputs/validation/sample_60.user_reviewed.csv", dtype=str)["product_code"])
    a = S4.holdout_pick(con, exclude_products=s60)
    b = S4.holdout_pick(con, exclude_products=s60)
    assert a.equals(b) and len(a) == 30
    assert a.groupby("stratum").size().to_dict() == {n: 3 for n, _, _ in S4.HOLDOUT_STRATA}
    assert not (set(a["product_code"]) | set(a["counterpart"].dropna())) & s60
    assert a["product_code"].is_unique
    blind = pd.read_csv(ROOT / "outputs/validation/holdout_30.blind.csv", dtype=str)
    key = pd.read_csv(ROOT / "outputs/validation/holdout_30.key.csv", dtype=str)
    # 라벨 파일이 있으면 표본은 고정 (대응표 재사용, 2026-11-18): 라벨용 파일 = 고정 대응표의 제품
    fixed = (ROOT / "outputs/validation/holdout_30.labeled.csv").exists()
    want = list(key["product_code"]) if fixed else list(a["product_code"])
    assert len(blind) == 30 and list(blind["product_code"]) == want
    assert key.groupby("stratum").size().to_dict() == {n: 3 for n, _, _ in S4.HOLDOUT_STRATA}
    assert not set(key["product_code"]) & s60
    leak = {"stratum", "record_id", "outcome", "kind", "event_type", "signal_type", "ledger_claim", "counterpart", "reason"}
    assert not leak & set(blind.columns)
    assert blind[list(S4.TRUTH_COLS)].isna().all().all()


def test_holdout_not_overwritten(tmp_path, monkeypatch):
    """정답을 채운 holdout_30.blind.csv는 덮어쓰지 않고 holdout_30.new.csv에 쓴다"""
    import pandas as pd
    from pbd.ledger import s4_analysis as S4
    monkeypatch.setattr(S4, "VAL", tmp_path)
    monkeypatch.setattr(S4, "OUT", tmp_path / "s4")
    monkeypatch.setattr(S4, "build_a1", lambda con: {})
    for f in ("build_a2", "build_a2_buckets", "build_a2_snapshot_order", "build_a3", "build_a5"):
        monkeypatch.setattr(S4, f, lambda con: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(S4, "build_a4_with_sensitivity", lambda con: {})
    monkeypatch.setattr(S4, "build_holdout", lambda con: (pd.DataFrame({"holdout_id": [1], "truth_type": [""], "truth_date": [""], "truth_note": [""]}),
                                                         pd.DataFrame({"holdout_id": [1]})))
    (tmp_path / "holdout_30.blind.csv").write_text("holdout_id,truth_type,truth_date,truth_note\n1,SUCCESSION|transfer,2024-01-01,메모\n", encoding="utf-8")
    S4.write_all(None)
    assert "SUCCESSION|transfer" in (tmp_path / "holdout_30.blind.csv").read_text(encoding="utf-8")
    assert (tmp_path / "holdout_30.new.csv").exists()


@db
def test_a4_denominator_v5(con):
    """A4 "절반 이상"의 분모 = 최초 기재 달 급여 상태이고 산정불가가 아닌 같은 성분 제품 (사용자 결정 2026-11-15, v5 약가 유형).
    민감도는 산정불가 포함. 기대값은 state_month_v5를 이 시험에서 직접 센 값"""
    import pandas as pd
    t = pd.read_csv(ROOT / "outputs/s4/a4_targets.csv", dtype={"c4": str})
    d = pd.read_csv(ROOT / "outputs/s4/a4_denominator_diff.csv", dtype={"c4": str})
    q = """select min(first_month) from signal_v5 where market_c4 = ? and signal_date = ? and realized_month = ?
           and signal_type = 'PRICE_NOTICE' and source_rule = 'R2' and outcome in ('REALIZED', 'REALIZED_CHANGED')"""
    for r in t.head(5).itertuples():
        issued = con.execute(q, [r.c4, r.signal_date, r.check_month]).fetchone()[0]
        want = con.execute("""select count(*) from state_month_v5 where c4 = ? and list_month = ? and state = 'reimbursed'
                              and coalesce(price_type, '') <> 'unpriced'""", [r.c4, issued]).fetchone()[0]
        assert r.n_prod == want
    for r in d.itertuples():
        issued = con.execute(q, [r.c4, r.signal_date, r.check_month]).fetchone()[0]
        n_unpriced = con.execute("""select count(*) from state_month_v5 where c4 = ? and list_month = ? and state = 'reimbursed'
                                    and price_type = 'unpriced'""", [r.c4, issued]).fetchone()[0]
        assert n_unpriced > 0 and r.n_prod_sens - (0 if pd.isna(r.n_prod_main) else r.n_prod_main) == n_unpriced
