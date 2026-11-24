"""P2 v3 실험 r2 산출·보고 (PLAN_P2_v3 §13-5 ~ §13-8). exp.main()이 부른다. 숫자는 모두 이번 실행 값."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pbd.common import ROOT
from pbd.forecast_v2.metrics import decompose

OUT = ROOT / "outputs/p2v3/exp"
REPORT = ROOT / "reports/P2v3_exp_result.md"
ARM = {"G": "G", "B3": "B3", "E1": "E1", "E1g": "E1g", "E2": "E2", "E2a": "E2a", "E2b": "E2b", "E2c": "E2c"}
DESC = {"G": "전역 G(기준, cls2 특징 포함)", "E1": "군별 G·ETS 결합", "E1g": "전역 G·ETS 결합(군 없음, 대조)",
        "E2": "전역 G + 군 특징 전부", "E2a": "+ ATC 범주만", "E2b": "+ 군 패턴만", "E2c": "+ 동조 흐름만", "B3": "ETS(참고)"}


def pct(x, d=2):
    return "" if x is None or not np.isfinite(x) else f"{100 * x:.{d}f}%"


def pp(x):
    return "" if x is None or not np.isfinite(x) else f"{100 * x:+.2f}%p"


def _strip(c: dict) -> dict:
    return {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in c.items() if k not in ("d_a", "d_b", "D")}


def save(R: dict, mask: np.ndarray, ev: dict, rep: dict, lock: dict, cfg: dict, out=None, report=None) -> None:
    """out·report: 동작 확인(--smoke)에서만 다른 곳(임시 폴더)으로 보낸다"""
    out = out or OUT
    report = report or REPORT
    out.mkdir(parents=True, exist_ok=True)
    tgt, grp, em, y, w, P = R["tgt"], R["grp"], R["ev_t"], R["y"], R["w"], R["pred"]
    rows = []
    for k, M in P.items():
        df = pd.DataFrame(M, index=tgt.c4, columns=em).stack(future_stack=True).rename("yhat").reset_index()
        df.columns = ["c4", "t", "yhat"]
        rows.append(df.assign(model=k))
    pd.concat(rows).to_csv(out / "predictions.csv", index=False, float_format="%.6g")
    pd.DataFrame(mask, index=tgt.c4, columns=em).to_csv(out / "eval_mask.csv")
    arms = ["G", "E1", "E1g", "E2", "E2a", "E2b", "E2c", "B3"]
    x1 = pd.DataFrame(ev["by_year"]).T[arms]
    x1.to_csv(out / "x1_by_year.csv", float_format="%.6g")
    x4 = []
    for g in sorted(grp.atc1.unique()):
        r = (grp.atc1 == g).to_numpy()
        ww = np.where(r, w, 0.0)
        ww = ww / ww.sum()
        x4.append({"atc1": g, "w_share": float(w[r].sum()), **{a: float(decompose(y, P[a], ww, mask).sum()) for a in arms}})
    x4 = pd.DataFrame(x4)
    x4.to_csv(out / "x4_by_atc1.csv", index=False, float_format="%.6g")
    x5 = pd.DataFrame([{"t": t, "a": v["a"], "alpha_global": v["alpha_global"], "n_used": v["n_used"],
                        "alpha_group_mean": float(np.mean(list(v["alpha_group"].values()))),
                        "share_groups_alpha_gt0": float(np.mean([x > 0 for x in v["alpha_group"].values()])),
                        "b_median": float(np.median(list(v["b_group"].values())))} for t, v in R["winfo"].items()])
    x5.to_csv(out / "x5_e1_weights_by_origin.csv", index=False, float_format="%.6g")
    pd.DataFrame({t: v["alpha_group"] for t, v in R["winfo"].items()}).to_csv(out / "x5_e1_alpha_group.csv", float_format="%.3g")
    x6 = pd.DataFrame({k: pd.DataFrame(v).mean() for k, v in R["imp"].items()})
    x6 = x6 / x6.sum()
    x6.to_csv(out / "x6_e2_gain_share.csv", float_format="%.4g")
    verdict = {"lock": lock, "reproduction": rep, "eval_cells": int(mask.sum()), "eval_months": len(em), "n_target": len(tgt),
               "wape": ev["wape"], "timing_s": R["timing"], "success": ev["success"], "main": {a: _strip(c) for a, c in ev["main"].items()},
               "aux": {a: _strip(c) for a, c in ev["aux"].items()}, "seconds": R["seconds"]}
    (out / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    pd.DataFrame({a: c["D"] for a, c in ev["main"].items()}, index=em).to_csv(out / "D_series.csv", float_format="%.6g")

    # ---------------- 보고서
    M, X, S = ev["main"], ev["aux"], ev["success"]
    ok = [k for k, v in S.items() if v]
    sg = lambda v: "−" if v < 0 else "+"
    L = []
    a = L.append
    a("# P2 v3 실험 r2 결과: 분류군 차이를 쓴 두 방식 대 전역 G\n")
    a("> **먼저 읽을 한계: 완전한 맹검 평가가 아니다.** 평가 기간 2023-01 ~ 2025-12는 P2 v1·v2·v3 탐색이 이미 봤고, E1의 착안(군별 G·ETS 우열)은 "
      "같은 기간 오차에서 나왔으며, 되돌린 r1도 같은 기간을 봤다. 결과는 낙관 쪽으로 기울 수 있다. 군 배정의 ATC는 2026 약가정보(속성표)에서 왔다. "
      "수정본 사용통계로 평가했다. G·LightGBM 설정은 고정(튜닝 없음). 사전등록 `docs/PLAN_P2_v3.md` §13(r2), 태그 "
      f"`{lock['tag']}`({lock['tag_commit'][:7]}).\n")
    a("## 목적 · 성공 기준 · 기대 결과 (사전등록 §13-0)\n")
    a("- 목적: 분류군 사이의 차이를 활용해 전역 G보다 예측력이 높은 모델. G는 이미 약효분류 2자리(cls2)를 특징으로 쓴다.")
    a("- 성공 기준: E1 = E1 대 G '나음'(EWC + Holm(2), δ = 0.5%p, R1·R2 부호) 그리고 E1 대 E1g D̄ < 0. E2 = E2 대 G '나음'.")
    a("- 기대 결과(사전): 둘 다 개선이 작을 것(0.5%p 미만), '판단 보류' 또는 '동등' 가능성이 높음.\n")
    head = ("분류군 정보를 더한 두 방식 모두 G보다 낫다고 할 수 없음" if not ok else "성공 기준을 만족: " + ", ".join(ok))
    a(f"**종합: {head}** — 금액 가중 WAPE G {pct(ev['wape']['G'])}, E1 {pct(ev['wape']['E1'])}, E2 {pct(ev['wape']['E2'])} "
      f"(h = {cfg['h']}, 공개 시차 5개월 가정, 대상월 {em[0]} ~ {em[-1]}, 평가 칸 {int(mask.sum()):,}).\n")
    a("| 주 비교 | ΔWAPE (팔 − G) | 90% 구간 | EWC p | Holm(2) p | 판정 (Holm) | R1 부호 | R2 부호 | 민감도 DM+HLN p | 민감도 Bartlett 12 p | 성공 기준 |")
    a("|---|---|---|---|---|---|---|---|---|---|---|")
    for k in cfg["arms_main"]:
        c = M[k]
        a(f"| {k} {DESC[k]} 대 G | {pp(c['diff'])} | {pp(c['lo90'])} ~ {pp(c['hi90'])} | {c['p']:.3g} | {c['p_holm']:.3g} | {c['verdict_holm']} | "
          f"{sg(c['r1_mean'])} | {sg(c['r2_mean'])} | {c['p_dm']:.3g} | {c['p_b12']:.3g} | {'만족' if S[k] else '불만족'} |")
    a(f"\nδ = {pct(cfg['delta'], 1)}p. ΔWAPE 음수 = 팔이 G보다 오차가 작음. 판정 규칙은 P2 v2 §5-8과 같다.\n")
    a("## 완료 기준\n")
    a("| 기준 | 결과 |\n|---|---|")
    a(f"| 잠금 확인 | 통과: 태그 `{lock['tag']}`의 잠금 파일 {len(lock['locked'])}개와 작업 파일이 같음 |")
    for mname, r in rep.items():
        a(f"| {mname} 재현 | {'통과' if r['ok'] else '불일치'}: 대상월 {r['months']}, {r['cells']:,}칸, 최대 상대 차이 {r['max_rel_diff']:.2e}, 결측 모양 같음 {r['same_nan_pattern']} |")
    a(f"| 평가 칸 | {int(mask.sum()):,} (P2 v2 평가 칸 ∩ 모든 팔 예측) |")
    a("| 시험 | `tests/test_p2v3_exp.py` |\n")
    a("## 보조 (판정 아님)\n")
    a("| 비교 | ΔWAPE | 90% 구간 | EWC p |\n|---|---|---|---|")
    for k, c in X.items():
        x, yv = k.split("_vs_")
        a(f"| {x} 대 {yv} | {pp(c['diff'])} | {pp(c['lo90'])} ~ {pp(c['hi90'])} | {c['p']:.3g} |")
    a("\n**X1 연도별 금액 가중 WAPE**\n")
    a("| 연도 | " + " | ".join(arms) + " |\n|---|" + "---|" * len(arms))
    for yr, r in x1.iterrows():
        a(f"| {yr} | " + " | ".join(pct(r[k]) for k in arms) + " |")
    a("\n**X4 ATC 1단계별 금액 가중 WAPE − G**(군 안 가중치 다시 정규화)\n")
    a("| ATC 1단계 | 가중치 몫 | G | E1 − G | E2 − G | B3 − G |\n|---|---|---|---|---|---|")
    for _, r in x4.iterrows():
        a(f"| {r.atc1} | {pct(r.w_share, 1)} | {pct(r.G)} | {pp(r.E1 - r.G)} | {pp(r.E2 - r.G)} | {pp(r.B3 - r.G)} |")
    a(f"\n- **X5 E1 가중치**: 전역 α(E1g) {x5.alpha_global.min():.1f} ~ {x5.alpha_global.max():.1f}, 군 α 평균 {x5.alpha_group_mean.min():.2f} ~ "
      f"{x5.alpha_group_mean.max():.2f}, α > 0인 군 비율 {pct(x5.share_groups_alpha_gt0.min(), 0)} ~ {pct(x5.share_groups_alpha_gt0.max(), 0)}, "
      f"줄임 계수 b 중앙값 {x5.b_median.min():.2f} ~ {x5.b_median.max():.2f} (`x5_e1_weights_by_origin.csv`, 군별 `x5_e1_alpha_group.csv`).")
    top = x6["E2"].drop(index=[f for f in x6.index if f in ("g1", "g3", "seas", "size", "mon", "dwork", "lunar_t", "lunar_b", "cls2",
                                                              "cov_base", "cov_anchor")], errors="ignore")
    a("- **X6 E2 추가 특징의 gain 몫**(origin 평균): " + ", ".join(f"{i} {pct(v, 1)}" for i, v in top.sort_values(ascending=False).items())
      + f"; cls2 {pct(x6.loc['cls2', 'E2'], 1)} (`x6_e2_gain_share.csv`).")
    a(f"- **X7 B3(ETS) 참고**: WAPE {pct(ev['wape']['B3'])}.\n")
    a("## 문서와 달랐던 점 · 확인 필요\n")
    a("- (실행 뒤 손으로 적는다.)\n")
    a("## 출력 파일\n")
    a("`outputs/p2v3/exp/`: `verdict.json`, `predictions.csv`, `eval_mask.csv`, `D_series.csv`, `x1_by_year.csv`, `x4_by_atc1.csv`, "
      "`x5_e1_weights_by_origin.csv`, `x5_e1_alpha_group.csv`, `x6_e2_gain_share.csv`")
    report.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"썼다: {report}, {out}/verdict.json")
