"""P1 원장 v5 규칙 (docs/RULES_2026-11-17.md §0~§7). 순수 함수만 둔다 (DB 없음).

상태 (§1·§2·§4)
- 상태는 **그 달 적용일(1일) 시점**의 급여 상태다. as_known = 그 스냅샷까지 보인 것, revised = 이후 스냅샷으로 고친 값.
  한 컬럼 안에서 판정 시점을 섞지 않는다 (월중 변화는 이벤트 유효일로만 나타난다).
- 급여·급여 외 판정은 **급여목록만** (급여정지 표시·해제 문구·집행정지 표시). 등재일(R1)은 처분 정보라 두 목록을 함께 본다.
- 상태 값: reimbursed / scheduled_listing / off_benefit(sub: suspended / delisted / transferred_out).
신호 (§6): 처분 날짜가 그 달 1일보다 뒤인 처분 비고의 최초 기재 (두 목록). 기간 중지는 신호 하나.
코드 이어받기 (§3-1): 순서 1~7, 1:1, 순서 1은 같은 품목기준코드 + 같은 주성분코드 (§10 Q3), 기간 제한 없음.
"""
from __future__ import annotations

import calendar
import datetime as dt
import math
import re
from dataclasses import dataclass, field, replace

ONE = dt.timedelta(days=1)

REALIZED_FAMILY = ("REALIZED", "REALIZED_CHANGED", "REALIZED_LATE", "REALIZED_BY_DELISTING")
# 판정 제외(EXCLUDED)의 사유 (사용자 결정 2026-11-18): 급여중지 중 예고 / 인하일 전 급여중지 / 대상 소멸
EXCLUDED_REASONS = ("listed_during_suspension", "suspended_before_price_date", "target_gone")
ONGOING_STAY_TEXT = "2025-12 기준 집행정지가 계속 유지되어 최종 실현 여부 확인 불가"
BARE_UNITS = {"mCi", "mCi/kit", "L", "mL", "g", "회"}      # RULES §2-1
AMBIGUOUS_UNITS = {"L/통", "L/백", "g/백"}


# ---------------------------------------------------------------- 입력
@dataclass(frozen=True)
class Tok:
    type: str                       # R1 R2 R3 R3b R4 R5 R6 R7 R9
    date: dt.date | None
    date_end: dt.date | None = None
    amount: float | None = None
    src: str = "B"                  # B 급여목록 / S 대체조제 목록


@dataclass(frozen=True)
class Obs:
    """한 제품 한 달의 관측"""
    in_b: bool = False
    in_s: bool = False
    price_b: float | None = None
    price_s: float | None = None
    unit: str | None = None
    toks: tuple = ()


@dataclass(frozen=True)
class Life:
    """코드 이어받기용 제품 요약 (급여목록 관측 달)"""
    months: tuple
    firm_first: str | None
    firm_last: str | None
    name_first: str | None
    name_last: str | None
    ing_first: str | None
    ing_last: str | None
    item: str | None
    prev_codes: frozenset = frozenset()


def base(month: str) -> dt.date:
    return dt.date(int(month[:4]), int(month[5:7]), 1)


