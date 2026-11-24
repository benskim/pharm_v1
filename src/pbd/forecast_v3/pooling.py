"""P2 v3 부분 풀링 비교: 딱 잘라 나눈 묶음(ATC 2단계·1단계·혼합 H1~H4)과 부분 풀링(ATC 2단계 → ATC 1단계 → 전체로 줄임)을
'한 성분 빼고 맞히기(LOO)'로 비교한다. 사후 탐색, 판정 없음. 설계 docs/PLAN_P2_v3.md §11.
python -m pbd.forecast_v3.pooling → outputs/p2v3/pooling/ (make p2v3-hybrid 산출이 있어야 한다)

- 딱 잘라 나눔: 성분 i의 예측 = i를 뺀 같은 묶음 성분의 가중 평균(묶음에 i만 있으면 i를 뺀 전체 평균).
- 부분 풀링(경험적 베이즈, 두 단계): ATC 1단계 평균을 전체 평균 쪽으로, ATC 2단계 평균을 그 ATC 1단계 추정 쪽으로 줄인다.
  줄이는 정도 = 군간 분산 ÷ (군간 분산 + 군 안 분산 ÷ 유효 성분 수). 분산은 적률법, 음수면 0. i를 뺀 자료로 매번 다시 추정.
- 지표: LOO R² = 1 − Σ w (e − ê)² ÷ Σ w (e − ê_전체평균)². 0 = 군 정보가 전체 평균보다 낫지 않음.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from pbd.common import ROOT

OUT = ROOT / "outputs/p2v3/pooling"
SRC = ROOT / "outputs/p2v3"
METRICS = ("lr_G_B3", "log_wape_G", "lr_G_B1")


def _gstats(e, w, lab, K):
    W = np.bincount(lab, weights=w, minlength=K)
    S = np.bincount(lab, weights=w * e, minlength=K)
    W2 = np.bincount(lab, weights=w * w, minlength=K)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = S / W
        neff = W ** 2 / W2
    return W, m, neff


def eb_fit(e, w, l2, top2, method: str = "mom"):
    """두 단계 경험적 베이즈. l2 = 성분의 ATC 2단계 번호, top2 = ATC 2단계 번호 → ATC 1단계 번호(고정 대응표, 빈 군 포함).
    method: 'mom' = ATC 2단계 군간 분산을 군 평균 편차² − 잡음의 단순 평균으로(적률법),
            'dl' = 정밀도(n_eff ÷ σ²) 가중 DerSimonian–Laird(작은 군의 잡음이 덜 섞임). ATC 1단계 쪽은 둘 다 적률법.
    반환: (ATC 2단계별 줄인 추정, ATC 1단계별 줄인 추정, 전체 평균, 진단 dict). 자료가 없는 군은 상위 추정을 쓴다."""
    K2, K1 = len(top2), top2.max() + 1
    l1 = top2[l2]
    top = top2
    mu = (w * e).sum() / w.sum()
    W2_, m2, n2 = _gstats(e, w, l2, K2)
    W1_, m1, n1 = _gstats(e, w, l1, K1)
    has2, has1 = W2_ > 0, W1_ > 0
    # 군 안 분산(ATC 2단계 안, 가중 합동)
    N, J = len(e), has2.sum()
    sig2 = (w * (e - m2[l2]) ** 2).sum() / w.sum() * N / max(N - J, 1)
    # ATC 2단계 평균이 ATC 1단계 평균 둘레에 퍼진 정도 − 표본 잡음
    if method == "dl":
        p = np.where(has2, n2 / sig2, 0.0)
        P = np.bincount(top, weights=p, minlength=K1)
        Pm = np.bincount(top, weights=p * np.nan_to_num(m2), minlength=K1)
        with np.errstate(invalid="ignore", divide="ignore"):
            Mhat = np.where(P > 0, Pm / P, 0.0)
        Q = (p * (np.nan_to_num(m2) - Mhat[top]) ** 2).sum()
        df = has2.sum() - (P > 0).sum()
        P2 = np.bincount(top, weights=p * p, minlength=K1)
        c = (P[P > 0] - P2[P > 0] / P[P > 0]).sum()
        tau2 = max(float((Q - df) / c), 0.0) if c > 0 else 0.0
    else:
        d2 = (m2[has2] - m1[top[has2]]) ** 2 - sig2 / n2[has2]
        tau2 = max(float(np.mean(d2)), 0.0) if d2.size else 0.0
    J1 = np.bincount(top[has2], minlength=K1)
    d1 = (m1[has1] - mu) ** 2 - (tau2 / np.maximum(J1[has1], 1) + sig2 / n1[has1])
    tau1 = max(float(np.mean(d1)), 0.0) if d1.size else 0.0
    B1 = np.where(has1, tau1 / (tau1 + tau2 / np.maximum(J1, 1) + sig2 / np.where(has1, n1, 1)), 0.0) if tau1 > 0 else np.zeros(K1)
    m1s = np.where(has1, mu + B1 * (np.nan_to_num(m1) - mu), mu)
    b2 = np.where(has2, tau2 / (tau2 + sig2 / np.where(has2, n2, 1)), 0.0) if tau2 > 0 else np.zeros(K2)
    m2s = np.where(has2, m1s[top] + b2 * (np.nan_to_num(m2) - m1s[top]), m1s[top])
    return m2s, m1s, mu, {"sigma2": sig2, "tau2_atc2": tau2, "tau2_atc1": tau1, "b_atc2": b2, "B_atc1": B1, "n_eff_atc2": n2}


def loo_hard(e, w, lab):
    K = lab.max() + 1
    W = np.bincount(lab, weights=w, minlength=K)
    S = np.bincount(lab, weights=w * e, minlength=K)
    Wi, Si = W[lab] - w, S[lab] - w * e
    mu_i = (S.sum() - w * e) / (W.sum() - w)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(Wi > 1e-15, Si / Wi, mu_i)


def loo_mean(e, w):
    return ((w * e).sum() - w * e) / (w.sum() - w)


def loo_pool(e, w, l2, top2, method: str = "mom"):
    """성분마다 그 성분을 뺀 자료로 eb_fit을 다시 하고, 그 성분 ATC 2단계 군의 줄인 추정을 예측으로"""
    out = np.empty(len(e))
    for i in range(len(e)):
        k = np.arange(len(e)) != i
        m2s = eb_fit(e[k], w[k], l2[k], top2, method)[0]
        out[i] = m2s[l2[i]]
    return out


def loo_r2(e, w, pred):
    base = loo_mean(e, w)
    return 1 - (w * (e - pred) ** 2).sum() / (w * (e - base) ** 2).sum()


def codes(s: pd.Series) -> np.ndarray:
    return pd.factorize(s, sort=True)[0]


def main() -> int:
    import yaml

    seed = yaml.safe_load((ROOT / "config/params.yaml").read_text(encoding="utf-8"))["seed"]
    ing = pd.read_csv(SRC / "ingredient_metrics.csv", dtype={"c4": str, "cls3": str, "cls2": str, "cls2_raw": str}).set_index("c4")
    ing["lr_G_B3"] = np.log(ing.wape_G / ing.wape_B3)
    ing["lr_G_B1"] = np.log(ing.wape_G / ing.wape_B1)
    ing["atc1"] = ing.atc2_raw.str[0]
    mapping = pd.read_csv(SRC / "hybrid/hybrid_mapping.csv").set_index("atc2_raw")
    hard = {"atc2_raw": ing.atc2_raw, "atc2": ing.atc2, "atc1": ing.atc1, "cls2": ing.cls2}
    hard.update({c: ing.atc2_raw.map(mapping[c]) for c in mapping.columns if c.startswith("H")})
    l2 = codes(ing.atc2_raw)
    g2 = pd.Index(sorted(ing.atc2_raw.unique()))
    top2 = pd.factorize(pd.Series(g2.str[0]), sort=True)[0]
    rows, diag, preds = [], {}, {}
    for m in METRICS:
        for wt in ("w", "1"):
            e = ing[m].to_numpy()
            w = ing.w.to_numpy() if wt == "w" else np.ones(len(e))
            for g, lab in hard.items():
                pred = loo_hard(e, w, codes(lab))
                preds[(m, wt, g)] = pred
                rows.append({"method": g, "kind": "딱 잘라 나눔", "n_groups": int(lab.nunique()), "metric": m, "weighting": wt,
                             "loo_r2": loo_r2(e, w, pred)})
            for pm in ("mom", "dl"):
                pred = loo_pool(e, w, l2, top2, pm)
                preds[(m, wt, f"pool_{pm}")] = pred
                rows.append({"method": f"pool_{pm}", "kind": "부분 풀링", "n_groups": int(ing.atc2_raw.nunique()), "metric": m,
                             "weighting": wt, "loo_r2": loo_r2(e, w, pred)})
                diag[(m, wt, pm)] = eb_fit(e, w, l2, top2, pm)[3]
    res = pd.DataFrame(rows)
    # 전체 자료로 맞춘 줄임 계수 (ATC 2단계 군마다)
    groups2 = pd.Index(sorted(ing.atc2_raw.unique()))
    nn = ing.atc2_raw.value_counts().reindex(groups2).values
    shr = pd.concat([pd.DataFrame({"atc2_raw": groups2, "metric": m, "weighting": wt, "pool": pm, "n_ingr": nn,
                                   "n_eff": dg["n_eff_atc2"], "shrink_b": dg["b_atc2"]}) for (m, wt, pm), dg in diag.items()])
    vc = pd.DataFrame([{"metric": m, "weighting": wt, "pool": pm, "sigma2_within": dg["sigma2"], "tau2_atc2_within_atc1": dg["tau2_atc2"],
                        "tau2_atc1": dg["tau2_atc1"]} for (m, wt, pm), dg in diag.items()])
    # 차이의 90% 구간: 성분 재표집 2,000회(예측은 고정), 기준 = 원래 ATC 2단계와 풀링(DL)
    rng = np.random.default_rng(seed)
    B = rng.integers(0, len(ing), size=(2000, len(ing)))
    ci = []
    for m in METRICS:
        for wt in ("w", "1"):
            e = ing[m].to_numpy()
            w = ing.w.to_numpy() if wt == "w" else np.ones(len(e))
            base = w * (e - loo_mean(e, w)) ** 2
            r2b = lambda p: 1 - (w * (e - p) ** 2)[B].sum(1) / base[B].sum(1)
            meths = [k[2] for k in preds if k[0] == m and k[1] == wt]
            boot = {g: r2b(preds[(m, wt, g)]) for g in meths}
            for ref in ("atc2_raw", "pool_dl"):
                for g in meths:
                    if g == ref:
                        continue
                    d = boot[g] - boot[ref]
                    ci.append({"metric": m, "weighting": wt, "method": g, "vs": ref,
                               "diff_r2": loo_r2(e, w, preds[(m, wt, g)]) - loo_r2(e, w, preds[(m, wt, ref)]),
                               "lo90": np.quantile(d, 0.05), "hi90": np.quantile(d, 0.95)})
    ci = pd.DataFrame(ci)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / "loo_comparison.csv", index=False, encoding="utf-8-sig", float_format="%.4g")
    ci.to_csv(OUT / "loo_diff_ci.csv", index=False, encoding="utf-8-sig", float_format="%.4g")
    shr.to_csv(OUT / "pooling_shrinkage.csv", index=False, encoding="utf-8-sig", float_format="%.4g")
    vc.to_csv(OUT / "pooling_variance_components.csv", index=False, encoding="utf-8-sig", float_format="%.4g")
    with pd.ExcelWriter(OUT / "p2v3_pooling.xlsx") as xw:
        res.to_excel(xw, sheet_name="loo_comparison", index=False)
        ci.to_excel(xw, sheet_name="loo_diff_ci", index=False)
        vc.to_excel(xw, sheet_name="variance_components", index=False)
        shr.to_excel(xw, sheet_name="pooling_shrinkage", index=False)
    print(res[res.weighting == "w"].pivot(index="method", columns="metric", values="loo_r2").round(3).to_string())
    print(res[res.weighting == "1"].pivot(index="method", columns="metric", values="loo_r2").round(3).to_string())
    print(vc.round(4).to_string(index=False))
    write_report()
    return 0


# ---------------------------------------------------------------- 보고서 (숫자는 산출 파일에서 읽음)
REPORT = ROOT / "reports/P2v3_pooling_result.md"
NAME = {"pool_dl": "부분 풀링 (DL)", "pool_mom": "부분 풀링 (적률법)", "atc2_raw": "ATC 2단계(원래 69)", "atc2": "ATC 2단계(합친 54)",
        "atc1": "ATC 1단계", "cls2": "약효분류 2자리", "H1_pattern_q25": "H1 패턴 τ25%", "H1_pattern_q50": "H1 패턴 τ50%",
        "H1_pattern_q75": "H1 패턴 τ75%", "H2_err2022_q25": "H2 2022 오차 τ25%", "H2_err2022_q50": "H2 2022 오차 τ50%",
        "H2_err2022_q75": "H2 2022 오차 τ75%", "H3_size5": "H3 크기 5", "H3_size10": "H3 크기 10",
        "H4_size5_pattern": "H4 크기 5 + 패턴", "H4_size10_pattern": "H4 크기 10 + 패턴"}
MLAB = {"lr_G_B3": "모델 우열 log(G/B3)", "lr_G_B1": "log(G/B1)", "log_wape_G": "G 오차(log)"}


def write_report() -> None:
    r = pd.read_csv(OUT / "loo_comparison.csv", dtype={"weighting": str})
    ci = pd.read_csv(OUT / "loo_diff_ci.csv", dtype={"weighting": str})
    vc = pd.read_csv(OUT / "pooling_variance_components.csv", dtype={"weighting": str})
    sh = pd.read_csv(OUT / "pooling_shrinkage.csv", dtype={"weighting": str})
    hs = json.loads((SRC / "hybrid/summary.json").read_text(encoding="utf-8"))
    v = lambda m, w, g: r[(r.metric == m) & (r.weighting == w) & (r.method == g)].loo_r2.iloc[0]
    c = lambda m, w, g, ref: ci[(ci.metric == m) & (ci.weighting == w) & (ci.method == g) & (ci.vs == ref)].iloc[0]
    f3 = lambda x: f"{x:.3f}"
    iv = lambda q: f"{q.diff_r2:+.3f} ({q.lo90:+.3f} ~ {q.hi90:+.3f})"
    L = []
    a = L.append
    a("# P2 v3 부분 풀링: 딱 잘라 나눈 묶음과 비교 (사후 탐색)\n")
    a("> 성격: P2 v3 혼합 묶음 비교(`reports/P2v3_hybrid_result.md`)에 붙이는 사후 탐색. 판정 없음. 군별 예측 모델 실험의 군 단위를 고르는 사전 자료이며 "
      "그 실험은 하지 않았다. 평가 기간(2023-01 ~ 2025-12) 오차의 군 구조를 비교한 것이고 예측 개선을 보인 것이 아니다. 설계 `docs/PLAN_P2_v3.md` §11, 실행 `make p2v3-pooling`.\n")
    a("## 1. 완료 기준\n")
    a("| 기준 | 결과 |\n|---|---|")
    a("| 부분 풀링 구현 | 통과: 두 단계 경험적 베이즈(ATC 2단계 → ATC 1단계 → 전체), 군간 분산 추정 두 가지(적률법, DerSimonian–Laird) |")
    a(f"| 같은 기준으로 비교 | 통과: 한 성분 빼고 맞히기(LOO) R², 딱 잘라 나눈 묶음 {len([k for k in NAME if not k.startswith('pool')])}개와 풀링 2개, 지표 3개 × 가중·비가중 |")
    a("| 차이의 불확실성 | 성분 재표집 2,000회(예측 고정, 시드 고정) 90% 구간, 기준 = 원래 ATC 2단계와 풀링(DL) |")
    a("| 시험 | `tests/test_p2v3_group.py` 20개 (LOO 손 계산, 신호 없음 → 전체 평균, 강한 신호 → 줄임 없음, 빈 군 → 상위 추정, DL·적률법 손 계산) |\n")

    a("## 2. 답\n")
    a("비교 방법을 바꾼 이유: 부분 풀링은 군을 나누지 않고 군 효과를 줄여 쓰므로 η²·유지율을 계산할 수 없다. 그래서 모든 방식을 '성분 하나를 빼고 나머지로 그 성분의 값을 "
      "맞히는 정확도'(LOO R²)로 비교했다. 작은 군·잡음이 많은 군은 이 기준에서 저절로 손해를 보므로 군 수가 다른 방식끼리 공정하다. 0 = 전체 평균보다 낫지 않음.\n")
    m = "lr_G_B3"
    for w, lab in (("w", "가중"), ("1", "비가중")):
        top = r[(r.metric == m) & (r.weighting == w)].sort_values("loo_r2", ascending=False)
        a(f"- **{lab}, 모델 우열**: 1위 {NAME[top.method.iloc[0]]} {f3(top.loo_r2.iloc[0])}, 2위 {NAME[top.method.iloc[1]]} {f3(top.loo_r2.iloc[1])}, "
          f"3위 {NAME[top.method.iloc[2]]} {f3(top.loo_r2.iloc[2])}. 부분 풀링(DL) − 원래 ATC 2단계 = {iv(c(m, w, 'pool_dl', 'atc2_raw'))}. "
          f"H4 크기 10 − 풀링(DL) = {iv(c(m, w, 'H4_size10_pattern', 'pool_dl'))}, H1 패턴 τ50% − 풀링(DL) = {iv(c(m, w, 'H1_pattern_q50', 'pool_dl'))}.")
    wins = sum(r[(r.metric == mm) & (r.weighting == ww)].sort_values("loo_r2", ascending=False).method.iloc[0] == "pool_dl"
               for mm in METRICS for ww in ("w", "1"))
    a(f"- 부분 풀링(DL)은 지표 3개 × 가중·비가중 6개 기준 중 {wins}개에서 1위이고, 나머지에서도 상위권이다(§3). 원래 ATC 2단계보다는 모델 우열에서 "
      "가중·비가중 모두 90% 구간이 0 위에 있다. H3·H4와는 구간이 0을 포함해 차이를 말할 수 없다.")
    a(f"- 적률법 풀링은 비가중 모델 우열에서 ATC 2단계 군간 분산을 0으로 추정해(성분 1개 군의 큰 잡음이 평균에 섞임) ATC 1단계와 같아졌다"
      f"(LOO R² {f3(v(m, '1', 'pool_mom'))}, ATC 1단계 {f3(v(m, '1', 'atc1'))}). 정밀도 가중(DL)이 이 문제를 피한다. 풀링을 쓸 때는 DL 쪽을 권한다.")
    a(f"- 앞 보고서와 다른 점: η² 유지율로는 H1(패턴 유사)이 약했지만, LOO 가중 기준에서는 H1 τ50%({f3(v(m, 'w', 'H1_pattern_q50'))})가 상위권이다. "
      f"비가중 기준에서는 여전히 약하다({f3(v(m, '1', 'H1_pattern_q50'))}). 가중 기준은 큰 군(순환계 등)이 정하고, H1이 합친 큰 군들은 서로 모델 우열이 비슷했기 때문으로 보인다(해석).")
    a("- 실험에 주는 뜻(해석, 시험하지 않음): 부분 풀링은 문턱(τ, 크기 m)을 고르지 않고도 H4만큼 또는 그 이상으로 군 구조를 잡는다. 군별 모델 실험에서 "
      "'군마다 따로 학습'보다 '전역 모델 + ATC 1단계·2단계 효과를 자료 양만큼만 반영'하는 쪽이 같은 원리다. 같은 평가 기간으로 16개 방식을 비교했으므로 "
      "고른 방식은 실험에서 다른 기간으로 확인해야 한다.\n")

    a("## 3. LOO R² 전체\n")
    a("| 방식 | 종류 | 군 수 | " + " | ".join(f"{MLAB[mm]} {'가중' if ww == 'w' else '비가중'}" for mm in METRICS for ww in ("w", "1")) + " |")
    a("|---|---|---|" + "---|" * 6)
    order = r[(r.metric == m) & (r.weighting == "w")].sort_values("loo_r2", ascending=False).method
    for g in order:
        x = r[r.method == g].iloc[0]
        a(f"| {NAME[g]} | {x.kind} | {x.n_groups} | " + " | ".join(f3(v(mm, ww, g)) for mm in METRICS for ww in ("w", "1")) + " |")
    a("\n정렬: 모델 우열 가중 기준. 부분 풀링의 '군 수'는 쓰는 ATC 2단계 군 수(줄여 씀).\n")
    a("## 4. 원래 ATC 2단계 대비 차이 (모델 우열, 90% 구간)\n")
    a("| 방식 | 가중 | 비가중 |\n|---|---|---|")
    for g in [k for k in NAME if k != "atc2_raw"]:
        a(f"| {NAME[g]} | {iv(c(m, 'w', g, 'atc2_raw'))} | {iv(c(m, '1', g, 'atc2_raw'))} |")
    a("\n## 5. 부분 풀링이 하는 일\n")
    a("| 지표 | 가중 | 추정 | 군 안 분산 σ² | ATC 2단계 군간 τ²(ATC 1단계 안) | ATC 1단계 군간 τ² |\n|---|---|---|---|---|---|")
    for _, x in vc.iterrows():
        a(f"| {MLAB[x.metric]} | {'가중' if x.weighting == 'w' else '비가중'} | {x.pool} | {x.sigma2_within:.4f} | {x.tau2_atc2_within_atc1:.4f} | {x.tau2_atc1:.4f} |")
    s1 = sh[(sh.metric == m) & (sh.weighting == "w") & (sh["pool"] == "dl")]
    bins = pd.cut(s1.n_ingr, [0, 2, 4, 9, 1000], labels=["1~2개", "3~4개", "5~9개", "10개 이상"])
    tb = s1.groupby(bins, observed=True).agg(n=("shrink_b", "size"), b=("shrink_b", "median"))
    a("\n줄임 계수 b = ATC 2단계 군 평균을 얼마나 믿는가(1 = 그대로, 0 = ATC 1단계 추정으로). 모델 우열, 가중, DL. 성분 수별 중앙값: " +
      ", ".join(f"{k} {int(x.n)}개 군 b {x.b:.2f}" for k, x in tb.iterrows()) + ". 군별 값은 `pooling_shrinkage.csv`.\n")
    a("## 6. 알아 둘 것\n")
    a("- 경험적 베이즈 식: ATC 1단계 추정 = 전체 평균 + B(ATC 1단계 평균 − 전체 평균), ATC 2단계 추정 = ATC 1단계 추정 + b(ATC 2단계 평균 − ATC 1단계 추정), "
      "b = τ² ÷ (τ² + σ² ÷ 유효 성분 수). 유효 성분 수 = (Σw)² ÷ Σw². ATC 1단계 쪽 τ²는 두 방식 모두 적률법. LOO에서는 뺀 성분 없이 τ²·σ²부터 다시 추정했다.")
    a("- 딱 잘라 나눈 묶음의 LOO 예측은 같은 묶음의 다른 성분 평균, 혼자인 성분은 전체 평균이다. 그래서 성분 1~2개 군이 많은 원래 ATC 2단계는 이 기준에서 불리하다.")
    a("- 구간은 예측을 고정하고 성분만 재표집했다(LOO를 다시 하지 않음). 실제 불확실성보다 좁을 수 있다.")
    rc = hs["rows_check"]
    a(f"- 학습 행 수 기준 제안은 철회했다: 학습 기간 {rc['months']}개월을 모두 관측한 성분 {100 * rc['share_full']:.0f}%, 성분 수와 행 수의 순위 상관 {rc['spearman_n_vs_rows']:.3f}.\n")
    a("## 7. 확인 필요\n")
    a("1. 군별 모델 실험 후보를 '부분 풀링(DL)'과 'H4 크기 10 + 패턴(23개 군)' 둘로 좁힐지. 비교 기준으로 ATC 2단계·ATC 1단계를 함께 둘지.")
    a("2. 실험에서 부분 풀링을 구현하는 방법: 군 효과를 줄여 쓰는 사후 보정으로 할지, 전역 모델에 ATC 1단계·2단계 범주를 넣는 방식으로 할지.\n")
    a("## 8. 출력 파일\n")
    a("- `outputs/p2v3/pooling/p2v3_pooling.xlsx`, `loo_comparison.csv`, `loo_diff_ci.csv`, `pooling_variance_components.csv`, `pooling_shrinkage.csv`")
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"썼다: {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
