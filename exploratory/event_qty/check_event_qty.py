"""기대값은 손으로 만든 합성 자료에서 온다."""
import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

_s = importlib.util.spec_from_file_location("eq", Path(__file__).resolve().parent / "analysis.py")
eq = importlib.util.module_from_spec(_s); _s.loader.exec_module(eq)


def test_계절과_수준은_지우고_계단만_남긴다():
    y = np.tile(np.arange(12) / 10.0, 4)
    assert eq.calc_d(y, 30, 6) == pytest.approx(0)
    y2 = y.copy(); y2[30:] += 0.2
    assert eq.calc_d(y2, 30, 6) == pytest.approx(0.2)


def test_일정한_성장은_0():
    assert eq.calc_d(np.arange(48) * 0.01, 30, 6) == pytest.approx(0)


def test_미관측이면_제외():
    y = np.zeros(48); y[33] = np.nan
    assert np.isnan(eq.calc_d(y, 30, 6))
    assert np.isnan(eq.calc_d(np.zeros(48), 10, 6))
    assert np.isnan(eq.calc_d(np.zeros(48), 45, 6))


def test_추정이_월_충격을_빼고_심은_값을_찾는다():
    rng = np.random.default_rng(1); rows = []
    for t in range(20):
        for c in range(40):
            tr = int(c < 8 + t % 5)
            rows.append(dict(c4=f"{c}_{t}", t=t, treat=tr, D=t * .1 + tr * np.log(1.1) + rng.normal(0, .02)))
    f = eq.fit(pd.DataFrame(rows))
    assert f["pct"] == pytest.approx(10, abs=.7) and f["lo"] < f["pct"] < f["hi"]


def test_사건_칸_선정():
    first = 2018 * 12
    Y = pd.DataFrame(np.zeros((4, 96)), index=list("abcd"), columns=range(first, first + 96))
    t0 = 2023 * 12 + 2
    npd = pd.Series({(c, t): (1 if c == "d" else 3) for c in "abcd" for t in range(2022 * 12, 2026 * 12)})
    E = pd.Series({("a", t0): 1, ("a", t0 + 3): 1, ("d", t0): 1})
    z = pd.Series({("zz", 0): 1})
    L = dict(Y=Y, first=first, npd=npd, nsub=z, dk=z, P=z, P_any=z, E=E, E_any=E, S=z, S_ex=z, cls=pd.Series({"a": "1"}))
    d, drop = eq.build(L, "E")
    assert list(zip(d[d.treat == 1].c4, d[d.treat == 1].t)) == [("a", t0)]
    assert drop == {"사용": 1, "직전 6개월에 같은 유형 사건": 1, "전달 제품 2개 미만": 1}
    assert not ((d.c4 == "a") & (d.treat == 0) & d.t.between(t0 - 5, t0 + 9)).any()