def month_end(month: str) -> dt.date:
    y, m = int(month[:4]), int(month[5:7])
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def month_of(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _get(obs: dict, m: str) -> Obs:
    return obs.get(m) or Obs()


# ---------------------------------------------------------------- 약가 유형 (§2, §10 Q1)
def price_type(o: Obs) -> tuple[str, str, bool]:
    """(유형, 근거, unit_ambiguous). 판정에 쓰지 않는 속성"""
    if any(t.type == "R7" for t in o.toks):
        return ("unpriced", "r7", False)
    if o.price_b is None:
        return ("unpriced", "price_null", False)
    u = (o.unit or "").strip()
    if u in BARE_UNITS:
        return ("minimal_unit", "unit_bare_measure", False)
    return ("production_unit", "default_majority", "I.U" in u or u in AMBIGUOUS_UNITS)


# ---------------------------------------------------------------- 상태 (§1·§2·§4)
@dataclass
class StatusRow:
    month: str
    in_b: bool
    as_known: str
    sub_as_known: str | None
    revised: str
    sub_revised: str | None
    suspend_date: dt.date | None = None
    listing_date: dt.date | None = None
    subst_lag: bool = False
    stay_dates: tuple = ()
    revision_reason: str | None = None


@dataclass
class Revision:
    month: str
    field: str
    as_known: str
    revised: str
    known_month: str
    reason: str


@dataclass
class Event:
    event_type: str
    kind: str | None
    observed_month: str
    effective_date: dt.date | None = None
    effective_lo: dt.date | None = None
    effective_hi: dt.date | None = None
    date_source: str | None = None
    known_month: str | None = None
    retracted_month: str | None = None
    link_basis: str | None = None
    counterpart: str | None = None
    gap_months: int | None = None
    signal_id: str | None = None
    outcome: str | None = None
    price_before: float | None = None
    price_after: float | None = None
    delisting_case_id: str | None = None
    stay_target: str | None = None
    stay_target_source: str | None = None
    evidence: str | None = None


@dataclass
class StatusResult:
    rows: dict
    events: list
    revisions: list


def state_of(row: StatusRow, which: str = "revised") -> str:
    st, sub = (row.as_known, row.sub_as_known) if which == "as_known" else (row.revised, row.sub_revised)
    return sub if st == "off_benefit" else st


def _marks(o: Obs, b: dt.date):
    """급여목록의 급여중지 표시 (R3 날짜 ≤ 1일, R3b 시작일 ≤ 1일 ≤ 종료일)와 급여목록 R6(≤ 1일)"""
    r3 = {t.date for t in o.toks if t.src == "B" and t.type == "R3" and t.date and t.date <= b}
    r3b = {(t.date, t.date_end) for t in o.toks if t.src == "B" and t.type == "R3b" and t.date and t.date_end and t.date <= b}
    r6 = {t.date for t in o.toks if t.src == "B" and t.type == "R6" and t.date and t.date <= b}
    return r3, r3b, r6


def derive_status(months: list[str], obs: dict, external_lift: dict | None = None) -> StatusResult:
    """급여목록에 처음 실린 달부터 분석 끝까지의 행. 급여목록에 없는 달은 off_benefit/delisted (이어받기는 빌드에서 고친다).
    external_lift: {급여정지 표시 날짜: 외부 확인 해제일} (§4.4)"""
    external_lift = external_lift or {}
    first = next((i for i, m in enumerate(months) if _get(obs, m).in_b), None)
    rows: dict[str, StatusRow] = {}
    if first is None:
        return StatusResult(rows, [], [])
    ms = months[first:]
    n = len(ms)
    info = []
    for i, m in enumerate(ms):
        o = _get(obs, m)
        b = base(m)
        r3, r3b, r6 = _marks(o, b) if o.in_b else (set(), set(), set())
        info.append((o, b, r3, r3b, r6))

    # 기간 중지가 시작된 것으로 보인 키 (그 달까지): 시작일 ≤ 그 달 1일인 달에 급여목록 표시가 있었다
    started_by: list[set] = []
    acc: set = set()
    for o, b, r3, r3b, r6 in info:
        acc = acc | {k for k in r3b if k[0] <= b}
        started_by.append(set(acc))
    # R6 해제: 보인 달까지 (as_known) / 전체 (revised)
    r6_seen: list[set] = []
    acc6: set = set()
    for o, b, r3, r3b, r6 in info:
        acc6 = acc6 | r6
        r6_seen.append(set(acc6))
    r6_all = acc6

    def susp_dates(i: int, r6set: set, period_keys: set) -> list[dt.date]:
        o, b, r3, r3b, _ = info[i]
        out = [d for d in r3 if not any(d <= x <= b for x in r6set)]
        out += [s for (s, e) in period_keys if s <= b <= e]
        return sorted(out)

    # 등재 예정: 처음 관측된 달부터 이어진 달에 미래 R1 (두 목록)
    sched: dict[int, dt.date] = {}
    for i, m in enumerate(ms):
        o, b = info[i][0], info[i][1]
        fut = [t.date for t in o.toks if t.type == "R1" and t.date and t.date > b]
        if not o.in_b or not fut or (i > 0 and i - 1 not in sched):
            break
        sched[i] = min(fut)

    as_known, revised, sdate = [], [], []
    for i, m in enumerate(ms):
        o, b = info[i][0], info[i][1]
        if not o.in_b:
            as_known.append(("off_benefit", "delisted"))
            sdate.append(None)
            continue
        if i in sched:
            as_known.append(("scheduled_listing", None))
            sdate.append(None)
            continue
        ds = susp_dates(i, r6_seen[i], started_by[i])
        as_known.append(("off_benefit", "suspended") if ds else ("reimbursed", None))
        sdate.append(ds[0] if ds else None)
    revised = list(as_known)
    reason: list[str | None] = [None] * n
    # revised: 전체 R6 (늦게 보인 해제 문구)
    for i in range(n):
        if revised[i] == ("off_benefit", "suspended"):
            if not susp_dates(i, r6_all, started_by[i]):
                revised[i], reason[i], sdate[i] = ("reimbursed", None), "late_lift_note", None

    # R3 표시가 사라진 달: 한 달 누락 / 다음 달 삭제 / 두 달 연속 → 해제
    lift_kind: dict[int, tuple] = {}
    for i in range(1, n):
        o, b, r3, r3b, _ = info[i]
        po, pb, pr3, pr3b, _ = info[i - 1]
        if not (o.in_b and po.in_b) or revised[i] != ("reimbursed", None) or revised[i - 1] != ("off_benefit", "suspended"):
            continue
        gone = [d for d in pr3 if d not in r3]
        if not gone:
            continue
        if i + 1 < n:
            no, nb, nr3, _, _ = info[i + 1]
            if no.in_b and any(d in nr3 for d in gone):
                revised[i], reason[i], sdate[i] = ("off_benefit", "suspended"), "list_omission", min(gone)
            elif not no.in_b:
                revised[i], reason[i], sdate[i] = ("off_benefit", "suspended"), "delisted_after_mark_gone", min(gone)
            else:
                lift_kind[i] = ("list_only", ms[i + 1], min(gone))
        else:
            lift_kind[i] = ("list_only_unconfirmed", ms[i], min(gone))

    # 외부 확인 해제일로 덮어씀 (§4.4)
    ext_lift: dict[int, dt.date] = {}
    for i, (kind, known, mark) in list(lift_kind.items()):
        if mark in external_lift:
            d = external_lift[mark]
            ext_lift[i] = d
            j = i
            while j < n and base(ms[j]) < d and info[j][0].in_b:
                if revised[j] == ("reimbursed", None):
                    revised[j], reason[j], sdate[j] = ("off_benefit", "suspended"), "external_date", mark
                j += 1
            if j != i:
                lift_kind[j] = lift_kind.pop(i)
                ext_lift[j] = ext_lift.pop(i)

    row_out = {}
    revisions = []
    for i, m in enumerate(ms):
        o, b = info[i][0], info[i][1]
        s_marks = {t.date for t in o.toks if t.src == "S" and t.type in ("R3", "R3b") and t.date and t.date <= b}
        b_marks = {t.date for t in o.toks if t.src == "B" and t.type in ("R3", "R3b") and t.date}
        stay = tuple(sorted({t.date for t in o.toks if t.src == "B" and t.type == "R4" and t.date}))
        r5_past = any(t.type == "R5" and t.date and t.date <= b for t in o.toks)
        r = StatusRow(m, o.in_b, as_known[i][0], as_known[i][1], revised[i][0], revised[i][1],
                      suspend_date=sdate[i] if revised[i][1] == "suspended" else None,
                      listing_date=sched.get(i), subst_lag=o.in_b and bool(s_marks - b_marks),
                      stay_dates=() if r5_past else stay, revision_reason=reason[i])
        row_out[m] = r
        if as_known[i] != revised[i]:
            known = ms[i + 1] if i + 1 < n else m
            if reason[i] == "external_date":
                known = m
            revisions.append(Revision(m, "status", state_of(r, "as_known"), state_of(r, "revised"), known, reason[i]))

    # 이벤트: revised 상태의 전이
    events = []
    cal_first = months[0]
    last_ok_price = None
    for i, m in enumerate(ms):
        o = info[i][0]
        cur = state_of(row_out[m])
        prev = state_of(row_out[ms[i - 1]]) if i > 0 else None
        if cur in ("reimbursed",) and o.in_b:
            last_ok_price = o.price_b
        if cur == "suspended" and prev != "suspended":
            if m == cal_first and row_out[m].suspend_date and row_out[m].suspend_date < base(m):
                continue                                                  # 창 시작 전 정지 (좌측 절단)
            kind = "at_entry" if prev is None else ("at_reentry" if prev in ("delisted", "transferred_out") else "in_window")
            src = "note_benefit"
            events.append(Event("SUSPEND", kind, m, effective_date=row_out[m].suspend_date, date_source=src, known_month=m))
        if prev == "suspended" and cur == "reimbursed":
            # 중지 전 금액: 이 정지 직전의 급여 달 금액
            j = i - 1
            while j >= 0 and state_of(row_out[ms[j]]) == "suspended":
                j -= 1
            before = _get(obs, ms[j]).price_b if j >= 0 and _get(obs, ms[j]).in_b else None
            pr = row_out[ms[i - 1]]
            r6d = [d for d in r6_all if pr.suspend_date and d >= pr.suspend_date and d <= base(m)]
            pkeys = [(s, e) for (s, e) in started_by[i - 1] if s <= base(ms[i - 1]) <= e]
            if i in ext_lift:
                ev = Event("SUSPEND_LIFT", lift_kind[i][0], m, effective_date=ext_lift[i], date_source="external",
                           known_month=lift_kind[i][1])
            elif r6d:
                ev = Event("SUSPEND_LIFT", "r6_note", m, effective_date=min(r6d), date_source="note_benefit", known_month=m)
            elif pkeys and all(e < base(m) for (s, e) in pkeys):
                ev = Event("SUSPEND_LIFT", "period_end", m, effective_date=max(e for s, e in pkeys) + ONE,
                           date_source="note_benefit", known_month=m)
            elif i in lift_kind:
                ev = Event("SUSPEND_LIFT", lift_kind[i][0], m, effective_date=base(m), date_source="snapshot_first",
                           known_month=lift_kind[i][1])
            else:
                ev = Event("SUSPEND_LIFT", "other", m, effective_date=base(m), date_source="snapshot_first", known_month=m)
            ev.price_before, ev.price_after = before, o.price_b
            events.append(ev)
    return StatusResult(row_out, events, revisions)


# ---------------------------------------------------------------- 집행정지 (§5)
@dataclass
class Stay:
    date: dt.date
    first_month: str
    end_month: str | None            # 표시가 처음 사라진 달 (제품이 급여목록에 있을 때). None = 분석 끝까지 또는 제품이 사라짐
    left_censored: bool
    released_by_r5: dt.date | None = None
    months: tuple = ()


def derive_stays(months: list[str], obs: dict) -> list[Stay]:
    """급여목록 집행정지(R4) 표시. 같은 날짜 = 같은 집행정지. R5(1일 이전)가 있으면 그 달에 끝난 것으로 본다"""
    by: dict[dt.date, list[str]] = {}
    for m in months:
        o = _get(obs, m)
        if not o.in_b:
            continue
        for t in o.toks:
            if t.src == "B" and t.type == "R4" and t.date:
                by.setdefault(t.date, []).append(m)
    out = []
    for d, ms in sorted(by.items()):
        first = ms[0]
        end, r5 = None, None
        for m in months[months.index(first) + 1:]:
            o = _get(obs, m)
            if not o.in_b:
                continue
            has = any(t.src == "B" and t.type == "R4" and t.date == d for t in o.toks)
            r5d = [t.date for t in o.toks if t.type == "R5" and t.date and t.date <= base(m)]
            if not has or r5d:
                end, r5 = m, (min(r5d) if r5d else None)
                break
        out.append(Stay(d, first, end, first == months[0], r5, tuple(ms)))
    return out


def _active(stay: Stay, m: str) -> bool:
    return stay.first_month <= m and (stay.end_month is None or m < stay.end_month)


# ---------------------------------------------------------------- 신호 (§6)
@dataclass
class Signal:
    signal_id: str
    signal_type: str
    source_rule: str
    product_code: str
    first_month: str
    signal_date: dt.date
    period_end: dt.date | None = None
    amount: float | None = None
    source_list: tuple = ()
    raw_types: tuple = ()
    parent_signal_id: str | None = None
    outcome: str | None = None              # final_outcome (RULES §6.4)
    current_status: str | None = None       # CENSORED일 때 ONGOING_STAY / AWAITING
    reason: str | None = None
    stay_blocked: bool = False
    blocking_stays: tuple = ()
    stay_target_source: str | None = None
    stay_end_month: str | None = None
    check_month: str | None = None
    realized_month: str | None = None
    resolved_month: str | None = None
    effective_date: dt.date | None = None
    date_gap_days: int | None = None
    amount_gap: float | None = None
    successor_code: str | None = None
    blocking_stay_id: str | None = None      # §5: 막은 집행정지 (STAY:제품코드:날짜, 여럿이면 쉼표)


def _collect(code: str, months: list[str], obs: dict, status: StatusResult, stays: list[Stay]) -> list[Signal]:
    raw: dict[tuple, dict] = {}
    first_b = next((m for m in months if _get(obs, m).in_b), None)
    for m in months:
        o = _get(obs, m)
        if not (o.in_b or o.in_s):
            continue
        b = base(m)
        for t in o.toks:
            if t.type not in ("R1", "R2", "R3", "R3b", "R5", "R9") or not t.date or t.date <= b:
                continue
            typ = {"R1": "LISTING", "R2": "PRICE_NOTICE", "R3": "SUSPEND_NOTICE", "R3b": "SUSPEND_NOTICE",
                   "R5": "STAY_RELEASE_PRICE", "R9": "DELISTING_NOTICE"}[t.type]
            key = (typ, t.date)
            g = raw.setdefault(key, dict(first=m, src=set(), types=set(), amount=None, amount_src=None, end=None))
            g["src"].add("benefit" if t.src == "B" else "substitute")
            g["types"].add(t.type)
            if t.amount is not None and (g["amount"] is None or (g["amount_src"] == "S" and t.src == "B" and m == g["first"])):
                if g["amount"] is None or m == g["first"]:
                    g["amount"], g["amount_src"] = t.amount, t.src
            if t.type == "R3b":
                g["end"] = t.date_end
    sigs = []
    for (typ, d), g in raw.items():
        st = typ
        if typ == "LISTING":
            row = status.rows.get(g["first"])
            new = (row is not None and row.as_known == "scheduled_listing") or (row is None and (first_b is None or first_b > g["first"]))
            st = "LISTING_NOTICE" if new else "LISTING_NOTE_EXISTING"
        rule = "R3b" if "R3b" in g["types"] else sorted(g["types"])[0]
        sigs.append(Signal(f"{st}:{code}:{d.isoformat()}", st, rule, code, g["first"], d, g["end"], g["amount"],
                           tuple(sorted(g["src"])), tuple(sorted(g["types"]))))
    # §1: 대체조제 금액 ≠ 급여목록 금액 + 그 달 급여목록에 새 집행정지 → 막힌 인하 처분 금액
    new_stay = {s.first_month for s in stays if not s.left_censored}
    for m in months:
        o = _get(obs, m)
        if not (o.in_b and o.in_s) or o.price_b is None or o.price_s is None or o.price_s >= o.price_b or m not in new_stay:
            continue
        if any(s.signal_type == "PRICE_NOTICE" and s.first_month == m and s.amount == o.price_s for s in sigs):
            continue
        sigs.append(Signal(f"PRICE_NOTICE:{code}:{base(m).isoformat()}:S_AMT", "PRICE_NOTICE", "S_AMT", code, m, base(m),
                           amount=o.price_s, source_list=("substitute",), raw_types=("S_AMT",)))
    order = {"SUSPEND_NOTICE": 0, "LISTING_NOTICE": 1, "LISTING_NOTE_EXISTING": 1, "DELISTING_NOTICE": 2,
             "STAY_RELEASE_PRICE": 3, "PRICE_NOTICE": 4}
    return sorted(sigs, key=lambda s: (order[s.signal_type], s.first_month, s.signal_date, s.signal_id))


def derive_signals(code: str, months: list[str], obs: dict, status: StatusResult, stays: list[Stay],
                   successors: dict | None = None, external_targets: dict | None = None,
                   external_stays: dict | None = None) -> list[Signal]:
    """successors: {사라진 첫 달: (새 코드, 새 코드 obs, 새 코드 StatusResult)}.
    external_targets: {(신호 유형, 신호 날짜): (집행정지 날짜, ...)} — 대상 집행정지를 외부로 정함 (stay_target_source = external)"""
    successors = successors or {}
    external_targets = external_targets or {}
    # 외부 집행정지 (2026-11-18): {(신호 유형, 신호 날짜): (Stay, ...)} — 급여목록에 집행정지 비고가 없을 때 외부 자료로 막힘
    external_stays = external_stays or {}
    sigs = _collect(code, months, obs, status, stays)
    idx = {m: i for i, m in enumerate(months)}
    stay_by = {s.date: s for s in stays}

    def check_month(d: dt.date, strict: bool = False):
        return next((m for m in months if (base(m) > d if strict else base(m) >= d)), None)

    def st_at(o_status: StatusResult, m: str, which="revised"):
        r = o_status.rows.get(m)
        return state_of(r, which) if r else "absent"

    def candidates(s: Signal) -> tuple[list[Stay], str]:
        key = (s.signal_type, s.signal_date)
        if key in external_stays:
            return list(external_stays[key]), "external"
        if key in external_targets:
            return [stay_by[d] for d in external_targets[key] if d in stay_by], "external"
        return [x for x in stays if not x.left_censored and x.first_month >= s.first_month], "timing"

    def prev_price(o: dict, m: str):
        for mm in reversed(months[:idx[m]]):
            if _get(o, mm).in_b:
                return _get(o, mm).price_b
        return None

    def note_present(o: dict, m: str, s: Signal) -> bool:
        return any(t.type in s.raw_types and t.date == s.signal_date for t in _get(o, m).toks)

    def finish(s, outcome, reason=None, month=None, eff=None, status=None):
        s.outcome, s.reason, s.resolved_month, s.current_status = outcome, reason, month, status
        if outcome in REALIZED_FAMILY:
            s.realized_month, s.effective_date = month, eff
        return s

    def follow(s: Signal, m: str):
        """제품이 사라진 달 m: 이어받은 코드가 있으면 (코드, obs, status)"""
        j = idx[m]
        while j > 0 and not _get(obs, months[j - 1]).in_b:
            j -= 1
        return successors.get(months[j])

    def late_date(o: dict, m: str):
        """지연 실현의 유효일: 실현 달 비고에 직전 스냅샷 뒤·그 달 1일 이전 날짜(R2 금액 일치 또는 R5)가 있으면 그 날짜, 없으면 1일 (§6.4)"""
        j = idx[m]
        lo = base(months[j - 1]) if j > 0 else dt.date.min
        x = _get(o, m)
        nd = [t.date for t in x.toks if t.date and lo < t.date <= base(m)
              and (t.type == "R5" or (t.type == "R2" and t.amount == x.price_b))]
        return min(nd) if nd else base(m)

    def announce_date(o: dict, m: str):
        """변경 실현의 유효일 (§6.4 ★2026-11-18): 실현 달 또는 직전 달 비고 중 실현된 금액을 알린 것(R2 금액 = 실현 금액, 또는
        R5 집행정지 해제 및 상한금액 인하)의 날짜. 날짜가 직전 스냅샷 뒤 ~ 실현 달 말일. 없으면 실현 달 1일"""
        j = idx[m]
        lo = base(months[j - 1]) if j > 0 else dt.date.min
        amt = _get(o, m).price_b
        nd = [t.date for mm in months[max(0, j - 1): j + 1] for t in _get(o, mm).toks
              if t.date and lo < t.date <= month_end(m) and (t.type == "R5" or (t.type == "R2" and t.amount == amt))]
        return min(nd) if nd else base(m)

    def judge_price(s: Signal, o: dict, ost: StatusResult, m: str, after_stay: bool):
        """한 달 m에서 약가 신호 판정. 결과 또는 None (계속 추적)"""
        x = _get(o, m)
        p0 = prev_price(o, m)
        if x.price_b == s.amount:
            return ("REALIZED_LATE" if (after_stay or m != s.check_month) else "REALIZED", None,
                    late_date(o, m) if (after_stay or m != s.check_month) else s.signal_date)
        if x.price_b is not None and p0 is not None and x.price_b != p0:
            s.amount_gap = x.price_b - s.amount
            eff = announce_date(o, m)          # §6.4 ★: 실현된 금액을 알린 비고의 날짜, 없으면 실현 달 1일
            if after_stay or m != s.check_month:
                s.date_gap_days = (base(m) - s.signal_date).days
            return ("REALIZED_CHANGED", None, eff)
        return None

    def judge_susp(s: Signal, o: dict, m: str, after_stay: bool):
        x = _get(o, m)
        b = base(m)
        r3 = sorted(t.date for t in x.toks if t.src == "B" and t.type == "R3" and t.date and t.date <= b)
        r3b = sorted((t.date, t.date_end) for t in x.toks if t.src == "B" and t.type == "R3b" and t.date and t.date <= b)
        if s.source_rule == "R3b" and (s.signal_date, s.period_end) in r3b:
            return ("REALIZED_LATE", None, s.signal_date) if after_stay else ("REALIZED", None, s.signal_date)
        starts = [d for d in r3] + [a for a, _ in r3b]
        if s.signal_date in starts and not after_stay:
            if s.source_rule == "R3b":
                return ("REALIZED_CHANGED", None, s.signal_date)
            return ("REALIZED", None, s.signal_date)
        cand = [d for d in starts if d >= s.signal_date - dt.timedelta(days=31)]
        if cand:
            d = min(cand, key=lambda z: abs((z - s.signal_date).days))
            s.date_gap_days = (d - s.signal_date).days
            if after_stay or d > s.signal_date:
                return ("REALIZED_LATE", None, d)
            return ("REALIZED_CHANGED", None, d)
        return None

    def track(s: Signal, o: dict, ost: StatusResult, start: str, cand: list[Stay], cand_src: str):
        """start(확인 스냅샷)부터 판정. o/ost = 판정 대상 코드(이어받은 코드일 수 있음)"""
        s.check_month = s.check_month or start
        blocked = False
        for m in months[idx[start]:]:
            x = _get(o, m)
            if not x.in_b:
                f = follow(s, m) if o is obs else None
                if f:
                    s.successor_code = f[0]
                    return track(s, f[1], f[2], m, cand, cand_src)
                if blocked:                                 # 집행정지 중 대상 삭제 (§6.3, 2026-11-18)
                    return finish(s, "NOT_REALIZED", "stay_then_delisted", m)
                if s.signal_type == "SUSPEND_NOTICE":
                    return finish(s, "REALIZED_BY_DELISTING", None, m, base(m))
                if s.signal_type == "DELISTING_NOTICE":
                    return finish(s, "REALIZED", None, m, s.signal_date)
                if s.signal_type in ("LISTING_NOTICE", "LISTING_NOTE_EXISTING"):
                    return finish(s, "NOT_REALIZED", "absent", m)
                return finish(s, "EXCLUDED", "target_gone", m)        # 대상 소멸 (§6.2, §11-9)
            active = [c for c in cand if c.first_month <= m and _active(c, m)]
            if blocked and not active:                     # 대상 집행정지가 모두 끝난 첫 달
                s.stay_end_month = m
                if s.signal_type == "SUSPEND_NOTICE":
                    r = judge_susp(s, o, m, True)
                    return finish(s, *r[:2], m, r[2]) if r else finish(s, "NOT_REALIZED", "stay_ended_not_executed", m)
                if s.signal_type in ("PRICE_NOTICE", "STAY_RELEASE_PRICE"):
                    r = judge_price(s, o, ost, m, True)
                    return finish(s, *r[:2], m, r[2]) if r else finish(s, "NOT_REALIZED", "stay_ended_not_executed", m)
            if blocked:
                # 막힌 처분이 그 내용대로 시행되면 지연 실현 (다른 집행정지가 남아 있어도)
                if s.signal_type in ("PRICE_NOTICE", "STAY_RELEASE_PRICE") and x.price_b == s.amount:
                    return finish(s, "REALIZED_LATE", None, m, late_date(o, m))
                if s.signal_type == "SUSPEND_NOTICE":
                    r = judge_susp(s, o, m, True)
                    if r:
                        return finish(s, *r[:2], m, r[2])
                continue
            # 막히지 않은 상태의 판정
            if s.signal_type in ("PRICE_NOTICE", "STAY_RELEASE_PRICE"):
                r = judge_price(s, o, ost, m, False)
                if r:
                    return finish(s, *r[:2], m, r[2])
            elif s.signal_type == "SUSPEND_NOTICE":
                if s.source_rule == "R3b" and s.period_end and base(m) > s.period_end and m == s.check_month \
                        and judge_susp(s, o, m, False) is None and not active:
                    return finish(s, "CENSORED", "period_passed_before_check", m, status="AWAITING")
                r = judge_susp(s, o, m, False)
                if r:
                    return finish(s, *r[:2], m, r[2])
            elif s.signal_type in ("LISTING_NOTICE", "LISTING_NOTE_EXISTING"):
                stt = st_at(ost, m)
                if stt == "reimbursed":
                    return finish(s, "REALIZED" if m == s.check_month else "REALIZED_LATE", None, m,
                                  s.signal_date if m == s.check_month else base(m))
                if stt == "suspended":
                    return finish(s, "NOT_REALIZED", "suspended", m)
                continue                                    # 아직 등재 예정
            elif s.signal_type == "DELISTING_NOTICE":
                return finish(s, "NOT_REALIZED", "still_listed", m)
            if active:
                blocked = True
                s.stay_blocked = True
                s.blocking_stays = tuple(sorted(c.date for c in active))
                s.blocking_stay_id = ",".join(f"STAY:{code}:{d.isoformat()}" for d in s.blocking_stays)
                s.stay_target_source = cand_src
                continue
            if s.signal_type == "PRICE_NOTICE" and note_present(o, m, s):
                continue                                    # 비고가 남아 있으면 분석 끝까지 추적 (§10 Q4)
            return finish(s, "NOT_REALIZED", "note_gone" if s.signal_type != "STAY_RELEASE_PRICE" else "stay_continues", m)
        if blocked:
            return finish(s, "CENSORED", None, None, status="ONGOING_STAY")
        return finish(s, "CENSORED", "note_remains_at_end", None, status="AWAITING")

    done: dict[str, Signal] = {}
    for s in sigs:
        cand, cand_src = candidates(s)
        if s.source_rule == "S_AMT":
            c = s.first_month
        elif s.signal_type == "DELISTING_NOTICE":
            # 발행 뒤 제품이 처음 사라진 스냅샷, 없으면 신호 날짜 이후 첫 스냅샷
            c = next((m for m in months[idx[s.first_month] + 1:] if not _get(obs, m).in_b and base(m) <= (base(check_month(s.signal_date)) if check_month(s.signal_date) else dt.date.max)), None) \
                or check_month(s.signal_date)
        else:
            c = check_month(s.signal_date)
        s.check_month = c
        if s.signal_type == "PRICE_NOTICE":
            # 판정 순서 1: 최초 기재 시 급여중지 중 → 판정 불가(중지 중 기재)
            row = status.rows.get(s.first_month)
            if row is not None and state_of(row, "as_known") == "suspended":
                finish(s, "EXCLUDED", "listed_during_suspension", s.first_month)
                done[s.signal_id] = s
                continue
            parents = [p for p in done.values() if p.signal_type == "SUSPEND_NOTICE" and p.first_month <= s.first_month
                       and p.signal_date <= s.signal_date and (p.check_month is None or p.check_month >= s.first_month)]
            if parents:
                p = min(parents, key=lambda z: z.signal_date)
                s.parent_signal_id = p.signal_id
                # 인하일 전 급여중지: 부모 급여중지가 인하 예정일 전에 시작됨 (실현돼 인하일에 급여중지 중, 또는 급여중지 뒤 삭제)
                if c is not None and ((p.outcome in ("REALIZED", "REALIZED_CHANGED", "REALIZED_LATE") and st_at(status, c) == "suspended")
                                      or (p.outcome == "REALIZED_BY_DELISTING" and p.signal_date < s.signal_date)):
                    finish(s, "EXCLUDED", "suspended_before_price_date", c)
                    done[s.signal_id] = s
                    continue
        if c is None:
            finish(s, "CENSORED", None, None, status="AWAITING")
            done[s.signal_id] = s
            continue
        if s.signal_type == "PRICE_NOTICE" and s.source_rule != "S_AMT":
            # 발행 뒤 상한금액이 예고 금액이 된 첫 스냅샷 (확인 스냅샷 전이라도 예정일 실현으로 본다)
            early = next((m for m in months[idx[s.first_month] + 1: idx[c]] if _get(obs, m).in_b and _get(obs, m).price_b == s.amount), None)
            if early:
                s.check_month = early
                finish(s, "REALIZED", None, early, s.signal_date)
                done[s.signal_id] = s
                continue
        track(s, obs, status, c, cand, cand_src)
        done[s.signal_id] = s
    return list(done.values())


# ---------------------------------------------------------------- 약가 이벤트 (§3)
def price_events(code: str, months: list[str], obs: dict, status: StatusResult, signals: list[Signal]) -> list[Event]:
    """전후 두 달 모두 급여 상태(revised)일 때만 약가 변경. 실현된 약가 신호와 연결되면 signaled, 아니면 비고 미기재 약가 변경"""
    out = []
    linked = {s.realized_month: s for s in signals
              if s.signal_type in ("PRICE_NOTICE", "STAY_RELEASE_PRICE") and s.outcome in ("REALIZED", "REALIZED_CHANGED", "REALIZED_LATE")
              and s.successor_code is None}
    for i in range(1, len(months)):
        m0, m1 = months[i - 1], months[i]
        r0, r1 = status.rows.get(m0), status.rows.get(m1)
        if not (r0 and r1 and state_of(r0) == "reimbursed" and state_of(r1) == "reimbursed"):
            continue
        p0, p1 = _get(obs, m0).price_b, _get(obs, m1).price_b
        if p0 is None or p1 is None or p0 == p1:
            continue
        s = linked.get(m1)
        if s:
            out.append(Event("PRICE_CHANGE", "signaled", m1, effective_date=s.effective_date, date_source="signal_confirmed",
                             known_month=m1, signal_id=s.signal_id, outcome=s.outcome, price_before=p0, price_after=p1))
            continue
        # 이번 달 비고의 지난 날짜(직전 스냅샷 뒤, 이번 달 1일 이전)에 같은 금액이 적혀 있으면 그 날짜
        nd = [(t.date, t.src) for t in _get(obs, m1).toks if t.type == "R2" and t.amount == p1 and t.date
              and base(m0) < t.date <= base(m1)]
        if nd:
            d, src = min(nd)
            out.append(Event("PRICE_CHANGE", "unobserved_notice", m1, effective_date=d,
                             date_source="note_benefit" if src == "B" else "note_substitute", known_month=m1, price_before=p0, price_after=p1))
        else:
            out.append(Event("PRICE_CHANGE", "unobserved_notice", m1, effective_date=base(m1), date_source="snapshot_first",
                             known_month=m1, price_before=p0, price_after=p1))
    return out


def flag_batch(rows: list[dict], min_c4: int, min_products: int = 0) -> tuple[set, dict]:
    """§3 ★·§11-2. rows: {key, month, c4, kind}. 한 달 안의 비고 미기재 약가 변경(kind = unobserved_notice)이 걸친 성분4가 min_c4개 이상이면
    그달의 비고 미기재 약가 변경을 batch. 판정에 쓰지 않는 설명용 표시. 반환: (batch 키, {달: n_products, n_ingredients, batch})"""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r["kind"] == "unobserved_notice":
            groups.setdefault(r["month"], []).append(r)
    out, stats = set(), {}
    for m, g in sorted(groups.items()):
        ni = len({r["c4"] for r in g if r["c4"]})
        b = ni >= min_c4 and len(g) >= min_products
        stats[m] = dict(n_products=len(g), n_ingredients=ni, batch=b)
        if b:
            out.update(r["key"] for r in g)
    return out, stats


def stay_records(code: str, stays: list, signals: list) -> list[dict]:
    """§5 집행정지 기록 칸. target_type: price_cut(약가인하) / suspension(급여중지) / delisting(급여삭제, 빌드에서) / unknown(미상)"""
    ttype = {"PRICE_NOTICE": "price_cut", "STAY_RELEASE_PRICE": "price_cut", "SUSPEND_NOTICE": "suspension"}
    out = []
    for sy in stays:
        tg = [g for g in signals if g.stay_blocked and sy.date in g.blocking_stays]

        def detail(g):
            d = f"{g.signal_date.year % 100}.{g.signal_date.month}.{g.signal_date.day}."
            if g.signal_type == "SUSPEND_NOTICE":
                return (d + (f"~{g.period_end.month}.{g.period_end.day}." if g.period_end else "")) + " 급여중지"
            if g.source_rule == "S_AMT":
                return f"대체조제 금액 {g.amount:,.0f}원 인하"
            return f"{d} {g.amount:,.0f}원 인하"
        out.append(dict(stay_id=f"STAY:{code}:{sy.date.isoformat()}", product_code=code, stay_date=sy.date, stay_decision_date=None,
                        first_month=sy.first_month, end_month=sy.end_month,
                        stay_end_date=base(sy.end_month) if sy.end_month else None,
                        stay_end_reason=None if sy.end_month else None, left_censored=sy.left_censored, released_by_r5=sy.released_by_r5,
                        target_type=",".join(sorted({ttype[g.signal_type] for g in tg})) if tg else "unknown",
                        target_signal_id=",".join(g.signal_id for g in tg) if tg else None,
                        target_detail=" / ".join(detail(g) for g in tg) if tg else None,
                        target_source=",".join(sorted({g.stay_target_source for g in tg})) if tg else None, external_ref=None))
    return out


# ---------------------------------------------------------------- 코드 이어받기 (§3-1)
KEEP_PAREN_WORDS = ("1회용", "수출명", "무보존제", "프리필드", "펜", "바이알", "앰플", "백")


def name_key(name: str | None) -> str:
    """순서 2 제품명 비교용 이름 (사용자 결정 2026-11-18): 밑줄 앞 제품명에서 괄호 안 성분 표기를 지우고 공백을 지운다.
    괄호 안에 KEEP_PAREN_WORDS(제형·포장·수출명 표시)가 있으면 그 괄호는 남긴다. 중첩 괄호는 안쪽부터"""
    x = (name or "").split("_")[0]
    keep: list[str] = []

    def sub(mt):
        t = mt.group(0)
        if any(w in t for w in KEEP_PAREN_WORDS):
            keep.append(t)
            return f"\x00{len(keep) - 1}\x00"
        return ""
    while True:
        y = re.sub(r"\([^()]*\)", sub, x)
        if y == x:
            break
        x = y
    for i, t in enumerate(keep):                       # 남긴 괄호를 되돌린다 (안쪽부터 바꿨으므로 바깥쪽 안에 들어 있을 수 있다)
        x = x.replace(f"\x00{i}\x00", t)
    while "\x00" in x:
        x = re.sub(r"\x00(\d+)\x00", lambda mt: keep[int(mt.group(1))], x)
    return re.sub(r"\s+", "", x)

@dataclass
class Pair:
    old: str
    new: str
    gone_month: str
    entry_month: str
    kind: str                # transfer / recode / swap
    link_basis: str          # same_item / same_name / same_firm_ingredient / list_recode
    order: int
    gap_months: int
    known_month: str


@dataclass
class Entry:
    code: str
    month: str
    kind: str                # succession / new_listing / added_listing
    link_basis: str | None
    pair: Pair | None = None


@dataclass
class Exit:
    code: str
    gone_month: str
    kind: str                # succession / delisting / delisting_spec
    link_basis: str | None
    as_known_kind: str
    known_month: str
    retracted_month: str | None
    pair: Pair | None = None


@dataclass
class LinkResult:
    pairs: list
    entries: dict
    exits: dict


def _runs(ms: tuple, idx: dict) -> list[tuple[int, int]]:
    out = []
    for m in ms:
        i = idx[m]
        if out and out[-1][1] == i - 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def link_codes(months: list[str], lives: dict) -> LinkResult:
    idx = {m: i for i, m in enumerate(months)}
    n = len(months)
    presence: dict[int, set] = {}
    by_item: dict[str, set] = {}
    ent, ext = [], []
    for code, lf in lives.items():
        runs = _runs(lf.months, idx)
        for a, b in runs:
            for i in range(a, b + 1):
                presence.setdefault(i, set()).add(code)
        if lf.item:
            by_item.setdefault(lf.item, set()).add(code)
        if runs and runs[0][0] > 0:
            ent.append((code, runs[0][0]))
        for a, b in runs:
            if b < n - 1:
                ext.append((code, b + 1))
    prefix = name_key
    cands = []
    ext_by_item: dict[tuple, list] = {}
    ext_by_ing: dict[str, list] = {}
    ext_by_code: dict[str, list] = {}
    for code, g in ext:
        lf = lives[code]
        if lf.item:
            ext_by_item.setdefault((lf.item, lf.ing_last), []).append((code, g))
        ext_by_ing.setdefault(lf.ing_last, []).append((code, g))
        ext_by_code.setdefault(code, []).append((code, g))
    for code, e in ent:
        lf = lives[code]
        if lf.item:
            for x, g in ext_by_item.get((lf.item, lf.ing_first), []):
                if x != code and g <= e + 1:
                    cands.append((1, abs(e - g), g, x, code, e))
        for x, g in ext_by_ing.get(lf.ing_first, []):
            if x == code or abs(e - g) > 1:
                continue
            lx = lives[x]
            if prefix(lx.name_last) == prefix(lf.name_first) and prefix(lf.name_first):
                cands.append((2, abs(e - g), g, x, code, e))       # §11-3: 업체가 같으면 코드 변경 (이름이 같으면 교체 등재 아님)
            if lx.firm_last == lf.firm_first:
                cands.append((3, abs(e - g), g, x, code, e))
        for x in lf.prev_codes:
            for xx, g in ext_by_code.get(x, []):
                if xx != code and g <= e + 1:
                    cands.append((4, abs(e - g), g, xx, code, e))
    cands.sort(key=lambda c: (c[0], c[1], c[2], c[3], c[4]))
    used_x, used_e = set(), set()
    pairs = []
    basis = {1: "same_item", 2: "same_name", 3: "same_firm_ingredient", 4: "list_recode"}
    for order, _, g, x, code, e in cands:
        if (x, g) in used_x or (code, e) in used_e:
            continue
        used_x.add((x, g))
        used_e.add((code, e))
        lx, le = lives[x], lives[code]
        diff = lx.firm_last != le.firm_first
        kind = "swap" if order == 3 else ("transfer" if diff else "recode")
        pairs.append(Pair(x, code, months[g], months[e], kind, basis[order], order, max(0, e - g), months[max(e, g)]))
    entries, exits = {}, {}
    pair_e = {(p.new, p.entry_month): p for p in pairs}
    pair_x = {(p.old, p.gone_month): p for p in pairs}
    for code, e in ent:
        m = months[e]
        p = pair_e.get((code, m))
        lf = lives[code]
        if p:
            entries[(code, m)] = Entry(code, m, "succession", p.link_basis, p)
        elif lf.item and any(c != code for c in by_item.get(lf.item, ()) if c in presence.get(e, set())):
            entries[(code, m)] = Entry(code, m, "added_listing", "item_listed")
        else:
            entries[(code, m)] = Entry(code, m, "new_listing", None if lf.item else "no_item_link")
    for code, g in ext:
        m = months[g]
        lf = lives[code]
        remains = bool(lf.item) and any(c != code for c in by_item.get(lf.item, ()) if c in presence.get(g, set()))
        base_kind = "delisting_spec" if remains else "delisting"
        p = pair_x.get((code, m))
        if p:
            late = idx[p.entry_month] > g
            exits[(code, m)] = Exit(code, m, "succession", p.link_basis, base_kind if late else "succession", m,
                                    p.entry_month if late else None, p)
        else:
            exits[(code, m)] = Exit(code, m, base_kind, "item_remains" if remains else None, base_kind, m, None)
    return LinkResult(pairs, entries, exits)


def exit_reentry_events(code: str, months: list[str], obs: dict, link: LinkResult) -> list[Event]:
    """급여삭제·재등재·삭제 집행정지 (§3). 이어받기로 넘겨준 사라짐은 as_known 급여삭제 행만 (retracted_month로 철회)"""
    idx = {m: i for i, m in enumerate(months)}
    ms = tuple(m for m in months if _get(obs, m).in_b)
    runs = _runs(ms, idx)
    out = []
    case = None
    for k, (a, b) in enumerate(runs):
        nxt = runs[k + 1] if k + 1 < len(runs) else None
        if b >= len(months) - 1:
            break
        g = months[b + 1]
        lo, hi = base(months[b]) + ONE, base(g)
        x = link.exits.get((code, g))
        if x and x.kind == "succession":
            if x.retracted_month:
                out.append(Event("DELISTING", x.as_known_kind, g, effective_lo=lo, effective_hi=hi, date_source="interval",
                                 known_month=g, retracted_month=x.retracted_month, link_basis=x.link_basis,
                                 delisting_case_id=f"DL:{code}:{g}"))
            case = None
            continue
        kind = x.kind if x else "delisting"
        if case is not None:
            out.append(Event("DELISTING", "confirmed_after_stay", g, effective_lo=lo, effective_hi=hi, date_source="interval",
                             known_month=g, delisting_case_id=case[0], link_basis=x.link_basis if x else None,
                             evidence=f"1차 삭제 {case[1].isoformat()}~{case[2].isoformat()}"))
            case = None
        else:
            cid = f"DL:{code}:{g}"
            out.append(Event("DELISTING", kind, g, effective_lo=lo, effective_hi=hi, date_source="interval", known_month=g,
                             delisting_case_id=cid, link_basis=x.link_basis if x else None))
            case_pending = (cid, lo, hi)
            if nxt:
                r = months[nxt[0]]
                stays = sorted(t.date for t in _get(obs, r).toks if t.src == "B" and t.type == "R4" and t.date
                               and base(months[b]) < t.date <= base(r))
                if stays:
                    out.append(Event("DELISTING_STAY", "delisting_stay", r, effective_date=base(r), date_source="snapshot_first",
                                     known_month=r, delisting_case_id=cid))
                    out.append(Event("STAY_START", "delisting_target", r, effective_date=stays[0], date_source="note_benefit",
                                     known_month=r, delisting_case_id=cid, stay_target="delisting", stay_target_source="timing"))
                    case = case_pending
                else:
                    out.append(Event("LISTING", "relisting", r, effective_date=base(r), date_source="snapshot_first", known_month=r))
    return out


# ---------------------------------------------------------------- 지표 (§7)
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def rates(rows: list[dict]) -> dict:
    """§7 (2026-11-18 개정). rows: {outcome, reason, stay_blocked, current_status}.
    A 현재 시점 집행률 = 실현 계열 ÷ (전체 − 판정 제외 − 미결 AWAITING)  (미결 ONGOING_STAY는 미실현으로 셈)
    B 최종 실현율     = 실현 계열 ÷ (전체 − 판정 제외 − 미결 전체)
    정시 실현율       = 실현 ÷ (B 분모).  집행정지율 = 막힌 신호 ÷ (전체 − 판정 제외)"""
    n = len(rows)
    ex = sum(r["outcome"] == "EXCLUDED" for r in rows)
    cen = sum(r["outcome"] == "CENSORED" for r in rows)
    aw = sum(r["outcome"] == "CENSORED" and r.get("current_status") == "AWAITING" for r in rows)
    fam = sum(r["outcome"] in REALIZED_FAMILY for r in rows)
    on = sum(r["outcome"] == "REALIZED" for r in rows)
    st = sum(bool(r["stay_blocked"]) and r["outcome"] != "EXCLUDED" for r in rows)
    out = {"n": n, "n_excluded": ex, "n_censored": cen, "n_awaiting": aw, "n_ongoing_stay": cen - aw}
    for k, (a, b) in {"A": (fam, n - ex - aw), "B": (fam, n - ex - cen), "on_time": (on, n - ex - cen), "stay": (st, n - ex)}.items():
        lo, hi = wilson(a, b)
        out[k] = (a, b, (a / b) if b else None, lo, hi)
    return out


def omission_rate(n_signaled: int, n_unobserved: int) -> float | None:
    t = n_signaled + n_unobserved
    return n_unobserved / t if t else None


# ---------------------------------------------------------------- 전이표 (§8)
STATES = ("scheduled_listing", "reimbursed", "suspended", "delisted", "transferred_out")
ALLOWED = {(s, s) for s in STATES} | {
    ("scheduled_listing", "reimbursed"), ("scheduled_listing", "suspended"), ("scheduled_listing", "delisted"),
    ("scheduled_listing", "transferred_out"),
    ("reimbursed", "suspended"), ("reimbursed", "delisted"), ("reimbursed", "transferred_out"),
    ("suspended", "reimbursed"), ("suspended", "delisted"), ("suspended", "transferred_out"),
    ("delisted", "reimbursed"), ("delisted", "suspended"),
    ("transferred_out", "reimbursed"), ("transferred_out", "suspended"),
}


def check_transitions(seq: list[str]) -> None:
    """불가능한 칸(예: 급여 → 등재 예정, 급여삭제 ↔ 넘겨줌)이 나오면 ValueError"""
    for a, b in zip(seq, seq[1:]):
        if (a, b) not in ALLOWED:
            raise ValueError(f"불가능한 전이: {a} → {b}")
