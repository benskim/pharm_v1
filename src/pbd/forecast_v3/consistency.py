"""P2 v3 모델 우위의 시점 일관성: 성분 수가 충분하고, 발견 구간과 홀드아웃 구간·연도별로 B3(ETS) 또는 B1이 G보다 꾸준히 나은 군이 있는가.
사후 탐색, 판정 없음. 설계 docs/PLAN_P2_v3.md §12 (실행 전 고정). python -m pbd.forecast_v3.consistency → outputs/p2v3/consistency/

기준(대안 모델 A ∈ {B3, B1}, Δ = 군 G 오차 − 군 A 오차, 양수 = A가 나음. 군 오차 = 군 안 금액 가중 WAPE):
 ① 성분 수 ≥ 5(평가 칸이 있는 성분)  ② 발견 구간 2022-01 ~ 2023-12 Δ > 0  ③ 홀드아웃 2024-01 ~ 2025-12 Δ > 0
 ④ 연도 2023·2024·2025 모두 Δ > 0  ⑤ 홀드아웃 Δ의 90% 구간(군 안 성분 재표집 2,000회) 하한 > 0
 '일관 우위' = ①~⑤ 모두. 우연 기준: 군 표지를 성분 사이에서 섞어(군 크기 유지) ①~④를 만족하는 군 수, 999회.
부분 풀링: 홀드아웃 성분별 log(WAPE_G ÷ WAPE_A)를 DL 경험적 베이즈(군 → 상위: ATC 1단계 / 약효분류 첫 자리)로 줄인 군 추정.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import yaml

from pbd.common import ROOT
from pbd.forecast_v3 import group_compare as gc
from pbd.forecast_v3.pooling import eb_fit

OUT = ROOT / "outputs/p2v3/consistency"
SRC = ROOT / "outputs/p2v3"
PERIODS = {"disc": ("2022-01", "2023-12"), "hold": ("2024-01", "2025-12"),
           "y2022": ("2022-01", "2022-12"), "y2023": ("2023-01", "2023-12"), "y2024": ("2024-01", "2024-12"), "y2025": ("2025-01", "2025-12")}
ALTS = ("B3", "B1")
MIN_N = 5
N_BOOT, N_PERM = 2000, 999


def group_wape(err: pd.DataFrame, w: pd.Series, lab: pd.Series, model: str) -> pd.Series:
    """군 안 금액 가중 WAPE (그 구간에 평가 칸이 있는 성분만, 가중치 다시 정규화)"""
    d = pd.DataFrame({"e": err[f"wape_{model}"], "w": w.reindex(err.index), "g": lab.reindex(err.index)}).dropna()
    return (d.e * d.w).groupby(d.g).sum() / d.w.groupby(d.g).sum()


def sign_rule(delta: pd.DataFrame) -> pd.Series:
    """delta: 군 × 구간(disc, hold, y2023, y2024, y2025). ②~④ 모두 양수"""
    return (delta[["disc", "hold", "y2023", "y2024", "y2025"]] > 0).all(axis=1)


def boot_delta(err: pd.DataFrame, w: pd.Series, lab: pd.Series, alt: str, rng, n_boot=N_BOOT) -> pd.DataFrame:
    d = pd.DataFrame({"eg": err["wape_G"], "ea": err[f"wape_{alt}"],
                      "w": w.reindex(err.index), "lab": lab.reindex(err.index)}).dropna()
    rows = []
    for g, x in d.groupby("lab"):
        idx = rng.integers(0, len(x), size=(n_boot, len(x)))
        ww = x.w.to_numpy()[idx]
        b = ((x.eg.to_numpy()[idx] - x.ea.to_numpy()[idx]) * ww).sum(1) / ww.sum(1)
        rows.append({"group": g, "lo90": np.quantile(b, 0.05), "hi90": np.quantile(b, 0.95)})
    return pd.DataFrame(rows).set_index("group")


def main() -> int:
    import duckdb

    from pbd.forecast_v3.run import load

    seed = yaml.safe_load((ROOT / "config/params.yaml").read_text(encoding="utf-8"))["seed"]
    rng = np.random.default_rng(seed)
    ing = pd.read_csv(SRC / "ingredient_metrics.csv", dtype={"c4": str, "cls3": str, "cls2": str, "cls2_raw": str}).set_index("c4")
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    pred, tgt, drop, panel = load(con)
    errs = {p: gc.ingredient_errors(gc.eval_cells(pred, panel, drop, start=a, end=b)) for p, (a, b) in PERIODS.items()}
    w = ing.w
    groupings = {"atc2_raw": (ing.atc2_raw, ing.atc2_raw.str[0]), "cls2_raw": (ing.cls2_raw, ing.cls2_raw.str[0])}
    out, nulls, pooled = [], [], []
    for gname, (lab, upper) in groupings.items():
        n = lab[lab.index.isin(errs["hold"].index) & lab.index.isin(errs["disc"].index)].value_counts()
        big = n[n >= MIN_N].index
        for alt in ALTS:
            delta = pd.DataFrame({p: group_wape(e, w, lab, "G") - group_wape(e, w, lab, alt) for p, e in errs.items()})
            gw = pd.DataFrame({f"wapeG_{p}": group_wape(e, w, lab, "G") for p, e in errs.items()})
            aw = pd.DataFrame({f"wape{alt}_{p}": group_wape(e, w, lab, alt) for p, e in errs.items()})
            ci = boot_delta(errs["hold"], w, lab, alt, rng)
            eh = errs["hold"]
            share = ((eh[f"wape_{alt}"] < eh.wape_G).groupby(lab.reindex(eh.index)).mean()).rename("share_ingr_alt_better_hold")
            # 부분 풀링(DL): 홀드아웃 성분별 log(G/A)
            lr = np.log(eh.wape_G / eh[f"wape_{alt}"])
            g_codes = pd.Index(sorted(lab.reindex(eh.index).unique()))
            l2 = g_codes.get_indexer(lab.reindex(eh.index))
            top2 = pd.factorize(pd.Series(g_codes.str[0]), sort=True)[0]
            m2s, _, _, dg = eb_fit(lr.to_numpy(), w.reindex(eh.index).to_numpy(), l2, top2, "dl")
            pool = pd.DataFrame({"pooled_lr_hold": m2s, "shrink_b": dg["b_atc2"]}, index=g_codes)
            raw_lr = (lr * w.reindex(eh.index)).groupby(lab.reindex(eh.index)).sum() / w.reindex(eh.index).groupby(lab.reindex(eh.index)).sum()
            t = (delta.add_prefix("delta_").join(gw).join(aw).join(ci).join(share).join(pool).join(raw_lr.rename("raw_lr_hold"))
                 .join(n.rename("n_ingr")).loc[big])
            t["rule_sign"] = sign_rule(delta.loc[big])
            t["rule_ci"] = t.lo90 > 0
            t["consistent"] = t.rule_sign & t.rule_ci
            t["pooled_alt_better"] = t.pooled_lr_hold > 0
            t.insert(0, "alt", alt)
            t.insert(0, "grouping", gname)
            out.append(t.reset_index(names="group"))
            # 우연 기준: 군 표지 섞기(①~④만, 군 크기 유지)
            obs_cnt = int(t.rule_sign.sum())
            labs = lab.to_numpy()
            cnt = np.empty(N_PERM, dtype=int)
            for k in range(N_PERM):
                pl = pd.Series(rng.permutation(labs), index=lab.index)
                dp = pd.DataFrame({p: group_wape(e, w, pl, "G") - group_wape(e, w, pl, alt) for p, e in errs.items()})
                cnt[k] = int(sign_rule(dp.loc[dp.index.isin(big)]).sum())
            nulls.append({"grouping": gname, "alt": alt, "n_groups_eligible": len(big), "observed_sign_pass": obs_cnt,
                          "null_mean": float(cnt.mean()), "null_p95": float(np.quantile(cnt, 0.95)),
                          "p_ge_observed": float((1 + (cnt >= obs_cnt).sum()) / (N_PERM + 1))})
    res = pd.concat(out, ignore_index=True)
    nul = pd.DataFrame(nulls)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / "consistency_groups.csv", index=False, encoding="utf-8-sig", float_format="%.5g")
    nul.to_csv(OUT / "consistency_null.csv", index=False, encoding="utf-8-sig", float_format="%.4g")
    with pd.ExcelWriter(OUT / "p2v3_consistency.xlsx") as xw:
        res.to_excel(xw, sheet_name="consistency_groups", index=False)
        nul.to_excel(xw, sheet_name="consistency_null", index=False)
    summ = {"periods": PERIODS, "min_n": MIN_N, "n_boot": N_BOOT, "n_perm": N_PERM,
            "cells": {p: int(e.n_cells.sum()) for p, e in errs.items()}, "ingr": {p: int(len(e)) for p, e in errs.items()}}
    (OUT / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    write_report()
    return 0


# ---------------------------------------------------------------- 보고서 (숫자는 산출 파일에서 읽음)
REPORT = ROOT / "reports/P2v3_consistency_result.md"
GN = {"atc2_raw": "ATC 2단계", "cls2_raw": "약효분류 2자리"}


def write_report() -> None:
    r = pd.read_csv(OUT / "consistency_groups.csv", dtype={"group": str})
    nul = pd.read_csv(OUT / "consistency_null.csv")
    s = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    ing = pd.read_csv(SRC / "ingredient_metrics.csv", dtype={"c4": str, "cls2_raw": str})
    rep = {g: ing.sort_values("w", ascending=False).groupby(g).ing_name.apply(lambda x: " / ".join(" ".join(str(v).split()) for v in x.head(2)))
           for g in ("atc2_raw", "cls2_raw")}
    pp = lambda x: "" if pd.isna(x) else f"{100 * x:+.1f}%p"
    pc = lambda x: "" if pd.isna(x) else f"{100 * x:.0f}%"
    L = []
    a = L.append
    a("# P2 v3 모델 우위의 시점 일관성: 따로 모델을 둘 만한 군이 있는가 (사후 탐색)\n")
    a("> 성격: P2 v3에 붙이는 사후 탐색. 판정 없음. 기준은 결과를 보기 전에 정했다(`docs/PLAN_P2_v3.md` §12). 예측은 P2 v2 백테스트(h = 6)를 그대로 썼다. "
      "2022 대상월은 P2 v2의 '2022 회복기'(대상·가중치가 그 뒤 자료로 정해짐)다. 실행 `make p2v3-consistency`.\n")
    a("## 1. 답\n")
    tot = r[r.consistent]
    a(f"**미리 정한 기준을 모두 통과한 군은 없다**(ATC 2단계 성분 {s['min_n']}개 이상 {int(nul[(nul.grouping == 'atc2_raw')].n_groups_eligible.iloc[0])}개 군, "
      f"약효분류 2자리 {int(nul[(nul.grouping == 'cls2_raw')].n_groups_eligible.iloc[0])}개 군; 대안 모델 B3·B1). 통과 {len(tot)}개.\n")
    for _, x in nul.iterrows():
        a(f"- {GN[x.grouping]} · {x.alt}: 부호 조건(②~④) 통과 {int(x.observed_sign_pass)}개, 군 표지를 섞었을 때 평균 {x.null_mean:.1f}개(95% 분위 {x.null_p95:.0f}개).")
    a("\n**아깝게 떨어진 군**(B3 쪽, 홀드아웃 2024~2025에서 B3가 낫고 90% 구간이 0 위) — 떨어진 이유가 두 가지다.\n")
    b3 = r[(r.alt == "B3") & (r.delta_hold > 0) & (r.lo90 > 0)].copy()
    b3["why"] = ""
    for i, x in b3.iterrows():
        fails = [k for k, c in (("발견 구간", "delta_disc"), ("2023", "delta_y2023"), ("2024", "delta_y2024"), ("2025", "delta_y2025")) if x[c] <= 0]
        b3.loc[i, "why"] = ", ".join(fails)
    a("| 분류 | 군 | 대표 성분 | 성분 수 | Δ 2022 | Δ 2023 | Δ 2024 | Δ 2025 | Δ 발견 | Δ 홀드아웃 (90% 구간) | 홀드아웃 B3 나은 성분 | 풀링 log(G/B3) 홀드아웃 | 떨어진 조건 |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for _, x in b3.sort_values(["grouping", "delta_hold"], ascending=[True, False]).iterrows():
        a(f"| {GN[x.grouping]} | {x.group} | {rep[x.grouping].get(x.group, '')[:50]} | {int(x.n_ingr)} | {pp(x.delta_y2022)} | {pp(x.delta_y2023)} | "
          f"{pp(x.delta_y2024)} | {pp(x.delta_y2025)} | {pp(x.delta_disc)} | {pp(x.delta_hold)} ({pp(x.lo90)} ~ {pp(x.hi90)}) | "
          f"{pc(x.share_ingr_alt_better_hold)} | {x.pooled_lr_hold:+.3f} | {x.why} |")
    a("\nΔ = 군 G 오차 − 군 B3 오차(군 안 금액 가중 WAPE, 양수 = B3가 나음). 풀링 = 홀드아웃 성분별 log(G/B3)를 DL 경험적 베이즈로 상위 분류 쪽으로 줄인 값(양수 = B3 쪽).\n")
    def row(gr, g):
        return r[(r.grouping == gr) & (r.alt == "B3") & (r.group == g)].iloc[0]
    rr, c22 = row("atc2_raw", "R05"), row("cls2_raw", "22")
    jj, c61 = row("atc2_raw", "J01"), row("cls2_raw", "61")
    a(f"- **2022만 G가 크게 나음**: ATC R05(2022 {pp(rr.delta_y2022)}, 2023~2025 모두 B3 쪽, 홀드아웃 B3 나은 성분 {pc(rr.share_ingr_alt_better_hold)}), "
      f"약효분류 22(2022 {pp(c22.delta_y2022)}). 2022는 코로나 회복기라 이 해의 우열을 다른 해와 같게 보기 어렵다.")
    a(f"- **2023만 아주 조금 G가 나음**: ATC J01(2023 {pp(jj.delta_y2023)}, 2022·2024·2025는 B3 쪽), 약효분류 61(2023 {pp(c61.delta_y2023)}). S01도 같은 모양.")
    a(f"- 이 군들의 B3 우위는 **2024에 가장 크다**(Δ 2024 {pp(b3.delta_y2024.min())} ~ {pp(b3.delta_y2024.max())}). 여러 군이 같은 해에 함께 움직였으므로 "
      "군의 고정된 성질인지 한 시즌의 사건인지 이 자료로는 가를 수 없다(해석).")
    neg = b3[b3.pooled_lr_hold <= 0].group.tolist()
    a(f"- 부분 풀링으로 줄여도 " + (f"{', '.join(neg)}를 뺀 " if neg else "") + "위 군들의 홀드아웃 모델 우열은 B3 쪽으로 남는다(표의 풀링 열"
      + (f"; {', '.join(neg)}는 줄이면 G 쪽" if neg else "") + "). 단 홀드아웃 한 구간의 값이라 일관성의 근거는 아니다.\n")
    a("## 2. 실험에 주는 뜻 (해석, 시험하지 않음)\n")
    a("- 결과를 본 뒤 기준을 풀어(예: 2022 제외, 2023의 작은 역전 허용) R05·J01 등을 고르면 사후 선택이 된다. 이번 자료로는 '따로 ETS를 둘 군'을 확정할 근거가 없다.")
    a("- 고르려면 지금 규칙(성분 수, 구간, 부호, 구간 하한, 2022 처리)을 먼저 고정하고, 이번에 쓰지 않은 새 자료로 확인해야 한다.")
    a("- 고정 군을 따로 두는 대신, 군 효과를 자료 양만큼만 반영하는 부분 풀링이나 시점마다 갱신되는 모델 선택이 이 상황(우위가 일부 해에만 뚜렷함)에 더 맞을 수 있다.\n")
    a("## 3. 미리 정한 기준\n")
    a(f"대안 모델 A ∈ {{B3(ETS), B1(작년 같은 달)}}. ① 성분 {s['min_n']}개 이상 ② 발견 구간 {s['periods']['disc'][0]} ~ {s['periods']['disc'][1]} Δ > 0 "
      f"③ 홀드아웃 {s['periods']['hold'][0]} ~ {s['periods']['hold'][1]} Δ > 0 ④ 2023·2024·2025 모두 Δ > 0 ⑤ 홀드아웃 Δ 90% 구간 하한 > 0(군 안 성분 재표집 {s['n_boot']:,}회). "
      f"우연 기준: 군 표지를 섞어(크기 유지, {s['n_perm']}회) ②~④를 통과하는 군 수.\n")
    a("평가 칸 수: " + ", ".join(f"{k} {v:,}" for k, v in s["cells"].items()) + ".\n")
    a("## 4. 확인 필요\n")
    a("1. 2022(코로나 회복기)를 발견 구간에 넣을지. 빼면 발견 구간이 2023 한 해뿐이다.")
    a("2. 따로 모델을 둘 군을 정하는 규칙을 지금 고정하고, 새 자료가 들어온 뒤 확인할지.\n")
    a("## 5. 출력 파일\n")
    a("- `outputs/p2v3/consistency/p2v3_consistency.xlsx`, `consistency_groups.csv`(군 × 대안 모델 × 구간별 Δ·오차·구간·풀링), `consistency_null.csv`, `summary.json`")
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"썼다: {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
