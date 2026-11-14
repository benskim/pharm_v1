"""제품×월 상태의 날짜 규칙 (docs/PLAN_P1.md §4 state, docs/NOTE_RULES.md R1·R3·R3b·R6, §2).

한 제품의 급여목록 관측 달 목록과 그 달들의 비고 토큰(두 목록 합침)을 받아 달마다 두 가지 상태를 돌려준다.
순수 함수다. 한 컬럼 안에서 판정 시점을 섞지 않는다 (사용자 결정 2026-11-14).

state_asof (스냅샷 기준일 시점, `asof`)
- 그 스냅샷까지의 토큰만 쓴다. 이 달 이후의 스냅샷을 지워도 값이 같다 (시험).
- 등재 예정: 처음 관측된 달부터 이어서 R1 날짜가 기준일보다 미래.
- R3: 정지일 ≤ 그 달까지 본 스냅샷의 기준일인 토큰이 나타난 뒤부터 정지. 해제는 R6(정지일 ≤ 해제일 ≤ 기준일) 또는
  두 목록 모두 토큰이 없는 달이 2개월 연속. 공백 첫 달(그 달까지는 공백 1개월만 보임)은 정지.
- R3b: 시작일 ≤ 그 달까지 본 스냅샷의 기준일인 토큰이 나타난 뒤, 시작일 ≤ 기준일 ≤ 종료일이면 정지.

state_eom (월말, `eom`)
- 그 달 마지막 날의 상태. 이후 스냅샷으로 확인된 것만 반영한다. exposure_frac = 그 달 급여된 일수 / 일수.
- 미래 날짜 신호는 다음 스냅샷에서 확인될 때만 반영하고 신호 달 행을 고쳐 쓴다 (exposure_revised).
  R3·R3b 미래 시작일: 신호 날짜 이후 첫 스냅샷에 같은 토큰. R3b 미래 종료일: 다음 스냅샷에 제품이 있고 다른 정지 토큰이 없음.
  R1 미래 등재일: 등재일 이후 첫 스냅샷에 제품이 있고 정지·등재 예정이 아님 → 그 달 reimbursed, exposure = 등재일부터.
- R3 2개월 연속 공백: 첫 공백 달 1일 복귀, 첫 공백 달 행을 고쳐 쓴다 (state_revised). 분석 끝 1개월 공백은 resume_unconfirmed.
- **늦게 보인 지난 날짜** (late_note_revised): 다음 스냅샷에서 처음 보인 R6 해제일, R3 정지일, R3b 시작일이
  직전 스냅샷의 달 안이면 그 달의 월말 상태와 exposure를 고쳐 쓴다. 그보다 이른 날짜는 고쳐 쓰지 않는다.
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
    # 월말 (eom)
    scheduled: bool = False                           # 월말에도 등재 예정 (확인 안 됨)
    suspended: bool = False
    exposure_frac: float = 1.0
    exposure_revised: bool = False
    state_revised: bool = False
    late_note_revised: bool = False
    resume_unconfirmed: bool = False
    listing_date: dt.date | None = None
    suspend_date: dt.date | None = None
    intervals: list = field(default_factory=list)      # 정지 구간 (lo, hi, 정지 시작일)
    note: str | None = None
    # 기준일 시점 (asof)
    asof_scheduled: bool = False
    asof_suspended: bool = False
    asof_suspend_date: dt.date | None = None


def month_bounds(month: str) -> tuple[dt.date, dt.date]:
    y, m = int(month[:4]), int(month[5:7])
    return dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])


# ---------------------------------------------------------------- 기준일 시점
def asof(months: list[str], base: dict[str, dt.date], toks: dict[str, set[Tok]]) -> tuple[bool, bool, dt.date | None]:
    """months의 마지막 달 기준일 시점 상태 (scheduled, suspended, 정지 시작일). months 밖의 정보는 받지 않는다."""
    last = months[-1]
    b = base[last]
    present = lambda key, m: key in toks.get(m, ())
    sched = all(any(t.type == "R1" and t.date > base[m] for t in toks.get(m, ())) for m in months)
    starts = []
    keys = {t for m in months for t in toks.get(m, ()) if t.type in ("R3", "R3b")}
    for key in keys:
        if key.type == "R3":
            d, active, absent = key.date, False, 0
            for m in months:
                if present(key, m) and d <= base[m]:
                    active, absent = True, 0
                elif active and not present(key, m):
                    absent += 1
                    if absent >= 2:
                        active, absent = False, 0
                if active and any(t.type == "R6" and d <= t.date <= base[m] for t in toks.get(m, ())):
                    active, absent = False, 0
            if active:
                starts.append(d)
        else:
            s, t = key.date, key.date_end
            if any(present(key, m) and s <= base[m] for m in months) and s <= b <= t:
                starts.append(s)
    return sched, bool(starts), (min(starts) if starts else None)


# ---------------------------------------------------------------- 월말
def derive(months: list[str], base: dict[str, dt.date], toks: dict[str, set[Tok]], last_month: str) -> dict[str, MonthOut]:
    """months: 이 제품의 급여목록 관측 달(정렬). base: 달 → 기준일. toks: 달 → 토큰(두 목록 합침). last_month: 분석 끝 달."""
    out = {m: MonthOut() for m in months}
    n = len(months)
    present = lambda key, m: key in toks.get(m, ())

    def first_after(i: int, d: dt.date) -> int | None:
        for j in range(i + 1, n):
            if base[months[j]] >= d:
                return j
        return None

    def add(i: int, lo: dt.date, hi: dt.date, start: dt.date) -> None:
        ms, me = month_bounds(months[i])
        lo, hi = max(lo, ms), min(hi, me)
        if lo <= hi:
            out[months[i]].intervals.append((lo, hi, start))

    def in_month(i: int, d: dt.date) -> bool:
        ms, me = month_bounds(months[i])
        return ms <= d <= me

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
                            i += 1
                            continue
                        if d <= me:
                            out[m].exposure_revised = True
                    elif i > 0 and in_month(i - 1, d) and not present(key, months[i - 1]):
                        add(i - 1, d, month_bounds(months[i - 1])[1], d)    # 늦게 보인 정지일
                        out[months[i - 1]].late_note_revised = True
                    active = True
                else:
                    i += 1
                    continue
            rs = [t.date for t in toks.get(m, ()) if t.type == "R6" and d <= t.date <= base[m]]
            if not rs and i + 1 < n:                                   # 늦게 보인 해제일 (다음 스냅샷, 이 달 안)
                rs = [t.date for t in toks.get(months[i + 1], ()) if t.type == "R6" and d <= t.date and in_month(i, t.date)
                      and t not in toks.get(m, ())]
                if rs:
                    out[m].late_note_revised = True
            if rs:
                add(i, d, min(rs) - ONE, d)
                active = False
                i += 1
                continue
            if present(key, m):
                add(i, d, me, d)
                i += 1
                continue
            if i + 1 < n and present(key, months[i + 1]):              # 1개월 공백 뒤 재등장: 정지 유지
                add(i, d, me, d)
            elif i + 1 < n:                                            # 2개월 연속 공백: 첫 공백 달 1일 복귀
                out[m].state_revised = True
                active = False
            else:
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
                later = [i for i in idx if s <= base[months[i]]]
                if not later:
                    continue
                a, start_future = later[0], False
        if not start_future and a > 0 and in_month(a - 1, s) and not present(key, months[a - 1]):
            a -= 1                                                     # 늦게 보인 시작일
            out[months[a]].late_note_revised = True
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
    sched = []
    for i, m in enumerate(months):
        fut = [t.date for t in toks.get(m, ()) if t.type == "R1" and t.date > base[m]]
        if not fut or (i > 0 and i - 1 not in sched):
            break
        sched.append(i)
        out[m].listing_date = min(fut)
    if sched:
        L = out[months[sched[-1]]].listing_date
        j = first_after(sched[-1], L)
        confirmed = j is not None and j not in sched and not out[months[j]].suspended
        for k in sched:
            o = out[months[k]]
            ms, me = month_bounds(months[k])
            if confirmed and me >= L:                                  # 월말에는 등재됨
                o.exposure_frac = ((me - max(L, ms)).days + 1) / ((me - ms).days + 1)
                o.exposure_revised = True
            else:                                                      # 월말에도 시행 전, 또는 확인 안 됨
                o.scheduled, o.exposure_frac = True, 0.0

    # ---- 기준일 시점 (그 달까지의 정보만)
    for i, m in enumerate(months):
        o = out[m]
        o.asof_scheduled, o.asof_suspended, o.asof_suspend_date = asof(months[: i + 1], base, toks)
    return out
