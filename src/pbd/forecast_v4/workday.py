"""근무일수 달력과 성분별 계수 (PLAN_P2_v4 §4·§5). 달력은 G의 dwork와 같은 calendar_kr.workdays."""
from __future__ import annotations

import datetime as dt

import numpy as np

from pbd.forecast_v2.calendar_kr import workdays


def wd_known(month: str, temp: list[str]) -> int:
    """예측 달 근무일수: 임시공휴일을 모른다고 보고 평일이면 근무일로 되돌린다"""
    n = workdays(month)
    for s in temp:
        d = dt.date.fromisoformat(str(s))
        if s[:7] == month and d.weekday() < 5:
            n += 1
    return n


def diff_rows(Y: np.ndarray, c: np.ndarray, a: int) -> tuple[np.ndarray, np.ndarray]:
    """(u, v). u = 전년비 로그차의 1차 차분(성분 × 달, 쓸 수 없는 칸 NaN), v = dw의 1차 차분. 열 a 이하만 읽는다"""
    L = np.log(np.where(Y[:, :a + 1] > 0, Y[:, :a + 1], np.nan))
    z = L[:, 12:] - L[:, :-12]
    d = c[12:a + 1] - c[:a + 1 - 12]
    return z[:, 1:] - z[:, :-1], (d[1:] - d[:-1]).astype(float)


def huber(u: np.ndarray, v: np.ndarray, k: float, min_rows: int, iters: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """성분마다 절편 없는 Huber 회귀 u ~ v (IRLS, 척도 = MAD ÷ 0.6745). 반환 (β̂, se²), 유효 행 부족은 NaN"""
    ok = np.isfinite(u)
    n = ok.sum(1)
    U = np.where(ok, u, 0.0)
    V = np.where(ok, v[None, :], 0.0)
    sxx = (V * V).sum(1)
    use = (n >= min_rows) & (sxx > 0)
    den = np.where(sxx > 0, sxx, 1.0)
    b = (V * U).sum(1) / den
    for _ in range(iters):
        r = U - b[:, None] * V
        s = np.maximum(_mad(r, ok), 1e-12)
        x = np.abs(r) / s[:, None]
        w = np.where(x <= k, 1.0, k / np.maximum(x, k))
        new = (w * V * U).sum(1) / np.maximum((w * V * V).sum(1), 1e-300)
        done = np.max(np.abs(new - b)[use], initial=0.0) < 1e-10
        b = new
        if done:
            break
    r = U - b[:, None] * V
    s = np.maximum(_mad(r, ok), 1e-12)
    psi = np.clip(r / s[:, None], -k, k) * ok
    frac = ((np.abs(r) <= k * s[:, None]) & ok).sum(1) / np.maximum(n, 1)
    se2 = s ** 2 * (psi ** 2).sum(1) / np.maximum(n - 1, 1) / (np.maximum(frac, 1e-6) ** 2 * den)
    return np.where(use, b, np.nan), np.where(use, se2, np.nan)


def _mad(r, ok):
    m = np.where(ok, np.abs(r), np.nan)
    out = np.zeros(len(r))
    has = ok.any(1)
    out[has] = np.nanmedian(m[has], axis=1) / 0.6745
    return out


def dl(x: np.ndarray, s2: np.ndarray) -> dict:
    """DerSimonian–Laird: 전체 평균, τ², 줄임 계수 b, 줄인 값(NaN 성분 = 전체 평균)"""
    f = np.isfinite(x) & np.isfinite(s2)
    if not f.any():
        raise ValueError("줄임에 쓸 성분이 없다")
    e, v = x[f], np.maximum(s2[f], 1e-12)
    p = 1 / v
    m0 = (p * e).sum() / p.sum()
    Q = (p * (e - m0) ** 2).sum()
    c = p.sum() - (p ** 2).sum() / p.sum()
    tau2 = max((Q - (len(e) - 1)) / c, 0.0) if c > 0 else 0.0
    q = 1 / (v + tau2)
    mu = (q * e).sum() / q.sum()
    b = np.zeros(len(x))
    b[f] = tau2 / (tau2 + v)
    return dict(mu=float(mu), tau2=float(tau2), b=b, est=np.where(f, mu + b * (np.where(f, x, mu) - mu), mu))


def betas(Y: np.ndarray, c: np.ndarray, a: int, k: float, min_rows: int) -> dict:
    """origin 열 a에서 성분별 줄인 β와 전체 β̄. a 이하 열만 쓴다"""
    u, v = diff_rows(Y, c, a)
    b, s2 = huber(u, v, k, min_rows)
    r = dl(b, s2)
    return dict(beta=r["est"], mu=r["mu"], tau2=r["tau2"], b=r["b"], raw=b, se2=s2)
