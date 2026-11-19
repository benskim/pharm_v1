"""1쪽 포트폴리오 개요 docs/OVERVIEW.md 생성 (2026-11-19 사용자 지시). 외부용: 내부 용어 금지, 숫자는 결과 파일에서 계산·대조.
먼저 scripts/p2_min_report.py, p2_docs.py, p1_docs.py가 돈 뒤 실행한다 (같은 결과 파일을 읽는다).
사용: python -I scripts/overview.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ext_core as X  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
O = ROOT / "outputs/p2"
OUT = ROOT / "docs/OVERVIEW.md"
pct = X.pct


def main() -> None:
    P1, P2 = X.p1_numbers(), X.p2_numbers()
    e, t2, ock = P2["e"], P2["t2"], P2["ock"]
    rv = P2["rv"].loc["약효분류군 (주)"]
    sg = pd.read_csv(O / "extra_signif.csv").set_index(["set", "lag", "a", "b"])
    top_sig = sg.loc[("상위 200", 5, "M2", "M0"), "verdict"] == "M2가 나음"
    inj = pd.read_csv(O / "exp_inject.csv").set_index(["lag", "model", "injected"])["mase"]
    import re
    import subprocess
    n_tests = int(re.search(r"(\d+) tests? collected", subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"], cwd=ROOT,
                  capture_output=True, text=True, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}).stdout).group(1))
    X.core(P1, P2)   # 결론 문장의 숫자 대조(assert)를 같이 돈다
    import duckdb
    c = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    n_usage = c.execute("select count(*) from stg_usage").fetchone()[0]
    n_list = c.execute("select count(distinct list_month) from stg_benefit").fetchone()[0]
    pr = P1["price_B_k"], P1["price_B_n"]
    lo_m, hi_m = c.execute("select min(list_month), max(list_month) from stg_benefit").fetchone()
    assert (n_list, lo_m, hi_m) == (48, "2022-01", "2025-12")
    # 가중 오차: 상위 200, 5개월 지연. 평가 달을 바꿔도(공통 24개월·①에만 있는 11개월) 모델이 낮은지 대조
    wa2, wa0 = t2.loc[("M2", 5), "wape_amt"], t2.loc[("M0", 5), "wape_amt"]
    win = pd.read_csv(O / "extra_window.csv").set_index(["set", "part"]).loc["상위 200"]
    assert wa2 < wa0 and (win["wape_amt_m2"] < win["wape_amt_m0"]).all()
    assert abs(P1["unannounced"] - 2 / 3) < 0.01   # '3분의 2'
    # 외부 사실 대조: 서울행정법원 집행정지 잠정 인용 2022-05-09 (docs/RULES_2026-11-17.md 외부 검증 사례). 데이터의 목록 집행정지 날짜와 같은지
    COURT = "2022-05-09"
    n_donga = c.execute("select count(*) from stay_v5 where stay_date = ? and target_source = 'external'", [COURT]).fetchone()[0]
    assert n_donga == 72
    # 가짜 효과 크기: E6 주입 배수 INJECT(실측 × 0.8 = 20% 감소)를 실험 코드에서 읽는다
    inj_k = round(100 * (1 - float(re.search(r"^INJECT = ([0-9.]+)", (ROOT / "src/pbd/forecast/t3_experiments.py").read_text(encoding="utf-8"), re.M).group(1))))
    assert inj_k == 20
    T = X.texts(P1, P2)
    strip = lambda x: x.replace("   - ", "- ")   # noqa: E731  하위 항목을 개요에서는 한 단계로
    L = ["# 급여목록 처분 예고와 처방 수요 — 1쪽 개요", "",
         f"**한 줄 요약**: {T['one']}", "",
         f"**무엇을 했나**: {lo_m[:4]}~{hi_m[:4]}년 월별 건강보험 급여목록(보험 적용 약과 상한가 목록) {n_list}개의 메모 칸에 미리 적힌 처분 예고(약값 인하·신규 등재·급여중지 공지)를 월별 파일로 추적해, 실현됐는지 기록했다. "
         "같은 정보를 더하면 성분별 전국 처방량(병원 밖 약국 조제분) 예측(마지막 자료에서 6개월 앞, 공개 시차 5개월 가정)이 나아지는지 시험했다.", "",
         f"**규모와 도구**: 작성자 1인. 의약품 사용통계 96개월(약 {n_usage / 1e4:,.0f}만 행). Python·DuckDB SQL, LightGBM, 자동 시험 {n_tests}개.", "",
         "**결론**", "",
         *[strip(x) for x in T["c1"]], *[strip(x) for x in T["c2"]], *T["c3"], "",
         "**바로팜에 주는 의미**", "", *T["mean"], "",
         "**검증 방법**", "",
         "- **사람 판정과 대조**: " + T["prec"],
         "- **미래 자료 차단 시험**: 예측하는 날 이후의 자료 행이 섞이지 않는지, 일부러 섞으면 시험이 잡는지 확인했다. 수정본 자료 사용과 결과를 본 뒤의 규칙·대상 선택은 이 시험이 잡지 못한다.",
         f"- **답을 미리 알려 준 시험** (성분 32개): 실제로 일어난 가격 변경·등재·삭제를 미리 넣어도 예측이 나아지지 않았다. 가짜 효과(인하 예고가 있는 성분의 처방량 {inj_k}% 감소)를 일부러 심어도 모델이 일부만 찾아냈다. 작은 효과는 이 자료로 잡기 어렵다.",
         f"- **외부 사실 대조**: 동아에스티 급여중지 집행정지의 법원 결정일({COURT})이 목록 표시 날짜와 일치했다. 규칙의 날짜 순서 가정과 맞는 사례 1건이다.", "",
         "**한계와 다음 실험 (상위 3개)**", "",
         "| 한계 | 다음 실험 |", "|---|---|",
         "| 규칙을 고친 판의 판정 정밀도를 따로 재지 않았다 | 판정 결과와 규칙 용어를 가린 새 검증 표본 30건(라벨 대기), 두 번째 라벨러 |",
         "| 비교 기준이 작년 같은 달 값 하나뿐이고, 코로나 회복기(2022)는 표시하지 않았다 | 단순 시계열 기준 추가, 회복기 표시 또는 2022 제외 민감도 |",
         "| 사용통계는 심사가 끝난 청구까지만 담겨 최근 달이 나중에 늘 수 있다 | 같은 달 자료를 매달 다시 받아 수정 폭을 재고 최근 달을 보정한다 |", "",
         "**더 볼 곳**: [처분 예고 요약](../reports/SUMMARY.md) · [처방 수요 예측 요약](../reports/P2_SUMMARY.md) · [한계 전체](LIMITATIONS.md) · [재현 방법](../reports/REPRODUCTION.md)", "",
         "<sub>공개 데이터(건강보험심사평가원 급여목록 2022~2025, 국민건강보험공단 의약품 사용통계 2018~2025)만 썼다. 숫자는 `scripts/overview.py`가 결과 파일에서 계산해 넣었다.</sub>", ""]
    text = "\n".join(L)
    errs = X.check_external(OUT, text)
    for m in re.finditer(r"\]\(([^)#\s]+)\)", text):
        if not (OUT.parent / m.group(1)).resolve().exists():
            errs.append(f"링크 대상 없음 {m.group(1)}")
    if errs:
        raise SystemExit("\n".join(errs))
    OUT.write_text(text, encoding="utf-8")
    print(f"OVERVIEW {len(L)}줄, 검사 통과")


if __name__ == "__main__":
    main()
