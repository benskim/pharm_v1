"""P2 최소판 결과 보고서 생성: outputs/p2/*.csv → reports/P2_min_result.md. 숫자는 모두 CSV에서 읽는다 (손으로 옮기지 않는다).
주 결과 = 공개 시차 lag 5, 예측 대상 M+1 (PLAN_P2 §3·§5, 사용자 결정 2026-11-18). lag 0은 참고(부록 A), lag 3은 제외. 여섯 달은 부록 B.
사용: python -I scripts/p2_min_report.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
O = ROOT / "outputs/p2"
MAIN = 5
APPX = (0,)
CONCLUSION = ("이 설정(성분 32개, 신호가 0이 아닌 칸 2~11%, 전국 단위, 공개 시차 5개월)에서는 다음 달 예측에서 M2도 12개월 전 값(M0)보다 나빴고, "
              "선행 신호 특징(M3)은 M2를 개선하지 못했다. 주입 시험에서 20% 효과도 일부만 검출되어, 신호 효과를 검출할 힘이 부족했다. "
              "실제 사건을 미리 넣은 오라클과 시점 이동(−3~+3개월)으로도 개선되지 않았고, 청구금액을 목표로 한 오라클에서도 개선이 없었다. "
              "가격 몫이 금액 변화 분산의 3~4%에 그쳐 실제 가격 변화를 미리 알아도 개선 상한이 2% 안팎이었다. "
              "신호가 있는 학습 행이 적은 것(27~334행)도 원인일 수 있으나, 따로 검증하지 않았다.")   # 사용자가 고정한 결론 (2026-11-19 2차 교체, 검토 F-3~F-6 수용). 숫자는 main()에서 계산값과 대조
MODELS = ("M0", "M2", "M3", "M3p")


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def src(*names: str) -> str:
    return "출처: " + ", ".join(f"`outputs/p2/{n}`" for n in names)


def verdict_ko(v: str) -> str:
    return v.replace("가 나음", "이(가) 나음")


def extra_checks(w, src, pct) -> None:
    """추가 확인 A·B·C (2026-11-19 사용자 지시). 주 결과·결론 문장과 따로, 판단 기준은 DECISIONS에 먼저 기록"""
    sen = pd.read_csv(O / "extra_revision_sensitivity.csv")
    rv = pd.read_csv(O / "extra_revision_verdict.csv")
    hz = pd.read_csv(O / "extra_horizon.csv")
    hv = pd.read_csv(O / "extra_horizon_verdict.csv").iloc[0]
    t2s = pd.read_csv(O / "extra_top200_steps.csv")
    t2e = pd.read_csv(O / "extra_top200_errors.csv").set_index(["model", "lag"])
    t2c = pd.read_csv(O / "extra_top200_compare.csv").set_index(["lag", "a", "b"])
    t2n = pd.read_csv(O / "extra_top200_nonzero.csv").set_index("lag")["nonzero"]
    t2v = pd.read_csv(O / "extra_top200_coverage.csv").iloc[0]
    e32 = pd.read_csv(O / "t3_errors.csv")
    e32 = e32[e32["part"] == "전체"].set_index(["model", "lag"])
    c32 = pd.read_csv(O / "t3_compare.csv").set_index(["lag", "a", "b"])
    x32 = pd.read_csv(O / "exp_compare.csv").set_index(["lag", "a", "b"])
    w("## 추가 확인 (2026-11-19, 주 결과와 결론 문장은 바꾸지 않음. 판단 기준은 실행 전에 DECISIONS에 기록)\n")
    w("### A. 사용통계 사후 수정 — **사용자 가설, 판본 하나뿐이라 간접 시험만 함**\n")
    w("가설: \"공개된 지 얼마 안 된 달의 사용통계는 덜 집계되어 있고, 나중에 위로 수정된다.\" 같은 달을 다른 시점에 받은 판본이 없어 수정 크기는 직접 잴 수 없다.\n")
    w("**a. 민감도**: 평가 정답에서 분석 끝 기준 마지막 12개월(2025-01 ~ 12)을 뺀 오차.\n")
    w("| lag | 모델 | 전체 MASE | 전체 WAPE | 마지막 12개월 뺀 MASE | 마지막 12개월 뺀 WAPE | 칸 (전체 → 뺀 뒤) |\n|---|---|---|---|---|---|---|")
    for (lag, m), g in sen.groupby(["lag", "model"]):
        a_, b_ = g[g.part == "전체"].iloc[0], g[g.part != "전체"].iloc[0]
        w(f"| {lag} | {m} | {a_.mase:.3f} | {pct(a_.wape)} | {b_.mase:.3f} | {pct(b_.wape)} | {int(a_.n)} → {int(b_.n)} |")
    w(f"\n{src('extra_revision_sensitivity.csv')}.\n")
    w("**b. 간접 흔적**: 작년 같은 달 대비 비율 r = y_m ÷ y_{m−12}의 분포를 최근 12개월과 오래된 달(2023-01 ~ 2024-12)에서 비교. "
      "판단(기본값, 사용자가 바꿀 수 있음): 최근 3개월 r 중앙값 ÷ 오래된 달 r 중앙값 − 1(하락률)의 단위 간 중앙값 ≤ −5% 그리고 하락률 < 0인 단위 ≥ 70%이면 '덜 집계 흔적 있음'.\n")
    w("| 단위 | 단위 수 | 하락률 중앙값 | 하락한 단위 비율 | 판정 |\n|---|---|---|---|---|")
    for x in rv.itertuples():
        w(f"| {x.unit} | {x.units} | {100 * x.median_drop:+.1f}% | {pct(x.share_negative)} | **{x.verdict}** |")
    w(f"\n![작년 같은 달 대비 비율](figures/p2_revision_ratio.png)\n\n{src('extra_revision_verdict.csv', 'extra_revision_by_k.csv')}. "
      "**이 비교는 실제 수요 감소와 덜 집계를 구분하지 못한다.** 하락 방향은 단위의 다수에서 보였지만 판단 기준(−5%)에는 미치지 않았다.\n")
    w("**c. 직접 비교 준비**: `scripts/p2_usage_vintage_diff.py`(합성 자료 시험 `tests/test_p2_vintage_diff.py`). 같은 달의 두 판본을 성분 × 월·약효분류군 × 월로 비교해 "
      "공개 후 경과 개월별 수량·금액 차이(절대·%)를 낸다. 사용자가 MEDICINE_202509 ~ 202512를 다시 받아 넣으면 2026-10-28 판본과 비교한다(지금은 실행하지 않음).\n")
    w("### B. 몇 개월 앞까지 믿을 만한가 (공개 시차 0, origin 2023-01 ~ 2024-12 고정, 성분 32)\n")
    p_ = hz.pivot_table(index="h", columns="model", values=["wape", "mase"])
    p_ = hz.pivot_table(index="h", columns="model", values=["wape", "mase", "wape_amt"])
    w("| 몇 개월 앞 | M0 WAPE | M2 WAPE | M2가 M0보다 나은가 | M0 / M2 청구금액 가중 WAPE | 가중으로 M2가 나은가 | M0 MASE | M2 MASE |\n|---|---|---|---|---|---|---|---|")
    for h in p_.index:
        w(f"| {h} | {pct(p_.loc[h, ('wape', 'M0')])} | {pct(p_.loc[h, ('wape', 'M2')])} | {'예' if p_.loc[h, ('wape', 'M2')] < p_.loc[h, ('wape', 'M0')] else '아니오'} | "
          f"{pct(p_.loc[h, ('wape_amt', 'M0')])} / {pct(p_.loc[h, ('wape_amt', 'M2')])} | {'예' if p_.loc[h, ('wape_amt', 'M2')] < p_.loc[h, ('wape_amt', 'M0')] else '아니오'} | "
          f"{p_.loc[h, ('mase', 'M0')]:.3f} | {p_.loc[h, ('mase', 'M2')]:.3f} |")
    w(f"\n![몇 개월 앞 오차](figures/p2_horizon_curve.png)\n\n{src('extra_horizon.csv', 'extra_horizon_verdict.csv')}. "
      f"가설 '1개월 앞 대비 WAPE가 상대 10% 이상 커지는 첫 개월 수가 4개월 이상': 첫 개월 수 = **{int(hv.first_h_10pct)}** → **{'채택' if hv.first_h_10pct >= 4 else '기각'}**. "
      f"M2가 M0를 이기는 마지막 개월 수 = **{int(hv.last_h_m2_beats_m0)}**(1개월부터 끊김 없이 {int(hv.last_contiguous_h_m2_beats_m0)}개월). "
      + ("청구금액으로 가중해도 M2가 M0를 이기는 개월은 같다.\n" if ((p_[("wape", "M2")] < p_[("wape", "M0")]) == (p_[("wape_amt", "M2")] < p_[("wape_amt", "M0")])).all()
         else "**청구금액으로 가중하면 M2가 M0를 이기는 개월이 달라진다** (표 참고).\n"))
    w("### C. 상위 200개 성분 (약효분류 묶음 조건 없이, 96개월 관측 + 2022 원외 청구수량 상위 200)\n")
    w("| 단계 | 성분4 | 탈락 |\n|---|---|---|")
    for x in t2s.itertuples():
        w(f"| {x.step} | {x.n} | {'' if pd.isna(x.dropped) else int(x.dropped)} |")
    w(f"\n대상 200개 = 2022 원외 청구금액의 {pct(t2v.amt_share)}. 신호가 0이 아닌 칸: " + ", ".join(f"lag {l} {pct(v)}" for l, v in t2n.items()) + ".\n")
    w("| lag | 비교 | 성분 32: 상대 차이 (Holm 뒤) | 성분 200: 상대 차이 (Holm 뒤) |\n|---|---|---|---|")
    lab = lambda v: v.replace("E1_null", "E1").replace("E4_M3_2022", "M3").replace("E4_M2_2022", "M2")   # noqa: E731
    for lag in (MAIN, 0):
        for a_, b_, nm, src32 in (("M2", "M0", "M2 − M0", c32), ("M3", "M2", "M3 − M2", c32), ("E1_null", "M2", "E1 − M2 (빈칸 시기 표시)", x32),
                                  ("M3", "E1_null", "M3 − E1 (신호 내용)", x32), ("E4_M3_2022", "E4_M2_2022", "같은 학습 기간 M3 − M2", x32)):
            r32, r200 = src32.loc[(lag, a_, b_)], t2c.loc[(lag, a_, b_)]
            w(f"| {lag} | {nm} | {100 * r32.rel_diff:+.1f}% ({lab(r32.verdict_holm)}) | {100 * r200.rel_diff:+.1f}% ({lab(r200.verdict_holm)}) |")
    w("\n| lag | 모델 | 성분 32 MASE / WAPE | 성분 200 MASE / WAPE |\n|---|---|---|---|")
    for lag in (MAIN, 0):
        for m in ("M0", "M2", "M3"):
            w(f"| {lag} | {m} | {e32.loc[(m, lag), 'mase']:.3f} / {pct(e32.loc[(m, lag), 'wape'])} | {t2e.loc[(m, lag), 'mase']:.3f} / {pct(t2e.loc[(m, lag), 'wape'])} |")
    m20_200, m32_200 = t2c.loc[(MAIN, "M2", "M0")], t2c.loc[(MAIN, "M3", "M2")]
    keep_signal = m32_200.verdict_holm != "M3가 나음"
    keep_m0 = m20_200.mase_a > m20_200.mase_b
    w(f"\n{src('extra_top200_steps.csv', 'extra_top200_errors.csv', 'extra_top200_compare.csv')}. "
      f"**결론이 유지되는가**: 신호 특징이 개선하지 못한다는 부분은 {'유지된다' if keep_signal else '유지되지 않는다'}(lag 5 M3 − M2 {100 * m32_200.rel_diff:+.1f}%, {lab(m32_200.verdict_holm)}). "
      f"M2가 M0보다 나쁘다는 부분은 {'유지된다' if keep_m0 else '**유지되지 않는다**: 성분 200개에서는 lag 5에서도 M2가 M0보다 나았다'}"
      f"(MASE {m20_200.mase_a:.3f} 대 {m20_200.mase_b:.3f}). 결론 문장은 바꾸지 않는다(사용자 지시).\n")
    # D. 층화 200 + 세 대상 가중·비가중 (추가 지시 A·B)
    sv = pd.read_csv(O / "extra_strat200_coverage.csv").iloc[0]
    sst = pd.read_csv(O / "extra_strat200_strata.csv")
    se = pd.read_csv(O / "extra_strat200_errors.csv").set_index(["model", "lag"])
    scc = pd.read_csv(O / "extra_strat200_compare.csv").set_index(["lag", "a", "b"])
    w32 = pd.read_csv(O / "extra_weighted_32.csv").set_index(["model", "lag"])
    w("### D. 층화 200개 성분 (추가 지시 B, 규칙은 실행 전에 DECISIONS에 기록)\n")
    w(f"대상 = 96개월 원외 모두 관측 성분 {int(sv.eligible)}개(2022 원외 청구금액의 {pct(sv.eligible_amt_share)}, **생존 편향은 그대로**). 층 = 약효분류번호 앞 2자리 × 2022 청구금액 3분위 × 업체 수 구간(3~5, 6~10, 11+). "
      f"층 {int(sv.strata)}개, 층마다 최소 1개, 크기 비례 배분, 층 안 시드 고정 무작위 추출. 뽑힌 {int(sv.n)}개 = 2022 원외 청구금액의 {pct(sv.amt_share)}. **상위 200개(C)와 겹치는 성분 {int(sv.overlap_top200)}개**.\n")
    big = sst.sort_values("eligible", ascending=False).head(8)
    w("층별 성분 수 (대상이 많은 8개 층, 전체는 CSV): " + "; ".join(f"{r.stratum} {int(r.picked)}/{int(r.eligible)}" for r in big.itertuples()) + f" ({src('extra_strat200_strata.csv', 'extra_strat200_coverage.csv')}).\n")
    w("**세 대상 비교** (다음 달, 비가중 WAPE / 2022 청구금액 가중 WAPE):\n")
    w("| 대상 | lag | M0 | M2 | M3 | M2 < M0 (비가중 / 가중) | M3 − M2 (Holm 뒤) | M3 < M2 (비가중 / 가중) |\n|---|---|---|---|---|---|---|---|")
    yn = lambda b: "예" if b else "아니오"   # noqa: E731
    changed = []
    for name, ee, cc in (("성분 32", w32, c32), ("상위 200", t2e, t2c), ("층화 200", se, scc)):
        for lag in (MAIN, 0):
            v = {m: (ee.loc[(m, lag), "wape"], ee.loc[(m, lag), "wape_amt"]) for m in ("M0", "M2", "M3")}
            a0, a1 = v["M2"][0] < v["M0"][0], v["M2"][1] < v["M0"][1]
            b0, b1 = v["M3"][0] < v["M2"][0], v["M3"][1] < v["M2"][1]
            if a0 != a1 or b0 != b1:
                changed.append(f"{name} lag {lag}")
            w(f"| {name} | {lag} | " + " | ".join(f"{pct(v[m][0])} / {pct(v[m][1])}" for m in ("M0", "M2", "M3"))
              + f" | {yn(a0)} / {yn(a1)} | {100 * cc.loc[(lag, 'M3', 'M2')].rel_diff:+.1f}% ({lab(cc.loc[(lag, 'M3', 'M2')].verdict_holm)}) | {yn(b0)} / {yn(b1)} |")
    w(f"\n{src('extra_weighted_32.csv', 'extra_top200_errors.csv', 'extra_strat200_errors.csv', 'extra_strat200_compare.csv')}. "
      "가중 WAPE = 성분별 WAPE를 2022 원외 청구금액 비중으로 가중 평균(새 학습 없음, 상위 200·1~12개월은 같은 코드·시드로 다시 돌려 비가중 값이 이전과 같음을 확인).\n")
    w("**가중 전후로 결론이 달라지는가**: " + ("달라지지 않는다(세 대상 × 두 lag에서 'M2가 M0를 이기는가', 'M3가 M2를 개선하는가'의 답이 같다)." if not changed
                                        else "달라지는 곳: " + ", ".join(changed) + "."))
    m20s, m32s = scc.loc[(MAIN, "M2", "M0")], scc.loc[(MAIN, "M3", "M2")]
    w(f"\n**결론이 유지되는가 (층화 200)**: 신호 특징이 개선하지 못한다는 부분은 {'유지된다' if m32s.verdict_holm != 'M3가 나음' else '유지되지 않는다'}"
      f"(lag 5 M3 − M2 {100 * m32s.rel_diff:+.1f}%, {lab(m32s.verdict_holm)}). M2가 M0보다 나쁘다는 부분은 "
      f"{'유지된다' if m20s.mase_a > m20s.mase_b else '**유지되지 않는다**: lag 5에서도 M2가 M0보다 나았다'}(MASE {m20s.mase_a:.3f} 대 {m20s.mase_b:.3f}). 결론 문장은 바꾸지 않는다.\n")
    w("**200개로도 남는 한계**: 96개월 연속 관측 조건의 생존 편향, 업체 2곳 이하 성분은 사용통계에서 비공개(제품 하나의 변화가 성분 합계를 움직일 곳이 빠짐), "
      "성분 합계라 성분 안의 처방 이동이 안 보임, 예측 개선 여부로 효과를 재는 시험의 낮은 검출력.\n")
    w("**확인 (추가 지시 C)**: 사용통계에 제품(품목) 단위 처방량은 **없다**. 원본 96개 파일의 헤더가 모두 같은 11개 칸(STD_YYYYMM, WK_COMPN_4, AGE_G, SEX_TYPE, SIDO, "
      "YOYANG_CLSFC_CD_ADJ, OUT_IN_HOS, PRSCRPTN_TNDN_CNT, PRSCRPTN_AMT, PATIENT_CNT, PRSCRPTN_TOT_USE)이고 가장 세분된 코드는 주성분 4자리다.\n")


def main() -> None:
    steps = pd.read_csv(O / "t1_target_steps.csv")
    re_ = pd.read_csv(O / "t1_reagg_check.csv").iloc[0]
    cov = pd.read_csv(O / "t1_coverage.csv").iloc[0]
    tgt = pd.read_csv(O / "t1_target.csv", dtype={"c4": str})
    err = pd.read_csv(O / "t3_errors.csv")
    cmp_ = pd.read_csv(O / "t3_compare.csv")
    sim = pd.read_csv(O / "t3_order_sim.csv")
    fe = pd.read_csv(O / "t3_features.csv", dtype={"c4": str})
    pred = pd.read_csv(O / "t3_pred.csv", dtype={"c4": str})
    amt = pd.read_csv(O / "t3_compare_claim_amt.csv")
    exc = pd.read_csv(O / "exp_compare.csv")
    exn = pd.read_csv(O / "exp_null_seed.csv")
    exi = pd.read_csv(O / "exp_inject.csv")
    exm = pd.read_csv(O / "exp_mase_by_model.csv").set_index("lag")
    e1c = pd.read_csv(O / "exp_e1_constant.csv").set_index("lag")
    hor = pd.read_csv(O / "t3_horizons.csv")
    dgc = pd.read_csv(O / "diag_compare.csv")
    dgv = pd.read_csv(O / "diag_coverage.csv").set_index("lag")
    dgm = pd.read_csv(O / "diag_mase.csv").set_index("lag")
    dga = pd.read_csv(O / "diag_compare_claim_amt.csv")
    ock = pd.read_csv(O / "oracle_check.csv").set_index("lag")
    ocr = pd.read_csv(O / "oracle_check_train.csv")
    L: list[str] = []
    w = L.append
    tot = err[err["part"] == "전체"].pivot_table(index="model", columns="lag", values=["mase", "wape"])
    c = cmp_.set_index(["lag", "a", "b"])
    m32, m20 = c.loc[(MAIN, "M3", "M2")], c.loc[(MAIN, "M2", "M0")]

    w("# P2 최소판 결과 (T1 · T2 · T3)\n")
    w("> 숫자는 `scripts/p2_min_report.py`가 `outputs/p2/`의 CSV(이번 실행 산출, 표마다 출처를 적음)에서 읽어 쓴 것이다. "
      "설계: [PLAN_P2](../docs/PLAN_P2.md) §7 최소판, 결정: [DECISIONS](../docs/DECISIONS.md) \"P2 최소판\", 검토: [REVIEW](REVIEW_2026-11-18.md)·[WORKLOG](WORKLOG_2026-11-18.md).\n")
    nzr = fe.groupby("lag")["has_signal"].mean()
    assert 0.015 <= nzr.min() and nzr.max() <= 0.115, f"고정 결론의 '2~11%'와 계산값이 다르다: {nzr.to_dict()}"
    assert len(tgt) == 32, "고정 결론의 '성분 32개'와 대상 수가 다르다"
    # 고정 결론의 숫자와 계산값 대조 (가격 몫 '약 3%', 개선 상한 '2% 안팎', 시점 이동 −3~+3, 오라클·시점 이동 '개선되지 않음')
    assert ock["price_share"].between(0.03, 0.04).all(), f"'3~4%'와 다르다: {ock['price_share'].to_dict()}"
    assert m20.mase_a > m20.mase_b, "'M2도 12개월 전 값(M0)보다 나빴고'(시차 5)와 다르다"
    o5 = ocr[ocr["lag"] == MAIN]
    assert (o5["rows_price_nonzero"].min(), o5["rows_price_nonzero"].max()) == (27, 334), f"'27~334행'과 다르다: {o5['rows_price_nonzero'].min()}~{o5['rows_price_nonzero'].max()}"
    assert ock["improvement_ceiling"].between(0.012, 0.028).all(), f"'2% 안팎'과 다르다: {ock['improvement_ceiling'].to_dict()}"
    assert np.allclose(ock["improvement_ceiling"], 1 - np.sqrt(1 - ock["price_share"]))
    offs = sorted(int(a.replace("M3_off", "")) for a in dgc["a"] if a.startswith("M3_off"))
    assert offs[0] == -3 and offs[-1] == 3, offs
    better = lambda c: c[(c["verdict_holm"] == c["a"] + "가 나음")]   # noqa: E731
    assert better(dgc[dgc["a"].str.startswith(("M3o", "M3_off"))]).empty, "오라클·시점 이동 중 개선된 것이 있다 — 결론 문장과 다름"
    assert better(dga[dga["a"].str.startswith("M3o")]).empty, "금액 오라클이 개선됐다 — 결론 문장과 다름"
    w(f"**결론**: {CONCLUSION}\n")
    w("**주 결과의 숫자 (공개 시차 5개월, 예측 대상 = 발간월 M의 다음 달 M+1)**: 전역 모델 M2의 MASE "
      f"{m20.mase_a:.3f}는 12개월 전 값 M0 {m20.mase_b:.3f}보다 나빴다 (M2가 나은 성분 {int(m20.a_better_series)}/{int(m20.n_series)}). "
      f"P1 신호 특징을 넣은 M3 {m32.mase_a:.3f}는 M2보다 나빴다 (M3가 나은 성분 {int(m32.a_better_series)}/{int(m32.n_series)}, Holm 보정 뒤 판정: {m32.verdict_holm}). "
      "이 악화의 약 절반은 신호 내용이 아니라 신호 열의 결측 구조(2022 이전 NaN)가 만든 시기 표시에서 왔다(§5). "
      "신호가 0이 아닌 칸 비율은 " + ", ".join(f"lag {k} {pct(v)}" for k, v in nzr.items()) + f" ({src('t3_features.csv')}). "
      "공개 시차를 0으로 둔 이상적 가정(부록 A)에서만 M2가 M0보다 나았다.\n")

    w("## 먼저 읽을 한계\n")
    w("- **전국 성분 단위의 원외 청구수량 예측**이다. 약국 단위 구매·재고·판매, 제품별 판매량, 도매 유통은 데이터에 없다.")
    w(f"- **대상 선정에 평가 기간의 정보가 들어 있다.** 대상 {len(tgt)}개는 2018-01 ~ 2025-12 96개월 내내 관측된 성분이다(평가 끝까지 살아남은 성분, 생존 편향). "
      "주력 묶음은 2022~2025 겨울/여름 비율로 골랐고, 규모 조건은 2022 원외 청구수량이다(lag 5의 첫 origin 2023-01은 2022-08까지만 볼 수 있다). "
      "진입·퇴출·비공개 전환처럼 어려운 사례가 빠져 결과가 실제보다 좋게 나올 수 있다.")
    w(f"- 대상은 2022 원외 청구금액의 {pct(cov['amt_share'])}, 수량의 {pct(cov['qty_share'])}다. 업체 2곳 이하 성분은 사용통계에 없다.")
    w("- 사용통계는 2026-11-01에 받은 **수정본**으로 학습·평가했다. 실제 발간 시점에는 초본이라 결과가 낙관적일 수 있다 (BACKLOG X11). 시험으로는 잡을 수 없다.")
    w("- P1 규칙(v5.3)은 2022~2025 전체 결과와 holdout 불일치를 보고 고쳤다. as-of 재빌드는 같은 규칙을 과거 스냅샷으로 다시 돌릴 뿐이라, 규칙을 고를 때 쓴 미래 지식은 남는다. "
      "외부 근거(경보제약)를 어떤 사례에 넣을지도 결과를 보고 골랐다. 미리 잠가 둔 시간 holdout은 없다.")
    w("- 하이퍼파라미터·특징·창은 결과를 보기 전에 정했고 조정하지 않았다. 아래 진단 실험은 원인 후보를 가르는 데만 쓰고 주 모델을 바꾸지 않았다.")
    w("- 검정은 성분 32개의 평균 오차 차이에 대한 것이다. 성분들은 같은 origin을 공유해 서로 독립이 아니므로 p값은 낙관적일 수 있다.\n")

    w("## 집행정지로 막힌 신호와 미결의 처리 (지시 3)\n")
    w("- 원장의 '미결(집행정지 중)'·'미실현'은 뒤 스냅샷으로 정해지는 값이다. 특징에 쓰면 누수라 쓰지 않는다 (`src/pbd/ledger/posthoc.py`, 시험 `test_p2_posthoc.py`).")
    w("- 대신 **origin 시점 목록에 집행정지 표시가 보이는 제품**(표시 첫 달 ≤ s < 표시가 처음 사라진 달)의 약가·급여중지 예고를 '실현될 인하·중지'로 넣지 않는다.")
    w("- 외부 근거로만 넣은 집행정지(경보제약, 결정 2025-07-09)는 결정일 다음 달 스냅샷(2025-08)부터 보인 것으로 친다. 그래서 2025-06·2025-07 origin에서는 경보제약 급여중지 예고가 '막히지 않은 예고'로 들어간다. "
      "그 시점에 알 수 있던 정보가 그랬기 때문이다.")
    w("- 제품 상태는 `state_as_known`만 쓴다(뒤 스냅샷으로 고쳐 쓴 `revised` 칸을 읽지 않음). 최종 결과로 거르면 특징이 달라진다는 것을 누수 주입 시험으로 확인했다(아래).\n")

    w("## 가상 재고 시뮬레이션의 가정 (지시 4, 결과보다 먼저)\n")
    w("- **전국 성분 단위 가상 재고 시뮬레이션**이다. 약국 재고 최적화가 아니다. 시도 단위도 아니다(PLAN_P2 §6은 시도 × 성분).")
    w("- 결품 비용 / 재고 비용 = **1, 3, 9 (가정값)** → 목표 재고 = 분위 τ = 0.5, 0.75, 0.9 예측. 비용 비율마다 따로 학습한다.")
    w("- **달마다 독립(이월 없음)**: 그 달 목표 재고 S로 그 달 수요 D를 맞고, 남은 재고는 다음 달로 넘기지 않는다. PLAN_P2 §6의 '발주 = 목표 − 현재 재고'와 다르다.")
    w("- 리드타임 1개월 = origin M에 정한 S가 M+1에 쓰인다. 실제 수요 = 사용통계 실측(수정본).")
    w("- 정책: 기준 M0 × 1.1(안전계수 0.1, 가정값) / **M0 분위**(y_{t−12} × 과거 계절 비율 y_u/y_{u−12}의 τ 분위. 분위 형태의 기준이지만 포함률은 보정하지 않았다. 비율 이력은 M − lag까지라 lag마다 값이 다르다) / M2 분위·M3 분위(LightGBM 분위 회귀). "
      "포함률(실수요 ≤ S인 칸 비율)이 τ 근처면 분위가 맞은 것이다.\n")

    w("## 완료 기준별 통과 여부\n")
    w("| 단계 | 완료 기준 | 결과 |\n|---|---|---|")
    ok_re = re_["only_raw"] == 0 and re_["only_mart"] == 0 and re_["diff_cells"] == 0
    w(f"| T1 | 원본 재집계 합 = 마트 합 | {'통과' if ok_re else '실패'}: 원본 CSV 96개를 따로 읽어 성분4 × 월 원외 합 {int(re_['both_cells']):,}칸 비교, "
      f"차이 {int(re_['diff_cells'])}칸, 한쪽에만 있는 칸 {int(re_['only_raw'] + re_['only_mart'])} ({src('t1_reagg_check.csv')}) |")
    w("| T1 | 비공개 칸은 미관측 | 통과: 원본 행이 없는 칸은 claim_qty = NULL, observed = false (합성 시험) |")
    w("| T1 | 첫 관측월 제외 규칙 시험 | 통과: 자료 시작월이 아닌 첫 관측월은 usable = false (합성 시험) |")
    w("| T1 | 대상 선정 탈락 단계표 | 아래 §1 |")
    w("| T2 | 누수 시험 (미래 행을 바꾸거나 지워도 과거 origin 예측 불변, 허용오차 0) | 통과: M0(origin 3개 × lag 0·3·5), M2(lag 0·3·5). 학습 목표 달 ≤ M − lag 시험. "
      "**누수 주입 3가지(자르지 않은 입력 / lag 무시 / 공백 구간 실측을 학습 목표로)를 시험이 잡음** |")
    w("| T3 | origin 이후 스냅샷을 지워도 특징·예측이 같음 | 통과: 2023-06·2025-07·2025-10 이하 스냅샷만으로 v5 원장을 다시 만들어(build_asof) 특징 비교(lag 0·3·5, 허용오차 0). "
      "2025-07·2025-10에서는 **M3 학습 행 전체(2022-01 ~ origin)의 특징과 M3 예측**까지 같음(lag 0·5). 누수 주입 2가지(s 뒤 신호 사용 / 최종 결과로 거르기)를 시험이 잡음 |")
    w("| T3 | M3 대 M2 (origin 2023-01 이후) | 아래 §3. 유의하지 않으면 \"차이 없음\" |")
    files = ["tests/test_p2_t1.py", "tests/test_p2_t2.py", "tests/test_p2_t3.py", "tests/test_p2_posthoc.py"]
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", *files], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    last = [x for x in r.stdout.strip().splitlines() if x.strip()][-1]
    w(f"| 시험 | P2 시험 4개 파일 (사후 칸 시험 `test_p2_posthoc.py` 포함) | 이번 실행: `{last.strip('= ')}`. 전체 `make test`는 WORKLOG |\n")

    w("## 1. T1 대상 선정 탈락 단계\n")
    w("| 단계 | 성분4 | 탈락 |\n|---|---|---|")
    for x in steps.itertuples():
        w(f"| {x.step} | {x.n_c4} | {'' if pd.isna(x.dropped) else int(x.dropped)} |")
    by = tgt.groupby("cls").size()
    w("\n대상 분류별: " + ", ".join(f"{k} {v}개" for k, v in by.items()) + ". " + src("t1_target_steps.csv", "t1_target.csv", "t1_coverage.csv") + "\n")

    w(f"## 2. 주 결과: lag {MAIN} 오차 (origin 2023-01 ~ 2025-11, 35개 × 성분 {len(tgt)}, 예측 대상 M+1)\n")
    n = int(err[(err["part"] == "전체") & (err["model"] == "M2") & (err["lag"] == MAIN)]["n"].iloc[0])
    w32m = pd.read_csv(O / "extra_weighted_32.csv").set_index(["model", "lag"])
    w("| 모델 | MASE | WAPE | 청구금액 가중 WAPE |\n|---|---|---|---|")
    for m in MODELS:
        ww = pct(w32m.loc[(m, MAIN), "wape_amt"]) if (m, MAIN) in w32m.index else "—"
        w(f"| {m} | {tot.loc[m, ('mase', MAIN)]:.3f} | {pct(tot.loc[m, ('wape', MAIN)])} | {ww} |")
    w(f"\n평가 칸 {n:,}개. M0 = 12개월 전 값, M2 = LightGBM 전역(목표 log(y_t / y_a), a = 마지막 관측월), M3 = M2 + 신호(기준 스냅샷 M), "
      "M3p = 기준 스냅샷 M−1(보수적 민감도). MASE 척도 = 성분마다 2018-01 ~ 2022-12의 계절(12) 단순 예측 평균 절대오차(고정). 달력상 첫 origin 이전이지만 lag 5의 첫 origin(2023-01)에서는 2022-09 ~ 12가 아직 공개 전이다. 예측에는 쓰지 않는 지표 척도라 예측 누수는 아니다. " + src("t3_errors.csv") + "\n")
    s = err[(err["part"] != "전체") & (err["lag"] == MAIN)].copy()
    s["구분"] = s.apply(lambda x: ("유행기(11~3월)" if x["flu"] == True else "비유행기") if x["part"].startswith("유행기")  # noqa: E712
                       else ("origin에 창 안 신호 있음" if x["has_signal"] == True else "신호 없음"), axis=1)  # noqa: E712
    p = s.pivot_table(index="구분", columns="model", values="mase")
    nn = s[s["model"] == "M2"].set_index("구분")["n"]
    w("| 구분 | 칸 | M0 | M2 | M3 | M3p |\n|---|---|---|---|---|---|")
    for g, x in p.iterrows():
        w(f"| {g} | {int(nn[g])} | " + " | ".join(f"{x[m]:.3f}" for m in MODELS) + " |")
    w("")

    w("## 3. M3 대 M2, M2 대 M0 (검정)\n")
    w("성분별 평균 MASE 차이(a − b, 음수 = a가 나음)에 부호 검정·Wilcoxon 부호 순위 검정. 두 검정 모두 p < 0.05일 때 차이로 쓴다. "
      f"Holm 보정은 부호 검정과 Wilcoxon 검정에 따로, 각각 이 표의 비교 {len(cmp_)}개를 한 묶음으로 했다. Holm 뒤 판정 = 보정된 두 p가 모두 0.05 미만. "
      f"§4 금액 표는 그 표 {len(amt)}개, §5 진단 표는 진단 비교 {len(exc)}개, §5b 표는 {len(dgc)}개를 각각 한 묶음으로 보정했다. 상대 차이 = a MASE ÷ b MASE − 1.\n")
    w("| lag | a − b | a MASE | b MASE | 상대 차이 | a가 나은 성분 | 부호 p | Wilcoxon p | 부호 p (Holm) | Wilcoxon p (Holm) | 판정 | Holm 뒤 판정 |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    for x in cmp_.sort_values("lag", key=lambda k: k.map({MAIN: 0, 0: 1, 3: 2})).itertuples():
        w(f"| {x.lag}{' (주)' if x.lag == MAIN else ' (참고)'} | {x.a} − {x.b} | {x.mase_a:.3f} | {x.mase_b:.3f} | {100 * x.rel_diff:+.1f}% | {x.a_better_series}/{x.n_series} | "
          f"{x.sign_p:.4f} | {x.wilcoxon_p:.4f} | {x.sign_p_holm:.4f} | {x.wilcoxon_p_holm:.4f} | {x.verdict} | {x.verdict_holm} |")
    hs = fe.groupby("lag")["has_signal"].mean()
    nz = fe[["cut_frac", "susp_frac", "listing_n"]].gt(0).mean()
    w(f"\n{src('t3_compare.csv')}. 신호 특징이 0이 아닌 성분·origin 칸 비율(분모 = 특징표 전체: origin 2022-01 ~ 2025-11 47개 × 성분, 학습 행 포함. "
      "§2의 '신호 있음' 칸 수는 평가 칸 1,120개 중 창 안 신호가 하나라도 있는 칸, E6 주입 칸은 그중 인하 예고가 있는 칸이라 서로 다르다): "
      + ", ".join(f"lag {k} {pct(v)}" for k, v in hs.items())
      + f". 특징별(세 lag 합): 인하 예고 {pct(nz['cut_frac'])}, 급여중지 예고 {pct(nz['susp_frac'])}, 등재 예고 {pct(nz['listing_n'])} ({src('t3_features.csv')}).\n")
    v = cmp_[(cmp_["a"] == "M3") & (cmp_["b"] == "M2") & (cmp_["lag"] == 0)].iloc[0]
    w(f"lag 0의 M3 − M2 차이는 상대 {100 * v.rel_diff:+.1f}%로 작다. 통계적으로는 M2가 낫지만 실무상 차이는 작다.\n")
    # origin별 (b12)
    d = pred[(pred["lag"] == MAIN) & pred["model"].isin(["M3", "M2"])].pivot_table(index=["c4", "origin"], columns="model", values="sae").dropna()
    od = (d["M3"] - d["M2"]).groupby(level="origin").mean().sort_values(ascending=False)
    rest = (d["M3"] - d["M2"])[~d.index.get_level_values("origin").isin(od.index[:3])].mean()
    w(f"origin별 (lag {MAIN}): M3 − M2 평균 차이가 양수(M3가 나쁨)인 origin {int((od > 0).sum())}/{len(od)}개. 가장 큰 3개 origin("
      + ", ".join(f"{o} {x:+.3f}" for o, x in od.head(3).items()) + f")을 빼도 평균 차이 {rest:+.3f}. 몇 달에 몰린 차이가 아니다 ({src('t3_pred.csv')}).\n")

    w("## 4. 청구금액 긍정 대조 (지시 5)\n")
    w("가설: 약가 인하 예고는 수량보다 금액(= 수량 × 단가)에 먼저 나타나야 한다. 같은 M2·M3·M3p를 목표 = 원외 청구금액으로 돌렸다. "
      "금액에서 M3가 나아지면 '목표 변수가 수량이라 신호가 담을 정보가 없다'(검토 b1) 쪽, 금액에서도 나빠지면 특징·모델 쪽이다.\n")
    w("| lag | a − b | a MASE | b MASE | 상대 차이 | a가 나은 성분 | 판정 | Holm 뒤 판정 |\n|---|---|---|---|---|---|---|---|")
    for x in amt.sort_values("lag", key=lambda k: k.map({MAIN: 0, 0: 1, 3: 2})).itertuples():
        w(f"| {x.lag} | {x.a} − {x.b} | {x.mase_a:.3f} | {x.mase_b:.3f} | {100 * x.rel_diff:+.1f}% | {x.a_better_series}/{x.n_series} | {x.verdict} | {x.verdict_holm} |")
    am = amt.set_index(["lag", "a", "b"]).loc[(MAIN, "M3", "M2")]
    w(f"\n{src('t3_compare_claim_amt.csv')}. **금액에서도 M3는 M2보다 나아지지 않았다** (lag {MAIN} {am.verdict_holm}, Holm 뒤). "
      "목표 변수 불일치만으로는 설명되지 않는다. 다만 긍정 대조가 실패한 것이 신호의 쓸모를 판정하지는 않는다. "
      "E6에서 일부러 넣은 20% 효과도 일부만 검출됐으므로(§5), 이 틀의 검정력이 모자란 것과 구분되지 않는다. "
      "말할 수 있는 것은 '이 특징 정의와 이 모델에서는 금액 예측도 나아지지 않았다'까지다.\n")

    w("## 5. 진단 실험 (검토 b 항목, 주 모델은 바꾸지 않음)\n")
    w("| 실험 | 후보 | lag | a MASE | b MASE | 상대 차이 | 판정 |\n|---|---|---|---|---|---|---|")
    name = {("E1_null", "M2"): ("E1 신호 열 0 − M2", "b7 구현"), ("E2_perm", "M2"): ("E2 신호 섞음 − M2", "b3 열 추가 잡음"),
            ("E3_no_nprod", "M2"): ("E3 n_prod 뺀 M3 − M2", "b4 조밀 수준 변수"), ("E4_M3_2022", "E4_M2_2022"): ("E4 대상 달 2022-02 이후 학습 M3 − M2", "b5 NaN = 시기 표시"),
            ("E4_M2_2022", "M2"): ("E4 대상 달 2022-02 이후 학습 M2 − M2", "학습 기간"), ("E5_M3s", "E5_M2s"): ("E5 계절 목표 M3s − M2s", "b8 기반 모델"),
            ("E5_M2s", "M2"): ("E5 계절 목표 M2s − M2", "b8 기반 모델"), ("M3", "M2"): ("기준 M3 − M2", ""),
            ("M3", "E1_null"): ("**M3 − E1** (시기 표시를 같게 둔 신호 내용의 몫)", "신호 내용"),
            ("E2_perm", "E1_null"): ("E2 섞음 − E1", "열 추가 잡음"), ("E3_no_nprod", "E1_null"): ("E3 − E1", "n_prod")}
    for x in exc.sort_values(["a", "lag"]).itertuples():
        if x.lag != MAIN:
            continue
        nm, cand = name[(x.a, x.b)]
        w(f"| {nm} | {cand} | {x.lag} | {x.mase_a:.3f} | {x.mase_b:.3f} | {100 * x.rel_diff:+.1f}% | {x.verdict_holm} |")
    w(f"\n세 lag 전체는 {src('exp_compare.csv')}. 모델별 MASE (lag × 모델): {src('exp_mase_by_model.csv')}.\n")
    w("| lag | E1: 신호 열 0인 M3 = M2 (바이트 같음) | E0: 시드만 바꾼 M3 = M3 |\n|---|---|---|")
    for x in exn.itertuples():
        w(f"| {x.lag} | {'같음' if x.null_equals_m2 else '다름'} | {'같음' if x.identical else '다름'} (origin {x.origin}) |")
    w(f"\n{src('exp_null_seed.csv')}. E1의 정의: 특징표의 행(origin 2022-01 이후)만 신호 열을 0으로 바꿨고, 2022-01 이전 pseudo-origin은 특징표에 행이 없어 NaN으로 남는다. "
      "E0은 origin 하나(2024-06)에서만 확인했다. subsample = colsample = 1이라 학습이 결정적이다.\n")
    w("E1′ (검토 2차 r2-3, 두 해석을 가르는 실험): 2022 이전 행까지 신호 열을 0으로 채운 진짜 상수 열 / E1과 같은 'NaN → 0' 구조를 다시 만든 열 "
      f"({src('exp_e1_constant.csv')}).\n")
    w("| lag | 상수 열 = M2 (바이트) | 상수 열 MASE | NaN→0 열 = M2 | NaN→0 열 MASE | M2 MASE |\n|---|---|---|---|---|---|")
    for l, x in e1c.iterrows():
        w(f"| {l} | {'같음' if x.e1_const_equals_m2 else '다름'} | {x.mase_e1_const:.3f} | {'같음' if x.e1_flag_equals_m2 else '다름'} | {x.mase_e1_flag:.3f} | {x.mase_m2:.3f} |")
    w("\n상수 열은 M2와 바이트 단위로 같고, 'NaN → 0' 열만 M2와 달라진다. 구현 차이(검토 b7)는 없고, E1의 악화는 결측 구조가 만든 시기 표시에서 온다.\n")
    w(f"E6 합성 효과 주입 (인하 예고가 있는 칸의 실측 수량 × 0.8, 학습·평가 모두): 주입 칸과 나머지 칸의 MASE ({src('exp_inject.csv')}).\n")
    w("| lag | 모델 | 주입 칸 | 칸 수 | MASE |\n|---|---|---|---|---|")
    for x in exi.itertuples():
        w(f"| {x.lag} | {x.model} | {'예' if x.injected else '아니오'} | {x.n} | {x.mase:.3f} |")
    w("")
    w("E5 계절 목표 M2s의 MASE (lag별): " + ", ".join(f"lag {l} {exm.loc[l, 'E5_M2s']:.3f} (M0 {exm.loc[l, 'M0']:.3f}, M2 {exm.loc[l, 'M2']:.3f})" for l in exm.index) + ".\n")
    e = exc.set_index(["lag", "a", "b"])
    ei = exi.set_index(["lag", "model", "injected"])["mase"]
    relp = lambda l, a_, b_: 100 * e.loc[(l, a_, b_), "rel_diff"]
    w("**읽기 (lag 5)**: 신호 열을 모두 0으로 둔 E1이 M2와 같지 않았다. 2022-01 이전 pseudo-origin은 특징 행이 없어 NaN이고 이후는 값이 있으므로, "
      "신호 열이 내용 없이도 **'2022 이전 / 이후' 시기 표시**로 작동한다(검토 b5의 구조, 단 0이 아니라 NaN). "
      f"M3 − M2 {relp(MAIN, 'M3', 'M2'):+.1f}% 중 시기 표시(E1 − M2)가 {relp(MAIN, 'E1_null', 'M2'):+.1f}%, 시기 표시를 같게 둔 신호 내용(M3 − E1)이 {relp(MAIN, 'M3', 'E1_null'):+.1f}%다. "
      f"(E1′로 확인). lag 0에서 M3 − E1은 {relp(0, 'M3', 'E1_null'):+.1f}%({e.loc[(0, 'M3', 'E1_null'), 'verdict_holm']})다. "
      f"2022-02 이후 행으로만 학습하면(E4) lag 0에서 M3가 M2보다 나았다({relp(0, 'E4_M3_2022', 'E4_M2_2022'):+.1f}%, Holm 뒤 {e.loc[(0, 'E4_M3_2022', 'E4_M2_2022'), 'verdict_holm']}). "
      "다만 2022 이후 학습은 M2 자체를 크게 나쁘게 한다. 시드는 결과를 바꾸지 않았다(E0). "
      "합성 효과 주입(E6, 인하 예고가 있는 칸의 수량 20% 감소): 주입 칸 MASE M3 대 M2 = " + ", ".join(
          f"lag {l} {ei.loc[(l, 'E6_M3', True)]:.3f} 대 {ei.loc[(l, 'E6_M2', True)]:.3f}" for l in sorted(set(exi['lag'])))
      + ". " + "; ".join(f"lag {l}: 주입 칸 {int(exi[(exi.lag == l) & exi.injected & (exi.model == 'E6_M3')]['n'].iloc[0])}개, "
                          + ("M3가 오차를 줄임(일부 검출)" if ei.loc[(l, 'E6_M3', True)] < ei.loc[(l, 'E6_M2', True)] else "M3가 오차를 줄이지 못함(검출 못 함)")
                          for l in sorted(set(exi['lag'])))
      + ". 주입 칸이 아닌 칸에서는 M3가 M2보다 나빴다. "
      "즉 이 틀은 20% 크기의 효과도 일부만 되찾는다. "
      "후보별 판정은 [WORKLOG](WORKLOG_2026-11-18.md) §2.\n")
    w("**하지 않은 진단** (검토 b10·b11·b12 일부·b13): 특징 인코딩 비교, 대상 성분 확대, M2·M3 따로 튜닝, origin 블록 부트스트랩, 시드 여러 개 분포, 분위 포함률 보정. "
      "as-of 예측 비교는 origin 2개(2025-07·2025-10) × lag 0·5만 했다. (b6 시점 이동·b9 오라클은 아래 §5b)\n")

    w("## 5b. 오라클 상한(b9)과 특징 시점 이동(b6)\n")
    w("- **오라클 M3o**: 신호 특징 대신 사후 원장의 실제 이벤트(창 안에 유효일이 있는 약가 변경(예고 여부 무관)·등재·삭제·급여중지)를 넣었다. **일부러 미래를 본 상한 실험**이다. "
      "비교 기준은 같은 결측 구조를 가진 E1(신호 열 0)이다.")
    w("- **시점 이동 M3(k)**: 신호 창을 k개월 옮겼다(a+1+k ~ t+k). 기준 스냅샷은 M 그대로라 누수가 아니다.\n")
    w("| lag | a − b | a MASE | b MASE | 상대 차이 | a가 나은 성분 | Holm 뒤 판정 |\n|---|---|---|---|---|---|---|")
    for x in dgc.sort_values(["lag", "a"], key=lambda k: k.map({MAIN: 0, 0: 1}) if k.name == "lag" else k, ascending=[True, True]).itertuples():
        w(f"| {x.lag} | {x.a} − {x.b} | {x.mase_a:.3f} | {x.mase_b:.3f} | {100 * x.rel_diff:+.1f}% | {x.a_better_series}/{x.n_series} | {x.verdict_holm} |")
    w(f"\n{src('diag_compare.csv', 'diag_mase.csv', 'diag_coverage.csv')}. 0이 아닌 칸 비율(특징표 전체): "
      + ", ".join(f"lag {l} 오라클 {pct(r.oracle_nonzero)} / 신호 {pct(r.signal_nonzero)}" for l, r in dgv.iterrows()) + ".\n")
    dg = dgc.set_index(["lag", "a", "b"])
    for l in sorted(set(dgc["lag"]), key=lambda k: {MAIN: 0, 0: 1}.get(k, 2)):
        o = dg.loc[(l, "M3o_oracle", "E1_null")]
        offs = dgc[(dgc.lag == l) & dgc.a.str.startswith("M3_off")].copy()
        offs["k"] = offs["a"].str.replace("M3_off", "").astype(int)
        best = offs.sort_values("mase_a").iloc[0]
        w(f"- lag {l}: 오라클 − E1 {100 * o.rel_diff:+.1f}% ({o.verdict_holm}), 같은 학습 기간(대상 달 2022-02~) 오라클 − M2 "
          f"{100 * dg.loc[(l, 'M3o_oracle_2022', 'M2_2022')].rel_diff:+.1f}% ({dg.loc[(l, 'M3o_oracle_2022', 'M2_2022')].verdict_holm}). "
          f"시점 이동 중 MASE가 가장 낮은 k = {int(best.k):+d} (MASE {best.mase_a:.3f}, k = 0은 {offs[offs.k == 0].mase_a.iloc[0]:.3f}).")
    w("\n**금액 목표 오라클** (금액은 가격 변화를 기계적으로 반영하므로 개선되는지 확인, 시점 이동은 돌리지 않음):\n")
    w("| lag | a − b | a MASE | b MASE | 상대 차이 | a가 나은 성분 | Holm 뒤 판정 |\n|---|---|---|---|---|---|---|")
    for x in dga.assign(o=dga["lag"].map({MAIN: 0, 0: 1})).sort_values(["o", "a"]).itertuples():
        w(f"| {x.lag} | {x.a} − {x.b} | {x.mase_a:.3f} | {x.mase_b:.3f} | {100 * x.rel_diff:+.1f}% | {x.a_better_series}/{x.n_series} | {x.verdict_holm} |")
    w(f"\n{src('diag_compare_claim_amt.csv')}. Holm 보정은 이 표 {len(dga)}개를 한 묶음으로.\n")
    w("**오라클 특징 점검** (금액 오라클이 개선되지 않은 원인, `scripts/p2_check_oracle_features.py`):\n")
    w("| lag | 평가 칸 | 가격 특징 ≠ 0 칸 | 특징-실제 단가 변화 상관 (전체 / ≠0) | 기울기 (≠0) | 금액 변화 분산 중 단가 몫 | 개선 상한 1 − √(1 − 단가 몫) | 0이 아닌 학습 행 (origin별 최소 ~ 최대) / 전체 학습 행 |\n|---|---|---|---|---|---|---|---|")
    for l in sorted(ock.index, key=lambda k: {MAIN: 0, 0: 1}.get(k, 2)):
        x = ock.loc[l]
        t_ = ocr[ocr["lag"] == l]
        w(f"| {l} | {int(x.n_eval)} | {int(x.n_nonzero)} | {x.corr_all:.2f} / {x.corr_nonzero:.2f} | {x.slope_nonzero:.2f} | {pct(x.price_share)} | {pct(x.improvement_ceiling)} | "
          f"{t_.rows_price_nonzero.min()} ~ {t_.rows_price_nonzero.max()} / {t_.train_rows.min():,} ~ {t_.train_rows.max():,} |")
    w(f"\n{src('oracle_check.csv', 'oracle_check_train.csv')}. 가격 변경 이벤트의 성분 코드와 그 달 제품 성분 코드 일치 {pct(ock['c4_link_match'].iloc[0])}. "
      "특징은 실제 단가 변화를 잘 따라간다(특징 생성 코드는 의도대로 작동). 그러나 금액 변화의 대부분은 수량 변화이고 단가 몫은 작다. "
      "개선 상한은 단가와 나머지가 독립이라는 가정의 근사다. LightGBM은 잎마다 최소 20행이 필요한데(`min_child_samples`), 가격 특징이 0이 아닌 학습 행은 origin에 따라 위 범위뿐이다.\n")
    w("\n읽는 법: 오라클도 E1을 이기지 못하면, 신호가 완벽했더라도(실제 이벤트를 미리 알았더라도) 이 특징 정의와 이 모델에서는 수량 예측 개선의 여지가 작다. "
      "오라클은 이기고 M3는 못 이기면 신호의 범위·시점 쪽이다. 가장 좋은 k가 0이 아니면 창 정렬을 의심한다. 결론 문장의 범위를 넘는 해석은 하지 않는다.\n")

    w(f"## 6. 가상 재고 시뮬레이션 결과 (lag {MAIN} 주, 가정은 위 절)\n")
    w("| lag | 비용 비율 (τ) | 정책 | 칸 | 포함률 | 결품률(부족분 ÷ 수요) | 결품 칸 비율 | 평균 재고일수 | 상대 비용 (같은 비용 비율의 기준 M0×1.1 = 1) |\n|---|---|---|---|---|---|---|---|---|")
    order = {"기준 M0×1.1": 0, "M0 분위": 1, "M2 분위": 2, "M3 분위": 3}
    sm = sim.assign(o1=sim["lag"].map({MAIN: 0, 0: 1}), o2=sim["policy"].map(order)).sort_values(["o1", "cost_ratio", "o2"])
    for x in sm.itertuples():
        w(f"| {x.lag} | {int(x.cost_ratio)} ({x.tau:.2f}) | {x.policy} | {x.n} | {pct(x.coverage)} | {pct(x.shortage_rate)} | {pct(x.stockout_cells)} | {x.avg_inventory_days:.1f} | {x.cost_rel:.3f} |")
    w(f"\n{src('t3_order_sim.csv')}. 포함률이 τ보다 높으면 분위 예측이 넓게(재고를 많이) 잡힌 것이다. 비용 비율이 가정값이라 정책 순위는 이 가정 아래의 것이다.\n")
    w("**점 예측과 순서가 다를 때**: 분위 정책의 포함률이 τ에서 벗어나면(분위 보정이 안 됨) 비용 차이에 예측 정확도와 분위의 넓이 차이가 섞인다. "
      "그래서 이 표로 '어느 모델이 재고에 쓸모 있다'고 읽지 않는다. 포함률을 τ에 맞춘 뒤(conformal 등) 비교해야 하며, 하지 않았다.\n")

    extra_checks(w, src, pct)

    w("## 부록 A. lag 0 (참고: 이상적 가정, 사용통계가 그 달까지 공개됐다고 둠)\n")
    w("| 모델 | " + " | ".join(f"MASE lag {l}" for l in APPX) + " | " + " | ".join(f"WAPE lag {l}" for l in APPX) + " |")
    w("|---" * (1 + 2 * len(APPX)) + "|")
    for m in MODELS:
        w(f"| {m} | " + " | ".join(f"{tot.loc[m, ('mase', l)]:.3f}" for l in APPX) + " | " + " | ".join(pct(tot.loc[m, ('wape', l)]) for l in APPX) + " |")
    s = err[(err["part"] != "전체") & err["lag"].isin(APPX)].copy()
    s["구분"] = s.apply(lambda x: ("유행기" if x["flu"] == True else "비유행기") if x["part"].startswith("유행기")  # noqa: E712
                       else ("신호 있음" if x["has_signal"] == True else "신호 없음"), axis=1)  # noqa: E712
    p = s.pivot_table(index=["구분", "lag"], columns="model", values="mase")
    nn = s[s["model"] == "M2"].set_index(["구분", "lag"])["n"]
    w("\n| 구분 | lag | 칸 | M0 | M2 | M3 | M3p |\n|---|---|---|---|---|---|---|")
    for (g, l), x in p.iterrows():
        w(f"| {g} | {l} | {int(nn[(g, l)])} | " + " | ".join(f"{x[m]:.3f}" for m in MODELS) + " |")
    w(f"\n{src('t3_errors.csv')}. M0은 lag와 무관하다(12개월 전 값은 lag 11까지 늘 관측).\n")

    w(f"## 부록 B. 예측 대상 여섯 달 M−4 ~ M+1 (lag {MAIN})\n")
    w("마지막 관측월 a = M − 5에서 h개월 앞. M3의 신호 특징은 기준 스냅샷 M + d − 1(≤ M)로 만들었다(d < 1에서는 origin에 볼 수 있던 것보다 이른 스냅샷, 보수적).\n")
    hp = hor.pivot_table(index=["d", "target", "h"], columns="model", values="mase").reset_index().sort_values("d")
    w("| 대상 | h | M0 MASE | M2 MASE | M3 MASE |\n|---|---|---|---|---|")
    for x in hp.itertuples():
        w(f"| {x.target} | {x.h} | {x.M0:.3f} | {x.M2:.3f} | {x.M3:.3f} |")
    w(f"\n{src('t3_horizons.csv')}. M+1 행은 본문 §2와 같은 설정이다.\n")

    w("## 부록 C. 같은 학습 기간(2022년 이후)의 M2 대 M3\n")
    w("2022-01 이전에는 신호 칸이 비어 있다(채우지 않음). M2와 M3를 모두 **대상 달 2022-02 이후** 학습 행으로만 학습해 비교했다 "
      "(첫 특징 origin 2022-01의 대상 달이 2022-02라 2022-01 대상 행은 신호 칸이 비어 있다). 전체 기간 M2는 기준선으로 함께 둔다.\n")
    w("| lag | 전체 기간 M2 (기준선) | 2022~ M2 | 2022~ M3 | M3 − M2 (2022~) | M3가 나은 성분 | Holm 뒤 판정 |\n|---|---|---|---|---|---|---|")
    for l in sorted(set(exc["lag"]), key=lambda k: {MAIN: 0, 0: 1}.get(k, 2)):
        x = e.loc[(l, "E4_M3_2022", "E4_M2_2022")]
        w(f"| {l} | {exm.loc[l, 'M2']:.3f} | {x.mase_b:.3f} | {x.mase_a:.3f} | {100 * x.rel_diff:+.1f}% | {int(x.a_better_series)}/{int(x.n_series)} | {x.verdict_holm.replace('E4_M3_2022', 'M3').replace('E4_M2_2022', 'M2')} |")
    w(f"\n{src('exp_compare.csv', 'exp_mase_by_model.csv')}. 해석은 결론 문장의 범위 안에서만 한다.\n")

    w("## 문서와 달랐던 점\n")
    w("- 주 결과는 M+1이고 PLAN_P2 §7 최소판(2026-11-16)의 예측 기간 6개(M−4 ~ M+1)는 부록 B에 둔다 (사용자 결정 2026-11-18).")
    w("- `params.yaml`의 `origin_end: 2025-09`, `horizons: [1, 2, 3]`은 재정의 전 값이라 쓰지 않았다 (origin 2023-01 ~ 2025-11).")
    w("- 가상 재고는 전국 단위·달마다 독립(이월 없음)이다. PLAN_P2 §6은 시도 × 성분, '발주 = 목표 − 현재 재고'다 (가정 유지, 사용자 결정 2026-11-18).")
    w("- M1(ETS), P90 포함률, 계층 정합, 시도 단위, 의원급 보조 분석은 최소판 범위 밖이라 하지 않았다.\n")
    w("## 정한 것과 남은 것 (사용자 결정 2026-11-18, DECISIONS)\n")
    w("- 생존 편향은 한계로 적고 고치지 않는다. M2 정의는 바꾸지 않는다. 재고 가정은 유지한다. 막힌 신호·미결 처리 방식은 승인됐다.")
    w("- 남은 진단(향후 과제): 시드 분포, origin 블록 부트스트랩, 분위 포함률 보정, 특징 인코딩 비교, 대상 확대, 따로 튜닝.")
    (ROOT / "reports/P2_min_result.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    lim = ROOT / "docs/LIMITATIONS.md"
    t = lim.read_text(encoding="utf-8")
    st, en = "<!-- P2 최소판 한계 시작 (scripts/p2_min_report.py 생성) -->", "<!-- P2 최소판 한계 끝 -->"
    blk = "\n".join([st, "", "## P2 최소판 한계 (먼저 읽을 것)", "",
        "숫자는 `scripts/p2_min_report.py`가 `outputs/p2/` CSV(t1_coverage, t3_compare, exp_compare, exp_inject, exp_e1_constant, diag_compare)에서 계산했다. 자세한 것은 [P2 결과](../reports/P2_min_result.md).", "", "| # | 무엇이 | 영향 크기 | 대응 | 다음 실험 (요약의 '한계와 다음 실험' 번호) |", "|---|---|---|---|---|",
        f"| W1 | 대상 선정의 생존 편향: 96개월 내내 관측된 성분만 | 주력 묶음 106개 중 {len(tgt)}개, 2022 원외 청구금액의 {pct(cov['amt_share'])} | 한계로 적고 고치지 않는다 (사용자 결정 2026-11-18) |",
        f"| W2 | 주 결과(lag 5)에서 전역 모델이 12개월 전 값에 진다 | MASE M2 {m20.mase_a:.3f} 대 M0 {m20.mase_b:.3f} | 숨기지 않고 결론 첫 줄에 쓴다 |",
        f"| W3 | 신호 특징의 결측이 시기 표시로 작동 (상수 열 실험 E1′로 확인) | lag 5 M3 − M2 {relp(MAIN, 'M3', 'M2'):+.1f}% 중 {relp(MAIN, 'E1_null', 'M2'):+.1f}%가 시기 표시 | 결측을 채우지 않는다(CLAUDE.md). 신호 내용의 몫은 M3 − E1로 따로 보고 |",
        "| W4 | 수정본 사용통계로 학습·평가 | 측정 불가 (판이 하나) | L17, BACKLOG X11 |",
        "| W5 | 가상 재고는 전국·이월 없음·비용 비율 1·3·9·안전계수 0.1(가정값) | 약국 재고가 아니다. 분위 포함률이 τ에 맞지 않아 정책 비교가 섞인다 | 가정 유지 (사용자 결정). 포함률 보정은 향후 과제 |",
        "| W6 | 성분 간 독립을 가정한 검정 | 같은 origin을 공유해 p값이 낙관적일 수 있다 | 다루지 않음 (origin 블록 부트스트랩 미실행). Holm은 다중 비교만 다룬다 |",
        "| W7 | 주력 묶음을 평가 기간(2022~2025) 계절성으로, 규모 조건을 2022 전체로 골랐다 | 계절 모델(M0)에 유리하게 쏠릴 수 있다. lag 5 첫 origin은 2022-08까지만 본다 | 한계로 적고 고치지 않는다 |",
        "| W8 | P1 규칙(v5.3)은 전체 기간 결과를 보고 고쳤고, 미리 잠가 둔 시간 holdout이 없다 | as-of 시험은 규칙 선택의 미래 지식을 잡지 못한다 | 새 시간 holdout은 사용자 일 |",
        "| W9 | 신호 효과를 검출할 힘이 부족하다 | 일부러 넣은 20% 효과도 일부만 검출 (E6) | 결론은 '개선하지 못했고 검출할 힘이 부족했다'까지만 쓴다 |",
        "", en])
    nxt = {"W1": "4", "W2": "2, 3, 6", "W3": "7", "W4": "2", "W5": "6", "W6": "—", "W7": "4", "W8": "7", "W9": "1, 4"}
    blk = "\n".join(x.rstrip() + f" {nxt[x[2:4]]} |" if x.startswith("| W") else x for x in blk.split("\n"))
    if st in t:
        t = t[: t.index(st)] + blk + t[t.index(en) + len(en):]
    else:
        i = t.index("<!-- v5.3 한계 끝 -->") + len("<!-- v5.3 한계 끝 -->")
        t = t[:i] + "\n\n" + blk + t[i:]
    lim.write_text(t, encoding="utf-8")
    print("reports/P2_min_result.md", len(L), "줄")


if __name__ == "__main__":
    main()
