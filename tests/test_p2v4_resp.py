"""P2 v4 호흡기 분석 보조 함수: 손 계산"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import p2v4_resp_analysis as A


def _g(ew, eb):
    """성분 5개 × 세 해, 해마다 오차 ew·eb (가중치 0.1씩)"""
    return pd.DataFrame([dict(c4=f"{i}", t=f"{y}-01", yr=y, w=.1, e_W=ew, e_B3=eb) for i in range(5) for y in ("2023", "2024", "2025")])


def test_wape_renormalises_by_group_weight():
    assert A.wape(_g(.02, .03), "W") == pytest.approx(15 * .02 / .5)
    assert A.delta(_g(.02, .03)) == pytest.approx(-15 * .01 / .5)


def test_verdict_three_ways():
    assert A.verdict(_g(.01, .02), 0)["group_verdict"] == "A"
    assert A.verdict(_g(.02, .01), 0)["group_verdict"] == "B"
    g = _g(.01, .02)
    g.loc[g.yr == "2024", "e_W"] = .03           # 한 해라도 부호가 다르면 C
    assert A.verdict(g, 0)["group_verdict"] == "C"


def test_boot_constant_difference():
    lo, hi = A.boot(_g(.01, .02))
    assert lo == pytest.approx(-.3) and hi == pytest.approx(-.3)      # 성분마다 3 × 0.01 ÷ 0.1
