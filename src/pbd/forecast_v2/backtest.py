"""P2 v2 백테스트: 예측 만들기 (PLAN_P2_v2 §5-2 ~ §5-4). 판정은 evaluate.py.

- 패널(c4, month, observed, claim_qty, claim_amt) → 행렬. 쓸 수 있는 칸 = 관측 · 값 > 0 · 재등장 첫 달 아님
  (자료 시작월 뒤에 직전 달 미관측에서 관측으로 바뀐 달). 뺀 칸은 audit.
- 모든 예측은 a = t − h 이하 사용통계만 쓴다(행렬을 a 열까지 잘라 넘긴다 — 시험 L1).
- B0 = y_a, B1, B2 (baselines.py), B3 ETS (ets.py, 대체 = B2), G (gbm.py).
- ETS는 (성분, a)마다 한 번 적합해 12단계까지 예측을 캐시한다(예측 거리 곡선과 공유).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pbd.forecast_v2 import baselines as BL
from pbd.forecast_v2.ets import ets_path
from pbd.forecast_v2.gbm import Ctx, fit_predict
from pbd.forecast_v2.timeaxis import madd, mrange

MODELS = ("B0", "B1", "B2", "B3", "G")


def matrices(panel: pd.DataFrame, target: pd.DataFrame, start: str, end: str):
    """(Y, A, CNT, months, audit). target 순서대로 행. 미관측 = NaN, 0 = NaN(audit), 재등장 첫 달 = NaN(audit)"""
    months = mrange(start, end)
    p = panel[panel.c4.isin(target.c4) & panel.month.between(start, end)]

    def piv(col):
        return (p.assign(v=np.where(p["observed"], p[col], np.nan))
                .pivot(index="c4", columns="month", values="v").reindex(index=target.c4, columns=months).to_numpy(float))

    Y, A = piv("claim_qty"), piv("claim_amt")
    C = piv("claim_cnt") if "claim_cnt" in p else np.full_like(Y, np.nan)
    O = np.isfinite(Y)
    re = np.zeros_like(O)
    re[:, 1:] = O[:, 1:] & ~O[:, :-1]
    zero = O & ~(Y > 0)
    audit = []
    for name, M in (("재등장 첫 달", re), ("관측 0", zero)):
        for i, j in zip(*np.nonzero(M)):
            audit.append(dict(c4=target.c4.iloc[i], month=months[j], reason=name))
    bad = re | zero
    for X in (Y, A, C):
        X[bad] = np.nan
    A[~(A > 0)] = np.nan
    return Y, A, C, months, pd.DataFrame(audit, columns=["c4", "month", "reason"])


class Backtest:
    def __init__(self, Y, A, months, target: pd.DataFrame, seed: int, C=None):
        self.Y, self.A, self.C, self.months, self.tg, self.seed = Y, A, C, months, target, seed
        self.cls2 = target.cls3.str[:2].astype(int).to_numpy()
        self._ets: dict = {}
        self.ets_log: list[dict] = []

    def col(self, m: str) -> int:
        return self.months.index(m)

    def ctx(self, a: int, V: np.ndarray | None = None) -> Ctx:
        """a 열까지 자른 행렬(그 뒤는 NaN). 예측에 a 뒤 자료가 들어갈 수 없다"""
        Y = (self.Y if V is None else V).copy()
        A = self.A.copy()
        Y[:, a + 1:] = np.nan
        A[:, a + 1:] = np.nan
        return Ctx(Y, A, self.months, self.cls2)

    def ets_path(self, a: int) -> np.ndarray:
        """성분 × 12단계 ETS 예측(NaN = 대체 필요). (a)마다 캐시"""
        if a not in self._ets:
            out = np.full((self.Y.shape[0], 12), np.nan)
            for i in range(self.Y.shape[0]):
                f, why = ets_path(self.Y[i, :a + 1], 12)
                if why is None:
                    out[i] = f
                else:
                    self.ets_log.append(dict(c4=self.tg.c4.iloc[i], a=self.months[a], reason=why))
            self._ets[a] = out
        return self._ets[a]

    def predict(self, t: str, h: int, models=MODELS, target: str = "qty") -> dict:
        """대상월 t, 거리 h의 모델별 예측(성분 배열)과 B3 대체 표시"""
        j = self.col(t)
        a = j - h
        V = self.Y if target == "qty" else (self.A if target == "amt" else self.C)
        Vc = V.copy()
        Vc[:, a + 1:] = np.nan
        out = {}
        if "B0" in models:
            out["B0"] = np.where(np.isfinite(Vc[:, a]) & (Vc[:, a] > 0), Vc[:, a], np.nan)
        if "B1" in models or "B3" in models:
            out["B1"] = BL.b1(Vc, j, h)
        if "B2" in models or "B3" in models:
            out["B2"] = BL.b2(Vc, j, h)
        if "B3" in models:
            assert target == "qty"
            e = self.ets_path(a)[:, h - 1]
            fb = ~np.isfinite(e)
            out["B3"] = np.where(fb, out["B2"], e)
            out["B3_fallback"] = fb
        if "G" in models:
            c = self.ctx(a)
            if target != "qty":
                c = Ctx(c.Y, c.A, c.months, c.cls2)
                if target == "cnt":
                    c.Y = self.C.copy()
                    c.Y[:, a + 1:] = np.nan
            out["G"], out["G_rows"] = fit_predict(c, a, h, self.seed, "qty" if target == "cnt" else target)
        return out


def run_q1(bt: Backtest, targets: list[str], h: int, models=MODELS, target: str = "qty") -> dict:
    """대상월 목록 × 모델 → {모델: 성분 × 대상월 배열}, B3 대체 표시, G 학습 행 수"""
    res = {m: [] for m in models}
    fb, rows = [], []
    for t in targets:
        p = bt.predict(t, h, models, target)
        for m in models:
            res[m].append(p[m])
        if "B3" in models:
            fb.append(p["B3_fallback"])
        if "G" in models:
            rows.append(p["G_rows"])
    out = {m: np.stack(v, axis=1) for m, v in res.items()}
    if fb:
        out["B3_fallback"] = np.stack(fb, axis=1)
    out["G_rows"] = rows
    return out


def long_table(bt: Backtest, targets: list[str], h: int, pred: dict, models) -> pd.DataFrame:
    rows = []
    for k, t in enumerate(targets):
        a = madd(t, -h)
        for m in models:
            v = pred[m][:, k]
            rows.append(pd.DataFrame(dict(c4=bt.tg.c4.to_numpy(), t=t, a=a, M=madd(a, 5), h=h, model=m, yhat=v)))
    return pd.concat(rows, ignore_index=True)
