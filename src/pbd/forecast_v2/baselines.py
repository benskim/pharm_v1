"""비교 기준 B1·B2 (PLAN_P2_v2 §5-3). B3(ETS)·G는 이 단계에서 만들지 않는다.

입력 Y: 성분 × 달 수량 행렬(열 = 연속한 달, 미관측 = NaN). a = t − h. 입력은 a 이하만 쓴다.
- B1: ŷ_t = y_{t−12}                                   (y_{t−12} 관측·> 0)
- B2: ŷ_t = y_{t−12} · Σ_{k=0}^{11} y_{a−k} / Σ_{k=0}^{11} y_{a−k−12}   (24개월 모두 관측·> 0, y_{t−12} 관측·> 0)
h ≤ 12 (h = 12면 y_{t−12} = y_a). h > 12는 y_{t−12}가 a 뒤라 쓸 수 없다.
입력에 0이 있는 칸은 로그 모델(G)과 같은 평가 칸을 쓰려고 예측하지 않는다(NaN).
"""
from __future__ import annotations

import numpy as np


def _pos(x):
    return np.where(np.isfinite(x) & (x > 0), x, np.nan)


def b1(Y: np.ndarray, j: int, h: int) -> np.ndarray:
    """열 j(대상월 t)의 B1 예측. 열 j − 12가 없으면 NaN"""
    assert h <= 12
    if j - 12 < 0:
        return np.full(Y.shape[0], np.nan)
    return _pos(Y[:, j - 12])


def b2(Y: np.ndarray, j: int, h: int) -> np.ndarray:
    assert h <= 12
    a = j - h
    if a - 23 < 0:
        return np.full(Y.shape[0], np.nan)
    Yp = _pos(Y)
    num = np.sum(Yp[:, a - 11:a + 1], axis=1)
    den = np.sum(Yp[:, a - 23:a - 11], axis=1)
    return Yp[:, j - 12] * num / den     # NaN이 하나라도 있으면 NaN
