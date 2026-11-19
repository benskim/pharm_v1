"""v5.3 새 검증 표본 라벨 화면과 채점 함수 시험. 채점은 합성 자료로만 (실제 라벨로 실행하지 않음, 2026-11-19 사용자 지시)."""
from __future__ import annotations

import importlib.util

import pandas as pd
import pytest

from pbd.common import ROOT

VAL = ROOT / "outputs/validation"
spec = importlib.util.spec_from_file_location("v53s", ROOT / "scripts/holdout_v53_score.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

BANNED = ("REALIZED", "CENSORED", "SUCCESSION", "DELISTING", "SUSPEND", "PRICE_CHANGE", "LISTING", "signal", "outcome", "원장", "실현",
          "미결", "판정", "transfer", "recode", "swap", "unobserved", "list_only", "stay")


@pytest.fixture(scope="module")
def blind():
    p = VAL / "holdout_v53.blind.csv"
    if not p.exists():
        pytest.skip("라벨 화면 없음 (python -I scripts/holdout_v53.py)")
    return pd.read_csv(p, dtype=str).fillna("")


def test_blind_has_no_ledger_values_or_rule_terms(blind):
    assert len(blind) == 30
    assert list(blind.columns) == ["holdout_id", "product_code", "ref_month", "look_at", "benefit_window", "substitute_window",
                                   "same_item_codes", "same_ingredient_candidates", "truth_event", "truth_date", "truth_note"]
    text = " ".join(blind[["look_at", "same_item_codes", "same_ingredient_candidates"]].to_numpy().ravel())
    assert [w for w in BANNED if w in text] == []
    assert (blind["product_code"].str.len() == 9).all() and blind["product_code"].is_unique


def test_key_strata_and_mid_month():
    p = VAL / "holdout_v53.key.csv"
    if not p.exists():
        pytest.skip("key 없음")
    k = pd.read_csv(p, dtype=str)
    exp = {"정시 실현": 6, "변경·지연 실현": 4, "급여중지 관련": 4, "양도양수": 2, "코드 변경": 2, "교체 등재": 2,
           "급여삭제": 3, "중지해제": 2, "비고 미기재 약가 변경": 3, "신규·추가 등재": 2}
    assert k.groupby("stratum").size().to_dict() == exp
    assert (k["mid"] == "True").sum() >= 8
    old = pd.read_csv(VAL / "holdout_30.key.csv", dtype=str)
    s60 = pd.read_csv(VAL / "sample_60.user_reviewed.csv", dtype=str)
    assert not set(k["product_code"]) & (set(old["product_code"]) | set(s60["product_code"]))


def test_score_mapping_synthetic():
    d = pd.DataFrame(dict(stratum=["가", "가", "나", "나"],
                          truth_event=["예고대로 시행", "삭제", "이어받기(같은 회사)", "기타"],
                          ledger_value=["REALIZED", "REALIZED_BY_DELISTING", "SUCCESSION:transfer", "SUSPEND:in_window"]))
    r = S.score(d).set_index("group")
    assert (r.loc["전체", "correct"], r.loc["전체", "n"]) == (2, 4)       # 손 계산: 1·2행 일치, 3행(다른 회사 ≠ 같은 회사)·4행(기타) 불일치
    assert r.loc["가", "precision"] == 1.0 and r.loc["나", "precision"] == 0.0
    with pytest.raises(ValueError):
        S.match("모르는 범주", "REALIZED")


def test_score_refuses_without_run(monkeypatch):
    monkeypatch.setattr("sys.argv", ["x"])
    with pytest.raises(SystemExit):
        S.main()


def test_key_matches_blind(blind):
    """정답 키와 라벨 화면의 대응: 같은 holdout_id에 같은 제품코드·기준월 (2026-11-19 사용자 지시 3)"""
    p = VAL / "holdout_v53.key.csv"
    if not p.exists():
        pytest.skip("key 없음")
    k = pd.read_csv(p, dtype=str).set_index("holdout_id")[["product_code", "ref_month"]]
    b = blind.set_index("holdout_id")[["product_code", "ref_month"]]
    assert k.sort_index().equals(b.sort_index())


def test_suspend_without_note_maps_to_existing_category():
    """범주를 추가하지 않는다: 비고 없이 시작된 급여정지·중지는 '예고대로 시행' (목록 고시 원칙)"""
    assert S.match("예고대로 시행", "SUSPEND:in_window") and not S.match("기타", "SUSPEND:in_window")
