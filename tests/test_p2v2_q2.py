"""P2 v2 목록 정보·Q2 시험. 기대값은 손으로 만든 합성 목록 3개월과 손 계산 (구현 출력으로 만들지 않는다)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from pbd.forecast_v2 import q2 as Q2
from pbd.forecast_v2.list_info import ListData
from pbd.forecast_v2.timeaxis import mrange


def _ld():
    """성분 X: 제품 x1(업체 F1, 100 → 2024-02에 80), x2(F2, 200 그대로), x3(F2, 2024-02 신규 50).
    성분 Z: z1(급여, 10), z2(2024-02 suspended).
    토큰(2024-02 목록): x2 R2 시행 2024-03-16 금액 150 (t = 2024-03, 31일 → ω = 16/31),
    x1 R2 시행 2024-02-01(= s 1일, 창 밖), z1 R3 급여정지 예고 2024-03-10, R1 등재 예고 x9(성분 X) 2024-03-01.
    z1에 2024-02 집행정지 표시(→ ΔN·SN에서 빠짐)"""
    rows = []
    for m in ("2024-01", "2024-02", "2024-03"):
        rows.append(("x1", m, "X", "reimbursed", 100.0 if m == "2024-01" else 80.0, "F1"))
        rows.append(("x2", m, "X", "reimbursed", 200.0, "F2"))
        if m >= "2024-02":
            rows.append(("x3", m, "X", "reimbursed", 50.0, "F2"))
            rows.append(("x9", m, "X", "scheduled_listing", None, "F3"))
        rows.append(("z1", m, "Z", "reimbursed", 10.0, "G1"))
        rows.append(("z2", m, "Z", "reimbursed" if m == "2024-01" else "suspended", 20.0, "G2"))
    psm = pd.DataFrame(rows, columns=["product_code", "list_month", "c4", "state_as_known", "price_listed", "firm"])
    tok = pd.DataFrame([
        ("2024-02", "x2", "R2", "2024-03-16", 150.0, True, False),
        ("2024-02", "x1", "R2", "2024-02-01", 70.0, False, True),
        ("2024-02", "z1", "R3", "2024-03-10", None, True, False),
        ("2024-02", "x9", "R1", "2024-03-01", None, True, False),
    ], columns=["list_month", "product_code", "token_type", "token_date", "amount", "is_future", "in_month_dated"])
    stay = pd.DataFrame([("z1", "2024-02", None)], columns=["product_code", "known_month", "end_month"])
    return ListData(psm, tok, stay)


def test_list_info_hand():
    f = _ld().info("2024-01", "2024-02", "2024-03")
    X, Z = f.loc["X"], f.loc["Z"]
    assert X.dK == pytest.approx((math.log(80 / 100) + math.log(200 / 200)) / 2)       # 공통 x1·x2
    assert X.dN == pytest.approx((16 / 31) * math.log(150 / 200) / 3)                 # P_X(s) = 3개
    assert X.dF == pytest.approx(math.log(2 / 2))                                      # F1·F2 → F1·F2
    assert X.dP == pytest.approx(math.log(3 / 2))
    assert X.LN == pytest.approx(math.log1p(1))
    assert X.S == 0 and X.SN == 0
    assert Z.dK == pytest.approx(0.0)                                                  # z1 그대로, z2는 s에 급여 아님
    assert Z.S == pytest.approx(1 / 2)                                                 # suspended 1 / (급여 1 + 정지 1)
    assert Z.SN == 0                                                                   # z1은 집행정지 → 뺌
    assert Z.dN == 0


def test_list_info_exclude_in_month():
    """A15: in_month_dated 토큰 제외 — 창 밖 토큰이라 값은 같아야 한다"""
    ld = _ld()
    assert ld.info("2024-01", "2024-02", "2024-03", True).loc["X", "dN"] == pytest.approx(
        ld.info("2024-01", "2024-02", "2024-03").loc["X", "dN"])


def test_first_drop():
    assert _ld().first_drop("2024-03").to_dict() == {"X": "2024-02"}


def test_ridge_recovers_known_coefficient():
    """r = 2·ΔK (잡음 없음), ΔK ∈ {0, log 0.8}, 행 3,000개: 표준화 계수 / sd ≈ 2 (릿지 λ = 1의 수축은 작음)"""
    rng = np.random.default_rng(0)
    dk = np.where(rng.random(3000) < 0.3, math.log(0.8), 0.0)
    X = np.column_stack([dk] + [rng.standard_normal(3000) for _ in range(6)])
    r = 2.0 * dk
    m = Q2.ridge_fit(X, r, 1.0)
    assert m["beta"][0] / m["sd"][0] == pytest.approx(2.0, rel=0.01)
    assert Q2.ridge_predict(m, X[:5]) == pytest.approx(r[:5], abs=0.01)


def test_ridge_zero_variance_feature():
    X = np.column_stack([np.arange(10.0), np.zeros(10)])
    m = Q2.ridge_fit(X, np.arange(10.0), 1.0)
    assert m["beta"][1] == 0.0 and np.isfinite(Q2.ridge_predict(m, X)).all()


def test_inject_hand():
    months = mrange("2024-01", "2024-04")
    Y = np.ones((2, 4))
    A = np.full((2, 4), 10.0)
    Y2, A2 = Q2.inject(Y, A, months, np.array(["2024-03", None], dtype=object), 0.2)
    assert Y2[0].tolist() == [1, 1, 0.8, 0.8] and Y2[1].tolist() == [1, 1, 1, 1]
    assert (A2 / Y2).tolist() == (A / Y).tolist()                  # 단가 불변


def test_permutation_degenerate_when_all_equal():
    """ΔK ≠ 0 값이 모두 같으면 섞어도 같다 → p = 1 (검토 33 순열 퇴화)"""
    UP = np.full((4, 2), 0.9)
    dk = np.full((4, 2), math.log(0.9))
    upa = np.ones((4, 2))
    mask = np.ones((4, 2), bool)
    w = np.full(4, 0.25)

    def metric(UPx, U):
        return float(np.abs(np.log(U) - np.log(UPx)).mean())

    assert Q2.permutation_p(UP, None, dk, upa, w, mask, 49, 1, metric) == 1.0
