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
    L = ["# 급여목록 처분 예고와 처방 수요 — 1쪽 개요", "",
         f"**한 줄 요약**: 건강보험 약값 인하 예고는 거의 다 실행되지만 급여중지 예고는 절반 가까이 법원에서 막혔다. 공개 통계만으로도 넓은 범위의 성분 수요는 예측할 수 있지만, "
         "정확도는 자료가 얼마나 최근 것인지에 달려 있고, 급여목록의 예고 정보는 예측을 개선하지 못했다.", "",
         "**무엇을 했나**: 건강보험 급여목록(보험 적용 약과 상한가 목록)의 메모 칸에 미리 적힌 처분 예고(약값 인하·신규 등재·급여중지 공지)를 월별 파일로 추적해, 예고대로 실행됐는지 기록했다. "
         "같은 정보를 더하면 성분별 전국 처방량(병원 밖 약국 조제분)의 다음 달 예측이 나아지는지 시험했다.",
         "",
         f"**규모와 도구**: 작성자 1인. 급여목록 {n_list}개월, 의약품 사용통계 96개월(약 {n_usage / 1e4:,.0f}만 행). Python·DuckDB SQL, 기계학습 모델은 LightGBM, 자동 시험 {n_tests}개.", "",
         "**결론**", "",
         f"1. **약값 인하 예고는 믿을 만하고, 급여중지 예고는 법원에서 막힐 수 있다.** 예정일이 지나 결과를 확인할 수 있는 약값 인하 예고 {pr[1]:,}건 중 {pct(P1['price_B'])}가 예고대로 실행됐다. "
         f"급여중지 예고는 {P1['sus_stay_n']}건 중 {pct(P1['sus_stay'])}가 법원 집행정지로 막혔고, 막힌 {P1['sus_stay_k']}건 중 {P1['top2']}건이 두 회사 제품이다. "
         f"약값 변화 {P1['n_price']:,}건 중 {pct(P1['unannounced'])}는 미리 예고되지 않았다(정부가 한꺼번에 내린 3개월을 빼면 {pct(P1['unannounced_rest'])}).",
         f"2. **예측 정확도는 자료가 얼마나 최근 것인지가 좌우한다.** 통계가 5개월 늦게 공개되는 실제 조건에서, 처방량 상위 200개 성분은 모델 오차가 "
         f"작년 같은 달 처방량을 그대로 쓴 예측보다 {'작았다' if top_sig else '비슷했다'}({pct(t2.loc[('M2', 5), 'wape'])} vs {pct(t2.loc[('M0', 5), 'wape'])}). 지난달 자료가 있다면 {pct(t2.loc[('M2', 0), 'wape'])}. "
         f"계절을 크게 타는 호흡기 계열 32개 성분은 작년 값이 더 나았다({pct(e.loc[('M0', 5), 'wape'])} vs {pct(e.loc[('M2', 5), 'wape'])}). 평가하는 달에 따라 차이가 줄거나 뒤집힐 수 있다.",
         f"3. **급여목록의 예고 정보는 성분 전체 처방량 예측을 개선하지 못했다.** 성분 묶음을 바꿔도, 실제로 일어난 변화를 미리 알려 줘도 같았다. "
         f"가격을 미리 알아도 줄일 수 있는 오차는 약 {round(100 * ock.loc[5, 'improvement_ceiling'])}%뿐이었고, 작은 효과는 잡기 어려운 설정이다.", "",
         "**바로팜에 주는 의미**", "",
         f"- 공개 통계만으로도 넓은 범위의 성분 월 수요를 예측할 수 있다(상위 200개, 5개월 지연 조건, 청구금액 가중 오차 {pct(t2.loc[('M2', 5), 'wape_amt'])}).",
         "- 정확도가 자료의 신선도에 달려 있으므로 약국 주문·판매처럼 바로 쌓이는 자료가 도움이 될 것으로 본다(검증하지 않은 가정).",
         "- 급여목록 정보는 수요 예측보다 약국 알림에 맞다: 약값 인하 예고는 신뢰도가 높고, 급여중지 예고는 법원에서 막힐 수 있다는 표시가 필요하다. 다만 약값 변화의 3분의 2는 예고 없이 온다(시험하지 않은 제안).", "",
         "**검증 방법**", "",
         f"- **처음 보는 검증 표본**: 자동 판정 30건을 사람이 따로 정답을 달아 맞춰 봤다. 변화 종류 {pct(P1['hold_init'])}(처음 정답) / {pct(P1['hold_after'])}(판정을 본 뒤 2건 고친 정답), 날짜 {pct(P1['hold_date'], 0)} 일치.",
         "- **미래 자료 차단 시험**: 예측하는 날 이후의 자료가 섞이지 않는지, 일부러 섞으면 시험이 잡는지 확인했다.",
         "- **답을 미리 알려 준 시험**: 실제로 일어난 가격 변경·등재·삭제를 미리 넣어도 예측이 나아지지 않았다. 처방량을 일부러 20% 바꿔 넣으면 일부만 잡혔다.", "",
         "**한계와 다음 실험 (상위 3개)**", "",
         "| 한계 | 다음 실험 |", "|---|---|",
         "| 공개 통계는 성분 합계라 제품별 처방 비중을 볼 수 없다 | 제품 단위 자료로 인하 예고 제품의 비중 변화를 본다 |",
         "| 사용통계는 심사가 끝난 청구까지만 담겨 최근 달이 나중에 늘 수 있다 | 같은 달 자료를 매달 다시 받아 수정 폭을 재고 최근 달을 보정한다 |",
         "| 약국 단위 자료가 없다 | 약국 주문·판매 자료에 같은 방법을 적용한다 |", "",
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
