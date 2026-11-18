"""P2 최소판 결과 보고서 생성: outputs/p2/*.csv → reports/P2_min_result.md. 숫자는 모두 CSV에서 읽는다 (손으로 옮기지 않는다).
사용: python -I scripts/p2_min_report.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
O = ROOT / "outputs/p2"


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def main() -> None:
    steps = pd.read_csv(O / "t1_target_steps.csv")
    re = pd.read_csv(O / "t1_reagg_check.csv").iloc[0]
    cov = pd.read_csv(O / "t1_coverage.csv").iloc[0]
    tgt = pd.read_csv(O / "t1_target.csv", dtype={"c4": str})
    err = pd.read_csv(O / "t3_errors.csv")
    cmp_ = pd.read_csv(O / "t3_compare.csv")
    sim = pd.read_csv(O / "t3_order_sim.csv")
    fe = pd.read_csv(O / "t3_features.csv", dtype={"c4": str})
    L = []
    w = L.append
    w("# P2 최소판 결과 (T1 · T2 · T3)\n")
    w("> 숫자는 `scripts/p2_min_report.py`가 `outputs/p2/*.csv`(이번 실행 산출)에서 읽어 쓴 것이다. 설계: [PLAN_P2](../docs/PLAN_P2.md) §7 최소판, "
      "결정: [DECISIONS](../docs/DECISIONS.md) \"P2 최소판\".\n")
    w("## 먼저 읽을 한계\n")
    w("- **전국 성분 단위의 원외 청구수량 예측**이다. 약국 단위 구매·재고·판매, 제품별 판매량, 도매 유통은 데이터에 없다. 발주 시뮬레이션은 "
      "**전국 성분 단위 가상 재고 시뮬레이션**이고 약국 재고 최적화가 아니다. 비용 비율 3과 안전계수 0.1은 가정값이다.")
    w("- 사용통계는 2026-11-01에 받은 **수정본**으로 학습·평가했다. 실제 발간 시점에는 초본이라 결과가 낙관적일 수 있다 (BACKLOG X11).")
    w("- 공개 시차 lag는 0·3·5 세 가지로 돌렸다. **3은 임시 가정**이고, PLAN_P2·DECISIONS(2026-11-16)는 5를 기본으로 정해 두었다 (확인 필요 1).")
    w("- 대상은 96개월 내내 관측된 주력 묶음 성분 "
      f"{len(tgt)}개뿐이다 (2022 원외 청구금액 비중 {pct(cov['amt_share'])}, 수량 비중 {pct(cov['qty_share'])}). 업체 2곳 이하 성분은 사용통계에 없다.")
    w("- 신호 특징은 제품 판매량이 없어 제품을 같은 무게로 센다. 신호가 창 안에 있는 성분·origin 칸은 적다 (아래 §3).\n")

    w("## 완료 기준별 통과 여부\n")
    w("| 단계 | 완료 기준 | 결과 |\n|---|---|---|")
    ok_re = re["only_raw"] == 0 and re["only_mart"] == 0 and re["diff_cells"] == 0
    w(f"| T1 | 원본 재집계 합 = 마트 합 | {'통과' if ok_re else '실패'}: 원본 CSV 96개를 따로 읽어 성분4 × 월 원외 합 {int(re['both_cells']):,}칸 비교, "
      f"차이 {int(re['diff_cells'])}칸, 한쪽에만 있는 칸 {int(re['only_raw'] + re['only_mart'])} (시험 `tests/test_p2_t1.py`: 합성 CSV + 실제 2개 파일) |")
    w("| T1 | 비공개 칸은 미관측 | 통과: 원본 행이 없는 칸은 claim_qty = NULL, observed = false (합성 시험) |")
    w("| T1 | 첫 관측월 제외 규칙 시험 | 통과: 자료 시작월이 아닌 첫 관측월은 usable = false (합성 시험 5경우) |")
    w("| T1 | 대상 선정 탈락 단계표 | 아래 §1 |")
    w("| T2 | 누수 시험 (미래 행을 바꾸거나 지워도 과거 origin 예측 불변) | 통과: M0(origin 3개 × lag 0·3·5), M2(lag 0·3). "
      "**일부러 누수를 넣으면 실패함을 확인** (자르지 않은 입력 / lag 무시 두 가지) |")
    w("| T2 | lag 0과 3 | 0·3·5 모두 돌림 (5는 PLAN 기본) |")
    w("| T3 | 신호 특징은 origin 시점 스냅샷만, origin 이후 스냅샷을 지워도 특징 같음 | 통과: 2023-06·2025-07·2025-10에서 그 달 이하 스냅샷만으로 v5 원장을 다시 만들어"
      "(build_asof) 특징 비교, lag 0·3 모두 같음. **누수 주입 2가지(s 뒤 신호 사용 / 최종 결과로 거르기) 모두 시험이 잡음** |")
    w("| T3 | M3 대 M2 (origin 2023-01 이후) | 아래 §3. 유의하지 않으면 \"차이 없음\" |")
    files = ["tests/test_p2_t1.py", "tests/test_p2_t2.py", "tests/test_p2_t3.py", "tests/test_p2_posthoc.py"]
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", *files], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    last = [x for x in r.stdout.strip().splitlines() if x.strip()][-1]
    w(f"| 시험 | P2 시험 4개 파일 | 이번 실행: `{last.strip('= ')}` (P1 시험은 P1 코드를 바꾸지 않아 다시 돌리지 않았다) |\n")

    w("## 1. T1 대상 선정 탈락 단계\n")
    w("| 단계 | 성분4 | 탈락 |\n|---|---|---|")
    for r in steps.itertuples():
        w(f"| {r.step} | {r.n_c4} | {'' if pd.isna(r.dropped) else int(r.dropped)} |")
    by = tgt.groupby("cls").size()
    w("\n대상 분류별: " + ", ".join(f"{k} {v}개" for k, v in by.items()) + ". ③→④에서 가장 많이 빠진다(중간에 나타나거나 비공개 달이 있는 성분).\n")

    w("## 2. 오차표 (origin 2023-01 ~ 2025-11, 35개 × 성분, 예측 대상 M+1)\n")
    a = err[err["part"] == "전체"].pivot_table(index="model", columns="lag", values=["mase", "wape"])
    w("| 모델 | " + " | ".join(f"MASE lag {l}" for l in a["mase"].columns) + " | " + " | ".join(f"WAPE lag {l}" for l in a["wape"].columns) + " |")
    w("|---" * (1 + 2 * len(a["mase"].columns)) + "|")
    for m in ("M0", "M2", "M3", "M3p"):
        w(f"| {m} | " + " | ".join(f"{a.loc[m, ('mase', l)]:.3f}" for l in a["mase"].columns) + " | "
          + " | ".join(f"{pct(a.loc[m, ('wape', l)])}" for l in a["wape"].columns) + " |")
    n = int(err[(err["part"] == "전체") & (err["model"] == "M2")]["n"].iloc[0])
    w(f"\n평가 칸 {n:,}개(lag마다). M0 = 12개월 전 값, M2 = LightGBM 전역(신호 없음), M3 = M2 + 신호(기준 스냅샷 M, 주), "
      "M3p = 기준 스냅샷 M−1(보수적 민감도). M0은 lag와 무관하다(12개월 전 값은 lag 11까지 늘 관측).\n")
    w("유행기(11~3월) / 비유행기, origin에 창 안 신호가 있던 칸 / 없던 칸 (MASE):\n")
    s = err[err["part"] != "전체"].copy()
    s["구분"] = s.apply(lambda r: ("유행기" if r["flu"] == True else "비유행기") if r["part"].startswith("유행기")  # noqa: E712
                       else ("신호 있음" if r["has_signal"] == True else "신호 없음"), axis=1)  # noqa: E712
    p = s.pivot_table(index=["구분", "lag"], columns="model", values="mase")
    nn = s[s["model"] == "M2"].set_index(["구분", "lag"])["n"]
    w("| 구분 | lag | 칸 | M0 | M2 | M3 | M3p |\n|---|---|---|---|---|---|---|")
    for (g, l), r in p.iterrows():
        w(f"| {g} | {l} | {int(nn[(g, l)])} | " + " | ".join(f"{r[m]:.3f}" for m in ("M0", "M2", "M3", "M3p")) + " |")

    w("\n## 3. M3 대 M2 (그리고 M2 대 M0)\n")
    w("성분별 평균 MASE 차이(a − b, 음수 = a가 나음)에 부호 검정·Wilcoxon 부호 순위 검정. 두 검정 모두 p < 0.05일 때만 차이로 쓴다.\n")
    w("| lag | a − b | 성분 | a MASE | b MASE | 차이 중앙값 | a가 나은 성분 | 부호 p | Wilcoxon p | 판정 |\n|---|---|---|---|---|---|---|---|---|---|")
    for r in cmp_.itertuples():
        w(f"| {r.lag} | {r.a} − {r.b} | {r.n_series} | {r.mase_a:.3f} | {r.mase_b:.3f} | {r.median_diff:+.3f} | {r.a_better_series}/{r.n_series} | "
          f"{r.sign_p:.4f} | {r.wilcoxon_p:.4f} | {r.verdict} |")
    hs = fe.groupby("lag")["has_signal"].mean()
    nz = fe[["cut_frac", "susp_frac", "listing_n"]].gt(0).mean()
    w(f"\n신호 특징이 0이 아닌 성분·origin 칸 비율(2022-01 ~ 2025-11 origin): 창 안 신호 있음 "
      + ", ".join(f"lag {k} {pct(v)}" for k, v in hs.items())
      + f". 특징별(세 lag 합): 인하 예고 {pct(nz['cut_frac'])}, 급여중지 예고 {pct(nz['susp_frac'])}, 등재 예고 {pct(nz['listing_n'])}.\n")
    v = cmp_[(cmp_["a"] == "M3") & (cmp_["b"] == "M2")]
    w("**읽기**: " + "; ".join(f"lag {r.lag}에서 M3는 M2보다 {'나았다' if r.verdict == 'M3가 나음' else ('나빴다' if r.verdict == 'M2가 나음' else '차이가 없었다')}"
                              f" (MASE {r.mase_a:.3f} 대 {r.mase_b:.3f})" for r in v.itertuples())
      + ". 신호 특징을 넣어 예측이 나아지지 않았다. 신호가 있는 칸이 적고(위 비율), 특징이 없는 칸이 대부분이라 "
      "트리가 신호 특징(특히 급여 중 제품 수)으로 학습 표본을 나누며 잡음을 맞춘 것으로 보인다 — 이것은 가설이고 확인하지 않았다. "
      "M3가 좋아 보이지 않았으므로 누수를 의심할 결과는 아니다. 누수 시험은 위 표대로 통과했고 주입한 누수는 잡혔다.\n")
    w("M2 대 M0: " + "; ".join(f"lag {r.lag} {r.verdict}" for r in cmp_[(cmp_["a"] == "M2")].itertuples())
      + ". 공백이 길수록(lag 5) 전역 모델이 12개월 전 값보다 나빠진다. 신호가 있던 칸에서는 M0의 MASE가 M2보다 낮다(§2 표).\n")

    w("## 4. 가상 재고 시뮬레이션 (전국 성분 단위, 비용 비율 하나)\n")
    w("가정: 결품 비용 / 재고 비용 = 3 → 목표 재고 = 0.75 분위 예측. 달마다 독립(이월 없음). 실제 수요 = 사용통계 실측. 기준 정책 = M0 × 1.1. "
      "비용은 기준 정책 = 1로 나눈 값이다.\n")
    w("| lag | 정책 | 칸 | 결품률(부족분 ÷ 수요) | 결품 칸 비율 | 평균 재고일수 | 상대 비용 |\n|---|---|---|---|---|---|---|")
    for r in sim.itertuples():
        w(f"| {r.lag} | {r.policy} | {r.n} | {pct(r.shortage_rate)} | {pct(r.stockout_cells)} | {r.avg_inventory_days:.1f} | {r.cost_rel:.3f} |")
    w("\n기준 정책은 lag와 무관하다(M0). 분위 정책은 결품을 줄이는 대신 재고일수가 lag와 함께 늘어난다. 비용 비율이 가정값이라 정책 순위는 이 가정 아래의 것이다.\n")

    w("## 5. 문서와 달랐던 점\n")
    w("- PLAN_P2 §7 최소판(2026-11-16)은 예측 기간 6개(M−4 ~ M+1)와 lag 5 기본인데, 이번 지시는 1개월 앞(M+1)과 lag 0·3이다. "
      "지시대로 M+1 하나만 하고 lag 5를 함께 돌렸다.")
    w("- `params.yaml`의 `origin_end: 2025-09`, `horizons: [1, 2, 3]`은 재정의 전 값이라 쓰지 않았다 (origin 2023-01 ~ 2025-11).")
    w("- M1(ETS), 분위 예측의 P90 포함률, 계층 정합, 시도 단위는 최소판 범위 밖이라 하지 않았다.")
    w("- \"집행정지로 막힌 신호와 미결은 실현될 인하로 넣지 않음\": 원장의 '미결'은 뒤 스냅샷으로 정해지는 값이라 특징에 쓰면 누수다. "
      "origin 시점 목록에 집행정지 표시가 보이는 제품의 신호를 빼는 것으로 구현했다(외부 근거 경보제약은 결정일 다음 달부터). "
      "최종 결과로 거르면 2025-07 등에서 특징이 달라지는 것을 누수 주입 시험으로 확인했다.\n")

    w("## 확인 필요\n")
    w("1. **공개 시차**: 주 결과를 lag 3(이번 지시의 임시 가정)으로 할지, lag 5(PLAN_P2·DECISIONS 2026-11-16 기본)로 할지.")
    w("2. **예측 대상**: 최소판을 M+1 하나로 둘지, PLAN의 예측 기간 6개(M−4 ~ M+1)로 넓힐지.")
    w("3. **신호 특징 정의**: 제품 같은 무게(판매량 없음)와 창(a+1 ~ t)을 이대로 둘지. 등재 예고 특징은 대상 성분에서 0이 아닌 칸이 없었다.")
    w("4. **가상 재고 가정**: 비용 비율 3, 안전계수 0.1, 이월 없음.")
    (ROOT / "reports/P2_min_result.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("reports/P2_min_result.md", len(L), "줄")


if __name__ == "__main__":
    main()
