"""사전 검출력 시뮬레이션 (PLAN_P2_v2 §5-8, 사용자 결정 Q15: 태그 전 허용, 평가 기간 정답 미사용).

1. 사용통계는 2022-12 이하만 읽는다(`panel_upto`). 대상·가중치는 select.py와 같은 함수(≤ 2022-07).
2. 사전 창: h = 6에서 B1·B2가 모두 계산되는 첫 대상월(2018-01 + 23 + 6 = 2020-06) ~ 2022-12.
   평가 칸 = 정답 관측·> 0, 직전 달 관측(재등장 첫 달 제외), B1·B2 모두 예측.
3. 계열 두 가지(공분산 구조의 출처):
   - S_B2 (PLAN 문언): B2의 대상월 분해 T_pre · d_t^{B2}, 평균을 뺀 것.
   - S_DIFF (참고): 두 예측의 차이 T_pre · (d_t^{B2} − d_t^{B1}). 실제 검정 계열(모델 차이)과 같은 꼴.
4. 구조 두 가지: MA = 표본 자기공분산 지연 0 ~ 5(그 뒤 0, 양정치가 아니면 Bartlett 가중), AR1 = 같은 분산·지연 1 상관 ρ의 AR(1).
5. 각 조합에서 T = 36 정규 계열을 n_sim개 만들어 참 ΔWAPE = 0, −δ, −2δ일 때 사전등록 판정 규칙의 결과 비율을 센다.
사용: python -m pbd.forecast_v2.power  (make p2v2-power)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pbd.common import AUDIT, DB_PATH, ROOT, connect, load_yaml
from pbd.forecast_v2 import dm as DM
from pbd.forecast_v2.baselines import b1, b2
from pbd.forecast_v2.metrics import decompose
from pbd.forecast_v2.select import CFG, c4_class, select_target
from pbd.forecast_v2.timeaxis import madd, mnum, mrange, panel_upto

E, V, PW = CFG["eval"], CFG["verdict"], CFG["power"]
OUT = ROOT / "outputs/p2v2"
SEED = load_yaml("params.yaml")["seed"]


def pre_series(panel: pd.DataFrame, t: pd.DataFrame, h: int, start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """사전 창 대상월별 d_t (B1, B2)와 뺀 칸 audit"""
    months = mrange(start, end)
    allm = mrange(panel.month.min(), end)
    p = panel[panel.c4.isin(t.c4)]
    Y = (p.assign(q=np.where(p["observed"], p.claim_qty, np.nan))
         .pivot(index="c4", columns="month", values="q").reindex(index=t.c4, columns=allm).to_numpy(dtype=float))
    O = np.isfinite(Y)
    w = t.w.to_numpy(dtype=float)
    js = [allm.index(m) for m in months]
    P1 = np.stack([b1(Y, j, h) for j in js], axis=1)
    P2 = np.stack([b2(Y, j, h) for j in js], axis=1)
    y = Y[:, js]
    prev_obs = O[:, [j - 1 for j in js]]
    ok_ans = O[:, js] & (np.nan_to_num(y) > 0) & prev_obs
    mask = ok_ans & np.isfinite(P1) & np.isfinite(P2)
    drop = []
    for i, c in enumerate(t.c4):
        for k, m in enumerate(months):
            if mask[i, k]:
                continue
            if not O[i, js[k]]:
                why = "정답 미관측"
            elif not prev_obs[i, k]:
                why = "재등장 첫 달"
            elif y[i, k] <= 0:
                why = "정답 0"
            else:
                why = "B1 또는 B2 입력 없음(미관측 또는 0)"
            drop.append(dict(c4=c, month=m, reason=why))
    d1 = decompose(y, P1, w, mask)
    d2 = decompose(y, P2, w, mask)
    T = len(months)
    ser = pd.DataFrame(dict(month=months, n_cells=mask.sum(axis=0), d_b1=d1, d_b2=d2,
                            S_B2=T * d2, S_DIFF=T * (d2 - d1)))
    return ser, pd.DataFrame(drop, columns=["c4", "month", "reason"])


def acov(x: np.ndarray, L: int) -> np.ndarray:
    return DM._gammas(np.asarray(x, dtype=float), L)


def structures(x: np.ndarray, lag_max: int, T: int) -> dict:
    g = acov(x, max(12, lag_max))
    ma = g[:lag_max + 1].copy()
    cov = DM.toeplitz_cov(ma, T)
    tapered = False
    if np.linalg.eigvalsh(cov).min() <= 0:
        ma = ma * (1 - np.arange(lag_max + 1) / (lag_max + 1))
        cov = DM.toeplitz_cov(ma, T)
        tapered = True
    rho = g[1] / g[0]
    ar = g[0] * rho ** np.arange(T)
    return {"MA": (cov, tapered), "AR1": (DM.toeplitz_cov(ar, T), False)}, g


def run(ser: pd.DataFrame, n_sim: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    h, T = E["h"], len(mrange(E["start"], E["end"]))
    r1 = slice(mnum(E["r1"][0]) - mnum(E["start"]), mnum(E["r1"][1]) - mnum(E["start"]) + 1)
    r2 = slice(mnum(E["r2"][0]) - mnum(E["start"]), mnum(E["r2"][1]) - mnum(E["start"]) + 1)
    delta = V["delta_q1"]
    rows, acf, combo = [], [], 0
    for src in ("S_B2", "S_DIFF"):
        st, g = structures(ser[src].to_numpy(), V["dm_lag_max"], T)
        acf.append(dict(source=src, sd=float(np.sqrt(g[0])), **{f"rho{k}": float(g[k] / g[0]) for k in range(1, 13)}))
        for name, (cov, tapered) in st.items():
            for k in PW["effects_in_delta"] + PW.get("effects_ref_in_delta", []):
                combo += 1
                rng = np.random.default_rng([seed, combo])     # 조합마다 고정 순번 (같은 입력 → 같은 출력)
                r = DM.power(cov, -k * delta, n_sim, rng, h, delta, r1, r2, V["dm_lag_max"],
                             V["dm_fallback_bartlett"], V["dm_robust_bartlett"], V["alpha"])
                rows.append(dict(source=src, structure=name, tapered=tapered, k_delta=k,
                                 role="사전등록" if k in PW["effects_in_delta"] else "참고", true_dwape=-k * delta,
                                 sd_D=float(np.sqrt(cov[0, 0])), **r))
    return pd.DataFrame(rows), pd.DataFrame(acf)


def main() -> None:
    con = connect(DB_PATH, read_only=True)
    panel = panel_upto(con, PW["data_max_month"])
    S = CFG["selection"]
    t, _, _ = select_target(panel[panel.month <= S["a0"]], c4_class(con))
    tf = pd.read_csv(OUT / "target.csv", dtype={"c4": str})
    assert tf.c4.tolist() == t.c4.tolist() and np.allclose(tf.w, t.w), "target.csv와 다시 고른 대상이 다르다"
    h = E["h"]
    start = madd(panel.month.min(), 23 + h)
    ser, drop = pre_series(panel, t, h, start, PW["data_max_month"])
    assert ser.month.max() < E["start"]
    ser.to_csv(OUT / "power_pre_series.csv", index=False, float_format="%.6g")
    drop.to_csv(AUDIT / "dropped_p2v2_power_cells.csv", index=False)
    res, acf = run(ser, PW["n_sim"], SEED)
    res.to_csv(OUT / "power.csv", index=False, float_format="%.4g")
    acf.to_csv(OUT / "power_acf.csv", index=False, float_format="%.4g")
    print(f"사전 창 {ser.month.min()} ~ {ser.month.max()} ({len(ser)}개월), 뺀 칸 {len(drop)}")
    print(acf.round(3).to_string(index=False))
    print(res[["source", "structure", "role", "true_dwape", "sd_D", DM.BETTER, DM.EQUIV, DM.HOLD,
               "p_main_sig_neg", "mean_ci90_halfwidth"]].round(4).to_string(index=False))


def run_ewc(ser: pd.DataFrame, n_sim: int, seed: int) -> pd.DataFrame:
    """주 검정(EWC) 기준 사전 검출력 (2026-11-20). S_B2·S_DIFF 모두 참고용 사전 검출력. 기존 DM 기준 power.csv는 보존"""
    T = len(mrange(E["start"], E["end"]))
    nu = DM.nu_rule(T)
    r1 = slice(mnum(E["r1"][0]) - mnum(E["start"]), mnum(E["r1"][1]) - mnum(E["start"]) + 1)
    r2 = slice(mnum(E["r2"][0]) - mnum(E["start"]), mnum(E["r2"][1]) - mnum(E["start"]) + 1)
    delta = V["delta_q1"]
    rows, combo = [], 100       # 순번을 DM 판(1 ~)과 겹치지 않게
    for src in ("S_B2", "S_DIFF"):
        st, _ = structures(ser[src].to_numpy(), V["dm_lag_max"], T)
        for name, (cov, tapered) in st.items():
            for k in PW["effects_in_delta"] + PW.get("effects_ref_in_delta", []):
                combo += 1
                rng = np.random.default_rng([seed, combo])
                r = DM.power_main(cov, -k * delta, n_sim, rng, delta, r1, r2, nu, V["alpha"])
                rows.append(dict(source=src, structure=name, k_delta=k,
                                 role="참고용 사전 검출력" if k in PW["effects_in_delta"] else "참고(효과 크기 가늠)",
                                 true_dwape=-k * delta, sd_D=float(np.sqrt(cov[0, 0])), **r))
    return pd.DataFrame(rows)


def main_ewc() -> None:
    ser = pd.read_csv(OUT / "power_pre_series.csv")
    res = run_ewc(ser, PW["n_sim"], SEED)
    res.to_csv(OUT / "power_ewc.csv", index=False, float_format="%.4g")
    print(res[["source", "structure", "role", "true_dwape", "nu", DM.BETTER, DM.EQUIV, DM.HOLD, "sig_neg",
               "ci90_halfwidth_median"]].round(4).to_string(index=False))


if __name__ == "__main__":
    import sys
    main_ewc() if "--ewc" in sys.argv else main()
