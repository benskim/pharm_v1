"""모델 G (PLAN_P2_v2 §5-4): LightGBM 전역 직접 모델, h마다·origin마다 처음부터 적합, 튜닝 없음.

목표 z = log y_τ − log y_{τ−12}, 예측 ŷ_t = y_{t−12} · exp(ẑ). 손실 l1. 학습 모집단 = 대상 집합, 학습 창 = 2018-01 ~ a.
특징(기준월 a′ = τ − h, a′ 이하 관측으로만, 하나라도 계산 안 되면 그 행을 쓰지 않음):
 g1 = log(y_{a′}/y_{a′−12}), g3 = log(Σ_{k=0}^{2} y_{a′−k} / Σ_{k=0}^{2} y_{a′−k−12}), seas = log(y_{τ−12} / mean(y_{a′−11..a′})),
 size = log Σ_{k=0}^{11} A_{a′−k}, mon = τ의 달(범주), dwork = 평일 수(τ) − 평일 수(τ−12), lunar_t·lunar_b = τ·τ−12 설·추석,
 cls2 = 분류 앞 2자리(범주), cov_base·cov_anchor = τ−12·a′가 코로나 기간(2020-01 ~ 2021-12).
목표 값 target: "qty"(주) 또는 "amt"(Q2-b G_A, A6). 입력 특징은 항상 수량·금액으로 같다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pbd.forecast_v2.calendar_kr import lunar, workdays
from pbd.forecast_v2.timeaxis import mnum

PARAMS = dict(objective="l1", n_estimators=500, learning_rate=0.03, num_leaves=15, min_child_samples=100, max_depth=-1,
              subsample=1.0, colsample_bytree=1.0, reg_lambda=1.0, max_bin=255, deterministic=True, force_row_wise=True,
              n_jobs=1, max_cat_to_onehot=4, verbose=-1)
FEATS = ["g1", "g3", "seas", "size", "mon", "dwork", "lunar_t", "lunar_b", "cls2", "cov_base", "cov_anchor"]
CATS = ["mon", "cls2"]
COVID = ("2020-01", "2021-12")


class Ctx:
    """패널 행렬과 달력. Y·A: 성분 × 달(열 0 = months[0]), 쓸 수 없는 칸 = NaN"""

    def __init__(self, Y: np.ndarray, A: np.ndarray, months: list[str], cls2: np.ndarray):
        self.Y, self.A, self.months, self.cls2 = Y, A, months, np.asarray(cls2, dtype=int)
        self.wd = np.array([workdays(m) for m in months], dtype=float)
        self.lu = np.array([lunar(m) for m in months], dtype=float)
        self.mon = np.array([int(m[5:7]) for m in months])
        self.cov = np.array([COVID[0] <= m <= COVID[1] for m in months], dtype=float)

    def col(self, m: str) -> int:
        return mnum(m) - mnum(self.months[0])


def _pos(x):
    return np.where(np.isfinite(x) & (x > 0), x, np.nan)


def features(c: Ctx, tau: int, h: int, rows: np.ndarray | None = None) -> pd.DataFrame:
    """대상 열 tau, 거리 h의 특징 (성분마다 한 행). 열 번호가 음수면 NaN"""
    ap = tau - h
    n = c.Y.shape[0]
    if ap - 14 < 0 or tau - 12 < 0:
        return pd.DataFrame({f: np.full(n, np.nan) for f in FEATS})
    Y, A = _pos(c.Y), _pos(c.A)
    f = pd.DataFrame({
        "g1": np.log(Y[:, ap] / Y[:, ap - 12]),
        "g3": np.log(Y[:, ap - 2:ap + 1].sum(1) / Y[:, ap - 14:ap - 11].sum(1)),
        "seas": np.log(Y[:, tau - 12] / Y[:, ap - 11:ap + 1].mean(1)),
        "size": np.log(A[:, ap - 11:ap + 1].sum(1)),
        "mon": np.full(n, c.mon[tau]),
        "dwork": np.full(n, c.wd[tau] - c.wd[tau - 12]),
        "lunar_t": np.full(n, c.lu[tau]), "lunar_b": np.full(n, c.lu[tau - 12]),
        "cls2": c.cls2,
        "cov_base": np.full(n, c.cov[tau - 12]), "cov_anchor": np.full(n, c.cov[ap]),
    })
    # NaN 합이 NaN이 되도록: sum은 NaN을 건너뛰므로 구성 칸을 따로 확인
    bad = ~np.isfinite(Y[:, ap - 14:ap + 1]).all(1) | ~np.isfinite(A[:, ap - 11:ap + 1]).all(1) | ~np.isfinite(Y[:, tau - 12])
    f.loc[bad, ["g1", "g3", "seas", "size"]] = np.nan
    return f


def target_vals(c: Ctx, tau: int, target: str) -> np.ndarray:
    V = _pos(c.Y if target == "qty" else c.A)
    return np.log(V[:, tau]) - np.log(V[:, tau - 12])


def fit_predict(c: Ctx, a: int, h: int, seed: int, target: str = "qty", first_tau: int | None = None):
    """origin의 마지막 관측 열 a에서 t = a + h 예측. 반환 (ŷ_t 배열, 학습 행 수)"""
    import lightgbm as lgb
    lo = (14 + h) if first_tau is None else first_tau
    X, z = [], []
    for tau in range(lo, a + 1):
        f = features(c, tau, h)
        y = target_vals(c, tau, target)
        ok = f[["g1", "g3", "seas", "size"]].notna().all(1).to_numpy() & np.isfinite(y)
        if ok.any():
            X.append(f[ok])
            z.append(y[ok])
    t = a + h
    ft = features(c, t, h)
    base = _pos(c.Y if target == "qty" else c.A)[:, t - 12]
    out = np.full(c.Y.shape[0], np.nan)
    if not X:
        return out, 0
    X = pd.concat(X, ignore_index=True)
    z = np.concatenate(z)
    for k in CATS:
        X[k] = pd.Categorical(X[k].astype(int), categories=sorted(set(X[k].astype(int)) | set(ft[k].astype(int))))
    m = lgb.LGBMRegressor(random_state=seed, **PARAMS)
    m.fit(X[FEATS], z, categorical_feature=CATS)
    okt = ft[["g1", "g3", "seas", "size"]].notna().all(1).to_numpy() & np.isfinite(base)
    if okt.any():
        Xt = ft[okt].copy()
        for k in CATS:
            Xt[k] = pd.Categorical(Xt[k].astype(int), categories=X[k].cat.categories)
        out[okt] = base[okt] * np.exp(m.predict(Xt[FEATS]))
    return out, len(z)
