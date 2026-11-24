"""P2 v3 보고서 '현실적 권장안'의 근거 숫자: 전년 같은 달 대비 흔들림이 큰 성분의 금액 비중과 오차 기여.

입력: outputs/p2v3/ingredient_metrics.csv (make p2v3-group 산출; 흔들림 yoy_sd = 2023-01 ~ 2025-12 sd(log(y_t ÷ y_{t−12})),
      오차 = P2 v2 백테스트 G의 성분 WAPE, 가중치 w).
기준: yoy_sd ≥ 0.30 (약 ±30%). 평가 기간을 돌아본 서술이며, 운영 플래그는 예측 시점 이전 자료로 정해야 한다.
사용: python -I scripts/p2v3_volatile_share.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
THRESHOLD = 0.30


def main() -> None:
    i = pd.read_csv(ROOT / "outputs/p2v3/ingredient_metrics.csv", dtype={"c4": str})
    i["contrib"] = i.w * i.wape_G
    hi = i.yoy_sd >= THRESHOLD
    lo = i.yoy_sd < THRESHOLD
    wavg = lambda m: (i.w[m] * i.wape_G[m]).sum() / i.w[m].sum()
    print(f"성분 {len(i)}개, 흔들림 계산 {int(i.yoy_sd.notna().sum())}개")
    print(f"흔들림 ±{THRESHOLD:.0%} 이상: {int(hi.sum())}개, 금액 비중 {i.w[hi].sum():.1%}, "
          f"금액 가중 오차 기여 {i.contrib[hi].sum() / i.contrib.sum():.1%}")
    print(f"평균 오차(금액 가중): 고변동 {wavg(hi):.1%}, 나머지 {wavg(lo):.1%}")
    print("ATC 1단계:", i[hi].atc2_raw.str[0].value_counts().head(6).to_dict())


if __name__ == "__main__":
    main()
