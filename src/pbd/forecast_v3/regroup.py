"""P2 v3 다시 묶기: ATC 2단계 군을 (a) ATC 1단계로, (b) 사용 패턴이 비슷한 것끼리 묶으면 군간 차이가 커지는가.
사후 탐색, 판정 없음. 설계 docs/PLAN_P2_v3.md §9.
python -m pbd.forecast_v3.regroup → outputs/p2v3/regroup/ (먼저 make p2v3-group 산출이 있어야 한다)

비교 방법
- 군간 몫 η²_w(가중치 w). 군 수가 다르면 그대로 비교할 수 없어 순열 기준과 비교한다.
  · 성분 순열: 성분의 군 표지를 섞음(군 크기 유지). 원래 분류(약효분류, ATC 2단계)에 쓴다.
  · 블록 순열: ATC 2단계 군(ATC 1단계는 원래 군, 패턴 군집은 합친 군)을 통째로 섞어 같은 수의 묶음에 배정(묶음마다 ATC 2단계 군 수 유지).
    ATC 1단계와 패턴 군집처럼 'ATC 2단계 군을 묶은 것'에 쓴다. "아무렇게나 묶은 것보다 나은가"를 본다.
- 패턴 군집: ATC 2단계 군(합친 군 54개)마다 성분 패턴 지표 중앙값 → 표준화 → Ward 계층 군집.
  주 = 선정 기준 시점까지의 창(2018-01 ~ 2022-07, 실험에서 쓸 수 있는 정보), 참고 = 평가 기간 창(2023-01 ~ 2025-12, 순환).
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import yaml

from pbd.common import ROOT
from pbd.forecast_v3 import group_compare as gc

OUT = ROOT / "outputs/p2v3/regroup"
SRC = ROOT / "outputs/p2v3"
CLUSTER_FEATURES = ("seas_strength", "trend_strength", "annual_growth", "resid_sd", "winter_summer")
PRE_WINDOW = ("2018-01", "2022-07")
# lr_G_B3 = log(WAPE_G ÷ WAPE_B3): 모델 우열의 상대 크기(음수 = G가 나음). d_ = 원 척도 차이(꼬리가 길어 참고)
EVAL_METRICS = ("log_wape_G", "log_wape_B3", "lr_G_B3", "lr_G_B1", "d_G_B3", "d_G_B1")
N_PERM = 999


# ---------------------------------------------------------------- 계산 도구
def eta2(e: np.ndarray, w: np.ndarray, lab: np.ndarray) -> float:
    """가중 η² (lab = 0..K−1 정수)"""
    mu = (w * e).sum() / w.sum()
    W = np.bincount(lab, weights=w)
    S = np.bincount(lab, weights=w * e)
    m = W > 0
    between = (S[m] ** 2 / W[m]).sum() - w.sum() * mu ** 2
    total = (w * (e - mu) ** 2).sum()
    return float(between / total) if total > 0 else np.nan


def perm_test(e, w, lab, rng, n_perm=N_PERM, block=None):
    """η², 순열 평균, 순열 p(≥, +1 보정). block: 성분마다 블록 번호(블록 단위로 표지를 섞음), lab은 블록 안에서 같아야 한다."""
    obs = eta2(e, w, lab)
    null = np.empty(n_perm)
    if block is None:
        for k in range(n_perm):
            null[k] = eta2(e, w, rng.permutation(lab))
    else:
        nb = block.max() + 1
        blab = np.zeros(nb, dtype=int)
        blab[block] = lab  # 블록 → 묶음
        for k in range(n_perm):
            null[k] = eta2(e, w, rng.permutation(blab)[block])
    return obs, float(null.mean()), float((1 + (null >= obs).sum()) / (n_perm + 1))


def ward_clusters(feat: pd.DataFrame, k: int) -> pd.Series:
    """feat: index = 군, 열 = 지표(결측 없음). 열마다 표준화 → Ward. 묶음 번호는 첫 등장 군 순서(index 정렬)로 1부터."""
    from scipy.cluster.hierarchy import fcluster, linkage

    f = feat.sort_index()
    z = (f - f.mean()) / f.std(ddof=0).replace(0, 1)
    lab = fcluster(linkage(z.to_numpy(), method="ward"), t=k, criterion="maxclust")
    order = {c: i + 1 for i, c in enumerate(dict.fromkeys(lab))}
    return pd.Series([order[c] for c in lab], index=f.index, name="cluster")


def codes(s: pd.Series) -> np.ndarray:
    return pd.factorize(s, sort=True)[0]


# ---------------------------------------------------------------- 실행
def pre_window_patterns(ing_index, con) -> pd.DataFrame:
    a, b = PRE_WINDOW
    panel = con.execute(f"select c4, month t, claim_qty y from fc_panel where observed and month between '{a}' and '{b}'").df()
    months = pd.period_range(a, b, freq="M").strftime("%Y-%m")
    ps = panel.set_index(["c4", "t"]).y
    rows = {}
    for c4 in ing_index:
        s = ps.reindex(pd.MultiIndex.from_product([[c4], months])).droplevel(0)
        rows[c4] = gc.pattern_metrics(s)
    return pd.DataFrame.from_dict(rows, orient="index")[list(CLUSTER_FEATURES)].add_prefix("pre_")


def main() -> int:
    import duckdb

    seed = yaml.safe_load((ROOT / "config/params.yaml").read_text(encoding="utf-8"))["seed"]
    rng = np.random.default_rng(seed)
    ing = pd.read_csv(SRC / "ingredient_metrics.csv", dtype={"c4": str, "cls3": str, "cls2": str, "cls2_raw": str}).set_index("c4")
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    pre = pre_window_patterns(ing.index, con)
    ing = ing.join(pre)
    ing["lr_G_B3"] = np.log(ing.wape_G / ing.wape_B3)
    ing["lr_G_B1"] = np.log(ing.wape_G / ing.wape_B1)
    ing["atc1"] = ing.atc2_raw.str[0]
    assert (ing.atc1.value_counts() >= gc.SMALL_N).all(), "ATC 1단계 군에 3개 미만이 있다 — 규칙 필요"

    # ATC 2단계(합친 군)마다 패턴 중앙값
    feats = {"pre": ing.groupby("atc2")[[f"pre_{c}" for c in CLUSTER_FEATURES]].median().rename(columns=lambda c: c[4:]),
             "eval": ing.groupby("atc2")[list(CLUSTER_FEATURES)].median()}
    for nm, f in feats.items():
        if f.isna().any().any():
            print(f"{nm} 창 패턴이 없는 ATC 2단계 군: {f.index[f.isna().any(axis=1)].tolist()} — 멈춘다")
            return 1
    n_atc1 = ing.atc1.nunique()
    n_cls2 = ing.cls2.nunique()
    ks = sorted({4, 6, 8, n_atc1, n_cls2})

    # 블록: ATC 1단계 = 원래 ATC 2단계 군(합친 기타 군에는 여러 글자가 섞임), 패턴 군집 = 합친 ATC 2단계 군
    groupings = {"cls2": (ing.cls2, None), "atc2": (ing.atc2, None), "atc1": (ing.atc1, "atc2_raw")}
    clus_rows = []
    for nm, f in feats.items():
        for k in ks:
            cl = ward_clusters(f, k)
            groupings[f"pat_{nm}_k{k}"] = (ing.atc2.map(cl).map(lambda c: f"P{c:02d}"), "atc2")
            clus_rows.append(pd.DataFrame({"atc2": cl.index, "cluster": cl.values, "window": nm, "k": k}))
    clus = pd.concat(clus_rows)

    metrics = EVAL_METRICS + gc.PATTERN_COLS + tuple(f"pre_{c}" for c in CLUSTER_FEATURES)
    rows = []
    for gname, (lab_s, mode) in groupings.items():
        for m in metrics:
            for weighting in (("w", "1") if m in EVAL_METRICS else ("w",)):
                d = ing[[m, "w"]].assign(lab=lab_s, blk=ing[mode] if mode else 0).dropna()
                e, lab = d[m].to_numpy(), codes(d.lab)
                w = d.w.to_numpy() if weighting == "w" else np.ones(len(d))
                obs, null_ing, p_ing = perm_test(e, w, lab, rng)
                null_blk = p_blk = np.nan
                if mode:  # 블록 번호를 다시 매기되, 블록 안 표지가 같아야 한다
                    blk = codes(d.blk)
                    assert (pd.Series(lab).groupby(blk).nunique() == 1).all()
                    _, null_blk, p_blk = perm_test(e, w, lab, rng, block=blk)
                circular = (gname.startswith("pat_eval") and m in gc.PATTERN_COLS) or \
                           (gname.startswith("pat_pre") and m.startswith("pre_"))
                rows.append({"grouping": gname, "n_groups": int(lab.max() + 1), "metric": m, "weighting": weighting,
                             "n_ingr": len(d), "eta2": obs,
                             "null_ingr_mean": null_ing, "excess_ingr": obs - null_ing, "p_ingr": p_ing,
                             "block": mode or "", "null_block_mean": null_blk, "excess_block": obs - null_blk, "p_block": p_blk,
                             "circular": circular})
    res = pd.DataFrame(rows)

    # 묶음 표: 패턴 군집(주 = pre)마다 구성과 오차
    def cluster_table(gname):
        lab_s = groupings[gname][0]
        src = ing.atc2_raw if gname == "atc1" else ing.atc2
        t = gc.group_table(ing.assign(src_group=src, grp=lab_s), "grp", seed)
        return t.assign(grouping=gname)

    ctabs = pd.concat([cluster_table(g) for g in groupings if g.startswith("pat_") or g == "atc1"])

    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / "grouping_comparison.csv", index=False, encoding="utf-8-sig", float_format="%.4g")
    clus.to_csv(OUT / "pattern_clusters_atc2.csv", index=False, encoding="utf-8-sig")
    ctabs.to_csv(OUT / "grouping_tables.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    for nm, f in feats.items():
        f.to_csv(OUT / f"atc2_features_{nm}.csv", encoding="utf-8-sig", float_format="%.4g")
    ing[[f"pre_{c}" for c in CLUSTER_FEATURES]].to_csv(OUT / "ingredient_pre_patterns.csv", encoding="utf-8-sig", float_format="%.6g")
    with pd.ExcelWriter(OUT / "p2v3_regroup.xlsx") as xw:
        res.to_excel(xw, sheet_name="grouping_comparison", index=False)
        ctabs.to_excel(xw, sheet_name="grouping_tables", index=False)
        clus.to_excel(xw, sheet_name="pattern_clusters_atc2", index=False)
        for nm, f in feats.items():
            f.to_excel(xw, sheet_name=f"atc2_features_{nm}")
    summ = {"ks": ks, "n_atc1": n_atc1, "n_cls2": n_cls2, "n_atc2": int(ing.atc2.nunique()), "n_perm": N_PERM,
            "pre_window": PRE_WINDOW, "pre_pattern_missing": int(ing.pre_seas_strength.isna().sum()),
            "pre_pattern_missing_w": float(ing.loc[ing.pre_seas_strength.isna(), "w"].sum())}
    (OUT / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False))
    write_report()
    return 0


# ---------------------------------------------------------------- 보고서 (숫자는 모두 위 산출 파일에서 읽음)
REPORT = ROOT / "reports/P2v3_regroup_result.md"
GLABEL = {"cls2": "약효분류 2자리", "atc2": "ATC 2단계", "atc1": "ATC 1단계"}


def _glabel(g: str) -> str:
    if g in GLABEL:
        return GLABEL[g]
    _, win, k = g.split("_")
    return f"패턴 군집 {k[1:]}개 ({'선정 시점 창' if win == 'pre' else '평가 기간 창 · 참고'})"


def write_report() -> None:
    s = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    r = pd.read_csv(OUT / "grouping_comparison.csv", dtype={"weighting": str})
    t = pd.read_csv(OUT / "grouping_tables.csv", dtype={"group": str})
    clus = pd.read_csv(OUT / "pattern_clusters_atc2.csv")
    fpre = pd.read_csv(OUT / "atc2_features_pre.csv").set_index("atc2")
    f2 = lambda x, d=2: "" if pd.isna(x) else f"{x:.{d}f}"
    pv = lambda x: "" if pd.isna(x) else f"{x:.3f}"
    order = ["atc2", "cls2", "atc1"] + [f"pat_pre_k{k}" for k in s["ks"]] + [f"pat_eval_k{k}" for k in s["ks"]]
    get = lambda g, m, w="w": r[(r.grouping == g) & (r.metric == m) & (r.weighting == w)].iloc[0]
    L = []
    a = L.append
    a("# P2 v3 다시 묶기: ATC 1단계·사용 패턴으로 묶으면 군간 차이가 커지는가 (사후 탐색)\n")
    a("> 성격: P2 v3 분류군 비교(`reports/P2v3_group_result.md`)에 붙이는 사후 탐색. 판정을 내리지 않는다. 목적은 군별 예측 모델 실험의 "
      "군 단위를 고르는 사전 자료이고, 그 실험은 하지 않았다. 분류군과 오차의 관계는 연관이다. 설계 `docs/PLAN_P2_v3.md` §9, 실행 `make p2v3-regroup`.\n")
    a("## 1. 완료 기준\n")
    a("| 기준 | 결과 |\n|---|---|")
    a(f"| ATC 1단계 묶음 | 통과: {s['n_atc1']}개 군, 모두 성분 {gc.SMALL_N}개 이상(합칠 군 없음) |")
    a(f"| 패턴 군집 | 통과: ATC 2단계 군 {s['n_atc2']}개를 패턴 지표 중앙값으로 Ward 군집, 군 수 {', '.join(map(str, s['ks']))} "
      f"(14 = ATC 1단계, 22 = 약효분류 2자리와 같은 수). 주 창 {s['pre_window'][0]} ~ {s['pre_window'][1]}, 패턴 빈칸 {s['pre_pattern_missing']}개 성분 |")
    a(f"| 공정한 비교 | 순열 {s['n_perm']}회(시드 고정): 성분 순열과 블록 순열(ATC 2단계 군 통째) 기준을 함께 |")
    a("| 시험 | `tests/test_p2v3_group.py` 10개 (η² 손 계산, 블록 순열, Ward 결정성) |\n")

    a("## 2. 답\n")
    at, c2, p14, p22 = (lambda m, w="w": get("atc1", m, w)), (lambda m, w="w": get("cls2", m, w)), \
        (lambda m, w="w": get("pat_pre_k14", m, w)), (lambda m, w="w": get("pat_pre_k22", m, w))
    a2 = lambda m, w="w": get("atc2", m, w)
    a("먼저 알아 둘 점: ATC 1단계와 패턴 군집은 모두 ATC 2단계 군을 **합친** 것이라 군간 몫(η²)이 ATC 2단계보다 커질 수 없다"
      "(합치면 군간 몫은 줄거나 같다). 그래서 \"커지나\"는 (1) 군 수를 줄여도 차이를 얼마나 남기는가(같은 수로 ATC 2단계 군을 무작위로 묶은 것 대비 = 블록 초과), "
      "(2) 같은 군 수의 다른 분류보다 큰가로 본다.\n")
    a(f"- **ATC 1단계로 묶으면**: 군 {s['n_atc1']}개로 줄어 η²는 ATC 2단계보다 작다(G 오차 log {f2(at('log_wape_G').eta2)} 대 {f2(a2('log_wape_G').eta2)}, "
      f"모델 우열 log(G/B3) {f2(at('lr_G_B3').eta2)} 대 {f2(a2('lr_G_B3').eta2)}). 그러나 무작위로 14개로 묶은 것보다 차이를 더 남긴다"
      f"(블록 초과 {f2(at('log_wape_G').excess_block)}, p {pv(at('log_wape_G').p_block)}; 모델 우열 {f2(at('lr_G_B3').excess_block)}, p {pv(at('lr_G_B3').p_block)}). "
      f"군이 더 많은 약효분류 2자리와 비슷하다(성분 순열 초과: G 오차 {f2(at('log_wape_G').excess_ingr)} 대 {f2(c2('log_wape_G').excess_ingr)}, "
      f"모델 우열 {f2(at('lr_G_B3').excess_ingr)} 대 {f2(c2('lr_G_B3').excess_ingr)}).")
    a(f"- **패턴(선정 시점 창)으로 묶으면**: 같은 군 수에서 분류 체계보다 차이가 작다. 14개: 모델 우열 η² {f2(p14('lr_G_B3').eta2)}(ATC 1단계 {f2(at('lr_G_B3').eta2)}), "
      f"22개: {f2(p22('lr_G_B3').eta2)}(약효분류 2자리 {f2(c2('lr_G_B3').eta2)}). 군 수를 4~6개로 크게 줄일 때는 무작위 묶음보다 오차 수준 차이를 많이 남긴다"
      f"(6개 블록 초과 {f2(get('pat_pre_k6', 'log_wape_G').excess_block)}, p {pv(get('pat_pre_k6', 'log_wape_G').p_block)}). 모델 우열은 가중 기준 p "
      f"{pv(get('pat_pre_k6', 'lr_G_B3').p_block)}, 비가중 p {pv(get('pat_pre_k6', 'lr_G_B3', '1').p_block)}.")
    pe22 = get("pat_eval_k22", "lr_G_B3")
    a(f"- 평가 기간 패턴으로 묶으면(참고) 22개에서 약효분류 2자리와 비슷하거나 크다(모델 우열 η² {f2(pe22.eta2)}). 그러나 평가 기간 정보를 쓴 묶음이라 "
      "군별 모델 실험에는 쓸 수 없다.")
    a("- 원 척도 오차 차이(G − B3)는 극단값 몇 개가 분산을 차지해 어느 묶음에서도 순열 기준과 비슷하다. 그래서 모델 우열은 상대 차이 log(G/B3)로 본다.\n")

    a("## 3. 묶음 비교표\n")
    a("η² = 가중 군간 몫. 성분 초과 = η² − 성분 순열 평균. 블록 초과 = η² − 블록 순열 평균(ATC 2단계 군을 통째로 같은 수의 묶음에 무작위 배정), p는 순열 p. "
      "모델 우열 = log(WAPE_G ÷ WAPE_B3), log(WAPE_G ÷ WAPE_B1) (성분마다).\n")
    for w, title in (("w", "가중(금액 가중치 w)"), ("1", "비가중(성분마다 같은 무게)")):
        a(f"### 3-{1 if w == 'w' else 2}. {title}\n")
        a("| 묶음 | 군 수 | G 오차(log) η² | 성분 초과 | 블록 초과 (p) | log(G/B3) η² | 성분 초과 | 블록 초과 (p) | log(G/B1) η² | 성분 초과 | 블록 초과 (p) |")
        a("|---|---|---|---|---|---|---|---|---|---|---|")
        for g in order:
            cells = []
            for m in ("log_wape_G", "lr_G_B3", "lr_G_B1"):
                x = get(g, m, w)
                blk = "" if pd.isna(x.excess_block) else f"{f2(x.excess_block)} ({pv(x.p_block)})"
                cells += [f2(x.eta2), f2(x.excess_ingr), blk]
            a(f"| {_glabel(g)} | {int(get(g, 'log_wape_G', w).n_groups)} | " + " | ".join(cells) + " |")
        a("")

    a("## 4. 묶음 구성\n")
    a("### 4-1. 패턴 군집 6개 (선정 시점 창 2018-01 ~ 2022-07)\n")
    a("패턴 = 묶음 안 ATC 2단계 군 패턴 중앙값의 중앙값. 오차는 2023-01 ~ 2025-12 평가 칸, 묶음 안 금액 가중 WAPE.\n")
    a("| 묶음 | 성분 수 | 가중치 몫 | G 오차 | B3 | B1 | 최저 모델 | 계절성 | 추세 | 연간 증가율 | 잔차 sd | 겨울/여름 | ATC 2단계 군 |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    c6 = clus[(clus.window == "pre") & (clus.k == 6)].set_index("atc2")
    for _, x in t[t.grouping == "pat_pre_k6"].iterrows():
        mem = c6.index[c6.cluster == int(x.group[1:])]
        fm = fpre.loc[mem].median()
        a(f"| {x.group} | {x.n_ingr} | {100 * x.w_share:.1f}% | {100 * x.wape_G:.1f}% | {100 * x.wape_B3:.1f}% | {100 * x.wape_B1:.1f}% | {x.best_model} | "
          f"{f2(fm.seas_strength)} | {f2(fm.trend_strength)} | {100 * fm.annual_growth:.1f}% | {f2(fm.resid_sd, 3)} | {f2(fm.winter_summer)} | {', '.join(mem)} |")
    a("\n### 4-2. ATC 1단계\n")
    a("| 순위 | 군 | 성분 수 | 가중치 몫 | G 오차 | B3 | B1 | 최저 모델 | 원래 ATC 2단계 군 |\n|---|---|---|---|---|---|---|---|---|")
    for _, x in t[t.grouping == "atc1"].iterrows():
        a(f"| {x.rank_G} | {x.group} | {x.n_ingr} | {100 * x.w_share:.1f}% | {100 * x.wape_G:.1f}% | {100 * x.wape_B3:.1f}% | {100 * x.wape_B1:.1f}% | "
          f"{x.best_model} | {x.merged_from} |")
    a("\n다른 군 수(4, 8, 14, 22)와 평가 기간 창의 구성은 `outputs/p2v3/regroup/grouping_tables.csv`, `pattern_clusters_atc2.csv`.\n")

    a("## 5. 문서와 달랐던 점, 알아 둘 것\n")
    a("- 군집 지표 = 계절성 강도, 추세 강도, 연간 증가율, 잔차 sd, 겨울/여름(ATC 2단계 군마다 성분 중앙값 → 표준화 → Ward). 전년비 sd는 G·B1의 목표와 정의가 겹쳐"
      "(성분 단위로 G 오차와 Spearman 0.94) 넣지 않았다.")
    a("- 선정 시점 창(2018-01 ~ 2022-07)에는 코로나 시기(2020~2021)가 들어 있다. 호흡기 계열의 계절성이 이 시기에 크게 흔들렸으므로 군집에 그 영향이 있다.")
    a("- 같은 창의 패턴 지표로 같은 창의 패턴 군간 몫을 재는 것은 순환이라 `grouping_comparison.csv`에 `circular = True`로 표시했고 본문에 쓰지 않았다.")
    a("- 원 척도 오차 차이 G − B3, G − B1은 CSV에만 둔다(꼬리가 길어 η²가 극단값 몇 개로 정해짐). 앞 보고서 §2의 'G − B3 군간 몫이 우연 수준'은 이 원 척도 지표 기준이었다. "
      "상대 차이 log(G/B3)로는 군간 차이가 뚜렷하다(위 표). 앞 보고서의 순위 시험(ε²)과는 맞는다.")
    a("- `전체_기타`(약효분류 34·71, 성분 2개)는 그대로 둔다(사용자 결정 2026-11-24).\n")
    a("## 6. 확인 필요\n")
    a("1. 군별 모델 실험의 단위 후보: ATC 1단계(14개), 약효분류 2자리(22개), 선정 시점 패턴 군집(4~6개). 어느 것(들)로 할지.")
    a("2. 패턴 군집의 창: 코로나 시기를 피하려면 2018-01 ~ 2019-12(24개월)만 쓸 수 있으나 계절 주기가 2번뿐이다.")
    a("3. 패턴 군집 군 수: 이번에는 4, 6, 8, 14, 22를 미리 정해 모두 보고했다. 실험에 쓸 하나를 고를 기준(예: 학습 자료 크기)을 정해야 한다.\n")
    a("## 7. 출력 파일\n")
    a("- `outputs/p2v3/regroup/p2v3_regroup.xlsx` (아래 전부)")
    a("- `grouping_comparison.csv`: 묶음 × 지표 × 가중 η², 순열 기준 두 가지")
    a("- `grouping_tables.csv`: ATC 1단계·패턴 군집 묶음별 오차표, `pattern_clusters_atc2.csv`: ATC 2단계 군 → 묶음")
    a("- `atc2_features_pre.csv`, `atc2_features_eval.csv`: ATC 2단계 군 패턴 중앙값, `ingredient_pre_patterns.csv`, `summary.json`")
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"썼다: {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
