"""EWC 주 검정 후보 검증 V2 ~ V5 (DECISIONS 2026-11-20 승인 기준). V1·V6은 tests/test_p2v2_ewc.py.

입력: outputs/p2v2/power_pre_series.csv (3단계 산출, 사용통계 2022-12 이하). 평가 기간 정답을 쓰지 않는다.
- V2 크기: S_B2·MA, S_B2·AR1, S_DIFF·MA, S_DIFF·AR1, iid(S_DIFF 표준편차) × 20,000회, T = 36, ν = 4, 참 ΔWAPE = 0.
       기준: 한쪽 거짓 양성(유의하고 D̄ < 0) ≤ 3.5%, 양측 기각률 3 ~ 7%. (추가: T = 24, ν = 3 같은 기준, Q2-d용)
- V3 스트레스: (a) AR(1) ρ = 0.6, 분산 = S_DIFF 분산 (b) S_DIFF·MA 공분산 + 평균 경로(처음 12개월 +s, 나머지 24개월 −s/2,
       s = 계열 표준편차). 기준: 한쪽 거짓 양성 ≤ 5% (넘으면 한계로 기록, 승인은 사용자).
- V4 ν = 3, 5, 6: 크기·검출력 보고만.  V5 ν = 4: −δ, −2δ '나음' 비율, 참 0에서 '동등' 비율 보고만.
사용: python -I scripts/p2v2_ewc_check.py  (make p2v2-ewc-check)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pbd.common import load_yaml  # noqa: E402
from pbd.forecast_v2 import dm as DM  # noqa: E402
from pbd.forecast_v2.power import structures  # noqa: E402

OUT = ROOT / "outputs/p2v2"
SEED = load_yaml("params.yaml")["seed"]
V = load_yaml("p2v2.yaml")["verdict"]
DELTA, ALPHA = V["delta_q1"], V["alpha"]
N = 20000
CRIT_V2 = dict(sig_neg_max=0.035, two_sided_lo=0.03, two_sided_hi=0.07)
CRIT_V3 = dict(sig_neg_max=0.05)


def windows(T: int):
    """R1·R2 위치: T = 36은 평가 기간(R1 = 13 ~ 36번째 달, R2 = 1 ~ 22번째). T = 24(Q2-d 2024-01 ~ 2025-12)는
    R1 = 전체, R2 = 1 ~ 10번째(2024-01 ~ 2024-10)"""
    return (slice(12, 36), slice(0, 22)) if T == 36 else (slice(0, 24), slice(0, 10))


def build_structs(ser: pd.DataFrame, T: int) -> dict:
    out = {}
    for src in ("S_B2", "S_DIFF"):
        st, _ = structures(ser[src].to_numpy(), 5, T)
        for name, (cov, _) in st.items():
            out[(src, name)] = cov
    sd = float(np.sqrt(out[("S_DIFF", "MA")][0, 0]))
    out[("iid", "iid")] = np.eye(T) * sd ** 2
    return out


def main() -> None:
    ser = pd.read_csv(OUT / "power_pre_series.csv")
    rows, combo = [], 0

    def run(check, src, name, T, nu, k, cov, mu=0.0, note=""):
        nonlocal combo
        combo += 1
        rng = np.random.default_rng([SEED, combo])
        r1, r2 = windows(T)
        mu_v = mu if np.ndim(mu) else -k * DELTA
        r = DM.power_main(cov, mu_v, N, rng, DELTA, r1, r2, nu, ALPHA)
        rows.append(dict(check=check, source=src, structure=name, T=T, nu=r.pop("nu"), k_delta=k,
                         true_dwape=-k * DELTA, note=note, **r))

    s36, s24 = build_structs(ser, 36), build_structs(ser, 24)
    for (src, name), cov in s36.items():                       # V2
        run("V2", src, name, 36, 4, 0, cov)
    for (src, name), cov in s24.items():                       # V2 추가 (T = 24)
        run("V2-T24", src, name, 24, 3, 0, cov, note="Q2-d용 추가")
    sd = float(np.sqrt(s36[("S_DIFF", "MA")][0, 0]))           # V3
    run("V3", "stress", "AR1 rho=0.6", 36, 4, 0, DM.toeplitz_cov(sd ** 2 * 0.6 ** np.arange(36), 36))
    path = np.r_[np.full(12, sd), np.full(24, -sd / 2)]
    assert abs(path.mean()) < 1e-15
    run("V3", "stress", "S_DIFF·MA + 2023 평균 이동", 36, 4, 0, s36[("S_DIFF", "MA")], mu=path)
    for nu in (3, 5, 6):                                       # V4
        for (src, name), cov in s36.items():
            for k in (0, 1, 2):
                run("V4", src, name, 36, nu, k, cov)
    for (src, name), cov in s36.items():                       # V5
        for k in (1, 2):
            run("V5", src, name, 36, 4, k, cov)

    res = pd.DataFrame(rows)
    v2 = res.check.isin(["V2", "V2-T24"])
    res.loc[v2, "pass"] = ((res.sig_neg <= CRIT_V2["sig_neg_max"]) & (res.sig_two_sided >= CRIT_V2["two_sided_lo"])
                           & (res.sig_two_sided <= CRIT_V2["two_sided_hi"]))[v2]
    v3 = res.check == "V3"
    res.loc[v3, "pass"] = (res.sig_neg <= CRIT_V3["sig_neg_max"])[v3]
    res["mc_se_sig_neg"] = np.sqrt(res.sig_neg * (1 - res.sig_neg) / N)
    res.to_csv(OUT / "ewc_check.csv", index=False, float_format="%.5g")
    pd.set_option("display.width", 220)
    cols = ["check", "source", "structure", "T", "nu", "k_delta", "sig_neg", "sig_two_sided", DM.BETTER, DM.EQUIV,
            DM.HOLD, "invalid", "ci90_halfwidth_median", "pass"]
    print(res[cols].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
