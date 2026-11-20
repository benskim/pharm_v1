"""DM 검정·판정·사전 검출력 시뮬레이션 (PLAN_P2_v2 §5-8).

2026-11-20 개정: 주 검정 후보 = EWC(아래 `ewc`, 검증 후 승인). 이 위의 `dm`은 민감도(DM + HLN, Bartlett 12)로 남는다.
`verdict`·`power`(두 p가 갈리면 판단 보류)는 3단계 결과(`outputs/p2v2/power.csv`) 기록용이며, 이 규칙은 삭제되었다.
새 판정은 `verdict_single`(검정 하나)이다.

D_t = T · (d_t^A − d_t^B), D̄ = ΔWAPE. γ_k = (1/T) Σ_{t>k} (D_t − D̄)(D_{t−k} − D̄).
- 주 분산: V = (γ_0 + 2 Σ_{k=1}^{L} γ_k) / T, L = h − 1 (가중 없음). V ≤ 0이면 Bartlett 대역폭 5로 바꾸고 표시.
- HLN 계수 c = √((T + 1 − 2h + h(h−1)/T) / T). 통계량 S = c · D̄ / √V, p = 양측 t(T − 1).
- Bartlett 대역폭 L: V_B = (γ_0 + 2 Σ_{k=1}^{L} (1 − k/(L+1)) γ_k) / T. 대체·견고성 판에는 HLN을 곱하지 않는다
  (HLN은 가중 없는 절단을 전제로 한 식, 검토 K3).
- 90% 구간: D̄ ± t_{0.95}(T − 1) · √V / c (주 분산 기준).
- 판정: 두 p(주·Bartlett 12)의 유의 여부가 갈리면 '판단 보류'. 둘 다 유의하면 D̄·R1·R2 부호로 나음/나쁨/기간 의존.
  둘 다 유의하지 않으면 90% 구간 ⊂ (−δ, +δ)일 때 동등, 아니면 판단 보류.
"""
from __future__ import annotations

import numpy as np
from scipy import stats

BETTER, WORSE, PERIOD, EQUIV, HOLD = "나음", "나쁨", "기간 의존 — 판단 보류", "동등", "판단 보류"


def _gammas(D: np.ndarray, L: int) -> np.ndarray:
    """D: (..., T). 반환 (..., L + 1)"""
    T = D.shape[-1]
    x = D - D.mean(axis=-1, keepdims=True)
    return np.stack([(x[..., k:] * x[..., :T - k]).sum(axis=-1) / T for k in range(L + 1)], axis=-1)


def var_unweighted(g: np.ndarray, T: int) -> np.ndarray:
    return (g[..., 0] + 2 * g[..., 1:].sum(axis=-1)) / T


def var_bartlett(g: np.ndarray, T: int, L: int) -> np.ndarray:
    k = np.arange(1, L + 1)
    return (g[..., 0] + 2 * (g[..., 1:L + 1] * (1 - k / (L + 1))).sum(axis=-1)) / T


def hln(T: int, h: int) -> float:
    return float(np.sqrt((T + 1 - 2 * h + h * (h - 1) / T) / T))


