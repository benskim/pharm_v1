"""P2 T2·T3 롤링 origin 틀과 모델 (최소판: 주력 묶음 성분 × 전국, 예측 대상 = 발간월 M의 다음 달 M+1).

시점 (PLAN_P2 §1·§5):
- origin = 발간월 M의 1일. 사용통계는 M − lag 이하만 쓴다 (lag = usage_publication_lag_months).
- 예측 대상 t = M+1. 마지막 관측월 a = M − lag에서 h = lag + 1개월 앞.
- 모든 모델은 available()이 자른 표만 받는다. 누수 시험(tests/test_p2_t2.py): M − lag보다 뒤의 행을 바꾸거나 지워도
  origin M의 예측이 같아야 한다.

모델:
- M0: 계절 단순 예측. ŷ_t = y_{t−12} (t−12 ≤ a인 lag ≤ 11에서만 정의)
- M2: LightGBM 전역 모델 (전 성분 한 모델). 목표 log(y_t / y_a), 특징은 a 이전 시차·계절 시차·달·분류·코로나 표시
- M3: M2 + 선행 신호 특징 (signals.py). 같은 하이퍼파라미터·같은 시드
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pbd.common import load_yaml

PARAMS = load_yaml("params.yaml")
P = PARAMS["p2"]
SEED = PARAMS["seed"]
COVID = tuple(P["covid_months"])
N_LAGS = 12
LGB_PARAMS = dict(objective="regression", n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=20,
                  subsample=1.0, colsample_bytree=1.0, reg_lambda=1.0, random_state=SEED, deterministic=True,
                  force_row_wise=True, n_jobs=1, verbose=-1)


# ---------------------------------------------------------------- 달 계산
def midx(m: str) -> int:
    return int(m[:4]) * 12 + int(m[5:7]) - 1


def mstr(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def madd(m: str, k: int) -> str:
    return mstr(midx(m) + k)


def months(lo: str, hi: str) -> list[str]:
    return [mstr(i) for i in range(midx(lo), midx(hi) + 1)]


# ---------------------------------------------------------------- 누수 차단
def available(panel: pd.DataFrame, origin: str, lag: int) -> pd.DataFrame:
    """origin M에 쓸 수 있는 사용통계: month ≤ M − lag. 모든 모델 입력은 이 함수를 거친다"""
    return panel[panel["month"] <= madd(origin, -lag)]


def to_matrix(hist: pd.DataFrame, series: list[str], lo: str, hi: str) -> np.ndarray:
    """성분 × 달 행렬. usable이 아닌 칸(미관측·첫 관측월)은 NaN"""
    ms = months(lo, hi)
    y = hist[hist["usable"]].pivot(index="c4", columns="month", values="claim_qty")
    return y.reindex(index=series, columns=ms).to_numpy(dtype=float)


# ---------------------------------------------------------------- 모델
def m0(hist: pd.DataFrame, origin: str, lag: int, series: list[str], **_) -> pd.DataFrame:
    t = madd(origin, 1)
    s = madd(t, -12)
    if midx(s) > midx(origin) - lag:
        raise ValueError(f"M0: 12개월 전({s})이 마지막 관측월보다 뒤 (lag {lag})")
    v = hist[(hist["month"] == s) & hist["usable"]].set_index("c4")["claim_qty"]
    return pd.DataFrame(dict(c4=series, target=t, yhat=[v.get(c, np.nan) for c in series]))


def m0_quantile(hist: pd.DataFrame, origin: str, lag: int, series: list[str], q: float = 0.75, **_) -> pd.DataFrame:
    """M0의 분위 판: ŷ = y_{t−12} × (과거 y_u / y_{u−12} 비율의 q 분위). 비율은 u ≤ M − lag인 성분 자신의 값 (검토 a10 기준 정책)"""
    t = madd(origin, 1)
    last = madd(origin, -lag)
    y = to_matrix(hist, series, P["start_month"], last)
    r = y[:, 12:] / y[:, :-12]
    qq = np.nanquantile(np.where(np.isfinite(r) & (r > 0), r, np.nan), q, axis=1)
    base = m0(hist, origin, lag, series)
    return base.assign(yhat=base["yhat"].to_numpy() * qq)


def design(y: np.ndarray, lo: str, series: list[str], cls: dict, h: int, t_lo: int, t_hi: int,
           feat: pd.DataFrame | None = None, target_mode: str = "last") -> pd.DataFrame:
    """대상 달 t(행렬 열 번호 t_lo..t_hi)마다 한 행. a = t − h 이하 값만 쓴다. feat: (c4, origin) 신호 특징.
    target_mode = 'last': 목표 log(y_t / y_a) (주), 'seasonal': 목표 log(y_t / y_{t−12}) (실험, 검토 b8)"""
    base = midx(lo)
    rows = []
    for i, c in enumerate(series):
        for t in range(t_lo, t_hi + 1):
            a = t - h
            if a - N_LAGS + 1 < 0:
                continue
            ya = y[i, a]
            if not np.isfinite(ya) or ya <= 0:
                continue
            r = dict(c4=c, target=mstr(base + t), origin=mstr(base + t - 1), level=np.log(ya),
                     month_of_year=(base + t) % 12 + 1, cls=cls.get(c, -1),
                     covid=int(COVID[0] <= mstr(base + t) <= COVID[1]))
            for k in range(1, N_LAGS):
                v = y[i, a - k]
                r[f"d{k}"] = np.log(v / ya) if np.isfinite(v) and v > 0 else np.nan
            for s in (12, 24):
                j = t - s
                v = y[i, j] if 0 <= j <= a else np.nan
                r[f"s{s}"] = np.log(v / ya) if np.isfinite(v) and v > 0 else np.nan
            yt = y[i, t] if t < y.shape[1] else np.nan
            if target_mode == "seasonal":
                yb = y[i, t - 12] if t - 12 >= 0 else np.nan
                if not np.isfinite(yb) or yb <= 0:
                    continue
            else:
                yb = ya
            r["y_target"] = np.log(yt / yb) if np.isfinite(yt) and yt > 0 else np.nan
            r["ya"] = yb
            rows.append(r)
    d = pd.DataFrame(rows)
    if feat is not None and len(d):
        d = d.merge(feat, on=["c4", "origin"], how="left")
    return d


def train_frame(y: np.ndarray, lo: str, series: list[str], cls: dict, h: int, last: int, feat, target_mode: str = "last",
                train_lo: str | None = None) -> pd.DataFrame:
    """학습 행: 목표가 관측된 대상 달 t ≤ last(= M − lag). 목표 달이 공백 구간(M − lag, M]에 들어가면 라벨 누수다"""
    tr = design(y, lo, series, cls, h, 0, y.shape[1] - 1, feat, target_mode)
    tr = tr[tr["y_target"].notna()]
    if train_lo is not None:
        tr = tr[tr["target"] >= train_lo]
    assert len(tr) == 0 or tr["target"].max() <= mstr(last), "학습 목표가 마지막 관측월 뒤 (라벨 누수)"
    return tr


def gbm(hist: pd.DataFrame, origin: str, lag: int, series: list[str], cls: dict, feat: pd.DataFrame | None = None,
        lo: str = P["start_month"], quantile: float | None = None, target_mode: str = "last", train_lo: str | None = None,
        params: dict | None = None, **_) -> pd.DataFrame:
    """train_lo: 학습 행의 대상 달 하한 (실험, 검토 b5). params: LGB_PARAMS 덮어쓰기 (실험)"""
    """M2 (feat 없음) / M3 (feat = 신호 특징). 학습: 대상 달 t ≤ M − lag (관측된 목표만). 예측: t = M+1"""
    import lightgbm as lgb

    h = lag + 1
    last = midx(origin) - lag
    assert hist["month"].max() <= mstr(last), "available()을 거치지 않은 입력"
    y = to_matrix(hist, series, lo, mstr(last))
    base = midx(lo)
    tr = train_frame(y, lo, series, cls, h, last, feat, target_mode, train_lo)
    te = design(np.concatenate([y, np.full((len(series), h), np.nan)], axis=1), lo, series, cls, h,
                last - base + h, last - base + h, feat, target_mode)
    cols = [c for c in tr.columns if c not in ("c4", "target", "origin", "y_target", "ya")]
    params = {**LGB_PARAMS, **(params or {})}
    if quantile is not None:
        params.update(objective="quantile", alpha=quantile)
    mdl = lgb.LGBMRegressor(**params)
    mdl.fit(tr[cols], tr["y_target"], categorical_feature=["cls"])
    out = pd.DataFrame(dict(c4=series, target=madd(origin, 1), yhat=np.nan))
    if len(te):
        pr = te[["c4"]].assign(yhat=te["ya"].to_numpy() * np.exp(mdl.predict(te[cols])))
        out = out.drop(columns="yhat").merge(pr, on="c4", how="left")
    return out


# ---------------------------------------------------------------- 롤링
def rolling(panel: pd.DataFrame, model, origins: list[str], lag: int, series: list[str], **kw) -> pd.DataFrame:
    out = []
    for o in origins:
        p = model(available(panel, o, lag), o, lag, series, **kw)
        out.append(p.assign(origin=o, lag=lag))
    return pd.concat(out, ignore_index=True)


def scale12(panel: pd.DataFrame, series: list[str], hi: str = "2022-12") -> pd.Series:
    """MASE 척도: 2018-01 ~ hi의 계절(12) 단순 예측 평균 절대오차. 가장 이른 origin의 자료 안이라 누수 없음"""
    y = to_matrix(panel[panel["month"] <= hi], series, P["start_month"], hi)
    d = np.abs(y[:, 12:] - y[:, :-12])
    return pd.Series(np.nanmean(d, axis=1), index=series)


def score(pred: pd.DataFrame, panel: pd.DataFrame, scale: pd.Series) -> pd.DataFrame:
    """예측에 실측을 붙인다. 실측이 usable이 아닌 칸은 평가에서 뺀다"""
    act = panel[panel["usable"]][["c4", "month", "claim_qty"]].rename(columns={"month": "target", "claim_qty": "y"})
    d = pred.merge(act, on=["c4", "target"], how="inner")
    d["ae"] = (d["yhat"] - d["y"]).abs()
    d["sae"] = d["ae"] / d["c4"].map(scale)
    return d


def summary(d: pd.DataFrame) -> dict:
    v = d.dropna(subset=["yhat"])
    return dict(n=len(v), n_missing_pred=int(d["yhat"].isna().sum()), mase=v["sae"].mean(),
                wape=v["ae"].sum() / v["y"].sum())
