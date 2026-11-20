"""달력 특징 (PLAN_P2_v2 §5-4 dwork·lunar). 공휴일 = PyPI kr_holidays 1.0.0의 법정·대체·임시공휴일 모두
(사용자 결정 2026-11-20 Q16·§9-2 확인 3b). 판 검증(docs/HOLIDAYS_KR_CHECKLIST.md 대조)은 아직 하지 않았다(확인 3c).
"""
from __future__ import annotations

import calendar
import datetime as dt
from functools import lru_cache

import kr_holidays


@lru_cache(maxsize=None)
def _hol(year: int) -> frozenset:
    return frozenset(kr_holidays.get_holidays(year))


def workdays(month: str) -> int:
    """그 달의 평일(월 ~ 금) 중 공휴일이 아닌 날 수"""
    y, m = int(month[:4]), int(month[5:7])
    n = calendar.monthrange(y, m)[1]
    return sum(1 for d in range(1, n + 1)
               if dt.date(y, m, d).weekday() < 5 and dt.date(y, m, d) not in _hol(y))


@lru_cache(maxsize=None)
def lunar(month: str) -> int:
    """그 달에 설날·추석 연휴(대체공휴일 포함)가 있으면 1"""
    y, m = int(month[:4]), int(month[5:7])
    for d in _hol(y):
        if d.month == m:
            name = kr_holidays.get_holiday_name(d) or ""
            if "설날" in name or "추석" in name:
                return 1
    return 0
