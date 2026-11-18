"""P2 T3 진단 실험 (검토 1차 b 항목). 주 모델(M2·M3)은 바꾸지 않는다. 결과는 원인 후보를 가르는 데만 쓴다.

- E0 seed: M3를 다른 시드로 한 번 더 (subsample = colsample = 1이라 같아야 한다, b3·b14)
- E1 null: 신호 열을 모두 0으로 둔 M3 = M2인가 (b7 구현 오류의 귀무 시험). 2022-01 이전 pseudo-origin은 특징 행이 없어 NaN이므로
  E1은 '2022 이전 = NaN, 이후 = 0'이라는 시기 표시를 가진다. 신호 내용의 몫은 M3 − E1으로 본다
- E2 perm: 신호 특징 행을 (성분, origin) 사이에서 섞은 M3 (b3 열을 더해 생기는 잡음)
- E3 no_nprod: n_prod(급여 중 제품 수, 조밀한 수준 변수)를 뺀 M3 (b4)
- E4 train2022: M2·M3 모두 대상 달 2022-02 이후 행으로만 학습 (b5 NaN = 2022 이전 표시)
- E5 seasonal: 목표 log(y_t / y_{t−12})인 M2s·M3s (b8 기반 모델)
- E6 inject: 인하 예고가 있는 칸(cut_frac > 0)의 실측 수량을 0.8배로 바꾼 패널에서 M2·M3 (b2 검정력: M3가 되찾는가)
사용: python -m pbd.forecast.t3_experiments  (출력 outputs/p2/exp_*.csv)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pbd.common import DB_PATH, ROOT, connect
from pbd.forecast import rolling as R
from pbd.forecast import signals as S
from pbd.forecast.t2_baseline import LAGS, ORIGINS, load
from pbd.forecast.t3_models import add_holm, compare

OUT = ROOT / "outputs/p2"
INJECT = 0.8


def main() -> None:
    panel, series, cls = load()
    scale = R.scale12(panel, series)
    tb = S.load_tables(connect(DB_PATH, read_only=True))
    feat_origins = R.months(S.FIRST_SNAPSHOT, ORIGINS[-1])
    rng = np.random.default_rng(R.SEED)
    preds, scored_inj, seed_check = [], [], []
    for lag in LAGS:
        f = S.feature_table(tb, lag, "same", feat_origins, series)
        null = f.assign(**{c: 0.0 for c in S.FEATURES})
        perm = f[["c4", "origin"]].join(f[list(S.FEATURES)].iloc[rng.permutation(len(f))].reset_index(drop=True))
        nonp = f.drop(columns="n_prod")
        runs = {"M2": {}, "M3": dict(feat=f), "E1_null": dict(feat=null), "E2_perm": dict(feat=perm),
                "E3_no_nprod": dict(feat=nonp), "E4_M2_2022": dict(train_lo="2022-02"),
                "E4_M3_2022": dict(feat=f, train_lo="2022-02"), "E5_M2s": dict(target_mode="seasonal"),
                "E5_M3s": dict(feat=f, target_mode="seasonal")}
        for name, kw in runs.items():
            preds.append(R.rolling(panel, R.gbm, ORIGINS, lag, series, cls=cls, **kw).assign(model=name))
            print(f"lag {lag} {name}", flush=True)
        o = ORIGINS[len(ORIGINS) // 2]
        a = R.gbm(R.available(panel, o, lag), o, lag, series, cls=cls, feat=f)
        b = R.gbm(R.available(panel, o, lag), o, lag, series, cls=cls, feat=f, params=dict(random_state=R.SEED + 1))
        seed_check.append(dict(lag=lag, origin=o, identical=bool(a.equals(b))))
        # E6 주입: (c4, t)에서 origin t−1의 cut_frac > 0이면 실측 × INJECT
        cut = f[f["cut_frac"] > 0][["c4", "origin"]].assign(target=lambda d: d["origin"].map(lambda m: R.madd(m, 1)))
        key = set(zip(cut["c4"], cut["target"]))
        inj = panel.copy()
        hit = np.array([(c, m) in key for c, m in zip(inj["c4"], inj["month"])])
        inj.loc[hit, "claim_qty"] = inj.loc[hit, "claim_qty"] * INJECT
        for name, kw in (("E6_M2", {}), ("E6_M3", dict(feat=f))):
            p = R.rolling(inj, R.gbm, ORIGINS, lag, series, cls=cls, **kw).assign(model=name)
            s = R.score(p, inj, scale)
            s["injected"] = [(c, t) in key for c, t in zip(s["c4"], s["target"])]
            scored_inj.append(s)
            print(f"lag {lag} {name}", flush=True)
    sc = R.score(pd.concat(preds, ignore_index=True), panel, scale)
    pairs = (("M3", "M2"), ("E1_null", "M2"), ("E2_perm", "M2"), ("E3_no_nprod", "M2"), ("E4_M3_2022", "E4_M2_2022"),
             ("E4_M2_2022", "M2"), ("E5_M3s", "E5_M2s"), ("E5_M2s", "M2"),
             # E1_null은 신호 내용 없이 '2022 이전 = NaN, 이후 = 0'만 가진다 (시기 표시). 신호 내용의 몫 = M3 − E1_null
             ("M3", "E1_null"), ("E2_perm", "E1_null"), ("E3_no_nprod", "E1_null"))
    cmp_ = add_holm(pd.DataFrame([compare(sc, a, b, lag) for lag in LAGS for a, b in pairs]))
    null_same = [dict(lag=lag, null_equals_m2=bool(np.array_equal(
        sc[(sc.lag == lag) & (sc.model == "E1_null")].sort_values(["c4", "origin"])["yhat"].to_numpy(),
        sc[(sc.lag == lag) & (sc.model == "M2")].sort_values(["c4", "origin"])["yhat"].to_numpy()))) for lag in LAGS]
    si = pd.concat(scored_inj, ignore_index=True)
    inj_t = si.groupby(["lag", "model", "injected"]).agg(n=("sae", "size"), mase=("sae", "mean")).reset_index()
    m0 = R.score(pd.concat([R.rolling(panel, R.m0, ORIGINS, lag, series).assign(model="M0") for lag in LAGS]), panel, scale)
    OUT.mkdir(parents=True, exist_ok=True)
    cmp_.to_csv(OUT / "exp_compare.csv", index=False)
    sc.to_csv(OUT / "exp_pred.csv", index=False)
    pd.DataFrame(null_same).merge(pd.DataFrame(seed_check), on="lag").to_csv(OUT / "exp_null_seed.csv", index=False)
    inj_t.to_csv(OUT / "exp_inject.csv", index=False)
    e5 = pd.concat([sc, m0]).groupby(["lag", "model"])["sae"].mean().unstack()
    e5.to_csv(OUT / "exp_mase_by_model.csv")
    with pd.option_context("display.width", 250):
        print(cmp_.round(4).to_string(index=False))
        print(pd.DataFrame(null_same), pd.DataFrame(seed_check), inj_t.round(4), e5.round(3), sep="\n")


def e1_constant() -> None:
    """E1′ (검토 2차 r2-3): 2022 이전 pseudo-origin까지 포함해 모든 행의 신호 열을 0으로 둔 M3 (진짜 상수 열).
    E1′ = M2(바이트 같음)이고 E1 ≠ M2이면, E1의 차이는 '2022 이전 NaN / 이후 0' 시기 표시에서 온다"""
    panel, series, cls = load()
    scale = R.scale12(panel, series)
    all_o = R.months(R.madd(R.P["start_month"], 1), ORIGINS[-1])
    const = pd.DataFrame([(c, o) for c in series for o in all_o], columns=["c4", "origin"]).assign(**{c: 0.0 for c in S.FEATURES})
    flag = const.assign(**{c: 0.0 for c in S.FEATURES})
    flag.loc[flag["origin"] < S.FIRST_SNAPSHOT, list(S.FEATURES)] = np.nan   # E1과 같은 구조 (확인용)
    rows = []
    for lag in LAGS:
        m2 = R.rolling(panel, R.gbm, ORIGINS, lag, series, cls=cls)
        e1c = R.rolling(panel, R.gbm, ORIGINS, lag, series, cls=cls, feat=const)
        e1f = R.rolling(panel, R.gbm, ORIGINS, lag, series, cls=cls, feat=flag)
        sc = {k: R.score(v, panel, scale)["sae"].mean() for k, v in (("M2", m2), ("E1c", e1c), ("E1f", e1f))}
        rows.append(dict(lag=lag, e1_const_equals_m2=bool(np.array_equal(m2["yhat"].to_numpy(), e1c["yhat"].to_numpy())),
                         e1_flag_equals_m2=bool(np.array_equal(m2["yhat"].to_numpy(), e1f["yhat"].to_numpy())),
                         mase_m2=sc["M2"], mase_e1_const=sc["E1c"], mase_e1_flag=sc["E1f"]))
        print(rows[-1], flush=True)
    pd.DataFrame(rows).to_csv(OUT / "exp_e1_constant.csv", index=False)


if __name__ == "__main__":
    import sys
    e1_constant() if sys.argv[1:] == ["e1_constant"] else main()
