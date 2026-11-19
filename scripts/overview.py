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
    pr_n = P1["price_B_n"]
    L = ["# 급여목록 처분 예고와 처방 수요 — 1쪽 개요", "",
         f"**한 줄 요약**: {T['one_p1']} 처방 수요 예측(P2)은 재설계 중이다.", "",
         f"**무엇을 했나**: {lo_m[:4]}~{hi_m[:4]}년 월별 건강보험 급여목록 {n_list}개의 비고(메모 칸)에 미리 적힌 처분 예고(약값 인하·신규 등재·급여정지·중지 예정)를 추적해 실현됐는지 기록했다. "
         f"작성자 1인, Python·DuckDB SQL, 자동 시험 {n_tests}개. 처방 수요 예측(P2)은 재설계 중이다.", "",
         "**결론**", "",
         "1. **약값 인하 예고는 대부분 실현됐고, 급여정지·중지 예정이 법원 집행정지로 막힌 경우는 처분 기준으로 소수였다.** "
         "급여목록 비고의 예고는 이미 발표된 고시를 옮겨 적은 것이라 실현율이 높은 것이 자연스럽다.",
         f"- 약값 인하 예고: 결과가 난 제품 {pr_n:,}개 중 {pct(P1['price_B'])} 실현(늦은 시점·다른 금액 포함), 예정일 그대로 {pct(P1['price_on_time'])}.",
         f"- 급여정지·중지 예정: 처분 {P1['sus_units']}건 중 {P1['sus_units_all_realized']}건({pct(P1['sus_units_all_realized'] / P1['sus_units_resolved'])}) 실현, 나머지 {P1['sus_units_blocked']}건은 법원 집행정지로 막힘. "
         f"제품 기준 현재 실현율 {pct(P1['sus_A'])}, 예정일 그대로 {pct(P1['sus_on_time'])}.",
         f"- 가격이 바뀐 제품의 {pct(P1['unannounced'])}는 비고에 따로 적히지 않았고, 목록 고시로 시행됐다(대부분 일괄 인하 3개 달).",
         "2. 처방 수요 예측(P2): 재설계 중.",
         "3. 급여목록 예고 정보와 수요 예측(P2): 재설계 중.", "",
         "**바로팜에 주는 의미**", "", *T["mean_p1"], "- 수요 예측 쪽 의미(P2): 재설계 중.", "",
         "**검증 방법**", "",
         f"- **사람 판정과 대조**: 규칙을 고치기 전 판정(v5.2) 30건의 유형 일치 {pct(P1['hold_init'])}(작성자 1인 라벨, 불일치 2건을 고치면 {pct(P1['hold_after'])}). 규칙을 고친 v5.3은 판정 결과를 가린 새 표본 30건으로 잰다(라벨 대기).",
         f"- **외부 사실 대조**: 동아에스티 급여정지의 서울행정법원 잠정 집행정지 결정일({COURT})이 목록 표시 날짜와 일치했다(규칙의 날짜 순서 가정과 맞는 사례 1건).", "",
         "**한계와 다음 실험 (상위 3개)**", "",
         "| 한계 | 다음 실험 |", "|---|---|",
         "| 규칙을 고친 판(v5.3)의 판정 정밀도를 아직 재지 않았다 | 새 검증 표본 30건(라벨 대기), 두 번째 라벨러 |",
         "| 급여정지(제재)와 급여중지(허가 관련)의 사유가 섞였고 비고로는 나눌 수 없다 | 사유별 분해(외부 자료 필요) |",
         "| 급여목록에 없는 집행정지는 놓친다. 외부 근거는 1건만 넣었다 | 외부 집행정지 체계 탐색 |", "",
         "**더 볼 곳**: [처분 예고 요약](../reports/SUMMARY.md) · [처방 수요 예측 요약(재설계 전)](../reports/P2_SUMMARY.md) · [한계 전체](LIMITATIONS.md) · [재현 방법](../reports/REPRODUCTION.md)", "",
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
