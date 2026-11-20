"""B3 ETS (PLAN_P2_v2 §5-3).

log y에 statsmodels ETSModel: 오차 가법, 계절 가법(주기 12), 추세 {없음, 가법 감쇠} 중 AICc 최소, 최대우도, 초기값 estimated.
학습 = a에서 끝나는 가장 긴 연속 관측 구간, 36개월 이상. 예측 = h단계 앞 exp(평균).
대체: 예외 또는 ConvergenceWarning이면 B2 값을 쓰고 기록(fallback = True). 학습 36개월 미만도 대체.
"""
from __future__ import annotations

import warnings

import numpy as np


def _fit_one(x: np.ndarray, trend):
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        warnings.simplefilter("ignore", RuntimeWarning)
        warnings.simplefilter("ignore", UserWarning)
        m = ETSModel(x, error="add", trend=trend, damped_trend=trend is not None, seasonal="add",
                     seasonal_periods=12, initialization_method="estimated")
        return m.fit(disp=False, maxiter=1000)


def ets_path(y_hist: np.ndarray, H: int):
    """y_hist: a까지의 수량(NaN = 미관측). (1 ~ H단계 예측 배열 또는 None, 대체 사유 또는 None)"""
    ok = np.isfinite(y_hist) & (y_hist > 0)
    n = 0
    for v in ok[::-1]:
        if not v:
            break
        n += 1
    if n < 36:
        return None, f"학습 {n}개월 < 36"
    x = np.log(y_hist[-n:])
    best = None
    for trend in (None, "add"):
        try:
            r = _fit_one(x, trend)
        except Exception as e:                        # ConvergenceWarning 포함
            return None, type(e).__name__
        if best is None or r.aicc < best.aicc:
            best = r
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        f = np.exp(np.asarray(best.forecast(H), dtype=float))
    if not np.isfinite(f).all():
        return None, "예측값 비정상"
    return f, None
