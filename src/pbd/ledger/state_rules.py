"""제품×월 상태의 날짜 규칙 (docs/PLAN_P1.md §4 state, docs/NOTE_RULES.md R1·R3·R3b·R6, §2).

한 제품의 급여목록 관측 달 목록과 그 달들의 비고 토큰(두 목록 합침)을 받아,
달마다 등재 예정·급여정지 여부, exposure_frac, 고쳐 쓰기 표시를 돌려준다. 순수 함수다.

규칙 요약
- 월 상태는 월말(그 달 마지막 날) 기준. 단 등재 예정(scheduled_listing)은 스냅샷 기준일 기준이다
  (완료 기준: 2025-11의 '25.11.23 등재' 27행 = scheduled_listing 17 / reimbursed 10. DECISIONS 2026-11-14 S2).
- exposure_frac = 그 달 급여된 일수 / 그 달 일수.
- 미래 날짜 토큰(신호)은 다음 스냅샷에서 확인되기 전까지 상태를 바꾸지 않는다.
  확인되면 신호 달의 행을 고쳐 쓰고 exposure_revised = True.
  · R3 미래 정지일: 정지일 이후 첫 스냅샷에 같은 (제품, 정지일) 토큰이 과거 날짜로 있으면 확인.
  · R3b 미래 시작일: 같음. R3b 미래 종료일: 종료일 이후 첫 스냅샷에 제품이 있고 새 정지 토큰이 없으면 확인.
  · R1 미래 등재일: 등재일 이후 첫 스냅샷에 제품이 있고 정지·등재 예정이 아니면 확인.
    확인 전 exposure_frac = 0, 확인 뒤 등재일이 든 달의 행을 (월말 − 등재일 + 1) / 일수로 고쳐 쓴다.
- R3 (종료일 없음) 복귀: (a) R6 해제 토큰(해제일 ≥ 정지일), (b) 두 목록 어디에도 그 토큰이 없는 달 2개월 연속.
  (b)는 첫 공백 달 1일에 복귀하고 첫 공백 달 행을 고쳐 쓴다(state_revised). 1개월 공백 뒤 재등장은 정지 유지.
  분석 끝 달이 1개월 공백이면 resume_unconfirmed. 1개월 공백 뒤 제품이 사라지면(삭제) 정지 유지.
- R3b (기간 명시): 비고 유무가 아니라 시작일~종료일로 판정한다. 처음 관측된 달부터 적용한다.
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field

ONE = dt.timedelta(days=1)


@dataclass(frozen=True)
class Tok:
    type: str                      # R1 / R3 / R3b / R6
    date: dt.date
    date_end: dt.date | None = None


@dataclass
class MonthOut:
    scheduled: bool = False
    suspended: bool = False
    exposure_frac: float = 1.0
    exposure_revised: bool = False
    state_revised: bool = False
    resume_unconfirmed: bool = False
    listing_date: dt.date | None = None
    suspend_date: dt.date | None = None
    intervals: list = field(default_factory=list)      # 정지 구간 (lo, hi, 정지 시작일)
    note: str | None = None                           # 진단용 (정지 중 1개월 공백 뒤 삭제 등)


def month_bounds(month: str) -> tuple[dt.date, dt.date]:
    y, m = int(month[:4]), int(month[5:7])
    return dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])


def derive(months: list[str], base: dict[str, dt.date], toks: dict[str, set[Tok]], last_month: str) -> dict[str, MonthOut]:
    """months: 이 제품의 급여목록 관측 달(정렬). base: 달 → 스냅샷 기준일. toks: 달 → 토큰(두 목록 합침).
    last_month: 분석 끝 달."""
    out = {m: MonthOut() for m in months}
    n = len(months)
    present = lambda key, m: key in toks.get(m, ())

    def first_after(i: int, d: dt.date) -> int | None:
        """i번째 행 뒤에서 기준일 >= d 인 첫 행"""
        for j in range(i + 1, n):
            if base[months[j]] >= d:
                return j
        return None

    def add(i: int, lo: dt.date, hi: dt.date, start: dt.date) -> None:
        ms, me = month_bounds(months[i])
        lo, hi = max(lo, ms), min(hi, me)
        if lo <= hi:
            out[months[i]].intervals.append((lo, hi, start))

    keys = sorted({t for m in months for t in toks.get(m, ()) if t.type in ("R3", "R3b")},
                  key=lambda t: (t.type, t.date, t.date_end or dt.date.min))

    # ---- R3: 종료일 없는 급여정지
    for key in (k for k in keys if k.type == "R3"):
        d = key.date
        active = False
        i = 0
        while i < n:
            m = months[i]
            ms, me = month_bounds(m)
            if not active:
                if present(key, m):
                    if d > base[m]:                                   # 미래 정지일 (SUSPEND_SCHEDULED)
                        j = first_after(i, d)
                        if j is None or not present(key, months[j]):
                            i += 1                                     # 확인 안 됨 → 이 달은 상태 그대로
                            continue
                        if d <= me:
                            out[m].exposure_revised = True
                    active = True
                else:
                    i += 1
                    continue
            # 진행 중인 정지
            rs = [t.date for t in toks.get(m, ()) if t.type == "R6" and d <= t.date <= base[m]]
            if rs:                                                     # (a) 해제 비고: 해제일부터 급여
                add(i, d, min(rs) - ONE, d)
                active = False
                i += 1
                continue
            if present(key, m):
                add(i, d, me, d)
                i += 1
                continue
            # 토큰 공백 (두 목록 모두)
            if i + 1 < n and present(key, months[i + 1]):              # 1개월 공백 뒤 재등장: 정지 유지
                add(i, d, me, d)
            elif i + 1 < n:                                            # (b) 2개월 연속 공백: 첫 공백 달 1일 복귀
                out[m].state_revised = True
                active = False
            else:                                                      # 다음 행 없음
                add(i, d, me, d)
                if m == last_month:
                    out[m].resume_unconfirmed = True
                else:
                    out[m].note = "gap_then_absent"
            i += 1

    # ---- R3b: 기간 명시 급여정지 (날짜로 판정)
    for key in (k for k in keys if k.type == "R3b"):
        s, t = key.date, key.date_end
        idx = [i for i in range(n) if present(key, months[i])]
        a = idx[0]
        start_future = s > base[months[a]]
        if start_future:
            j = first_after(a, s)
            if j is None or not present(key, months[j]):
                # 미래 시작일이 확인되지 않음: 과거 날짜로 다시 나타난 첫 달부터 적용
                later = [i for i in idx if s <= base[months[i]]]
                if not later:
                    continue
                a, start_future = later[0], False
        for i in range(a, n):
            m = months[i]
            ms, me = month_bounds(m)
            if ms > t:
                break
            hi = min(t, me)
            if ms <= t < me and t > base[m]:                          # 미래 종료일이 이 달 안 (RESUME_SCHEDULED)
                ok = i + 1 < n and not any(
                    k2.type in ("R3", "R3b") and k2 != key for k2 in toks.get(months[i + 1], ()))
                if ok:
                    out[m].exposure_revised = True
                else:
                    hi = me
            add(i, s, hi, s)
            if i == a and start_future and s <= me:
                out[m].exposure_revised = True

    # ---- 월말 상태와 exposure
    for m in months:
        o = out[m]
        ms, me = month_bounds(m)
        if o.intervals:
            days = set()
            for lo, hi, _ in o.intervals:
                days.update(lo + ONE * k for k in range((hi - lo).days + 1))
            o.exposure_frac = 1 - len(days) / ((me - ms).days + 1)
            ends = [st for lo, hi, st in o.intervals if hi == me]
            o.suspended = bool(ends)
            o.suspend_date = min(ends) if ends else None

    # ---- R1: 등재 예정 (처음 관측된 달부터 이어진 행만)
    i = 0
    while i < n:
        m = months[i]
        fut = [t.date for t in toks.get(m, ()) if t.type == "R1" and t.date > base[m]]
        if not fut or (i > 0 and not out[months[i - 1]].scheduled):
            break
        o = out[m]
        o.scheduled, o.listing_date, o.exposure_frac = True, min(fut), 0.0
        i += 1
    sched = [k for k in range(n) if out[months[k]].scheduled]
    if sched:
        L = out[months[sched[-1]]].listing_date
        j = first_after(sched[-1], L)
        if j is not None and not out[months[j]].scheduled and not out[months[j]].suspended:
            for k in sched:
                ms, me = month_bounds(months[k])
                if ms <= L <= me:
                    out[months[k]].exposure_frac = ((me - L).days + 1) / ((me - ms).days + 1)
                    out[months[k]].exposure_revised = True
    return out
