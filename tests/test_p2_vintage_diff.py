"""사용통계 두 판본 비교 스크립트 시험 (합성 자료, 기대값은 손 계산)."""
from __future__ import annotations

import importlib.util

import pandas as pd
import pytest

from pbd.common import ROOT

spec = importlib.util.spec_from_file_location("vdiff", ROOT / "scripts/p2_usage_vintage_diff.py")
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

HEAD = "STD_YYYYMM,WK_COMPN_4,AGE_G,SEX_TYPE,SIDO,YOYANG_CLSFC_CD_ADJ,OUT_IN_HOS,PRSCRPTN_TNDN_CNT,PRSCRPTN_AMT,PATIENT_CNT,PRSCRPTN_TOT_USE\n"


def _write(d, name, rows):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(HEAD + "".join(rows), encoding="utf-8")


@pytest.fixture()
def vintages(tmp_path):
    # 기준 판본: 2025-11 성분 0123 원외 100(두 행 60+40), 원내 999는 뺀다. 성분 0456 원외 50. 2025-12 성분 0123 원외 80
    _write(tmp_path / "base", "MEDICINE_202511.csv", ["202511,0123,10,1,11,3,OUT,1,600,1,60\n", "202511,0123,20,2,26,1,OUT,1,400,1,40\n",
                                                       "202511,0123,20,2,26,1,IN,1,1,1,999\n", "202511,0456,10,1,11,3,OUT,1,500,1,50\n"])
    _write(tmp_path / "base", "MEDICINE_202512.csv", ["202512,0123,10,1,11,3,OUT,1,800,1,80\n"])
    # 새 판본: 2025-11 성분 0123 원외 110 (+10%), 0456 원외 50 (그대로), 2025-12 0123 원외 100 (+25%), 새 판본에만 있는 0789
    _write(tmp_path / "new", "MEDICINE_202511.csv", ["202511,0123,10,1,11,3,OUT,1,1100,1,110\n", "202511,0456,10,1,11,3,OUT,1,500,1,50\n"])
    _write(tmp_path / "new", "MEDICINE_202512.csv", ["202512,0123,10,1,11,3,OUT,1,1000,1,100\n", "202512,0789,10,1,11,3,OUT,1,30,1,3\n"])
    return tmp_path


def test_diff_by_c4_month(vintages, tmp_path):
    cls = pd.DataFrame({"c4": ["0123", "0456", "0789"], "cls": [141, 141, 222]})
    res = V.run(str(vintages / "base" / "MEDICINE_*.csv"), "2026-10-28", str(vintages / "new" / "MEDICINE_*.csv"), "2026-12-01", cls, tmp_path / "out")
    d = res["vintage_diff_c4"].set_index(["c4", "month"])
    assert d.loc[("0123", "2025-11"), "qty_base"] == 100 and d.loc[("0123", "2025-11"), "qty_new"] == 110
    assert d.loc[("0123", "2025-11"), "qty_pct"] == pytest.approx(0.10)
    assert d.loc[("0123", "2025-11"), "amt_pct"] == pytest.approx(0.10)       # 금액 1000 → 1100
    assert d.loc[("0123", "2025-12"), "qty_pct"] == pytest.approx(0.25)
    assert d.loc[("0456", "2025-11"), "qty_diff"] == 0
    assert d.loc[("0789", "2025-12"), "only"] == "새 판본에만"
    # 경과 개월: 2025-11 → 2026-10 공개 = 11개월, 2025-12 → 10개월. 새 판본(2026-12) 기준 13·12개월
    assert d.loc[("0123", "2025-11"), "elapsed_base"] == 11 and d.loc[("0123", "2025-12"), "elapsed_base"] == 10
    assert d.loc[("0123", "2025-11"), "elapsed_new"] == 13
    c = res["vintage_diff_class"].set_index(["cls", "month"])
    assert c.loc[(141, "2025-11"), "qty_base"] == 150 and c.loc[(141, "2025-11"), "qty_new"] == 160   # 100+50 → 110+50
    assert c.loc[(141, "2025-11"), "qty_pct"] == pytest.approx(10 / 150)
    e = res["vintage_diff_by_elapsed"].set_index("elapsed_base")
    assert e.loc[11, "qty_total_pct"] == pytest.approx(10 / 150) and e.loc[10, "qty_total_pct"] == pytest.approx(0.25)


def test_skips_without_new_files(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["x", "--new", "/nonexistent/MEDICINE_*.csv"])
    V.main()
    assert "건너뜀" in capsys.readouterr().out
