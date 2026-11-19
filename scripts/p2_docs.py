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
sys.path.insert(0, str(Path(__file__).resolve().parent))
O = ROOT / "outputs/p2"
MAIN, REF = 5, 0
RD_START, RD_END = "<!-- P2 요약 시작 (scripts/p2_docs.py 생성) -->", "<!-- P2 요약 끝 -->"
CORE_START, CORE_END = "<!-- 핵심 결론 시작 (scripts/p2_docs.py 생성) -->", "<!-- 핵심 결론 끝 -->"
PL_START, PL_END = "<!-- P2 §8 대응표 시작 (scripts/p2_docs.py 생성) -->", "<!-- P2 §8 대응표 끝 -->"
FORBIDDEN = ("정확히", "완벽히", "모두 맞", "인과", "영향을 미", "신호에 정보가 없", "확실히", "약국 재고 최적화")


def pct(x: float, nd: int = 1) -> str:
    return f"{100 * x:.{nd}f}%"


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
    """외부용 P2 2쪽 요약 (2026-11-19 사용자 지시 6: 내부 용어를 쓰지 않는다. 결론·한계 먼저)"""
    import ext_core as X
    P1, P2 = X.p1_numbers(), X.p2_numbers()
    t, c, w32 = N["tot"], N["cmp"], P2["w32"]
    err = pd.read_csv(O / "t3_errors.csv")
    wt = err[err["part"] == "전체"].pivot_table(index="model", columns="lag", values="wape")
    ock, ocr, inj = N["ock"], N["ocr"], N["inj"]
    o5 = ocr[ocr["lag"] == MAIN]
    sim = N["sim"]
    s5 = sim[(sim["lag"] == MAIN) & (sim["policy"] != "기준 M0×1.1")]
    lo, hi = s5.loc[s5.coverage.idxmin()], s5.loc[s5.coverage.idxmax()]
    t2, st, t2c, stc = P2["t2"], P2["st"], P2["t2c"], P2["stc"]
    NAME = {"M0": "작년 같은 달 값", "M2": "기계학습 예측 모델", "M3": "같은 모델에 목록 정보를 더한 것"}
    POL = {"M0 분위": "작년 같은 달 값 기반", "M2 분위": "기계학습 모델 기반", "M3 분위": "목록 정보 추가 모델 기반"}
    L = ["# 처방 수요 예측 (P2) — 2쪽 요약", "",
         "> 숫자는 `scripts/p2_docs.py`가 결과 파일(`outputs/p2/`)에서 읽어 쓴 것이다. 자세한 표·검토 기록은 내부 문서 [P2 결과](P2_min_result.md), "
         "[검토 기록](REVIEW_2026-11-18.md)·[작업 기록](WORKLOG_2026-11-18.md), 변화 기록표 쪽은 [P1 요약](SUMMARY.md).", ""]
    L += X.core(P1, P2, "p2")
    L += ["## 먼저 읽을 한계", "",
          "- **약국 단위가 아니다.** 건강보험공단 사용통계의 성분(주성분 4자리) × 전국 처방량(원외 청구수량)을 예측했다. 약국 구매·재고·판매, 제품별 판매량, 도매 유통은 데이터에 없다. 사용통계에는 제품 단위 처방량도 없다.",
          f"- **대상이 좁고 치우쳤다.** 주 결과는 호흡기·알레르기 계열 약효분류 4개 중 96개월 내내 관측되고 2022년 규모 상위인 성분 32개(2022년 원외 청구금액의 {pct(N['cov']['amt_share'])})다. "
          "끝까지 관측된 성분만이라 살아남은 성분 쪽으로 치우쳤고, 주 대상의 계절성은 평가 기간(2022~2025)을 보고 골라 작년 같은 달 값에 유리할 수 있다. 200개 성분으로 넓힌 확인(결과를 본 뒤)은 아래 표와 '한계와 다음 실험' 4번.",
          "- **결과는 이 모델 조건의 것이다.** 기계학습 모델은 하나(LightGBM: 작은 결정나무를 여러 개 쌓아 예측하는 모델)이고, 미래 정보가 섞이지 않도록 설정값을 조정 없이 고정했다. 작년 같은 달 값에 목록 정보를 공식으로 더하는 방식이나 다른 모델은 해 보지 않았다.",
          "- 사용통계는 2026-11에 받은 수정본(공개 뒤 고쳐진 판)으로 학습·평가했다. 실제로 그 달에 볼 수 있었던 것은 처음 공개본(초본)이다. 같은 달을 다른 날 받은 판본이 하나뿐이라 그 차이는 재지 못했다. "
          "미리 잠가 둔 검증 기간이 따로 없다. 최근 달이 덜 집계된 자료는 최근 수준을 학습하는 기계학습 모델 쪽에 유리하게 작동할 수 있다(최근 12개월을 빼도 같은 모양이었다).", "",
          "## 1. 무엇을 했나", "",
          "- **질문**: 사용통계는 처방 약 5개월 뒤에 공개된다고 가정했다(확인한 공개 시차는 약 4개월). 매달 1일, 그때까지 공개된 사용통계만으로 그다음 달의 성분별 처방량, 곧 마지막 자료에서 6개월 앞(공개 시차 5개월 가정)을 얼마나 맞추나. "
          "급여목록에 미리 적힌 처분 예고(약가 인하, 급여중지, 신규 등재)를 더하면 나아지나.",
          "- **비교한 예측**: ① 작년 같은 달 값 ② 기계학습 예측 모델(성분 전체를 한 모델로 학습) ③ 같은 모델에 목록 정보를 더한 것. 목록 정보는 예측하는 날에 실제로 볼 수 있었던 것만 쓰고, 법원 집행정지로 막힌 예고는 '실행될 인하'로 넣지 않았다.",
          f"- **평가**: 2023-01 ~ 2025-11의 매달(35번) × 성분 32 = {N['n_cells']:,}건. 오차는 WAPE(예측 오차의 합 ÷ 실제 처방량의 합)를 먼저 보고, "
          "보조로 2022년 청구금액으로 가중한 WAPE(비싼 약의 오차에 무게를 더 줌)와 MASE(2018~2022 계절 단순 예측 척도)를 함께 본다. MASE의 척도는 2018~2022년 학습 기간에서 작년 같은 달 값으로 예측했을 때의 오차라서, "
          f"평가 기간(2023~2025)의 작년 같은 달 값 자신은 1이 아니라 {t.loc['M0', MAIN]:.2f}로 나온다. 1보다 작을수록 좋다.", "",
          "## 2. 결과 (미리 정한 주 대상 성분 32개, 마지막 자료에서 6개월 앞 · 공개 시차 5개월 가정)", "",
          "| 예측 | 오차 WAPE, 공개 시차 5개월 가정 (6개월 앞) | 오차 WAPE, 시차 0 (1개월 앞, 수정이 끝난 자료가 바로 있다고 가정) | 청구금액 가중 WAPE, 시차 5 / 0 | MASE(2018~2022 계절 단순 예측 척도), 시차 5 / 0 |",
          "|---|---|---|---|---|"]
    for m in ("M0", "M2", "M3"):
        L.append(f"| {NAME[m]} | {pct(wt.loc[m, MAIN])} | {pct(wt.loc[m, REF])} | {pct(w32.loc[(m, MAIN), 'wape_amt'])} / {pct(w32.loc[(m, REF), 'wape_amt'])} | "
                 f"{t.loc[m, MAIN]:.2f} / {t.loc[m, REF]:.2f} |")
    m32 = c.loc[(MAIN, "M3", "M2")]
    # 문장으로 쓴 사실을 결과 파일과 대조 (어긋나면 멈춘다)
    assert m32.verdict_holm == "M2가 나음", "'유의하게 나쁨'과 다름"
    half = N["exp"].loc[(MAIN, "E1_null", "M2"), "rel_diff"] / m32.rel_diff
    assert 0.35 <= half <= 0.65, f"'절반가량'과 다름: {half:.2f}"
    assert inj.loc[(MAIN, "E6_M3", True)] < inj.loc[(MAIN, "E6_M2", True)] and inj.loc[(REF, "E6_M3", True)] >= inj.loc[(REF, "E6_M2", True)], "효과 넣은 시험 문장과 다름"
    off = N["diag"].reset_index()
    off = off[off["a"].str.startswith("M3_off")]
    assert not (off["verdict_holm"] == off["a"] + "가 나음").any(), "시점 이동 문장과 다름"
    keep_w = (w32.loc[("M3", MAIN), "wape_amt"] >= w32.loc[("M2", MAIN), "wape_amt"]) == (wt.loc["M3", MAIN] >= wt.loc["M2", MAIN]) and \
             (w32.loc[("M2", MAIN), "wape_amt"] > w32.loc[("M0", MAIN), "wape_amt"]) == (wt.loc["M2", MAIN] > wt.loc["M0", MAIN])
    L += ["",
          f"- 목록 정보를 더한 모델은 기계학습 모델보다 나빴다(공개 시차 5개월에서 성분 32개 중 {int(m32.a_better_series)}개만 나음. 성분별 오차 차이에 부호 검정과 윌콕슨 검정을 하고 "
          f"여러 비교를 함께 볼 때의 보정(Holm)을 한 p값이 {m32.sign_p_holm:.4f}·{m32.wilcoxon_p_holm:.4f}로 0.05보다 작아 유의하게 나쁨). "
          f"그 악화의 절반가량은 목록 정보의 내용 때문이 아니었다. 목록 자료가 2022년부터만 있어 그 전 학습 자료의 목록 칸은 비어 있는데, 모델이 '목록 칸이 비었는가'를 시기 구분으로 썼다. "
          f"목록 칸에 내용 없이 0만 넣은 판도 기계학습 모델보다 나빴다(MASE로 {sg(100 * N['exp'].loc[(MAIN, 'E1_null', 'M2'), 'rel_diff'])}%, 목록 정보를 더한 모델은 {sg(100 * m32.rel_diff)}%).",
          "- 청구금액으로 가중해도 결론(목록 정보가 개선하는가, 기계학습 모델이 작년 값을 이기는가)은 " + ("같다." if keep_w else "**달라진다**: 내부 문서 참고."), "",
          "**대상을 넓혀도 같은가** (공개 시차 5개월 = 마지막 자료에서 6개월 앞, 시차 0 = 1개월 앞. 오차 WAPE / 청구금액 가중 WAPE)", "",
          "| 대상 | 공개 시차 | 작년 같은 달 값 | 기계학습 모델 | 목록 정보 추가 | 목록 정보가 개선했나 |", "|---|---|---|---|---|---|"]
    for name, e_, w_, cc in (("미리 정한 주 대상 32개", None, w32, c), ("처방량 상위 200개 (결과를 본 뒤 넓힘)", t2, t2, t2c), ("층화 200개 (결과를 본 뒤 넓힘)", st, st, stc)):
        for lag in (MAIN, REF):
            if e_ is None:
                vals = [f"{pct(wt.loc[m, lag])} / {pct(w32.loc[(m, lag), 'wape_amt'])}" for m in ("M0", "M2", "M3")]
            else:
                vals = [f"{pct(e_.loc[(m, lag), 'wape'])} / {pct(e_.loc[(m, lag), 'wape_amt'])}" for m in ("M0", "M2", "M3")]
            v = cc.loc[(lag, "M3", "M2"), "verdict_holm"]
            L.append(f"| {name} | {lag}개월 | " + " | ".join(vals) + f" | {'아니오' if v != 'M3가 나음' else '예'} |")
    L += ["", "층화 200개 = 약효분류 앞 2자리 × 2022년 청구금액 3등분 × 업체 수 구간으로 나눈 층에서 크기에 비례해 무작위로 뽑은 200개(규칙은 실행 전에 정함).", "",
          "## 3. 이 결과를 믿을 수 있는가", "",
          f"1. **미래 자료 차단 시험** (P2 자동 시험 {N['n_tests_p2']}개, 마지막 실행에서 전부 통과): 예측하는 날 이후의 사용통계를 바꾸거나 지워도 예측이 그대로다. 예측하는 날 이후의 목록 파일을 지우고 변화 기록표를 다시 만들어도 목록 정보와 예측이 그대로다. "
          f"일부러 미래 자료를 섞는 실수 5가지를 넣으면 시험이 모두 잡았다. 원본 사용통계 파일 96개를 따로 다시 읽은 합이 예측용 자료와 {int(N['reagg']['both_cells']):,}칸(관측된 전 성분 × 월)에서 같다.",
          f"2. **일부러 효과를 넣은 시험**: 인하 예고가 있는 칸(공개 시차 5개월에서 평가 {int(N['inj_n'][(MAIN, 'E6_M3', True)])}칸, 시차 0에서 {int(N['inj_n'][(REF, 'E6_M3', True)])}칸)의 실제 처방량을 20% 줄여 넣었다. 그 칸의 오차(MASE)가 공개 시차 5개월에서는 줄었지만({inj.loc[(MAIN, 'E6_M2', True)]:.2f} → {inj.loc[(MAIN, 'E6_M3', True)]:.2f}, 일부만 검출), 시차 0에서는 줄지 않았다. "
          "효과를 다 잡았다면 그 칸의 오차가 다른 칸 수준으로 내려가야 하는데 그렇지 않았다. 이 설정은 20% 크기의 효과도 일부만 잡는다. 그래서 결론은 '개선하지 못했다'이지 '효과가 없다'가 아니다.",
          "3. **실제로 일어난 변화를 미리 알려 준 시험** (성분 32개에서만): 목록 예고 대신, 나중에 실제로 일어난 약가 변경·등재·삭제·급여중지를 미리 넣었다. 처방량 목표에서도, 가격이 그대로 반영되는 청구금액 목표에서도 개선되지 않았다(여러 비교를 보정한 검정에서 차이 없음).",
          "4. **시점을 옮긴 시험**: 목록 정보를 반영하는 기간을 −3 ~ +3개월 옮겨도 개선되는 시점이 없었다.",
          f"5. **개선의 상한** (성분 32개): 평가 칸 {int(ock.loc[MAIN, 'n_eval']):,}개에서 '마지막으로 관측한 달 → 예측하는 달'의 청구금액 변화(로그)를 수량 변화와 단가 변화로 나누면, 단가 변화가 차지하는 몫은 분산 기준 {pct(ock.loc[MAIN, 'price_share'])}(시차 5) / {pct(ock.loc[REF, 'price_share'])}(시차 0)뿐이다. "
          f"실제 가격 변화를 미리 알아도 오차를 줄일 수 있는 상한은 약 {pct(ock.loc[MAIN, 'improvement_ceiling'])} / {pct(ock.loc[REF, 'improvement_ceiling'])}다(제곱오차 기준 근사). "
          f"미리 알려 준 가격 정보는 실제 단가 변화를 잘 따라갔다(상관 {ock.loc[MAIN, 'corr_nonzero']:.2f} / {ock.loc[REF, 'corr_nonzero']:.2f}). 가격 정보가 들어간 학습 자료는 {o5.rows_price_nonzero.min()} ~ {o5.rows_price_nonzero.max()}행으로 적었다(이 점이 원인인지는 따로 검증하지 않았다).", "",
          "## 4. 전국 성분 단위 가상 재고 시험 (가정 먼저)", "",
          "약국 재고가 아니다. 가정: 전국 성분 단위, 매달 따로(남은 재고를 넘기지 않음), 발주 후 1개월에 도착, 품절 비용 ÷ 재고 비용 = 1·3·9(가정값). "
          f"공개 시차 5개월에서 정한 재고가 실제 수요를 덮은 비율이 {pct(lo.coverage)}({POL.get(lo.policy, lo.policy)}, 목표 {pct(lo.tau, 0)}) ~ {pct(hi.coverage)}({POL.get(hi.policy, hi.policy)}, 목표 {pct(hi.tau, 0)})로 목표와 맞지 않아, "
          "비용 순위로 어느 예측이 재고에 쓸모 있다고 읽지 않는다. 예측 3개 × 비용 비율 3개의 전체 표는 내부 문서 [P2 결과](P2_min_result.md) §6.", "",
          "## 5. 공고 문구 대응", "",
          "| 공고 문구 | 근거 |", "|---|---|",
          "| 약국 구매, 판매, 사용 패턴 분석 기반 예측 모델 개발 | 약국 단위 자료는 없다. 성분 × 전국 처방량 예측(위 §2). 미래 자료 차단 시험으로 예측하는 날 이후의 자료 행이 섞이지 않았음을 확인하고(수정본 자료 사용과 결과를 본 뒤의 규칙·대상 선택은 이 시험이 잡지 못한다), 효과를 넣은 시험 등으로 이 설정의 검출력이 낮음을 보였다(§3) |",
          "| 의약품 유통 데이터 기반 통계 분석 및 알고리즘 개발 | 유통 데이터는 없다. 전국 성분 단위 가상 재고 시험(§4)은 참고만 |",
          "| 심평원 빅데이터 및 처방 데이터 활용 (우대) | 건보공단 사용통계 96개월 × 심평원 급여목록 처분 예고 |", ""]
    L += X.next_table(P2)
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
    import ext_core as X
    P2 = X.p2_numbers()
    e, t2 = P2["e"], P2["t2"]
    sg = pd.read_csv(O / "extra_signif.csv").set_index(["set", "lag", "a", "b"])
    return "\n".join([RD_START,
        f"**P2 처방 수요 예측**: 성분별 전국 처방량을 마지막 자료에서 6개월 앞(공개 시차 5개월 가정) 예측했다(약국 단위 아님). 미리 정한 주 대상(호흡기 계열 32개)에서는 작년 같은 달 값이 더 나았고({pct(e.loc[('M0', MAIN), 'wape'])} 대 모델 {pct(e.loc[('M2', MAIN), 'wape'])}), "
        f"결과를 본 뒤 넓힌 상위 200개에서는 모델이 조금 나았다({pct(t2.loc[('M2', MAIN), 'wape'])} 대 {pct(t2.loc[('M0', MAIN), 'wape'])}, 차이는 거의 2023년 상반기). "
        "급여목록 처분 예고를 더해도 개선되지 않았다(실제 변화를 미리 넣은 시험은 32개에서만). 2쪽 요약: [reports/P2_SUMMARY.md](reports/P2_SUMMARY.md). 실행: `make p2-min`.",
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
    import ext_core as X
    core_blk = "\n".join([CORE_START, *X.core(X.p1_numbers(), X.p2_numbers()), CORE_END])
    t = put(t, CORE_START, CORE_END, core_blk, "<!-- P1 요약 시작", after=False)
    t = t.replace("| 질문 | 비고의 예고는 실제 변화로 얼마나, 얼마 만에 이어지나 | 사용통계 공백 5개월을 목록 신호로 얼마나 메울 수 있나 |",
                  "| 질문 | 비고의 예고는 실제 변화로 얼마나, 얼마 만에 이어지나 | 사용통계가 5개월 늦게 공개될 때 다음 달 성분 수요를 맞추나, 목록 신호를 넣으면 나아지나 |")
    t = t.replace("| 데이터 | 급여목록·대체조제 목록 2022-01 ~ 2025-12, 건보공단 사용통계 | 사용통계 2018-01 ~ 2025-12, P1 신호 |",
                  "| 데이터 | 급여목록·대체조제 목록 2022-01 ~ 2025-12, 건보공단 사용통계 | 사용통계 2018-01 ~ 2025-12, P1 신호(2022-01 ~) |")
    t = t.replace("| | P1 선행 신호 원장 (완료) | P2 수요 예측 (설계만) |", "| | P1 선행 신호 원장 (완료) | P2 수요 예측 (최소판 완료, 실험 동결) |")
    t = t.replace("| 산출물 | 원장, 분석 A·B·C, `make report MONTH=YYYY-MM` 월간 리포트 | [docs/PLAN_P2.md](docs/PLAN_P2.md) |",
                  "| 산출물 | 원장, 분석 A·B·C, `make report MONTH=YYYY-MM` 월간 리포트 | `make p2-min`, [2쪽 요약](reports/P2_SUMMARY.md), [결과](reports/P2_min_result.md), 설계 [PLAN_P2](docs/PLAN_P2.md) |")
    t = t.replace("| 사용통계가 5개월 늦게 공개될 때 다음 달 성분 처방량을 맞추나,", "| 사용통계가 5개월 늦게 공개된다고 둘 때 마지막 자료에서 6개월 앞 성분 처방량을 맞추나,")
    t = t.replace("P2 단계는 구현한 뒤 `all`에 넣는다.", "P2 최소판은 `make p2-min`(T1 → T2 → T3)으로 따로 돌린다(`all`에 넣지 않았다).")
    t = t.replace("| [reports/SUMMARY.md](reports/SUMMARY.md) | 2쪽 요약 (v5.3, `scripts/p1_docs.py` 생성) |",
                  "| [reports/SUMMARY.md](reports/SUMMARY.md) | P1 2쪽 요약 (v5.3, `scripts/p1_docs.py` 생성) |\n| [reports/P2_SUMMARY.md](reports/P2_SUMMARY.md) | P2 2쪽 요약 (최소판, `scripts/p2_docs.py` 생성) |")
    rd.write_text(t, encoding="utf-8")
    pl = ROOT / "docs/PLAN_P2.md"
    u = pl.read_text(encoding="utf-8")
    blk = "\n".join([PL_START, "", "(2026-11-19 갱신: 최소판 실제 결과. 아래 원래 표는 설계 때의 계획이다.)", "", *plan_table(N), "", PL_END])
    u = put(u, PL_START, PL_END, blk, "## 8. 바로팜 공고와의 대응")
    pl.write_text(u, encoding="utf-8")
    import ext_core as X
    errs = check(out, s) + check(rd, rd.read_text(encoding="utf-8")) + check(pl, blk)
    errs += X.check_external(out, s) + X.check_external(rd, rd.read_text(encoding="utf-8"))
    if errs:
        raise SystemExit("\n".join(errs))
    print(f"P2_SUMMARY {len(s.splitlines())}줄, README P2 블록, PLAN_P2 §8 대응표. 검사 통과 (P2 시험 {N['n_tests_p2']}개)")


if __name__ == "__main__":
    main()
