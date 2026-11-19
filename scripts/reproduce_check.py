"""재현 숫자 대조 (2026-11-19 사용자 지시): 원래 폴더와 깨끗한 클론 폴더의 주요 결과가 같은지 자동으로 본다.

1. 결과 CSV(처분 예고 비율의 근거, 예측 주 결과·추가 확인)를 숫자 칸은 상대 1e-9, 글자 칸은 그대로 대조
2. 생성 문서(README, SUMMARY, P2_SUMMARY, OVERVIEW, P2 결과, LIMITATIONS)를 줄 단위로 대조. 시험 실행 시간("passed in …")만 가린다
다르면 다른 곳을 출력하고 종료 코드 1.
사용: python -I scripts/reproduce_check.py <원래 폴더> <재현 폴더>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CSVS = ["outputs/p2/" + f for f in (
    "t1_target_steps.csv", "t1_reagg_check.csv", "t1_coverage.csv", "t3_errors.csv", "t3_compare.csv", "t3_compare_claim_amt.csv", "t3_order_sim.csv",
    "exp_compare.csv", "exp_null_seed.csv", "exp_inject.csv", "exp_e1_constant.csv", "t3_horizons.csv", "diag_compare.csv", "diag_compare_claim_amt.csv",
    "oracle_check.csv", "oracle_check_train.csv", "extra_revision_sensitivity.csv", "extra_revision_verdict.csv", "extra_weighted_32.csv",
    "extra_horizon.csv", "extra_horizon_top200.csv", "extra_top200_errors.csv", "extra_top200_compare.csv", "extra_strat200_errors.csv",
    "extra_strat200_compare.csv", "extra_strat200_coverage.csv", "extra_signif.csv", "extra_window.csv")] + [
    "outputs/s5/b1_main.csv", "outputs/s4/a4_selection.csv", "outputs/validation/holdout_precision_c63778c.csv"]
DOCS = ["README.md", "reports/SUMMARY.md", "reports/P2_SUMMARY.md", "docs/OVERVIEW.md", "reports/P2_min_result.md", "docs/LIMITATIONS.md"]
MASK = re.compile(r"\d+ passed(, \d+ \w+)* in [\d.]+s( \(\d+:\d+:\d+\))?")


def cmp_csv(a: Path, b: Path) -> list[str]:
    if not b.exists():
        return ["재현 폴더에 없음"]
    x, y = pd.read_csv(a, dtype=str), pd.read_csv(b, dtype=str)
    if x.shape != y.shape or list(x.columns) != list(y.columns):
        return [f"모양 다름 {x.shape} vs {y.shape}"]
    out = []
    for c in x.columns:
        xn, yn = pd.to_numeric(x[c], errors="coerce"), pd.to_numeric(y[c], errors="coerce")
        if xn.notna().all() and yn.notna().all():
            if not np.allclose(xn, yn, rtol=1e-9, atol=1e-12):
                i = int(np.argmax(np.abs(xn - yn)))
                out.append(f"칸 {c} 숫자 다름 (행 {i}: {x[c][i]} vs {y[c][i]})")
        elif not x[c].fillna("").equals(y[c].fillna("")):
            out.append(f"칸 {c} 글자 다름")
    return out


def cmp_doc(a: Path, b: Path) -> list[str]:
    if not b.exists():
        return ["재현 폴더에 없음"]
    la = [MASK.sub("<시험 시간>", s) for s in a.read_text(encoding="utf-8").splitlines()]
    lb = [MASK.sub("<시험 시간>", s) for s in b.read_text(encoding="utf-8").splitlines()]
    diff = [i for i, (p, q) in enumerate(zip(la, lb)) if p != q]
    out = [f"줄 수 다름 {len(la)} vs {len(lb)}"] if len(la) != len(lb) else []
    return out + [f"줄 {i + 1} 다름: {la[i][:80]} | {lb[i][:80]}" for i in diff[:5]]


def main() -> None:
    orig, rep = Path(sys.argv[1]), Path(sys.argv[2])
    bad = 0
    for rel in CSVS + DOCS:
        r = (cmp_csv if rel.endswith(".csv") else cmp_doc)(orig / rel, rep / rel)
        print(f"{'같음' if not r else '다름'}  {rel}")
        for x in r:
            print("     ", x)
        bad += bool(r)
    print(f"대조 {len(CSVS) + len(DOCS)}개 중 다른 것 {bad}개")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
