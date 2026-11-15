"""P2 특징이 사후 칸(뒤 스냅샷이 와야 정해지는 칸)을 읽지 않는지 (사용자 결정 2026-11-15, PLAN_P2 §2).
P2 특징 생성 코드(src/pbd/forecast/, market_month를 만드는 파일)가 생기면 이 시험이 그 코드를 검사한다."""
from pbd.common import ROOT
from pbd.ledger.posthoc import POSTHOC_COLUMNS, find_posthoc


def _feature_sources():
    files = sorted((ROOT / "src/pbd/forecast").rglob("*.py"))
    files += [p for p in sorted((ROOT / "src/pbd").rglob("*.py"))
              if p not in files and "market_month" in p.read_text(encoding="utf-8") and p.name != "posthoc.py"]
    return files


def test_feature_code_does_not_read_posthoc_columns():
    bad = {str(p.relative_to(ROOT)): find_posthoc(p.read_text(encoding="utf-8")) for p in _feature_sources()}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, f"P2 특징 코드가 사후 칸을 읽는다: {bad}"


def test_find_posthoc_catches_columns():
    """손으로 만든 SQL: 사후 칸은 잡고, 비슷한 이름(check_month_x 아님, issued_month)은 잡지 않는다"""
    sql = "select signal_id, issued_month, check_month, l.last_seen_month from signal_outcome o join signal_ledger l using (signal_id)"
    assert find_posthoc(sql) == ["check_month", "last_seen_month"]
    assert find_posthoc("select issued_month, signal_date, lead_days, state_asof from x") == []
    assert "check_month_by_date" in POSTHOC_COLUMNS["signal_outcome"] and "realized_month" in POSTHOC_COLUMNS["signal_outcome"]
