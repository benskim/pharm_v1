"""질문 2: 급여목록 정보 (PLAN_P2_v2 §5-6). 신호(ΔN)는 예측값에 곱하지 않고 보정층의 특징으로만 쓴다.

- 단가 UP = 금액 ÷ 수량. U0 = UP_a · (UP_a / UP_{a−12})^{(t−a)/12}, U1 = UP_a · exp(ΔK)(전가율 1).
- ΔK는 a ≥ 2022-01일 때만(목록 시작). 목록 기준월 s = M(주) 또는 M − 1(A14).
- 시점 이동 가짜: ΔK를 같은 성분의 k개월 앞 origin 값 ΔK(a − k, s − k)로 바꾼 판(k = 7, 17, a − k ≥ 2022-01인 대상월만).
- 성분 뒤섞기 순열: origin마다 ΔK ≠ 0인 성분끼리만 ΔK를 섞음, 999회, p = (1 + #{순열 지표 ≤ 실제 지표}) / 1000.
- 보정층(Q2-d): r = log y − log ŷ^G(정직한 롤링 G, h = 6), x = (ΔK, ΔN·ρ_M, ΔF, ΔP, S, SN, LN), 학습 행 = 대상월 τ ≤ a,
  기준월 a′ = τ − 6 ≥ 2022-01, x는 그 행 origin(a′, M′ = a′ + 5)의 목록. 학습 행으로 표준화, 릿지 λ = 1(절편 벌점 없음).
  학습 행 < 대상 성분 수 × 6이거나 x가 정의되지 않으면 F1q = F0q′(기록). F0q′ = ŷ^G · exp(b₀), b₀ = 학습 잔차 평균.
- 주입: 성분 목록 가격지수가 처음 내려간 목록월 m 이후 모든 처방월의 수량·금액 × (1 − δ)(단가 불변), 첫 번만.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pbd.forecast_v2.list_info import FIRST_LIST, ListData
from pbd.forecast_v2.timeaxis import madd, mnum

XCOLS = ["dK", "dNr", "dF", "dP", "S", "SN", "LN"]


class ListCache:
    def __init__(self, ld: ListData, c4: pd.Series, rho, lag: int = 5, s_offset: int = 0, exclude_in_month: bool = False):
        self.ld, self.c4, self.rho, self.lag, self.off, self.excl = ld, c4.reset_index(drop=True), rho, lag, s_offset, exclude_in_month
        self._dk, self._info = {}, {}

    def s_of(self, a: str) -> str:
        return madd(a, self.lag + self.off)

    def dK(self, a: str) -> np.ndarray:
        if a < FIRST_LIST:
            return np.full(len(self.c4), np.nan)
        if a not in self._dk:
            self._dk[a] = self.ld.dK(a, self.s_of(a)).reindex(self.c4).to_numpy(float)
        return self._dk[a]

    def x(self, a: str, t: str) -> np.ndarray:
        """성분 × XCOLS (a < 2022-01이면 NaN)"""
        if a < FIRST_LIST:
            return np.full((len(self.c4), len(XCOLS)), np.nan)
        k = (a, t)
        if k not in self._info:
            f = self.ld.info(a, self.s_of(a), t, self.excl).reindex(self.c4)
            f["dNr"] = f["dN"] * self.rho(madd(a, self.lag))
            self._info[k] = f[XCOLS].to_numpy(float)
        return self._info[k]


def unit_prices(bt, targets: list[str], h: int, lc: ListCache, shift: int = 0, dk_override=None):
    """(UP 정답, U0, U1) 성분 × 대상월. shift = 시점 이동 가짜 개월 수"""
    n = len(targets)
    UP, U0, U1 = (np.full((bt.Y.shape[0], n), np.nan) for _ in range(3))
    up = bt.A / bt.Y
    for k, t in enumerate(targets):
        j = bt.col(t)
        a = j - h
        am = bt.months[a]
        UP[:, k] = up[:, j]
        upa, upb = up[:, a], up[:, a - 12] if a - 12 >= 0 else np.full(bt.Y.shape[0], np.nan)
        U0[:, k] = upa * (upa / upb) ** ((j - a) / 12)
        dk = lc.dK(madd(am, -shift)) if dk_override is None else dk_override[:, k]
        U1[:, k] = upa * np.exp(dk)
    return UP, U0, U1


def dk_matrix(bt, targets, h, lc: ListCache, shift: int = 0) -> np.ndarray:
    return np.stack([lc.dK(madd(bt.months[bt.col(t) - h], -shift)) for t in targets], axis=1)


def permutation_p(UP, U0, dk: np.ndarray, upa: np.ndarray, w, mask, n_perm: int, seed: int, metric) -> float:
    """origin(열)마다 ΔK ≠ 0인 성분끼리 ΔK를 섞는다. metric(UP, U) → 지표(작을수록 좋음)"""
    rng = np.random.default_rng(seed)
    base = metric(UP, upa * np.exp(dk))
    cnt = 0
    for _ in range(n_perm):
        d = dk.copy()
        for k in range(dk.shape[1]):
            idx = np.nonzero(mask[:, k] & np.isfinite(dk[:, k]) & (dk[:, k] != 0))[0]
            if len(idx) > 1:
                d[idx, k] = dk[rng.permutation(idx), k]
        cnt += metric(UP, upa * np.exp(d)) <= base
    return (1 + cnt) / (1 + n_perm)


def ridge_fit(X: np.ndarray, r: np.ndarray, lam: float = 1.0):
    mu, sd = X.mean(0), X.std(0)
    sd = np.where(sd > 0, sd, np.inf)              # 분산 0 특징은 계수 0
    Z = (X - mu) / sd
    b0 = r.mean()
    beta = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (r - b0))
    return dict(mu=mu, sd=sd, b0=float(b0), beta=beta)


def ridge_predict(m: dict, X: np.ndarray) -> np.ndarray:
    return m["b0"] + ((X - m["mu"]) / m["sd"]) @ m["beta"]


def correction(bt, Gpred: np.ndarray, gcols: list[str], eval_targets: list[str], lc: ListCache, lam: float = 1.0,
               h: int = 6):
    """보정층. Gpred: 성분 × gcols(대상월) 정직한 롤링 G 예측(h = 6). 반환 (F0q′, F1q, 기록 표)"""
    n_c = bt.Y.shape[0]
    F0 = np.full((n_c, len(eval_targets)), np.nan)
    F1 = F0.copy()
    log = []
    gi = {m: k for k, m in enumerate(gcols)}
    for k, t in enumerate(eval_targets):
        j = bt.col(t)
        a = j - h
        Xs, rs = [], []
        for tau in range(mnum(madd(FIRST_LIST, h)) - mnum(bt.months[0]), a + 1):
            tm = bt.months[tau]
            if tm not in gi:
                continue
            ap = bt.months[tau - h]
            r = np.log(bt.Y[:, tau]) - np.log(Gpred[:, gi[tm]])
            X = lc.x(ap, tm)
            ok = np.isfinite(r) & np.isfinite(X).all(1)
            Xs.append(X[ok])
            rs.append(r[ok])
        X = np.concatenate(Xs) if Xs else np.zeros((0, len(XCOLS)))
        r = np.concatenate(rs) if rs else np.zeros(0)
        g = Gpred[:, gi[t]]
        xt = lc.x(bt.months[a], t)
        if len(r) == 0:
            log.append(dict(t=t, rows=0, fallback="학습 행 없음"))
            continue
        b0 = float(r.mean())
        F0[:, k] = g * np.exp(b0)
        if len(r) < n_c * 6:
            F1[:, k] = F0[:, k]
            log.append(dict(t=t, rows=len(r), fallback=f"학습 행 {len(r)} < {n_c * 6}"))
            continue
        m = ridge_fit(X, r, lam)
        okt = np.isfinite(xt).all(1)
        F1[:, k] = F0[:, k]
        F1[okt, k] = g[okt] * np.exp(ridge_predict(m, xt[okt]))
        log.append(dict(t=t, rows=len(r), fallback=None, x_undefined=int((~okt).sum()),
                        **{f"beta_{c}": float(b) for c, b in zip(XCOLS, m["beta"])}))
    return F0, F1, pd.DataFrame(log)


def inject(Y: np.ndarray, A: np.ndarray, months: list[str], first_drop: np.ndarray, delta: float):
    """first_drop: 성분마다 목록월(문자열) 또는 None. 그 달 이후 처방월 × (1 − δ)"""
    Y2, A2 = Y.copy(), A.copy()
    for i, m in enumerate(first_drop):
        if isinstance(m, str):
            j = months.index(m)
            Y2[i, j:] *= (1 - delta)
            A2[i, j:] *= (1 - delta)
    return Y2, A2


def passthrough(bt, lc: ListCache, a: str, h: int = 6, window: int = 24) -> float:
    """Q2-c as-of 전가율: origin 이전 window개월 성분×월 쌍(목록 변화 ≠ 0)의 사용통계 단가 변화 ÷ 목록 변화 중앙값.
    쌍 = 목록월 m − 1 → m (2022-02 ≤ m ≤ a), 사용통계 단가 m − 1 → m. 계산 불가면 1"""
    up = bt.A / bt.Y
    rat = []
    ja = bt.col(a)
    for j in range(max(ja - window + 1, bt.col(madd(FIRST_LIST, 1))), ja + 1):
        m, m0 = bt.months[j], bt.months[j - 1]
        dl = lc.ld.dK(m0, m).reindex(lc.c4).to_numpy(float)
        du = np.log(up[:, j] / up[:, j - 1])
        ok = np.isfinite(dl) & np.isfinite(du) & (dl != 0)
        rat.extend((du[ok] / dl[ok]).tolist())
    return float(np.median(rat)) if rat else 1.0
