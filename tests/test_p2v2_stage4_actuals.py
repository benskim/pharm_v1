"""Stage 4 정답 적재(우회 함수) 시험: 합성 원본 형식 CSV, 손 계산. 원외만, 성분4 0 채움, 성분 × 월 합."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from pbd.common import connect
from pbd.forecast_v2 import stage4 as S4

ROOT = Path(__file__).resolve().parents[1]


def _driver():
    spec = importlib.util.spec_from_file_location("stage4_driver", ROOT / "scripts/stage4_p2v2.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_load_actuals_fixed_hand(tmp_path):
    head = "STD_YYYYMM,WK_COMPN_4,AGE_G,SEX_TYPE,SIDO,YOYANG_CLSFC_CD_ADJ,OUT_IN_HOS,PRSCRPTN_TNDN_CNT,PRSCRPTN_AMT,PATIENT_CNT,PRSCRPTN_TOT_USE\n"
    (tmp_path / "MEDICINE_202601.csv").write_text(head + "202601,123,10,1,11,3,OUT,2,100,2,10.5\n"
                                                  "202601,0123,20,2,26,1,OUT,1,50,1,4.5\n202601,0123,20,2,26,1,IN,9,9,9,99\n",
                                                  encoding="utf-8")
    (tmp_path / "MEDICINE_202602.csv").write_text(head + "202602,0123,10,1,11,3,OUT,3,1,1,7\n", encoding="utf-8")
    d = _driver().load_actuals_fixed(S4.Stage4Unlock("actuals", None), connect(None),
                                     [str(tmp_path / "MEDICINE_202601.csv"), str(tmp_path / "MEDICINE_202602.csv")])
    d = d.sort_values("month")
    assert d[["c4", "month", "qty"]].values.tolist() == [["0123", "2026-01", 15.0], ["0123", "2026-02", 7.0]]


def test_load_actuals_fixed_requires_unlock():
    with pytest.raises(AssertionError):
        _driver().load_actuals_fixed(object(), connect(None), [])


def test_locked_load_actuals_has_the_syntax_bug(tmp_path):
    """잠금 함수는 문법 오류로 실패한다(우회의 근거를 고정)"""
    (tmp_path / "MEDICINE_202601.csv").write_text("STD_YYYYMM,WK_COMPN_4,OUT_IN_HOS,PRSCRPTN_TOT_USE\n202601,1,OUT,1\n", encoding="utf-8")
    with pytest.raises(Exception, match="month"):
        S4.load_actuals(S4.Stage4Unlock("actuals", None), connect(None), [str(tmp_path / "MEDICINE_202601.csv")])
