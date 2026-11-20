"""금액 가중 WAPE와 대상월 분해 (PLAN_P2_v2 §5-7).

WAPE_i = Σ_{t∈E_i} |y_it − ŷ_it| ÷ Y_i, Y_i = Σ_{t∈E_i} y_it.
금액 가중 WAPE = Σ_i w_i · WAPE_i = Σ_t d_t, d_t = Σ_i w_i |y_it − ŷ_it| ÷ Y_i.
w_i는 사전 고정(칸이 빠져도 다시 정규화하지 않는다).
"""
from __future__ import annotations

import numpy as np


def decompose(y: np.ndarray, yhat: np.ndarray, w: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """y, yhat, mask: 성분 × 대상월. mask = 평가 칸 E. 반환 d_t (대상월 길이)"""
    y0 = np.where(mask, y, 0.0)
    e = np.where(mask, np.abs(y - yhat), 0.0)
    Y = y0.sum(axis=1)
    k = np.divide(w, Y, out=np.zeros_like(w, dtype=float), where=Y > 0)
    return (e * k[:, None]).sum(axis=0)


def weighted_wape(y, yhat, w, mask) -> float:
    return float(decompose(y, yhat, w, mask).sum())
