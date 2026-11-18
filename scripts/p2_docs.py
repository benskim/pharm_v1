"""P2 T5 문서 생성: reports/P2_SUMMARY.md(2쪽), README P2 절(표시 사이 블록), PLAN_P2 §8 공고 문구 대응표(표시 사이 블록).
숫자는 모두 outputs/p2/*.csv(이번 실행 산출)와 reports/P2_min_result.md의 고정 결론 줄에서 읽는다 (손으로 옮기지 않는다).
먼저 scripts/p2_min_report.py를 돌려야 한다 (결론 줄과 숫자 대조가 그 스크립트에 있다).
사용: python -I scripts/p2_docs.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
O = ROOT / "outputs/p2"
MAIN, REF = 5, 0
RD_START, RD_END = "<!-- P2 요약 시작 (scripts/p2_docs.py 생성) -->", "<!-- P2 요약 끝 -->"
PL_START, PL_END = "<!-- P2 §8 대응표 시작 (scripts/p2_docs.py 생성) -->", "<!-- P2 §8 대응표 끝 -->"
FORBIDDEN = ("정확히", "완벽히", "모두 맞", "인과", "영향을 미", "신호에 정보가 없", "확실히", "약국 재고 최적화")


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def sg(x: float) -> str:
    """부호 붙인 퍼센트 값 (음수는 유니코드 마이너스, 검토 p-12)"""
    return f"{x:+.1f}".replace("-", "−")


def collect() -> dict:
    N: dict = {}
    rep = (ROOT / "reports/P2_min_result.md").read_text(encoding="utf-8")
    N["conclusion"] = next(x for x in rep.splitlines() if x.startswith("**결론**: ")).removeprefix("**결론**: ")
    err = pd.read_csv(O / "t3_errors.csv")
    N["tot"] = err[err["part"] == "전체"].pivot_table(index="model", columns="lag", values="mase")
    N["n_cells"] = int(err[(err["part"] == "전체") & (err["model"] == "M2") & (err["lag"] == MAIN)]["n"].iloc[0])
    N["cmp"] = pd.read_csv(O / "t3_compare.csv").set_index(["lag", "a", "b"])
    N["amt"] = pd.read_csv(O / "t3_compare_claim_amt.csv").set_index(["lag", "a", "b"])
    N["exp"] = pd.read_csv(O / "exp_compare.csv").set_index(["lag", "a", "b"])
    N["inj"] = pd.read_csv(O / "exp_inject.csv").set_index(["lag", "model", "injected"])["mase"]
    N["inj_n"] = pd.read_csv(O / "exp_inject.csv").set_index(["lag", "model", "injected"])["n"]
    N["e1c"] = pd.read_csv(O / "exp_e1_constant.csv").set_index("lag")
    N["diag"] = pd.read_csv(O / "diag_compare.csv").set_index(["lag", "a", "b"])
    N["diag_amt"] = pd.read_csv(O / "diag_compare_claim_amt.csv").set_index(["lag", "a", "b"])
    N["ock"] = pd.read_csv(O / "oracle_check.csv").set_index("lag")
    N["ocr"] = pd.read_csv(O / "oracle_check_train.csv")
    N["cov"] = pd.read_csv(O / "t1_coverage.csv").iloc[0]
    N["steps"] = pd.read_csv(O / "t1_target_steps.csv")
    N["reagg"] = pd.read_csv(O / "t1_reagg_check.csv").iloc[0]
    fe = pd.read_csv(O / "t3_features.csv")
    N["nz"] = fe.groupby("lag")["has_signal"].mean()
    N["sim"] = pd.read_csv(O / "t3_order_sim.csv")
    N["hor"] = pd.read_csv(O / "t3_horizons.csv").pivot_table(index="d", columns="model", values="mase")
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/test_p2_t1.py", "tests/test_p2_t2.py",
                          "tests/test_p2_t3.py", "tests/test_p2_posthoc.py"], cwd=ROOT, capture_output=True, text=True,
                         env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}).stdout
    N["n_tests_p2"] = int(re.search(r"(\d+) tests? collected", out).group(1))
    return N


def rel(N, key, l, a, b) -> float:
    return 100 * N[key].loc[(l, a, b), "rel_diff"]


def summary(N) -> str:
    t, c = N["tot"], N["cmp"]
    m20, m32 = c.loc[(MAIN, "M2", "M0")], c.loc[(MAIN, "M3", "M2")]
    ock, ocr, inj = N["ock"], N["ocr"], N["inj"]
    o5 = ocr[ocr["lag"] == MAIN]
    sim = N["sim"]
    s5 = sim[(sim["lag"] == MAIN) & (sim["policy"] != "기준 M0×1.1")]
    lo, hi = s5.loc[s5.coverage.idxmin()], s5.loc[s5.coverage.idxmax()]
    # '신호 특징이 유의하게 나은 설정은 하나뿐'(결론 보충) 대조: 모든 비교표에서 M3 계열이 Holm 뒤 나은 행
    allc = pd.concat([N[k].reset_index().assign(table=k) for k in ("cmp", "amt", "exp", "diag", "diag_amt")])
    win = allc[allc["a"].str.contains("M3") & (allc["verdict_holm"] == allc["a"] + "가 나음")]
    assert list(zip(win["table"], win["lag"], win["a"])) == [("exp", REF, "E4_M3_2022")], f"'유의하게 나은 설정은 하나뿐'과 다름: {win[['table', 'lag', 'a', 'b']].values.tolist()}"
    # 문장으로 쓴 사실을 CSV와 대조 (문장이 결과와 어긋나면 멈춘다)
    h = N["hor"]
    assert (h.loc[[-4, -3, -2, -1], "M2"] < h.loc[[-4, -3, -2, -1], "M0"]).all() and (h.loc[[0, 1], "M0"] < h.loc[[0, 1], "M2"]).all(), "여섯 달 문장과 다름"
    assert (h["M3"] > h["M2"]).all(), "'M3는 여섯 달 내내 M2보다 나쁘다'와 다름"
    assert inj.loc[(MAIN, "E6_M3", True)] < inj.loc[(MAIN, "E6_M2", True)] and inj.loc[(REF, "E6_M3", True)] >= inj.loc[(REF, "E6_M2", True)], "주입 시험 문장과 다름"
    for key in ("diag", "diag_amt"):
        for l in (MAIN, REF):
            assert N[key].loc[(l, "M3o_oracle", "E1_null"), "verdict_holm"] == "차이 없음", f"오라클 문장과 다름 {key} {l}"
    off = N["diag"].reset_index()
    off = off[off["a"].str.startswith("M3_off")]
    assert not (off["verdict_holm"] == off["a"] + "가 나음").any() and (off["rel_diff"] >= 0).sum() + ((off["rel_diff"] < 0) & (off["verdict_holm"] == "차이 없음")).sum() == len(off), "시점 이동 문장과 다름"
    e4 = N["exp"].loc[(REF, "E4_M3_2022", "E4_M2_2022"), "verdict_holm"].replace("E4_M3_2022", "M3").replace("E4_M2_2022", "M2")
    e4_5 = N["exp"].loc[(MAIN, "E4_M3_2022", "E4_M2_2022"), "verdict_holm"].replace("E4_M3_2022", "M3").replace("E4_M2_2022", "M2")
    full_m2 = N["tot"].loc["M2"]
    assert all(N["exp"].loc[(l, "E4_M3_2022", "E4_M2_2022"), "mase_a"] > full_m2[l] for l in (MAIN, REF)), "'그 M2·M3는 전체 기간 M2보다 나쁘다'와 다름"
    L = ["# 수요 예측 (P2 최소판) — 2쪽 요약", "",
         "> 숫자는 `scripts/p2_docs.py`가 `outputs/p2/` CSV(이번 실행 산출)에서 읽어 쓴 것이다. 자세한 표와 출처: [P2 결과](P2_min_result.md), "
         "검토 기록: [REVIEW](REVIEW_2026-11-18.md)·[WORKLOG](WORKLOG_2026-11-18.md), 결정: [DECISIONS](../docs/DECISIONS.md), 설계: [PLAN_P2](../docs/PLAN_P2.md).", "",
         "## 결론", "", N["conclusion"], "",
         f"(보충) 주 설정(시차 5)에서 M3는 M2보다 개선하지 못한 정도가 아니라 유의하게 나빴다(MASE {sg(100 * m32.rel_diff)}%, Holm 보정 뒤 {m32.verdict_holm}). "
         f"신호 특징이 유의하게 나은 설정은 하나뿐이다: 같은 학습 기간(2022년 이후)·시차 0(이상적 참고)에서 {sg(rel(N, 'exp', REF, 'E4_M3_2022', 'E4_M2_2022'))}%. "
         "그 기준 모델은 전체 기간 M2보다 약하고, 아래 한계대로 p값이 낙관적일 수 있다.", "",
         "## 먼저 읽을 한계", "",
         "- **약국 단위가 아니다.** 건보공단 사용통계의 성분(주성분코드 4자리) × 전국 원외 청구수량을 예측했다. 약국 구매·재고·판매, 제품별 판매량, 도매 유통은 데이터에 없다.",
         f"- **대상이 좁고 치우쳤다.** 주력 묶음(141·222·229·618) 중 96개월 내내 관측되고 2022 규모 상위인 성분 32개뿐이다(2022 원외 청구금액의 {pct(N['cov']['amt_share'])}). "
         "평가 끝까지 살아남은 성분만이라 생존 편향이 있고, 묶음은 평가 기간의 계절성으로 골랐다. 고치지 않고 한계로 둔다(사용자 결정).",
         f"- **주 결과(공개 시차 5개월)에서 전역 모델이 단순 예측에 진다.** MASE M2 {m20.mase_a:.3f} 대 12개월 전 값 M0 {m20.mase_b:.3f}.",
         "- **음의 결과는 이 모델 조건의 것이다.** LightGBM 전역 모델 하나, 하이퍼파라미터는 누수를 막으려고 조정 없이 고정했다. 단순 예측(M0)에 신호를 더한 보정이나 다른 모델군은 하지 않았다.",
         "- 사용통계는 2026-11 수정본으로 학습·평가했다(실제 발간 시점에는 초본). 초본과의 차이 크기는 판이 하나라 재지 않았다. P1 규칙은 전체 기간 결과를 보고 고쳤고, 미리 잠가 둔 시간 holdout이 없다.",
         "- 검정은 성분 32개의 평균 오차 차이에 대한 것이고 성분들이 같은 origin을 공유해 p값이 낙관적일 수 있다(블록 부트스트랩은 하지 않았다). "
         "이 한계는 아래의 '유의하게 나음/나쁨' 판정(시차 5의 M3 − M2, 같은 학습 기간 시차 0의 M3 − M2)에 걸린다.", "",
         "## 1. 질문과 설정", "",
         "- **질문**: 사용통계는 발생 5개월 뒤 공개된다. 발간월 M의 1일에 사용통계(≤ M−5)만으로 다음 달 M+1의 성분별 원외 청구수량을 얼마나 맞추나. "
         "P1의 선행 신호(급여목록 ≤ M의 약가 인하·급여중지·등재 예고)를 특징으로 넣으면 나아지나.",
         f"- **평가**: 롤링 origin 2023-01 ~ 2025-11(35개) × 성분 32 = 평가 칸 {N['n_cells']:,}개. 지표 MASE(계절 척도 12). 공개 시차 5개월이 주, 0개월은 이상적 참고.",
         "- **모델**: M0 = 12개월 전 값, M2 = LightGBM 전역 모델(신호 없음), M3 = M2 + 신호 특징(같은 하이퍼파라미터·시드, 조정 없이 고정). "
         "신호 특징은 origin 시점 목록에 보이는 것만, 집행정지로 막힌 예고는 '실현될 인하'로 넣지 않는다. M3p는 한 달 전 목록만 쓴 판(목록 공개가 늦을 때를 대비한 보수적 민감도). "
         "ETS 모델(M1)은 최소판 범위 밖이라 하지 않았다.",
         "- **E1 (대조 모델)**: M3와 열 구조가 같고, 2022-01 이후 신호 값은 모두 0, 그 전(신호가 없는 기간)은 빈칸(NaN). 신호 내용 없이 '빈칸 / 0'이라는 시기 표시만 가진다. "
         "오라클과 시점 이동은 M2가 아니라 E1과 비교해 빈칸 구조를 같게 맞추고 내용의 몫만 본다.", "",
         "**용어**: origin = 예측을 하는 시점(발간월 M의 1일). 시차 = 사용통계 공개 시차(5면 M−5까지 보임). "
         "MASE = 평균 절대오차 ÷ 학습 구간의 '12개월 전 값' 예측 평균 절대오차(1보다 작으면 그 단순 예측보다 낫다, 척도는 2018~2022 고정). "
         "상대 차이 '+x%' = 앞 모델의 평균 MASE ÷ 뒤 모델 − 1 (+면 앞 모델이 나쁨). Holm = 비교 여러 개를 함께 볼 때의 보정. 성분별 MASE 차이에 부호 검정·Wilcoxon 검정을 하고 두 p가 보정 뒤 0.05 미만일 때 '나음'으로 쓴다. "
         "주력 묶음 141·222·229·618 = 급여목록 약효분류 번호(호흡기·알레르기 계열).", "",
         "## 2. 결과", "",
         "| 모델 | MASE (시차 5, 주) | MASE (시차 0, 참고) |", "|---|---|---|"]
    for m, name in (("M0", "M0 12개월 전 값"), ("M2", "M2 전역 모델"), ("M3", "M3 = M2 + 신호"), ("M3p", "M3p (한 달 전 목록, 보수적)")):
        L.append(f"| {name} | {t.loc[m, MAIN]:.3f} | {t.loc[m, REF]:.3f} |")
    L += ["",
          f"- M3 − M2 (시차 5): {sg(100 * m32.rel_diff)}%, M3가 나은 성분 {int(m32.a_better_series)}/{int(m32.n_series)}, Holm 보정 뒤 {m32.verdict_holm}. "
          f"시차 0: {sg(rel(N, 'cmp', REF, 'M3', 'M2'))}%, {c.loc[(REF, 'M3', 'M2')].verdict_holm}.",
          "- 신호가 0이 아닌 성분·origin 칸: " + ", ".join(f"시차 {k} {pct(v)}" for k, v in N["nz"].items())
          + ". 신호 창은 마지막 관측월 다음 달부터 예측 대상 달까지라, 시차 5에서는 6개월, 시차 0에서는 1개월이다. 그래서 시차 0은 신호가 들어간 칸이 훨씬 적다.",
          f"- 예측 대상 여섯 달(M−4 ~ M+1, 시차 5): M2는 M−1까지 M0보다 낫고(M−4 {N['hor'].loc[-4, 'M2']:.3f} 대 {N['hor'].loc[-4, 'M0']:.3f}), "
          f"M과 M+1에서는 M0가 낫다. M3는 여섯 달 내내 M2보다 나쁘다.", "",
          "## 3. 검증 과정 (음의 결과가 구현 오류에서 오지 않았는지, 이 설정의 검출력은 어떤지)", "",
          "| 비교 | 무엇을 보나 | 기준선 |", "|---|---|---|",
          "| M3 − M2 | 신호 특징을 넣은 전체 효과 | M2 |",
          "| E1 − M2, M3 − E1 | 빈칸 구조(시기 표시)의 몫과 신호 내용의 몫 | M2, E1 |",
          "| 오라클 − E1, 시점 이동 − E1 | 완벽한 사후 정보·창 정렬의 몫 (빈칸 구조를 같게) | E1 |",
          "| 같은 학습 기간 M3 − M2 | 빈칸 없이 2022년 이후 행만으로 | 2022년 이후 M2 |", "",
          f"1. **누수 시험** (P2 시험 {N['n_tests_p2']}개): 미래 사용통계를 바꾸거나 지워도 과거 origin 예측이 같다(허용오차 0). origin 이후 목록 스냅샷을 지우고 P1 원장을 다시 만들어도 "
          "M3 학습 행 전체의 특징과 예측이 같다. **일부러 넣은 누수 5가지**(자르지 않은 입력, 시차 무시, 공백 구간 실측을 학습 목표로, 나중에 처음 보인 신호 사용, 최종 결과로 거르기)를 시험이 모두 잡았다. "
          f"원본 CSV 96개를 따로 다시 읽은 성분 × 월 원외 합이 예측 마트와 {int(N['reagg']['both_cells']):,}칸(관측된 전 성분 × 월)에서 같다.",
          "2. **결측 구조 (시기 표시)**: 신호 특징은 2022-01부터만 있어 그 전 학습 행은 비어 있다(채우지 않음). 신호 열을 0으로 둔 모델(E1)이 M2보다 "
          f"{sg(rel(N, 'exp', MAIN, 'E1_null', 'M2'))}% 나빴고, 2022 이전까지 0으로 채운 상수 열은 M2와 바이트 단위로 같았다. "
          f"M3 − M2 {sg(100 * m32.rel_diff)}%의 절반가량이 신호 내용 없이 이 빈칸이 만든 '2022 이전/이후' 표시만으로 재현된다(상대 차이는 더해지지 않아 근사). "
          f"빈칸 구조를 같게 둔 신호 내용의 몫 M3 − E1은 시차 5 {sg(rel(N, 'exp', MAIN, 'M3', 'E1_null'))}%({N['exp'].loc[(MAIN, 'M3', 'E1_null')].verdict_holm.replace('E1_null', 'E1')}), "
          f"시차 0 {sg(rel(N, 'exp', REF, 'M3', 'E1_null'))}%({N['exp'].loc[(REF, 'M3', 'E1_null')].verdict_holm.replace('E1_null', 'E1')})다. "
          f"같은 학습 기간(2022년 이후)으로 맞추면 M3 − M2는 시차 5 {sg(rel(N, 'exp', MAIN, 'E4_M3_2022', 'E4_M2_2022'))}%({e4_5}), "
          f"시차 0 {sg(rel(N, 'exp', REF, 'E4_M3_2022', 'E4_M2_2022'))}%(Holm 뒤 {e4})이지만, 그 M2·M3는 전체 기간 M2보다 나쁘다.",
          "3. **주입 시험 (검정력)**: 인하 예고가 있는 칸(origin 시점 신호 특징 cut_frac > 0)의 실제 수량을 학습·평가 모두에서 20% 줄여 넣고, 주입 칸의 오차를 봤다. "
          + "주입 평가 칸 수 " + ", ".join(f"시차 {l} {int(N['inj_n'][(l, 'E6_M3', True)])}개" for l in (MAIN, REF)) + ". 주입 칸 MASE M3 대 M2 = "
          + ", ".join(f"시차 {l} {inj.loc[(l, 'E6_M3', True)]:.3f} 대 {inj.loc[(l, 'E6_M2', True)]:.3f}" for l in (MAIN, REF))
          + ". 시차 5에서는 M3가 주입 칸 오차를 줄였지만(일부 검출) 주입 효과를 몇 % 되찾았는지는 재지 않았고 검정도 하지 않았다. 시차 0에서는 줄이지 못했다. "
          "주입 칸의 MASE가 전체보다 높은 것은 주입으로 그 칸의 수량이 평소 계절 패턴에서 벗어났기 때문이다.",
          f"4. **오라클 (일부러 미래를 본 상한)**: 신호 대신 사후 원장의 실제 이벤트(약가 변경·등재·삭제·급여중지)를 넣었다. 수량 목표 오라클 − E1: 시차 5 {sg(rel(N, 'diag', MAIN, 'M3o_oracle', 'E1_null'))}%, "
          f"시차 0 {sg(rel(N, 'diag', REF, 'M3o_oracle', 'E1_null'))}%. 금액 목표 오라클 − E1: 시차 5 {sg(rel(N, 'diag_amt', MAIN, 'M3o_oracle', 'E1_null'))}%, "
          f"시차 0 {sg(rel(N, 'diag_amt', REF, 'M3o_oracle', 'E1_null'))}%. 모두 Holm 보정 뒤 차이 없음.",
          "5. **시점 이동**: 신호 창을 −3 ~ +3개월 옮겨도 E1보다 나은 경우가 없었다(Holm 보정 뒤).",
          f"6. **개선 상한**: 오라클 가격 특징은 실제 단가 변화를 잘 따라간다(상관 시차 5 {ock.loc[MAIN, 'corr_nonzero']:.2f} / 시차 0 {ock.loc[REF, 'corr_nonzero']:.2f}, "
          f"기울기 {ock.loc[MAIN, 'slope_nonzero']:.2f} / {ock.loc[REF, 'slope_nonzero']:.2f}). 그러나 금액 변화 분산 중 단가 몫은 {pct(ock.loc[MAIN, 'price_share'])} / {pct(ock.loc[REF, 'price_share'])}뿐이라 "
          f"가격을 다 알아도 오차를 줄일 수 있는 상한은 1 − √(1 − 몫) = {pct(ock.loc[MAIN, 'improvement_ceiling'])} / {pct(ock.loc[REF, 'improvement_ceiling'])}다. "
          "여기서 분산은 평가 칸의 log 금액 변화(마지막 관측월 a → 예측 대상 t)이고 log 단가 변화 = log 금액 변화 − log 수량 변화다. 이 상한은 제곱오차(RMSE) 기준 근사이고 MASE(절대오차)의 상한과 같지 않을 수 있으며, "
          "금액 목표의 가격 부분에만 해당한다(등재·삭제·급여중지와 수량 목표의 상한은 따로 재지 않았다). "
          f"가격 특징이 0이 아닌 학습 행은 시차 5에서 origin에 따라 {o5.rows_price_nonzero.min()} ~ {o5.rows_price_nonzero.max()}행(전체 {o5.train_rows.min():,} ~ {o5.train_rows.max():,}행)이다.",
          f"7. **긍정 대조 (청구금액, 실패)**: 가격이 기계적으로 들어가는 금액 목표로 같은 M2·M3를 돌려도 M3 − M2 = 시차 5 {sg(rel(N, 'amt', MAIN, 'M3', 'M2'))}%({N['amt'].loc[(MAIN, 'M3', 'M2')].verdict_holm}). "
          "효과가 있어야 할 곳에서도 개선이 없었으므로, 이 결과는 '이 모델·표본 크기로는 작은 효과를 잡지 못한다'는 쪽의 근거로 읽는다. 다만 가격 몫이 3~4%라 강한 대조는 아니다.", "",
          "## 4. 전국 성분 단위 가상 재고 시뮬레이션 (가정 먼저)", "",
          "약국 재고가 아니다. 가정: 전국 성분 단위, 매달 독립(이월 없음), 리드타임 1개월, 결품/재고 비용 비율 1·3·9(가정값) → 분위 0.5·0.75·0.9, 기준 정책 M0 × 1.1(안전계수 가정값). "
          f"시차 5에서 분위 정책의 포함률은 {pct(s5.coverage.min())}({lo.policy}, 목표 {lo.tau:.2f}) ~ {pct(s5.coverage.max())}({hi.policy}, 목표 {hi.tau:.2f})로 목표 분위와 맞지 않아(보정 안 함), "
          "비용 순위로 어느 모델이 재고에 쓸모 있다고 읽지 않는다.", "",
          "## 5. 공고 문구 대응", ""]
    L += plan_table(N)
    return "\n".join(L) + "\n"


def plan_table(N) -> list[str]:
    t, c = N["tot"], N["cmp"]
    m20 = c.loc[(MAIN, "M2", "M0")]
    return ["| 공고 문구 | 근거 산출물 (실제 결과) |", "|---|---|",
            "| 약국 구매, 판매, 사용 패턴 분석 기반 예측 모델 개발 | **약국 단위 자료는 없다.** 대신 성분 × 전국 원외 청구수량 예측(M0·M2·M3, 롤링 origin 35개). "
            f"시차 5에서 M2 MASE {m20.mase_a:.3f}는 M0 {m20.mase_b:.3f}보다 나빴고, 신호 특징(M3 {t.loc['M3', MAIN]:.3f})은 개선하지 못했다. "
            "누수 시험으로 음의 결과가 구현 오류에서 온 것이 아님을 확인했고, 주입 시험·오라클·시점 이동으로 이 설정의 검출력이 낮음을 보였다 (P2 요약 §3). 의원급 원외 보조 분석은 하지 않았다 |",
            "| 의약품 유통 데이터 기반 통계 분석 및 알고리즘 개발 | 유통 데이터는 없다. 전국 성분 단위 가상 재고 시뮬레이션(비용 비율 1·3·9, 가정값)과 분위 예측. "
            "분위 포함률이 맞지 않아 정책 비교는 참고만. 계층 정합·시도 단위는 하지 않았다 |",
            "| 심평원 빅데이터 및 처방 데이터 활용 (우대) | 건보공단 사용통계 96개월(원본 재집계 = 마트 합 시험) × 심평원 급여목록 신호(P1 v5.3 원장, origin 시점 스냅샷만) |"]


def readme_block(N) -> str:
    c = N["cmp"]
    m20, m32 = c.loc[(MAIN, "M2", "M0")], c.loc[(MAIN, "M3", "M2")]
    return "\n".join([RD_START,
        f"**P2 (최소판)**: 성분 32개 × 전국 원외 청구수량의 다음 달 예측(약국 단위 아님, 사용통계 공개 시차 5개월). "
        f"MASE: 12개월 전 값 M0 {m20.mase_b:.3f} / 전역 모델 M2 {m20.mase_a:.3f} / M2 + P1 신호 M3 {m32.mase_a:.3f} — 전역 모델도 단순 예측에 졌다. "
        "결론(고정 문장): " + N["conclusion"] + " 2쪽 요약: [reports/P2_SUMMARY.md](reports/P2_SUMMARY.md), 결과: [reports/P2_min_result.md](reports/P2_min_result.md), "
        "한계(P2 W1~W9: 생존 편향, 단순 예측에 짐, 빈칸 시기 표시, 수정본, 검출력 등): [docs/LIMITATIONS.md](docs/LIMITATIONS.md). 실행: `make p2-min`. P2 실험은 동결(이후 버그 수정만).",
        RD_END])


def put(text: str, st: str, en: str, block: str, anchor: str, after: bool = True) -> str:
    if st in text:
        return text[: text.index(st)] + block + text[text.index(en) + len(en):]
    i = text.index(anchor) + (len(anchor) if after else 0)
    return text[:i] + "\n\n" + block + "\n" + text[i:]


def check(path: Path, text: str) -> list[str]:
    errs = []
    for m in re.finditer(r"\]\(([^)#\s]+)(#[^)]*)?\)", text):
        if not m.group(1).startswith("http") and not (path.parent / m.group(1)).resolve().exists():
            errs.append(f"{path.name}: 링크 대상 없음 {m.group(1)}")
    for w in FORBIDDEN:
        if w in text:
            errs.append(f"{path.name}: 금지 표현 '{w}'")
    return errs


def main() -> None:
    N = collect()
    s = summary(N)
    out = ROOT / "reports/P2_SUMMARY.md"
    out.write_text(s, encoding="utf-8")
    rd = ROOT / "README.md"
    t = rd.read_text(encoding="utf-8")
    t = put(t, RD_START, RD_END, readme_block(N), "<!-- P1 요약 끝 -->")
    t = t.replace("| 질문 | 비고의 예고는 실제 변화로 얼마나, 얼마 만에 이어지나 | 사용통계 공백 5개월을 목록 신호로 얼마나 메울 수 있나 |",
                  "| 질문 | 비고의 예고는 실제 변화로 얼마나, 얼마 만에 이어지나 | 사용통계가 5개월 늦게 공개될 때 다음 달 성분 수요를 맞추나, 목록 신호를 넣으면 나아지나 |")
    t = t.replace("| 데이터 | 급여목록·대체조제 목록 2022-01 ~ 2025-12, 건보공단 사용통계 | 사용통계 2018-01 ~ 2025-12, P1 신호 |",
                  "| 데이터 | 급여목록·대체조제 목록 2022-01 ~ 2025-12, 건보공단 사용통계 | 사용통계 2018-01 ~ 2025-12, P1 신호(2022-01 ~) |")
    t = t.replace("| | P1 선행 신호 원장 (완료) | P2 수요 예측 (설계만) |", "| | P1 선행 신호 원장 (완료) | P2 수요 예측 (최소판 완료, 실험 동결) |")
    t = t.replace("| 산출물 | 원장, 분석 A·B·C, `make report MONTH=YYYY-MM` 월간 리포트 | [docs/PLAN_P2.md](docs/PLAN_P2.md) |",
                  "| 산출물 | 원장, 분석 A·B·C, `make report MONTH=YYYY-MM` 월간 리포트 | `make p2-min`, [2쪽 요약](reports/P2_SUMMARY.md), [결과](reports/P2_min_result.md), 설계 [PLAN_P2](docs/PLAN_P2.md) |")
    t = t.replace("P2 단계는 구현한 뒤 `all`에 넣는다.", "P2 최소판은 `make p2-min`(T1 → T2 → T3)으로 따로 돌린다(`all`에 넣지 않았다).")
    t = t.replace("| [reports/SUMMARY.md](reports/SUMMARY.md) | 2쪽 요약 (v5.3, `scripts/p1_docs.py` 생성) |",
                  "| [reports/SUMMARY.md](reports/SUMMARY.md) | P1 2쪽 요약 (v5.3, `scripts/p1_docs.py` 생성) |\n| [reports/P2_SUMMARY.md](reports/P2_SUMMARY.md) | P2 2쪽 요약 (최소판, `scripts/p2_docs.py` 생성) |")
    rd.write_text(t, encoding="utf-8")
    pl = ROOT / "docs/PLAN_P2.md"
    u = pl.read_text(encoding="utf-8")
    blk = "\n".join([PL_START, "", "(2026-11-19 갱신: 최소판 실제 결과. 아래 원래 표는 설계 때의 계획이다.)", "", *plan_table(N), "", PL_END])
    u = put(u, PL_START, PL_END, blk, "## 8. 바로팜 공고와의 대응")
    pl.write_text(u, encoding="utf-8")
    errs = check(out, s) + check(rd, rd.read_text(encoding="utf-8")) + check(pl, blk)
    if errs:
        raise SystemExit("\n".join(errs))
    print(f"P2_SUMMARY {len(s.splitlines())}줄, README P2 블록, PLAN_P2 §8 대응표. 검사 통과 (P2 시험 {N['n_tests_p2']}개)")


if __name__ == "__main__":
    main()
