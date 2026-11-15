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
    if not c.execute("select count(*) from information_schema.tables where table_name = 'signal_outcome'").fetchone()[0]:
        pytest.skip("signal_outcome 없음")
    yield c
    c.close()


@db
def test_a1_denominator_excludes_censored_unverifiable_moot(con):
    import pandas as pd
    t = pd.read_csv(ROOT / "outputs/s4/a1_by_type.csv")
    want = dict(con.execute("""select signal_type, count(*) filter (where outcome not in ('censored', 'unverifiable', 'moot'))
                               from signal_outcome group by 1""").fetchall())
    assert dict(zip(t["signal_type"], t["denominator"])) == want
    r = t.set_index("signal_type")
    # RESUME_SCHEDULED: moot 72는 분모에서 빠진다 (83 − 72 = 11)
    assert (r.loc["RESUME_SCHEDULED", "moot"], r.loc["RESUME_SCHEDULED", "denominator"]) == (72, 11)
    # DELISTING_SCHEDULED는 건수로만 (실현율 없음)
    assert pd.isna(r.loc["DELISTING_SCHEDULED", "rate"])


@db
def test_sample_60(con):
    import pandas as pd
    s = pd.read_csv(ROOT / "outputs/validation/sample_60.csv", dtype=str)
    assert len(s) == 60 and s["record_id"].is_unique
    assert (s["product_code"] == "654700150").any()
    assert s[["labeler", "claude_label_type_correct", "claude_label_date_correct",
              "label_type_correct", "label_date_correct", "label_note"]].isna().all().all()
    assert s[["month", "product_code", "notes_3m", "stratum_size"]].notna().all().all()
    d = s[s["record_id"].str.startswith("DELISTING:") & s["ledger_claim"].str.contains("interval")]
    assert len(d) and d["interval_days"].notna().all()


@db
def test_sample_deterministic(tmp_path):
    from pbd.common import connect
    from pbd.ledger import s4_analysis as S4
    c = connect(None)
    try:
        c.execute(f"attach '{DB_PATH.as_posix()}' as src (read_only)")
        c.execute("use src")
        a = S4.build_sample(c)
        b = S4.build_sample(c)
    finally:
        c.close()
    assert a.equals(b)
    saved = __import__("pandas").read_csv(ROOT / "outputs/validation/sample_60.csv", dtype=str)
    assert list(a["record_id"]) == list(saved["record_id"])


def test_labeled_sample_not_overwritten(tmp_path, monkeypatch):
    """라벨이 있는 sample_60.csv는 덮어쓰지 않고 sample_60.new.csv에 쓴다"""
    import pandas as pd
    from pbd.ledger import s4_analysis as S4
    (tmp_path / "sample_60.csv").write_text("sample_id,label_type_correct,label_date_correct,label_note\n1,Y,,\n", encoding="utf-8")
    monkeypatch.setattr(S4, "VAL", tmp_path)
    monkeypatch.setattr(S4, "OUT", tmp_path / "s4")
    monkeypatch.setattr(S4, "build_a1", lambda con: {})
    for f in ("build_a2", "build_a2_buckets", "build_a2_snapshot_order", "build_a3"):
        monkeypatch.setattr(S4, f, lambda con: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(S4, "build_a4_with_sensitivity", lambda con: {})
    monkeypatch.setattr(S4, "build_sample", lambda con: pd.DataFrame({"sample_id": [1], "label_type_correct": [""]}))

    class C:
        def execute(self, q):
            class R:
                def df(self_inner):
                    return pd.DataFrame({"y": [1]})
            return R()
    S4.write_all(C())
    assert (tmp_path / "sample_60.csv").read_text(encoding="utf-8").startswith("sample_id,label_type_correct")
    assert "Y" in (tmp_path / "sample_60.csv").read_text(encoding="utf-8")
    assert (tmp_path / "sample_60.new.csv").exists()



@db
def test_a4_denominator_reimbursed_only(con):
    """A4 "절반 이상"의 분모 = 발행 달 state_asof = reimbursed인 같은 성분 제품 (사용자 결정 2026-11-15).
    기대값은 product_state_month를 이 시험에서 직접 센 값. 민감도 표는 reimbursed + unpriced."""
    import pandas as pd
    t = pd.read_csv(ROOT / "outputs/s4/a4_targets.csv", dtype={"c4": str})
    d = pd.read_csv(ROOT / "outputs/s4/a4_denominator_diff.csv", dtype={"c4": str})
    for r in t.head(5).itertuples():
        issued = con.execute("""select min(o.issued_month) from signal_outcome o join signal_ledger l using (signal_id)
            where l.market_c4 = ? and l.signal_date = ? and o.check_month = ? and o.signal_type = 'PRICE_PENDING'
              and o.outcome in ('realized', 'realized_partial')""", [r.c4, r.signal_date, r.check_month]).fetchone()[0]
        want = con.execute("""select count(*) from product_state_month where c4 = ? and list_month = ? and state_asof = 'reimbursed'""",
                           [r.c4, issued]).fetchone()[0]
        assert r.n_prod == want
    for r in d.itertuples():
        issued = con.execute("""select min(o.issued_month) from signal_outcome o join signal_ledger l using (signal_id)
            where l.market_c4 = ? and l.signal_date = ? and o.check_month = ? and o.signal_type = 'PRICE_PENDING'
              and o.outcome in ('realized', 'realized_partial')""", [r.c4, r.signal_date, r.check_month]).fetchone()[0]
        n_unpriced = con.execute("""select count(*) from product_state_month where c4 = ? and list_month = ? and state_asof = 'unpriced'""",
                                 [r.c4, issued]).fetchone()[0]
        assert n_unpriced > 0 and r.n_prod_sens - (0 if pd.isna(r.n_prod_main) else r.n_prod_main) == n_unpriced
