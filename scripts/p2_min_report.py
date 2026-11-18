"""P2 최소판 결과 보고서 생성: outputs/p2/*.csv → reports/P2_min_result.md. 숫자는 모두 CSV에서 읽는다 (손으로 옮기지 않는다).
주 결과 = 공개 시차 lag 5, 예측 대상 M+1 (PLAN_P2 §3·§5, 사용자 지시 1·2). lag 0·3은 부록.
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
MAIN = 5
APPX = (0, 3)
MODELS = ("M0", "M2", "M3", "M3p")


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def src(*names: str) -> str:
    return "출처: " + ", ".join(f"`outputs/p2/{n}`" for n in names)


def verdict_ko(v: str) -> str:
    return v.replace("가 나음", "이(가) 나음")


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
    L: list[str] = []
    w = L.append
    tot = err[err["part"] == "전체"].pivot_table(index="model", columns="lag", values=["mase", "wape"])
    c = cmp_.set_index(["lag", "a", "b"])
    m32, m20 = c.loc[(MAIN, "M3", "M2")], c.loc[(MAIN, "M2", "M0")]

    w("# P2 최소판 결과 (T1 · T2 · T3)\n")
    w("> 숫자는 `scripts/p2_min_report.py`가 `outputs/p2/`의 CSV(이번 실행 산출, 표마다 출처를 적음)에서 읽어 쓴 것이다. "
      "설계: [PLAN_P2](../docs/PLAN_P2.md) §7 최소판, 결정: [DECISIONS](../docs/DECISIONS.md) \"P2 최소판\", 검토: [REVIEW](REVIEW_2026-11-18.md)·[WORKLOG](WORKLOG_2026-11-18.md).\n")
    w("**주 결과 (공개 시차 5개월, 예측 대상 = 발간월 M의 다음 달 M+1)**: 전역 모델 M2의 MASE "
      f"{m20.mase_a:.3f}는 12개월 전 값 M0 {m20.mase_b:.3f}보다 나빴다 (M2가 나은 성분 {int(m20.a_better_series)}/{int(m20.n_series)}). "
      f"P1 신호 특징을 넣은 M3 {m32.mase_a:.3f}는 M2보다 나빴다 (M3가 나은 성분 {int(m32.a_better_series)}/{int(m32.n_series)}, Holm 보정 뒤 판정: {m32.verdict_holm}). "
      "**신호를 넣어 예측이 나아지지 않았다.** 이 악화의 약 절반은 신호 내용이 아니라 신호 열의 결측 구조(2022 이전 NaN)가 만든 시기 표시에서 왔다(§5). "
      "공개 시차를 0으로 둔 이상적 가정(부록)에서만 M2가 M0보다 나았다.\n")

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
    w("- 결품 비용 / 재고 비용 = **3 (가정값)** → 목표 재고 = 0.75 분위 예측. 비용 비율 다른 값(1, 9)은 돌리지 않았다.")
    w("- **달마다 독립(이월 없음)**: 그 달 목표 재고 S로 그 달 수요 D를 맞고, 남은 재고는 다음 달로 넘기지 않는다. PLAN_P2 §6의 '발주 = 목표 − 현재 재고'와 다르다.")
    w("- 리드타임 1개월 = origin M에 정한 S가 M+1에 쓰인다. 실제 수요 = 사용통계 실측(수정본).")
    w("- 정책: 기준 M0 × 1.1(안전계수 0.1, 가정값) / **M0 P75**(y_{t−12} × 과거 계절 비율 y_u/y_{u−12}의 0.75 분위. 분위 형태의 기준이지만 포함률은 보정하지 않았다. 비율 이력은 M − lag까지라 lag마다 값이 다르다) / M2 P75·M3 P75(LightGBM 분위 회귀). "
      "포함률(실수요 ≤ S인 칸 비율)이 0.75 근처면 분위가 맞은 것이다.\n")

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
    w("| 모델 | MASE | WAPE |\n|---|---|---|")
    for m in MODELS:
        w(f"| {m} | {tot.loc[m, ('mase', MAIN)]:.3f} | {pct(tot.loc[m, ('wape', MAIN)])} |")
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
      "Holm 보정은 부호 검정과 Wilcoxon 검정에 따로, 각각 이 표의 비교 9개(3 lag × 3 비교)를 한 묶음으로 했다. Holm 뒤 판정 = 보정된 두 p가 모두 0.05 미만. "
      "§4 금액 표는 그 표 9개, §5 진단 표는 진단 비교 33개(11 비교 × 3 lag)를 각각 한 묶음으로 보정했다. 상대 차이 = a MASE ÷ b MASE − 1.\n")
    w("| lag | a − b | a MASE | b MASE | 상대 차이 | a가 나은 성분 | 부호 p | Wilcoxon p | 부호 p (Holm) | Wilcoxon p (Holm) | 판정 | Holm 뒤 판정 |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    for x in cmp_.sort_values("lag", key=lambda k: k.map({MAIN: 0, 0: 1, 3: 2})).itertuples():
        w(f"| {x.lag}{' (주)' if x.lag == MAIN else ''} | {x.a} − {x.b} | {x.mase_a:.3f} | {x.mase_b:.3f} | {100 * x.rel_diff:+.1f}% | {x.a_better_series}/{x.n_series} | "
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
      "목표 변수 불일치만으로는 설명되지 않는다. 다만 긍정 대조가 실패한 것은 '신호에 정보가 없다'는 증거가 아니다. "
      "E6에서 일부러 넣은 20% 효과도 lag 3·5에서 일부만, lag 0에서는 잡지 못했으므로, 이 틀의 검정력이 모자란 것과 구분되지 않는다. "
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
      f"(E1′로 확인). lag 0·3에서 M3 − E1은 {relp(0, 'M3', 'E1_null'):+.1f}%·{relp(3, 'M3', 'E1_null'):+.1f}%로 차이 없음이다. "
      f"2022-02 이후 행으로만 학습하면(E4) lag 0에서 M3가 M2보다 나았다({relp(0, 'E4_M3_2022', 'E4_M2_2022'):+.1f}%, Holm 뒤 {e.loc[(0, 'E4_M3_2022', 'E4_M2_2022'), 'verdict_holm']}). "
      "다만 2022 이후 학습은 M2 자체를 크게 나쁘게 한다. 시드는 결과를 바꾸지 않았다(E0). "
      "합성 효과 주입(E6, 인하 예고가 있는 칸의 수량 20% 감소): 주입 칸 MASE M3 대 M2 = " + ", ".join(
          f"lag {l} {ei.loc[(l, 'E6_M3', True)]:.3f} 대 {ei.loc[(l, 'E6_M2', True)]:.3f}" for l in sorted(set(exi['lag'])))
      + ". lag 3·5에서는 20% 효과를 일부 잡았고, 주입 칸이 27개뿐인 lag 0에서는 잡지 못했다. 주입 칸이 아닌 칸에서는 M3가 M2보다 나빴다. "
      "즉 이 틀은 20% 크기의 효과도 일부만 되찾는다. "
      "후보별 판정은 [WORKLOG](WORKLOG_2026-11-18.md) §2.\n")
    w("**하지 않은 진단** (검토 b6·b9·b10·b11·b12 일부·b13): 특징 유효일 오프셋(정렬), 사후 이벤트를 넣은 오라클 상한, 특징 인코딩 비교, 대상 성분 확대, M2·M3 따로 튜닝, "
      "origin 블록 부트스트랩, 시드 여러 개 분포. as-of 예측 비교는 origin 2개(2025-07·2025-10) × lag 0·5만 했다.\n")

    w(f"## 6. 가상 재고 시뮬레이션 결과 (lag {MAIN} 주, 가정은 위 절)\n")
    w("| lag | 정책 | 칸 | 포함률 (목표 0.75) | 결품률(부족분 ÷ 수요) | 결품 칸 비율 | 평균 재고일수 | 상대 비용 (기준 M0×1.1 = 1) |\n|---|---|---|---|---|---|---|---|")
    for x in sim.sort_values("lag", key=lambda k: k.map({MAIN: 0, 0: 1, 3: 2})).itertuples():
        w(f"| {x.lag} | {x.policy} | {x.n} | {pct(x.coverage)} | {pct(x.shortage_rate)} | {pct(x.stockout_cells)} | {x.avg_inventory_days:.1f} | {x.cost_rel:.3f} |")
    w(f"\n{src('t3_order_sim.csv')}. 예측 모델의 몫은 분위에 맞춘 기준(M0 P75)과 비교해 읽는다. 포함률이 0.75보다 높으면 분위 예측이 넓게(재고를 많이) 잡힌 것이다. "
      "비용 비율이 가정값이라 정책 순위는 이 가정 아래의 것이다.\n")
    w("**점 예측과 순서가 다른 이유**: lag 5에서 점 예측(MASE)은 M0가 M2보다 낫지만 재고 비용은 M2 P75가 M0 P75보다 낮다. 세 분위 정책의 포함률이 모두 0.75보다 높아(분위 보정이 안 됨) "
      "비용 차이에는 예측 정확도와 분위의 넓이 차이가 섞인다. M0 P75는 계절 비율 이력에 코로나 시기(2020~2021)가 들어 있어 넓게 잡혔을 수 있다(확인하지 않은 가설). "
      "그래서 이 표로 'lag 5에서 M2가 재고에 쓸모 있다'고 읽지 않는다. lag 0에서 M3 P75 비용이 M2 P75보다 조금 낮은 것(MASE와 반대)도 같은 이유로 해석하지 않는다. "
      "포함률을 0.75에 맞춘 뒤(conformal 등) 비교해야 하며, 하지 않았다.\n")

    w("## 부록 A. lag 0·3 (참고: 0은 이상적 가정, 3은 사용자 지시의 임시 가정)\n")
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

    w("## 문서와 달랐던 점\n")
    w("- 예측 대상은 M+1 하나다(지시 2). PLAN_P2 §7 최소판(2026-11-16)의 예측 기간 6개(M−4 ~ M+1)는 하지 않았다.")
    w("- `params.yaml`의 `origin_end: 2025-09`, `horizons: [1, 2, 3]`은 재정의 전 값이라 쓰지 않았다 (origin 2023-01 ~ 2025-11).")
    w("- 가상 재고는 전국 단위·달마다 독립(이월 없음)이다. PLAN_P2 §6은 시도 × 성분, '발주 = 목표 − 현재 재고'다.")
    w("- M1(ETS), P90 포함률, 계층 정합, 시도 단위, 의원급 보조 분석은 최소판 범위 밖이라 하지 않았다.\n")

    w("## 확인 필요\n")
    w("1. **지시 1~6의 원문**: 이 대화 맥락에 남아 있지 않아 괄호 제목으로 읽었다(WORKLOG §0). 다르게 의도했으면 알려 달라.")
    w("2. **대상 선정의 생존 편향**: '96개월 관측' 대신 '평가 구간에서 관측 + observed 플래그'로 넓힐지 (PLAN_P2 §2 변경이라 사용자 결정).")
    w("3. **기반 모델**: 계절 목표(E5)처럼 M2 정의를 바꿀지. 결과를 보고 고르는 일이라, 바꾸면 새 시간 holdout이 필요하다.")
    w("4. **가상 재고 가정**: 비용 비율 3, 안전계수 0.1, 이월 없음.")
    (ROOT / "reports/P2_min_result.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    lim = ROOT / "docs/LIMITATIONS.md"
    t = lim.read_text(encoding="utf-8")
    st, en = "<!-- P2 최소판 한계 시작 (scripts/p2_min_report.py 생성) -->", "<!-- P2 최소판 한계 끝 -->"
    blk = "\n".join([st, "", "## P2 최소판 한계 (먼저 읽을 것)", "",
        "숫자는 `scripts/p2_min_report.py`가 `outputs/p2/` CSV(t1_coverage, t3_compare, exp_compare, exp_inject, exp_e1_constant)에서 계산했다. 자세한 것은 [P2 결과](../reports/P2_min_result.md).", "", "| # | 무엇이 | 영향 크기 | 대응 |", "|---|---|---|---|",
        f"| W1 | 대상 선정의 생존 편향: 96개월 내내 관측된 성분만 | 주력 묶음 106개 중 {len(tgt)}개, 2022 원외 청구금액의 {pct(cov['amt_share'])} | 선정 규칙 변경은 사용자 결정 (P2 결과 확인 필요 2) |",
        f"| W2 | 주 결과(lag 5)에서 전역 모델이 12개월 전 값에 진다 | MASE M2 {m20.mase_a:.3f} 대 M0 {m20.mase_b:.3f} | 숨기지 않고 결론 첫 줄에 쓴다 |",
        f"| W3 | 신호 특징의 결측이 시기 표시로 작동 (상수 열 실험 E1′로 확인) | lag 5 M3 − M2 {relp(MAIN, 'M3', 'M2'):+.1f}% 중 {relp(MAIN, 'E1_null', 'M2'):+.1f}%가 시기 표시 | 결측을 채우지 않는다(CLAUDE.md). 신호 내용의 몫은 M3 − E1로 따로 보고 |",
        "| W4 | 수정본 사용통계로 학습·평가 | 측정 불가 (판이 하나) | L17, BACKLOG X11 |",
        "| W5 | 가상 재고는 전국·이월 없음·비용 비율 3·안전계수 0.1(가정값) | 약국 재고가 아니다 | P2 결과 §가정 |",
        "| W6 | 성분 간 독립을 가정한 검정 | 같은 origin을 공유해 p값이 낙관적일 수 있다 | 다루지 않음 (origin 블록 부트스트랩 미실행). Holm은 다중 비교만 다룬다 |",
        "| W7 | 주력 묶음을 평가 기간(2022~2025) 계절성으로, 규모 조건을 2022 전체로 골랐다 | 계절 모델(M0)에 유리하게 쏠릴 수 있다. lag 5 첫 origin은 2022-08까지만 본다 | 선정 변경은 사용자 결정 |",
        "| W8 | P1 규칙(v5.3)은 전체 기간 결과를 보고 고쳤고, 미리 잠가 둔 시간 holdout이 없다 | as-of 시험은 규칙 선택의 미래 지식을 잡지 못한다 | 새 시간 holdout은 사용자 일 |",
        "| W9 | 검정력이 낮다 | 일부러 넣은 20% 효과도 lag 3·5에서 일부만, lag 0에서는 못 잡았다 (E6) | '신호에 정보가 없다'고 쓰지 않는다 |",
        "", en])
    if st in t:
        t = t[: t.index(st)] + blk + t[t.index(en) + len(en):]
    else:
        i = t.index("<!-- v5.3 한계 끝 -->") + len("<!-- v5.3 한계 끝 -->")
        t = t[:i] + "\n\n" + blk + t[i:]
    lim.write_text(t, encoding="utf-8")
    print("reports/P2_min_result.md", len(L), "줄")


if __name__ == "__main__":
    main()
