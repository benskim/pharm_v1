"""P2 v3 혼합 묶음: ATC 2단계 군을 ATC 1단계 안에서만 골라 합치는 여러 방식을 ATC 2단계·ATC 1단계와 비교 (사후 탐색).
설계 docs/PLAN_P2_v3.md §10. python -m pbd.forecast_v3.hybrid → outputs/p2v3/hybrid/ (make p2v3-regroup 산출이 있어야 한다)

혼합 방식 (모두 원래 ATC 2단계 군을 ATC 1단계 경계 안에서만 합친다. 합친 뒤 성분 3개 미만 군은 같은 ATC 1단계 안 가장 가까운 군에 붙인다)
- H1 패턴 유사: 2018-01 ~ 2022-07 패턴 지표 중앙값(표준화)이 가까운 군끼리 평균 연결 군집, 거리 문턱 τ.
- H2 사전 오차 유사: 2022 대상월(h = 6) 오차의 모델 우열 log(G/B3), log(G/B1)과 log(G 오차)(가중 평균, 표준화)가 가까운 군끼리, 문턱 τ.
- H3 크기: 성분 m개 이상 군은 그대로, 나머지는 '{ATC1}_기타'.
- H4 크기 + 패턴: 성분 m개 이상 군은 그대로, 나머지는 각각 패턴이 가장 가까운 큰 군에 붙인다(같은 ATC 1단계에 큰 군이 없으면 그 ATC 1단계 전체로).
τ = ATC 1단계가 같은 군 쌍 거리의 25·50·75% 분위수. m = 5, 10.
평가는 2023-01 ~ 2025-12 오차(군을 정할 때 쓰지 않은 기간).
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import yaml

from pbd.common import ROOT
from pbd.forecast_v3 import group_compare as gc
from pbd.forecast_v3.regroup import CLUSTER_FEATURES, N_PERM, codes, eta2, perm_test

OUT = ROOT / "outputs/p2v3/hybrid"
SRC = ROOT / "outputs/p2v3"
ERR_FEATURES = ("lr22_G_B3", "lr22_G_B1", "log_wape22_G")
METRICS = ("log_wape_G", "lr_G_B3", "lr_G_B1")
QUANTS = (0.25, 0.5, 0.75)
SIZES = (5, 10)


# ---------------------------------------------------------------- 군 특징과 거리
def zscore(f: pd.DataFrame) -> pd.DataFrame:
    return (f - f.mean()) / f.std(ddof=0).replace(0, 1)


def group_features(ing: pd.DataFrame, gcol: str, cols, how: str) -> pd.DataFrame:
    """군마다 성분 특징 요약: how = 'median'(비가중 중앙값) 또는 'wmean'(가중치 w 평균)"""
    if how == "median":
        return ing.groupby(gcol)[list(cols)].median()
    return ing.groupby(gcol).apply(lambda d: pd.Series({c: gc._wmean(d[c], d.w) for c in cols}))


def within_pair_distances(z: pd.DataFrame, top: pd.Series) -> np.ndarray:
    """같은 ATC 1단계 안 군 쌍의 유클리드 거리"""
    out = []
    for _, idx in top.groupby(top).groups.items():
        x = z.loc[list(idx)].to_numpy()
        for i in range(len(x)):
            for j in range(i + 1, len(x)):
                out.append(np.linalg.norm(x[i] - x[j]))
    return np.array(out)


def threshold_merge(z: pd.DataFrame, top: pd.Series, tau: float) -> pd.Series:
    """ATC 1단계마다 평균 연결 군집, 거리 τ 이하로 합친다. 반환: 원래 군 → 묶음 이름"""
    from scipy.cluster.hierarchy import fcluster, linkage

    lab = {}
    for t1, idx in sorted(top.groupby(top).groups.items()):
        idx = sorted(idx)
        if len(idx) == 1:
            lab[idx[0]] = idx[0]
            continue
        cl = fcluster(linkage(z.loc[idx].to_numpy(), method="average"), t=tau, criterion="distance")
        for c in set(cl):
            mem = [g for g, k in zip(idx, cl) if k == c]
            name = mem[0] if len(mem) == 1 else ("+".join(mem) if len(mem) < len(idx) else f"{t1}(전체)")
            for g in mem:
                lab[g] = name
    return pd.Series(lab)


def enforce_min_size(lab: pd.Series, n_ingr: pd.Series, z: pd.DataFrame, top: pd.Series, min_n: int) -> pd.Series:
    """묶음 성분 수 < min_n이면 같은 ATC 1단계 안에서 중심(원래 군 z의 성분 수 가중 평균)이 가장 가까운 묶음에 붙인다.
    가장 작은 묶음부터(같으면 이름 순). ATC 1단계에 묶음이 하나뿐이면 그대로."""
    lab = lab.copy()
    while True:
        size = n_ingr.groupby(lab).sum()
        t1 = top.groupby(lab).first()
        small = [g for g in size.sort_values(kind="stable").index if size[g] < min_n and (t1 == t1[g]).sum() > 1]
        if not small:
            return lab
        g = sorted(small, key=lambda x: (size[x], x))[0]
        cen = lambda h: np.average(z.loc[lab.index[lab == h]].to_numpy(), axis=0, weights=n_ingr[lab.index[lab == h]])
        cands = [h for h in size.index if h != g and t1[h] == t1[g]]
        tgt = min(cands, key=lambda h: (np.linalg.norm(cen(g) - cen(h)), h))
        mem = sorted(lab.index[(lab == g) | (lab == tgt)])
        n_top = (top == t1[g]).sum()
        name = f"{t1[g]}(전체)" if len(mem) == n_top else "+".join(mem)
        lab[(lab == g) | (lab == tgt)] = name


def size_merge(n_ingr: pd.Series, top: pd.Series, m: int, z: pd.DataFrame | None) -> pd.Series:
    """H3(z None): m개 미만 군 → '{ATC1}_기타'. H4: 각각 패턴이 가장 가까운 큰 군에. 큰 군이 없는 ATC 1단계는 전체로."""
    lab = {}
    for t1, idx in sorted(top.groupby(top).groups.items()):
        idx = sorted(idx)
        big = [g for g in idx if n_ingr[g] >= m]
        if not big:
            for g in idx:
                lab[g] = f"{t1}(전체)"
            continue
        for g in big:
            lab[g] = g
        rest = [g for g in idx if g not in big]
        for g in rest:
            if z is None:
                lab[g] = f"{t1}_기타"
            else:
                lab[g] = min(big, key=lambda b: (np.linalg.norm(z.loc[g] - z.loc[b]), b))
    lab = pd.Series(lab)
    if z is not None:  # 붙은 큰 군의 이름에 표시
        cnt = lab.value_counts()
        lab = lab.map(lambda v: v if cnt[v] == 1 or v.endswith("(전체)") else f"{v}+α")
    return lab


def perm_test_strata(e, w, lab, rng, block, strata, n_perm=N_PERM):
    """블록(원래 ATC 2단계 군) 표지를 같은 층(ATC 1단계) 안에서만 섞는다 → 'ATC 1단계 안에서 아무렇게나 합친 것' 대비"""
    obs = eta2(e, w, lab)
    nb = block.max() + 1
    blab = np.zeros(nb, dtype=int)
    blab[block] = lab
    bstr = np.zeros(nb, dtype=int)
    bstr[block] = strata
    groups = [np.flatnonzero(bstr == s) for s in np.unique(bstr)]
    null = np.empty(n_perm)
    for k in range(n_perm):
        pl = blab.copy()
        for g in groups:
            pl[g] = blab[rng.permutation(g)]
        null[k] = eta2(e, w, pl[block])
    return obs, float(null.mean()), float((1 + (null >= obs).sum()) / (n_perm + 1))


def adj_eta2(x: float, n: int, k: int) -> float:
    """군 수 보정 η² = 1 − (1 − η²)(n − 1)/(n − k)"""
    return 1 - (1 - x) * (n - 1) / (n - k)


# ---------------------------------------------------------------- 실행
def errors_2022(con) -> pd.DataFrame:
    from pbd.forecast_v3.run import load

    pred, tgt, drop, panel = load(con)
    cells = gc.eval_cells(pred, panel, drop, start="2022-01", end="2022-12")
    e = gc.ingredient_errors(cells)
    return pd.DataFrame({"lr22_G_B3": np.log(e.wape_G / e.wape_B3), "lr22_G_B1": np.log(e.wape_G / e.wape_B1),
                         "log_wape22_G": np.log(e.wape_G), "n_cells22": e.n_cells})


def main() -> int:
    import duckdb

    seed = yaml.safe_load((ROOT / "config/params.yaml").read_text(encoding="utf-8"))["seed"]
    rng = np.random.default_rng(seed)
    ing = pd.read_csv(SRC / "ingredient_metrics.csv", dtype={"c4": str, "cls3": str, "cls2": str, "cls2_raw": str}).set_index("c4")
    pre = pd.read_csv(SRC / "regroup/ingredient_pre_patterns.csv", dtype={"c4": str}).set_index("c4")
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    e22 = errors_2022(con)
    ing = ing.join(pre).join(e22)
    ing["lr_G_B3"] = np.log(ing.wape_G / ing.wape_B3)
    ing["lr_G_B1"] = np.log(ing.wape_G / ing.wape_B1)
    ing["atc1"] = ing.atc2_raw.str[0]

    raw = ing.atc2_raw
    n_ingr = raw.value_counts().sort_index()
    top = pd.Series({g: g[0] for g in n_ingr.index})
    fp = group_features(ing, "atc2_raw", [f"pre_{c}" for c in CLUSTER_FEATURES], "median").rename(columns=lambda c: c[4:])
    fe = group_features(ing, "atc2_raw", ERR_FEATURES, "wmean")
    miss = {"pattern": fp.index[fp.isna().any(axis=1)].tolist(), "err2022": fe.index[fe.isna().any(axis=1)].tolist()}
    if miss["pattern"] or miss["err2022"]:
        print(f"특징이 없는 원래 ATC 2단계 군: {miss} — 멈춘다")
        return 1
    zp, ze = zscore(fp), zscore(fe)

    maps, meta = {}, {}
    for nm, z in (("H1_pattern", zp), ("H2_err2022", ze)):
        dist = within_pair_distances(z, top)
        for q in QUANTS:
            tau = float(np.quantile(dist, q))
            lab = enforce_min_size(threshold_merge(z, top, tau), n_ingr, z, top, gc.SMALL_N)
            maps[f"{nm}_q{int(q * 100)}"] = lab
            meta[f"{nm}_q{int(q * 100)}"] = {"rule": nm, "tau": tau, "tau_quantile": q}
    for m in SIZES:
        maps[f"H3_size{m}"] = enforce_min_size(size_merge(n_ingr, top, m, None), n_ingr, zp, top, gc.SMALL_N)
        maps[f"H4_size{m}_pattern"] = enforce_min_size(size_merge(n_ingr, top, m, zp), n_ingr, zp, top, gc.SMALL_N)
        meta[f"H3_size{m}"] = {"rule": "H3_size", "m": m}
        meta[f"H4_size{m}_pattern"] = {"rule": "H4_size_pattern", "m": m}
    for k, lab in maps.items():  # 경계 확인
        assert (top.groupby(lab).nunique() == 1).all(), f"{k}: ATC 1단계 경계를 넘었다"

    groupings = {"atc2": ing.atc2, "atc2_raw": raw, "atc1": ing.atc1}
    groupings.update({k: raw.map(v) for k, v in maps.items()})
    block, strata = codes(raw), codes(ing.atc1)
    rows = []
    for g, lab_s in groupings.items():
        lab = codes(lab_s)
        size = lab_s.value_counts()
        for m in METRICS:
            for wt in ("w", "1"):
                d = ing[[m, "w"]].assign(lab=lab, blk=block, st=strata).dropna()
                e, l = d[m].to_numpy(), d.lab.to_numpy()
                w = d.w.to_numpy() if wt == "w" else np.ones(len(d))
                obs, null_i, p_i = perm_test(e, w, codes(pd.Series(l)), rng)
                nested = g not in ("atc2",)  # 합친 ATC 2단계(전체_기타)는 ATC 1단계를 넘는다
                if nested and g not in ("atc1", "atc2_raw"):
                    _, null_s, p_s = perm_test_strata(e, w, codes(pd.Series(l)), rng, codes(d.blk), d.st.to_numpy())
                else:
                    null_s = p_s = np.nan
                k = len(np.unique(l))
                rows.append({"grouping": g, "n_groups": k, "min_size": int(size.min()), "median_size": float(size.median()),
                             "metric": m, "weighting": wt, "eta2": obs, "adj_eta2": adj_eta2(obs, len(d), k),
                             "excess_ingr": obs - null_i, "p_ingr": p_i,
                             "excess_within_atc1": obs - null_s, "p_within_atc1": p_s})
    res = pd.DataFrame(rows)
    ref = res[res.grouping == "atc2_raw"].set_index(["metric", "weighting"]).eta2
    res["retain_vs_atc2_raw"] = res.apply(lambda r: r.eta2 / ref[(r.metric, r.weighting)], axis=1)

    tabs = []
    for g in [k for k in groupings if k.startswith("H")] + ["atc1"]:
        src = raw
        t = gc.group_table(ing.assign(src_group=src, grp=groupings[g]), "grp", seed)
        tabs.append(t.assign(grouping=g))
    tabs = pd.concat(tabs)
    mapping = pd.DataFrame(maps).rename_axis("atc2_raw").join(n_ingr.rename("n_ingr"))

    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / "hybrid_comparison.csv", index=False, encoding="utf-8-sig", float_format="%.4g")
    tabs.to_csv(OUT / "hybrid_tables.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    mapping.to_csv(OUT / "hybrid_mapping.csv", encoding="utf-8-sig")
    pd.concat([fp.add_prefix("pre_"), fe], axis=1).join(n_ingr.rename("n_ingr")).to_csv(
        OUT / "atc2_raw_features.csv", encoding="utf-8-sig", float_format="%.4g")
    with pd.ExcelWriter(OUT / "p2v3_hybrid.xlsx") as xw:
        res.to_excel(xw, sheet_name="hybrid_comparison", index=False)
        tabs.to_excel(xw, sheet_name="hybrid_tables", index=False)
        mapping.to_excel(xw, sheet_name="hybrid_mapping")
    # 학습 행 수 점검(성분 수 기준과 갈리는가): 2018-01 ~ 2025-06 관측·> 0 개월
    obs = con.execute("""select c4, count(*) filter (where observed and claim_qty > 0) n_obs from fc_panel
                         where month between '2018-01' and '2025-06' group by 1""").df().set_index("c4").n_obs.reindex(ing.index)
    rows_g = obs.groupby(raw).sum()
    n_months = 90
    rows_check = {"months": n_months, "share_full": float((obs == n_months).mean()), "min_obs": int(obs.min()),
                  "spearman_n_vs_rows": float(n_ingr.rank().corr(rows_g.reindex(n_ingr.index).rank())),
                  "split_at10": int(((n_ingr < 10) & (rows_g.reindex(n_ingr.index) >= 10 * n_months * 0.9)).sum()
                                    + ((n_ingr >= 10) & (rows_g.reindex(n_ingr.index) < 10 * n_months * 0.9)).sum())}
    summ = {"rows_check": rows_check, "methods": meta, "n_raw_atc2": int(len(n_ingr)), "n_atc1": int(top.nunique()), "n_perm": N_PERM,
            "cells_2022": int(ing.n_cells22.sum()), "ingr_with_2022": int(ing.n_cells22.notna().sum()),
            "raw_atc2_lt3": int((n_ingr < gc.SMALL_N).sum())}
    (OUT / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False))
    write_report()
    return 0


# ---------------------------------------------------------------- 보고서 (숫자는 산출 파일에서 읽음)
REPORT = ROOT / "reports/P2v3_hybrid_result.md"
NAME = {"atc2": "ATC 2단계(합친 군)", "atc2_raw": "ATC 2단계(원래)", "atc1": "ATC 1단계",
        "H1_pattern_q25": "H1 패턴 유사 τ25%", "H1_pattern_q50": "H1 패턴 유사 τ50%", "H1_pattern_q75": "H1 패턴 유사 τ75%",
        "H2_err2022_q25": "H2 2022 오차 유사 τ25%", "H2_err2022_q50": "H2 2022 오차 유사 τ50%", "H2_err2022_q75": "H2 2022 오차 유사 τ75%",
        "H3_size5": "H3 크기 5개 이상 유지", "H3_size10": "H3 크기 10개 이상 유지",
        "H4_size5_pattern": "H4 크기 5개 + 패턴 흡수", "H4_size10_pattern": "H4 크기 10개 + 패턴 흡수"}
ORDER = list(NAME)


def write_report() -> None:
    s = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    r = pd.read_csv(OUT / "hybrid_comparison.csv", dtype={"weighting": str})
    t = pd.read_csv(OUT / "hybrid_tables.csv", dtype={"group": str})
    g = lambda gr, m, w="w": r[(r.grouping == gr) & (r.metric == m) & (r.weighting == w)].iloc[0]
    f2 = lambda x: "" if pd.isna(x) else f"{x:.2f}"
    pc = lambda x: "" if pd.isna(x) else f"{100 * x:.0f}%"
    pv = lambda x: "" if pd.isna(x) else f"{x:.3f}"
    hyb = [k for k in ORDER if k.startswith("H")]
    # 순위: 모델 우열(가중) 군 수 보정 η²
    best = sorted(hyb, key=lambda k: -g(k, "lr_G_B3").adj_eta2)
    L = []
    a = L.append
    a("# P2 v3 혼합 묶음: ATC 2단계와 ATC 1단계를 섞는 방식 비교 (사후 탐색)\n")
    a("> 성격: P2 v3 분류군 비교에 붙이는 사후 탐색. 판정 없음. 군별 예측 모델 실험의 군 단위를 고르는 사전 자료이며 그 실험은 하지 않았다. "
      "군간 몫이 크다는 것은 군별 모델이 예측을 개선한다는 뜻이 아니다. 묶음은 평가 기간(2023-01 ~ 2025-12) 전 정보로만 만들고, 평가 기간 오차로 비교했다. "
      "설계 `docs/PLAN_P2_v3.md` §10, 실행 `make p2v3-hybrid`.\n")
    a("## 1. 완료 기준\n")
    a("| 기준 | 결과 |\n|---|---|")
    a(f"| 혼합 방식 여러 개 | 통과: 4가지 규칙 × 문턱 = {len(hyb)}개 묶음. 모두 ATC 1단계 경계 안에서만 합침(코드에서 확인), 군마다 성분 {gc.SMALL_N}개 이상 |")
    a(f"| ATC 2단계·1단계와 비교 | 통과: 원래 ATC 2단계 {s['n_raw_atc2']}개 군(성분 3개 미만 {s['raw_atc2_lt3']}개 포함), 합친 ATC 2단계 54개, ATC 1단계 {s['n_atc1']}개 |")
    a(f"| 공정한 비교 | 군 수 보정 η², 성분 순열 초과, ATC 1단계 안 무작위 합치기 대비 초과(순열 {s['n_perm']}회, 시드 고정) |")
    a(f"| H2 사전 오차 | 2022 대상월(h = 6) 평가 칸 {s['cells_2022']:,}개, 성분 {s['ingr_with_2022']}개 |")
    a("| 시험 | `tests/test_p2v3_group.py` 15개 (경계 유지, 최소 크기, H3·H4 규칙, 층 안 순열, 보정 η²) |\n")

    a("## 2. 답\n")
    h1, h4, h45, h310, h2 = g("H1_pattern_q50", "lr_G_B3"), g("H4_size10_pattern", "lr_G_B3"), g("H4_size5_pattern", "lr_G_B3"), \
        g("H3_size10", "lr_G_B3"), g("H2_err2022_q25", "lr_G_B3")
    h1u, h4u = g("H1_pattern_q50", "lr_G_B3", "1"), g("H4_size10_pattern", "lr_G_B3", "1")
    a("주 지표는 군별 모델 목적에 맞춰 **모델 우열 log(G 오차 ÷ B3 오차)의 군간 몫**으로 했다. 군 수가 달라 '원래 ATC 2단계 대비 유지율'과 "
      "'ATC 1단계 안에서 아무렇게나 합친 것 대비 초과'를 같이 본다.\n")
    a(f"- **제안하신 방식(H1: 패턴이 비슷한 군만 ATC 1단계로 묶기)은 기대만큼 좋지 않았다.** τ50%에서 {int(h1.n_groups)}개 군, 모델 우열 유지율 "
      f"{pc(h1.retain_vs_atc2_raw)}(비가중 {pc(h1u.retain_vs_atc2_raw)})이고, ATC 1단계 안에서 무작위로 합친 것과 차이가 없다"
      f"(초과 {f2(h1.excess_within_atc1)}, p {pv(h1.p_within_atc1)}). 이유: 2018~2022 패턴이 비슷하다고 2023~2025 모델 우열이 비슷하지 않았고, "
      "이 규칙은 성분이 많은 큰 군끼리도 합친다(예: 순환계 C01~C10 8개가 한 묶음). 큰 군을 합치면 ATC 2단계의 장점이 가장 크게 사라진다.")
    a(f"- **잘 된 방식은 '큰 군은 그대로, 작은 군만 붙이기'(H3·H4)였다.** H4(성분 10개 이상 유지, 작은 군은 패턴이 가장 가까운 큰 군에 붙임): "
      f"{int(h4.n_groups)}개 군, 유지율 {pc(h4.retain_vs_atc2_raw)}(비가중 {pc(h4u.retain_vs_atc2_raw)}), 무작위 합치기 대비 초과 {f2(h4.excess_within_atc1)} "
      f"(p {pv(h4.p_within_atc1)}; 비가중 p {pv(h4u.p_within_atc1)}). H4(5개 이상): {int(h45.n_groups)}개 군, 유지율 {pc(h45.retain_vs_atc2_raw)}. "
      f"H3(10개 이상 유지, 나머지는 ATC 1단계 기타): {int(h310.n_groups)}개 군, 유지율 {pc(h310.retain_vs_atc2_raw)}, p {pv(h310.p_within_atc1)}. "
      "작은 군의 평균은 성분 몇 개로 정해져 불안정하므로, 그런 군만 합칠 때 잃는 것이 적었다(해석, 따로 시험하지 않음).")
    a(f"- H2(2022 오차의 모델 우열이 비슷한 군끼리)는 H1보다 낫다. τ25%: {int(h2.n_groups)}개 군, 유지율 {pc(h2.retain_vs_atc2_raw)}"
      f"(비가중 {pc(g('H2_err2022_q25', 'lr_G_B3', '1').retain_vs_atc2_raw)}, 무작위 대비 비가중 p {pv(g('H2_err2022_q25', 'lr_G_B3', '1').p_within_atc1)}). "
      "다만 2022는 코로나 회복기이고, 대상·가중치가 그 뒤 자료로 정해졌다(P2 v2 '2022 회복기' 표시).")
    a(f"- ATC 1단계(14개)는 유지율 {pc(g('atc1', 'lr_G_B3').retain_vs_atc2_raw)}(비가중 {pc(g('atc1', 'lr_G_B3', '1').retain_vs_atc2_raw)})로 가장 많이 잃는다.")
    a(f"- 군 수 보정 η²(모델 우열, 가중) 상위: " + ", ".join(f"{NAME[k]} {f2(g(k, 'lr_G_B3').adj_eta2)}" for k in best[:4]) +
      f" (원래 ATC 2단계 {f2(g('atc2_raw', 'lr_G_B3').adj_eta2)}, ATC 1단계 {f2(g('atc1', 'lr_G_B3').adj_eta2)}).")
    a("- 주의: 혼합 묶음 10개를 같은 평가 기간 오차로 비교해 가장 좋은 것을 고르면 낙관적으로 보인다. 고른 묶음은 실험에서 다른 기간으로 확인해야 한다.\n")

    a("## 3. 다른 혼합 방식 제안 (이번에 시험하지 않음)\n")
    a("1. **딱 잘라 합치지 않고 계층으로 줄이기(부분 풀링)**: ATC 2단계 효과를 자료 양에 따라 ATC 1단계 쪽으로 줄여 쓰는 방식(경험적 베이즈·계층 모델). "
      "작은 군은 자동으로 ATC 1단계에 가깝고 큰 군은 자기 값을 쓴다. H3·H4가 잘 된 이유(작은 군만 합침)를 문턱 없이 구현한다. "
      "실험에서는 전역 모델에 ATC 1단계·2단계를 범주 특징으로 함께 넣는 것과 같은 계열이다.")
    rc = s["rows_check"]
    a(f"2. ~~자료 크기 기준을 학습 행 수로~~ **철회(2026-11-24)**: 이 대상에서는 성분 수와 같다. 학습 기간 {rc['months']}개월(2018-01 ~ 2025-06)을 모두 관측한 성분이 "
      f"{100 * rc['share_full']:.0f}%(최소 {rc['min_obs']}개월), 원래 ATC 2단계 군의 성분 수와 행 수의 순위 상관 {rc['spearman_n_vs_rows']:.3f}, "
      f"10개 기준으로 두 기준이 갈리는 군 {rc['split_at10']}개.")
    a("3. **두 기준 결합**: 크기로 큰 군을 지키고(H4), 작은 군을 붙일 곳은 패턴 대신 사전 기간 모델 우열(H2의 특징)로 고른다.")
    a("4. **군별 모델 대신 군별 모델 선택**: 군마다 새 모델을 만들지 않고, 군마다 G·B3 중 사전 기간에 나았던 것을 고르는 방식. 이번 결과(모델 우열이 군 사이에 다름)와 가장 직접 이어진다.\n")

    a("## 4. 비교표\n")
    a("유지율 = 묶음의 η² ÷ 원래 ATC 2단계(69개)의 η²: 원래 ATC 2단계가 보여 준 군간 차이 중 합친 뒤에도 남은 몫. "
      "원래 ATC 2단계의 η²에는 성분 1~2개 군의 잡음도 들어 있어(군이 많을수록 η²가 저절로 커짐) 100%가 목표는 아니다. "
      "군 수에 덜 민감한 '한 성분 빼고 맞히기' 비교는 `reports/P2v3_pooling_result.md`. 보정 η² = 1 − (1 − η²)(n − 1)/(n − k). 무작위 대비 = η² − 'ATC 1단계 안에서 원래 ATC 2단계 군을 같은 구조로 무작위 합친 것'의 평균(p).\n")
    for w, title in (("w", "가중(금액 가중치 w)"), ("1", "비가중")):
        a(f"### 4-{1 if w == 'w' else 2}. {title}\n")
        a("| 묶음 | 군 수 | 최소 크기 | 모델 우열 log(G/B3): η² | 보정 η² | 유지율 | 무작위 대비 (p) | G 오차(log): η² | 유지율 | 무작위 대비 (p) | log(G/B1): η² | 유지율 |")
        a("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for k in ORDER:
            x, y, z = g(k, "lr_G_B3", w), g(k, "log_wape_G", w), g(k, "lr_G_B1", w)
            rnd = lambda q: "" if pd.isna(q.excess_within_atc1) else f"{f2(q.excess_within_atc1)} ({pv(q.p_within_atc1)})"
            a(f"| {NAME[k]} | {int(x.n_groups)} | {int(x.min_size)} | {f2(x.eta2)} | {f2(x.adj_eta2)} | {pc(x.retain_vs_atc2_raw)} | {rnd(x)} | "
              f"{f2(y.eta2)} | {pc(y.retain_vs_atc2_raw)} | {rnd(y)} | {f2(z.eta2)} | {pc(z.retain_vs_atc2_raw)} |")
        a("")

    a("## 5. 묶음 구성: H4 크기 10개 + 패턴 흡수\n")
    a("`X+α` = 큰 군 X에 작은 군이 붙은 것, `X(전체)` = 그 ATC 1단계에 큰 군이 없어 통째로 묶은 것. 오차는 2023-01 ~ 2025-12, 묶음 안 금액 가중 WAPE.\n")
    a("| 순위 | 묶음 | 원래 ATC 2단계 군 | 성분 수 | 가중치 몫 | G 오차 | B3 | B1 | 최저 모델 |\n|---|---|---|---|---|---|---|---|---|")
    for _, x in t[t.grouping == "H4_size10_pattern"].iterrows():
        a(f"| {x.rank_G} | {x.group} | {x.merged_from} | {x.n_ingr} | {100 * x.w_share:.1f}% | {100 * x.wape_G:.1f}% | {100 * x.wape_B3:.1f}% | "
          f"{100 * x.wape_B1:.1f}% | {x.best_model} |")
    a("\n나머지 묶음의 구성은 `outputs/p2v3/hybrid/hybrid_mapping.csv`(원래 ATC 2단계 군 → 묶음), `hybrid_tables.csv`.\n")

    a("## 6. 정의와 알아 둘 것\n")
    a("- 원래 ATC 2단계 = 표준코드 경로로 배정한 ATC 앞 3자리(작은 군 합치기 전). 혼합 묶음은 모두 이것에서 출발한다.")
    a(f"- H1 특징 = 2018-01 ~ 2022-07 패턴 지표(계절성 강도, 추세 강도, 연간 증가율, 잔차 sd, 겨울/여름)의 군 안 중앙값 → 표준화. "
      f"H2 특징 = 2022 대상월 오차의 log(G/B3), log(G/B1), log(G 오차)의 군 안 가중 평균 → 표준화. ATC 1단계마다 평균 연결 군집, 문턱 τ = 같은 ATC 1단계 군 쌍 거리의 "
      f"25·50·75% 분위수(H1 {', '.join(f2(s['methods'][f'H1_pattern_q{q}']['tau']) for q in (25, 50, 75))}, "
      f"H2 {', '.join(f2(s['methods'][f'H2_err2022_q{q}']['tau']) for q in (25, 50, 75))}).")
    a(f"- 합친 뒤 성분 {gc.SMALL_N}개 미만 묶음은 같은 ATC 1단계 안 중심(z의 성분 수 가중 평균)이 가장 가까운 묶음에 붙였다(작은 것부터).")
    a("- 합친 ATC 2단계(54개)의 `전체_기타`는 ATC 1단계를 넘는 묶음이라 'ATC 1단계 안 무작위' 기준을 계산하지 않았다.")
    a("- 2018-01 ~ 2022-07 창에는 코로나 시기가 들어 있다. 2022 오차도 회복기 값이다.\n")
    a("## 7. 확인 필요\n")
    a("1. 실험 단위 후보를 H4(성분 10개 기준, 23개 군) 또는 H4(5개 기준, 37개 군)로 좁혀도 될지. ATC 2단계(54개)·ATC 1단계(14개)는 비교 기준으로 함께 둘지.")
    a("2. 큰 군 기준은 성분 수로 한다(사용자 결정 2026-11-24, §3-2 철회).")
    a("3. 부분 풀링 비교는 `reports/P2v3_pooling_result.md`. 군별 모델 선택(§3-4)을 실험 설계에 넣을지.\n")
    a("## 8. 출력 파일\n")
    a("- `outputs/p2v3/hybrid/p2v3_hybrid.xlsx` (아래 전부)")
    a("- `hybrid_comparison.csv`: 묶음 × 지표 × 가중, η²·보정 η²·유지율·순열 기준 두 가지")
    a("- `hybrid_mapping.csv`: 원래 ATC 2단계 군 → 묶음(방식별), `hybrid_tables.csv`: 묶음별 오차표, `atc2_raw_features.csv`, `summary.json`")
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"썼다: {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
