"""성분별 예측 구간 (PLAN_P2_v4 §6). 로그 척도 대칭, 보정 자료 = 이미 정답이 나온 과거 h단계 예측의 |log(y/ŷ)|"""
from __future__ import annotations

import math

import numpy as np

from pbd.forecast_v4.workday import dl


def rank_q(s: np.ndarray, level: float) -> float:
    """⌈(n + 1)·level⌉번째로 작은 값(n을 넘으면 최댓값)"""
    x = np.sort(s[np.isfinite(s)])
    if not len(x):
        return float("nan")
    k = min(math.ceil(round((len(x) + 1) * level, 9)), len(x))
    return float(x[k - 1])


def scale(S: np.ndarray, min_obs: int) -> np.ndarray:
    """성분별 평균 |로그오차|를 로그 척도에서 전체 쪽으로 줄인 σ_i. 관측이 모자란 성분은 전체 값. S: 성분 × 달(NaN = 없음)"""
    ok = np.isfinite(S)
    n = ok.sum(1)
    Z = np.where(ok, S, 0.0)
    m = np.divide(Z.sum(1), n, out=np.full(len(S), np.nan), where=n > 0)
    var = np.divide(np.where(ok, (S - m[:, None]) ** 2, 0.0).sum(1), n - 1, out=np.full(len(S), np.nan), where=n > 1)
    use = (n >= min_obs) & (m > 0) & (var > 0)
    if not use.any():
        return np.full(len(S), float(np.nanmean(S)))
    with np.errstate(invalid="ignore", divide="ignore"):
        x = np.where(use, np.log(np.where(use, m, 1.0)), np.nan)
        s2 = np.where(use, var / (n * m ** 2), np.nan)
    return np.exp(dl(x, s2)["est"])


def fit(S: np.ndarray, levels, min_obs: int) -> dict:
    """보정 자료 S(성분 × 달) → I0 q, I1 q와 σ. I1의 q는 그 달을 뺀 σ로 나눈 값의 분위"""
    sig = scale(S, min_obs)
    N = np.full_like(S, np.nan)
    for j in range(S.shape[1]):
        rest = np.delete(S, j, axis=1)
        if rest.shape[1] and np.isfinite(rest).any():
            N[:, j] = S[:, j] / scale(rest, min_obs)
    return dict(sigma=sig, q0={l: rank_q(S.ravel(), l) for l in levels}, q1={l: rank_q(N.ravel(), l) for l in levels})


def iscore(x, lo, hi, level: float):
    """구간 점수(로그 척도). = (2/α) × 두 끝 분위 pinball loss 합"""
    al = 1 - level
    return (hi - lo) + (2 / al) * np.maximum(lo - x, 0) + (2 / al) * np.maximum(x - hi, 0)


def decompose_mean(v: np.ndarray, w: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """성분 평균을 w로 가중한 지표의 대상월 분해"""
    n = mask.sum(1)
    k = np.divide(w, n, out=np.zeros(len(w)), where=n > 0)
    return (np.where(mask, v, 0.0) * k[:, None]).sum(0)