def dm(D: np.ndarray, h: int, lag_max: int = 5, fallback: int = 5, robust: int = 12) -> dict:
    """D: (T,) 또는 (n, T). 벡터화"""
    D = np.asarray(D, dtype=float)
    T = D.shape[-1]
    g = _gammas(D, max(lag_max, fallback, robust))
    v = var_unweighted(g[..., :lag_max + 1], T)
    fb = v <= 0
    c = np.where(fb, 1.0, hln(T, h))
    v = np.where(fb, var_bartlett(g, T, fallback), v)
    vr = var_bartlett(g, T, robust)
    m = D.mean(axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        s = c * m / np.sqrt(v)
        sr = m / np.sqrt(vr)
    p = 2 * stats.t.sf(np.abs(s), T - 1)
    pr = 2 * stats.t.sf(np.abs(sr), T - 1)
    half = stats.t.ppf(0.95, T - 1) * np.sqrt(v) / c
    return dict(mean=m, p=p, p_robust=pr, lo90=m - half, hi90=m + half, fallback=fb)


def verdict(r: dict, r1_mean, r2_mean, delta: float, alpha: float = 0.05):
    """r = dm() 결과(벡터 가능). 판정 문자열 배열"""
    sig, sigr = r["p"] < alpha, r["p_robust"] < alpha
    m = r["mean"]
    out = np.full(np.shape(m), HOLD, dtype=object)
    both = sig & sigr
    better = both & (m < 0) & (np.asarray(r1_mean) < 0) & (np.asarray(r2_mean) < 0)
    worse = both & (m > 0) & (np.asarray(r1_mean) > 0) & (np.asarray(r2_mean) > 0)
    out = np.where(better, BETTER, out)
    out = np.where(worse, WORSE, out)
    out = np.where(both & ~better & ~worse, PERIOD, out)
    eq = ~sig & ~sigr & (r["lo90"] > -delta) & (r["hi90"] < delta)
    out = np.where(eq, EQUIV, out)
    return out


def toeplitz_cov(acov: np.ndarray, T: int) -> np.ndarray:
    c = np.zeros(T)
    c[:min(T, len(acov))] = acov[:T]
    i = np.arange(T)
    return c[np.abs(i[:, None] - i[None, :])]


def simulate(cov: np.ndarray, mu: float, n: int, rng: np.random.Generator) -> np.ndarray:
    """평균 mu, 공분산 cov인 정규 계열 n개 (n, T)"""
    Lc = np.linalg.cholesky(cov + 1e-15 * np.eye(len(cov)))
    return mu + rng.standard_normal((n, len(cov))) @ Lc.T


def power(cov: np.ndarray, mu: float, n: int, rng, h: int, delta: float, r1: slice, r2: slice,
          lag_max=5, fallback=5, robust=12, alpha=0.05) -> dict:
    D = simulate(cov, mu, n, rng)
    r = dm(D, h, lag_max, fallback, robust)
    v = verdict(r, D[:, r1].mean(axis=1), D[:, r2].mean(axis=1), delta, alpha)
    out = {k: float(np.mean(v == k)) for k in (BETTER, WORSE, PERIOD, EQUIV, HOLD)}
    out["p_main_sig_neg"] = float(np.mean((r["p"] < alpha) & (r["mean"] < 0)))
    out["p_robust_sig_neg"] = float(np.mean((r["p_robust"] < alpha) & (r["mean"] < 0)))
    out["fallback_rate"] = float(np.mean(r["fallback"]))
    out["mean_ci90_halfwidth"] = float(np.mean((r["hi90"] - r["lo90"]) / 2))
    return out


# ---------------------------------------------------------------- 주 검정 후보: EWC (2026-11-20 사용자 결정, 검증 후 승인)
# Lazarus·Lewis·Stock·Watson(2018) equal-weighted cosine.
#   Λ_j = √(2/T) Σ_{t=1}^{T} cos(πj(t − ½)/T)(D_t − D̄), j = 1 … ν
#   Ω̂ = (1/ν) Σ_j Λ_j²,  S = D̄ / √(Ω̂/T),  p = 양측 t(ν),  90% 구간 = D̄ ± t_{0.95}(ν)·√(Ω̂/T)
#   ν = round(0.4·T^{2/3}) (T = 36 → 4, T = 24 → 3)
# 계열에 NaN·inf가 있거나 Ω̂ ≤ 1e-14 · mean(D²)(사실상 0)이면 invalid = True, p·구간 = NaN, 판정 = '판단 보류'.
# DM(dm())과 Bartlett 12는 민감도이고 판정에 쓰지 않는다. 검정끼리 결론이 달라도 자동으로 판단 보류하지 않는다.

def nu_rule(T: int) -> int:
    return int(round(0.4 * T ** (2 / 3)))


def ewc_omega(D: np.ndarray, nu: int) -> np.ndarray:
    D = np.asarray(D, dtype=float)
    T = D.shape[-1]
    t = np.arange(1, T + 1)
    x = D - D.mean(axis=-1, keepdims=True)
    lam = np.stack([np.sqrt(2 / T) * (x * np.cos(np.pi * j * (t - 0.5) / T)).sum(axis=-1)
                    for j in range(1, nu + 1)], axis=-1)
    return (lam ** 2).mean(axis=-1)


def ewc(D: np.ndarray, nu: int | None = None) -> dict:
    """D: (T,) 또는 (n, T). 벡터화"""
    D = np.asarray(D, dtype=float)
    T = D.shape[-1]
    nu = nu_rule(T) if nu is None else nu
    finite = np.isfinite(D).all(axis=-1)
    Dz = np.where(np.isfinite(D), D, 0.0)
    om = ewc_omega(Dz, nu)
    m = Dz.mean(axis=-1)
    tol = 1e-14 * (Dz ** 2).mean(axis=-1) + 1e-300     # 상수 계열의 부동소수점 잔차(≈ 0)를 0으로 본다
    invalid = ~finite | ~(om > tol)
    se = np.sqrt(np.where(invalid, np.nan, om) / T)
    with np.errstate(divide="ignore", invalid="ignore"):
        s = m / se
    p = 2 * stats.t.sf(np.abs(s), nu)
    half = stats.t.ppf(0.95, nu) * se
    m = np.where(finite, m, np.nan)
    return dict(mean=m, p=p, lo90=m - half, hi90=m + half, se=se, nu=nu, invalid=invalid)


def verdict_single(r: dict, r1_mean, r2_mean, delta: float, alpha: float = 0.05, use_r: bool = True):
    """검정 하나로 낸 판정 (PLAN §5-8 판정 표, 2026-11-20 개정: 검정 불일치 규칙 없음).
    유의: 부호와 R1·R2 → 나음 / 나쁨 / 기간 의존. 유의하지 않음: 90% 구간 ⊂ (−δ, +δ)면 동등, 아니면 판단 보류.
    invalid(NaN·Ω̂ ≤ 0)는 판단 보류. use_r = False(Q2-d)면 R1·R2 조건 없이 부호로만 나음·나쁨"""
    p, m = np.asarray(r["p"], dtype=float), np.asarray(r["mean"], dtype=float)
    if use_r:
        r1, r2 = np.asarray(r1_mean, dtype=float), np.asarray(r2_mean, dtype=float)
    else:
        r1 = r2 = m
    ok = np.isfinite(p) & np.isfinite(m)
    sig = ok & (p < alpha)
    better = sig & (m < 0) & (r1 < 0) & (r2 < 0)
    worse = sig & (m > 0) & (r1 > 0) & (r2 > 0)
    eq = ok & ~sig & (np.asarray(r["lo90"]) > -delta) & (np.asarray(r["hi90"]) < delta)
    out = np.full(np.shape(m), HOLD, dtype=object)
    out = np.where(sig, PERIOD, out)
    out = np.where(better, BETTER, out)
    out = np.where(worse, WORSE, out)
    out = np.where(eq, EQUIV, out)
    return out


def power_main(cov: np.ndarray, mu, n: int, rng, delta: float, r1: slice, r2: slice,
               nu: int | None = None, alpha: float = 0.05, D: np.ndarray | None = None, use_r: bool = True) -> dict:
    """주 검정(EWC) 기준 판정 비율. mu는 스칼라 또는 길이 T 평균 경로. D를 주면 그 계열을 쓴다"""
    if D is None:
        D = simulate(cov, 0.0, n, rng) + np.asarray(mu)
    r = ewc(D, nu)
    v = verdict_single(r, D[:, r1].mean(axis=1), D[:, r2].mean(axis=1), delta, alpha, use_r)
    sig = r["p"] < alpha
    out = {k: float(np.mean(v == k)) for k in (BETTER, WORSE, PERIOD, EQUIV, HOLD)}
    out["sig_two_sided"] = float(np.mean(sig))
    out["sig_neg"] = float(np.mean(sig & (r["mean"] < 0)))
    out["invalid"] = float(np.mean(r["invalid"]))
    out["ci90_halfwidth_median"] = float(np.nanmedian((r["hi90"] - r["lo90"]) / 2))
    out["nu"] = r["nu"]
    return out
