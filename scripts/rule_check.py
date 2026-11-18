"""독립 점검: docs/RULES_2026-11-17.md를 원본(data/raw)에 직접 적용해 검증 표본의 정답을 만든다.

- 파이프라인 표(data/interim)와 pbd 패키지를 쓰지 않는다. 비고 파서·상태·신호·코드 이어받기 판정을 여기서 따로 구현한다.
- 입력: data/raw/benefit/*.parquet, data/raw/substitute/*.parquet, data/raw/master/dim_drug_code_map_26.parquet,
        outputs/validation/sample_60.user_reviewed.csv (행 목록만: sample_id, record_type, record_id, month)
- 출력: outputs/validation/rule_check.csv (sample_id, truth_type, truth_date, truth_note)
- 정답 형식 (labels 스크립트와 같음)
  이벤트: truth_type = "EVENT_TYPE|kind", truth_date = 유효일 ISO 또는 "lo~hi"(삭제 구간)
  신호:   truth_type = "SIGNAL_TYPE|FINAL_OUTCOME[:사유 또는 미결 상태]" + ("|blocked" 집행정지로 막힘), truth_date = "신호일;확인 달"
  없음:   truth_type = "NONE" (그 규칙으로는 그 기록이 생기지 않음)
사용: python -I scripts/rule_check.py [sample_id ...]
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
VAL = ROOT / "outputs/validation"
USER_ROWS = {2, 7, 9, 16, 21, 22, 25, 28, 29, 31, 35, 44, 51, 59,      # 기존 사용자 검토 14
             11, 12, 17, 18, 37, 42, 58, 60,                            # RULES §9 (2026-11-18)
             32, 33, 50, 61}                                            # 대화에서 정한 4행
ONE = dt.timedelta(days=1)

# ---------------------------------------------------------------- 비고 파서 (독립)
DT = r"'?(\d{2}|\d{4})\.(\d{1,2})\.(\d{1,2})\.?"


def _date(y, m, d):
    y = int(y)
    y += 2000 if y < 100 else 0
    try:
        return dt.date(y, int(m), int(d))
    except ValueError:
        return None


PATS = [
    ("R5x", re.compile(rf"집행정지\({DT}\)\s*->\s*집행정지\s*해제\({DT},?\s*(\d+)원\)")),
    ("R5", re.compile(rf"집행정지 해제 및 상한금액 인하\({DT}\s*고시\)")),
    ("R6", re.compile(rf"급여정지 해제\({DT}\)")),
    ("R3b", re.compile(rf"급여정지\({DT}\s*~\s*{DT}\)")),
    ("R3", re.compile(rf"급여정지\({DT}\)")),
    ("R4", re.compile(rf"집행정지\({DT}\)")),
    ("R9", re.compile(rf"급여삭제\({DT}\)")),
    ("R1", re.compile(rf"{DT}\s*(?:등재|신설)")),
    ("R2", re.compile(rf"\(?\s*{DT},?\s*(\d+)원\s*\)?")),
]


def parse(note):
    """→ [(type, date, date_end, amount)]"""
    out = []
    if not note:
        return out
    s = str(note)
    for typ, pat in PATS:
        for m in pat.finditer(s):
            g = m.groups()
            if typ == "R5x":
                out.append(("R4", _date(*g[0:3]), None, None))
                out.append(("R5", _date(*g[3:6]), None, float(g[6])))
            elif typ == "R3b":
                out.append(("R3b", _date(*g[0:3]), _date(*g[3:6]), None))
            elif typ == "R2":
                out.append(("R2", _date(*g[0:3]), None, float(g[3])))
            else:
                out.append((typ, _date(*g[0:3]), None, None))
        s = pat.sub(" ", s)
    return out


# ---------------------------------------------------------------- 원본 적재
def load():
    c = duckdb.connect()
    pad = "case when regexp_matches(cast(제품코드 as varchar), '^[0-9]+$') then lpad(cast(제품코드 as varchar), 9, '0') end"
    c.execute(f"""create table b as select substr(cast(기준일자 as varchar), 1, 7) m, {pad} pc, 업체명 firm, 제품명 name,
                  주성분코드 ing, 상한금액 price, 단위 unit, 비고 note, 목록정비전코드 prev
                  from read_parquet('{RAW}/benefit/benefit_snapshot_table_*.parquet', union_by_name = true)""")
    c.execute("delete from b where pc is null")
    c.execute(f"""create table s as select substr(cast(기준일자 as varchar), 1, 7) m, {pad} pc, 상한금액 price,
                  "비고(급여정지)" note, cast(cast("②품목기준코드" as bigint) as varchar) item
                  from read_parquet('{RAW}/substitute/substitute_snapshot_table_*.parquet', union_by_name = true)""")
    c.execute("delete from s where pc is null")
    c.execute(f"""create table cm as select lpad(cast(cast("제품코드(개정후)" as bigint) as varchar), 9, '0') pc,
                  cast(품목기준코드 as varchar) item from read_parquet('{RAW}/master/dim_drug_code_map_26.parquet')
                  where "제품코드(개정후)" is not null""")
    c.execute("""create table items as select pc, min(item) item from (select pc, item from cm union select distinct pc, item from s where item is not null)
                 group by 1 having count(distinct item) = 1""")
    months = [r[0] for r in c.execute("select distinct m from b order by 1").fetchall()]
    return c, months


class Data:
    def __init__(self):
        self.c, self.months = load()
        self.idx = {m: i for i, m in enumerate(self.months)}
        self._cache = {}

    def base(self, m):
        return dt.date(int(m[:4]), int(m[5:]), 1)

    def series(self, pc):
        if pc in self._cache:
            return self._cache[pc]
        out = {m: dict(in_b=False, in_s=False, pb=None, ps=None, toks=[], firm=None, name=None, ing=None, prev=None) for m in self.months}
        for m, firm, name, ing, price, note, prev in self.c.execute(
                "select m, firm, name, ing, price, note, prev from b where pc = ?", [pc]).fetchall():
            o = out[m]
            o.update(in_b=True, pb=price, firm=firm, name=name, ing=ing, prev=prev)
            o["toks"] += [(t, d, e, a, "B") for t, d, e, a in parse(note)]
        for m, price, note in self.c.execute("select m, price, note from s where pc = ?", [pc]).fetchall():
            o = out[m]
            o.update(in_s=True, ps=price)
            o["toks"] += [(t, d, e, a, "S") for t, d, e, a in parse(note)]
        self._cache[pc] = out
        return out

    def item(self, pc):
        r = self.c.execute("select item from items where pc = ?", [pc]).fetchone()
        return r[0] if r else None


# ---------------------------------------------------------------- 상태 (급여목록만, 그 달 1일)
def marks(D, o, m):
    b = D.base(m)
    r3 = {d for t, d, e, a, src in o["toks"] if src == "B" and t == "R3" and d and d <= b}
    r3b = {(d, e) for t, d, e, a, src in o["toks"] if src == "B" and t == "R3b" and d and d <= b <= e}
    return r3, r3b


def status(D, pc, m):
    """revised 상태 (한 달 누락은 급여중지 유지). 'absent' / 'scheduled' / 'suspended' / 'reimbursed'"""
    S = D.series(pc)
    o = S[m]
    if not o["in_b"]:
        return "absent"
    i = D.idx[m]
    # 등재 예정: 처음 관측부터 이어진 달에 미래 R1
    first = next(mm for mm in D.months if S[mm]["in_b"])
    run_ok = all(any(t == "R1" and d and d > D.base(mm) for t, d, *_ in S[mm]["toks"]) for mm in D.months[D.idx[first]: i + 1])
    if run_ok:
        return "scheduled"
    r3, r3b = marks(D, o, m)
    r6 = {d for mm in D.months for t, d, *_ in S[mm]["toks"] if t == "R6" and d}
    if any(not any(x >= d and x <= D.base(m) for x in r6) for d in r3) or r3b:
        return "suspended"
    # 기간 중지: 시작일 뒤 표시가 한 번이라도 보였으면 날짜로 판정
    for mm in D.months[: i + 1]:
        for t, d, e, a, src in S[mm]["toks"]:
            if t == "R3b" and src == "B" and d <= D.base(mm) and d <= D.base(m) <= e:
                return "suspended"
    # 한 달 누락 / 표시 사라진 다음 달 삭제
    if 0 < i < len(D.months) - 1:
        p, n = S[D.months[i - 1]], S[D.months[i + 1]]
        pr3, _ = marks(D, p, D.months[i - 1]) if p["in_b"] else (set(), set())
        gone = pr3 - r3
        if gone and (not n["in_b"] or gone & marks(D, n, D.months[i + 1])[0]):
            return "suspended"
    return "reimbursed"


# ---------------------------------------------------------------- 코드 이어받기 (§3-1, 순서 1·2·3·4)
def first_last(D, pc):
    S = D.series(pc)
    ms = [m for m in D.months if S[m]["in_b"]]
    return (ms[0], ms[-1]) if ms else (None, None)


def link_for_exit(D, pc, gone):
    """사라진 코드 pc (gone = 처음 없는 달) → (순서, 새 코드, 종류, 근거) 또는 None"""
    S = D.series(pc)
    last = D.months[D.idx[gone] - 1]
    o = S[last]
    item = D.item(pc)
    g = D.idx[gone]
    cands = []
    rows = D.c.execute("""select pc, min(m) f, arg_min(firm, m) firm, arg_min(name, m) name, arg_min(ing, m) ing from b
                          where ing = ? or pc in (select pc from items where item = ?) or prev = ? group by 1""", [o["ing"], item, pc]).fetchall()
    for npc, f, firm, name, ing in rows:
        if npc == pc or f == D.months[0]:
            continue
        e = D.idx[f]
        ni = D.item(npc)
        diff = firm != o["firm"]
        if item and ni == item and ing == o["ing"] and g <= e + 1:
            cands.append((1, abs(e - g), npc, "transfer" if diff else "recode", "same_item", e))
        if ing == o["ing"] and abs(e - g) <= 1 and (name or "").split("_")[0] == (o["name"] or "").split("_")[0]:
            cands.append((2, abs(e - g), npc, "transfer" if diff else "recode", "same_name", e))     # §11-3
        if ing == o["ing"] and abs(e - g) <= 1 and not diff:
            cands.append((3, abs(e - g), npc, "swap", "same_firm_ingredient", e))
        prevs = {r[0] for r in D.c.execute("select distinct prev from b where pc = ? and prev is not null", [npc]).fetchall()}
        if pc in prevs and g <= e + 1:
            cands.append((4, abs(e - g), npc, "transfer" if diff else "recode", "list_recode", e))
    return min(cands) if cands else None


def link_for_entry(D, pc, entry):
    """새 코드 pc (entry = 처음 실린 달) → 이어받기 후보 또는 None"""
    S = D.series(pc)
    o = S[entry]
    item = D.item(pc)
    e = D.idx[entry]
    cands = []
    prevs = {r[0] for r in D.c.execute("select distinct prev from b where pc = ? and prev is not null", [pc]).fetchall()}
    rows = D.c.execute("""select pc, max(m) l, arg_max(firm, m) firm, arg_max(name, m) name, arg_max(ing, m) ing from b
                          where ing = ? or pc in (select pc from items where item = ?) or pc in (select unnest(?::varchar[])) group by 1""",
                       [o["ing"], item, list(prevs)]).fetchall()
    for xpc, l, firm, name, ing in rows:
        if xpc == pc or l == D.months[-1]:
            continue
        # 사라진 달들 (마지막 관측 뒤만 본다: 이어받기 후보는 그 코드가 사라진 첫 달)
        xs = D.series(xpc)
        gones = [D.idx[m] for m in D.months[1:] if not xs[m]["in_b"] and xs[D.months[D.idx[m] - 1]]["in_b"]]
        for g in gones:
            diff = firm != o["firm"]
            if item and D.item(xpc) == item and ing == o["ing"] and g <= e + 1:
                cands.append((1, abs(e - g), xpc, "transfer" if diff else "recode", "same_item", g))
            if ing == o["ing"] and abs(e - g) <= 1 and (name or "").split("_")[0] == (o["name"] or "").split("_")[0]:
                cands.append((2, abs(e - g), xpc, "transfer" if diff else "recode", "same_name", g))     # §11-3
            if ing == o["ing"] and abs(e - g) <= 1 and not diff:
                cands.append((3, abs(e - g), xpc, "swap", "same_firm_ingredient", g))
            if xpc in prevs and g <= e + 1:
                cands.append((4, abs(e - g), xpc, "transfer" if diff else "recode", "list_recode", g))
    return min(cands) if cands else None


def item_listed(D, pc, m):
    item = D.item(pc)
    if not item:
        return False
    r = D.c.execute("select count(*) from b where m = ? and pc <> ? and pc in (select pc from items where item = ?)", [m, pc, item]).fetchone()
    return r[0] > 0


# ---------------------------------------------------------------- 신호 (§6)
def stays(D, pc):
    """{집행정지 날짜: (처음 달, 끝 달 또는 None)}  (급여목록 R4)"""
    S = D.series(pc)
    out = {}
    for m in D.months:
        if not S[m]["in_b"]:
            continue
        for t, d, *_ in S[m]["toks"]:
            if t == "R4" and d and d not in out:
                out[d] = [m, None]
    for d, v in out.items():
        for m in D.months[D.idx[v[0]] + 1:]:
            o = S[m]
            if o["in_b"] and not any(t == "R4" and dd == d for t, dd, *_ in o["toks"]):
                v[1] = m
                break
            if o["in_b"] and any(t == "R5" and dd and dd <= D.base(m) for t, dd, *_ in o["toks"]):
                v[1] = m
                break
    return out


def signal_truth(D, pc, styp, sdate):
    """신호 하나의 판정. styp: PRICE_NOTICE / SUSPEND_NOTICE / LISTING_NOTICE / LISTING_NOTE_EXISTING / DELISTING_NOTICE / STAY_RELEASE_PRICE"""
    S = D.series(pc)
    raw = {"PRICE_NOTICE": ("R2",), "SUSPEND_NOTICE": ("R3", "R3b"), "LISTING_NOTICE": ("R1",), "LISTING_NOTE_EXISTING": ("R1",),
           "DELISTING_NOTICE": ("R9",), "STAY_RELEASE_PRICE": ("R5",)}[styp]
    hits = [(m, t, d, e, a) for m in D.months for t, d, e, a, src in S[m]["toks"] if t in raw and d == sdate and d > D.base(m)]
    if not hits:
        return None
    F = hits[0][0]
    amount = next((a for m, t, d, e, a in hits if m == F and a is not None), None)
    pend = next((e for m, t, d, e, a in hits if t == "R3b"), None)
    is_r3b = any(t == "R3b" for m, t, d, e, a in hits)
    C = next((m for m in D.months if D.base(m) >= sdate), None)
    ST = stays(D, pc)
    cand = {d: v for d, v in ST.items() if v[0] >= F and v[0] != D.months[0]}

    def out(o, why, blocked=False):
        return o, f"{sdate.isoformat()};{C or '-'}", why, blocked

    def price_prev(m):
        for mm in reversed(D.months[: D.idx[m]]):
            if S[mm]["in_b"]:
                return S[mm]["pb"]
        return None

    if styp == "PRICE_NOTICE":
        if status(D, pc, F) == "suspended":
            return out("EXCLUDED:listed_during_suspension", f"최초 기재 {F}에 급여중지 중")
        parents = [d for m, t, d, e, a in [(m, t, d, e, a) for m in D.months[: D.idx[F] + 1] for t, d, e, a, src in S[m]["toks"]]
                   if t in ("R3", "R3b") and d and d > D.base(m) and d <= sdate]
        if parents and C and (status(D, pc, C) == "suspended" or (status(D, pc, C) == "absent" and min(parents) < sdate)):
            return out("EXCLUDED:suspended_before_price_date", f"부모 급여중지 예고 {min(parents)}가 인하일 전에 시작 (인하일 전 급여중지)")
    if C is None:
        return out("CENSORED:AWAITING", "확인 스냅샷이 분석 끝 뒤")
    if styp == "PRICE_NOTICE":
        early = next((m for m in D.months[D.idx[F] + 1: D.idx[C]] if S[m]["in_b"] and S[m]["pb"] == amount), None)
        if early:
            return out("REALIZED", f"{early} 상한금액 = 예고 금액 {amount}")
    blocked = False
    for m in D.months[D.idx[C]:]:
        o = S[m]
        active = [d for d, v in cand.items() if v[0] <= m and (v[1] is None or m < v[1])]
        if not o["in_b"]:
            if blocked:
                return out("NOT_REALIZED:stay_then_delisted", f"{m} 집행정지 중 삭제", True)
            nxt = link_for_exit(D, pc, D.months[max(D.idx[m], 1)]) if m != C or True else None
            if nxt and styp in ("PRICE_NOTICE", "SUSPEND_NOTICE"):
                return out("FOLLOW:" + nxt[2], f"{m} 제품 없음, 이어받은 코드 {nxt[2]}")
            if styp == "SUSPEND_NOTICE":
                return out("REALIZED_BY_DELISTING", f"{m} 제품 없음 (급여삭제)")
            if styp == "DELISTING_NOTICE":
                return out("REALIZED", f"{m} 제품 없음")
            return out("EXCLUDED:target_gone", f"{m} 제품 없음 (대상 소멸)")
        r3, r3b = marks(D, o, m)
        starts = sorted(r3 | {a for a, b in r3b})
        if styp == "SUSPEND_NOTICE":
            if is_r3b and (sdate, pend) in r3b:
                return out("REALIZED_LATE" if blocked else "REALIZED", f"{m} 기간 중지 표시", blocked)
            if sdate in starts and not blocked:
                return out("REALIZED", f"{m} 같은 정지일 표시")
            near = [d for d in starts if d >= sdate - dt.timedelta(days=31)]
            if near:
                d = min(near, key=lambda z: abs((z - sdate).days))
                return out("REALIZED_LATE" if (blocked or d > sdate) else "REALIZED_CHANGED", f"{m} 정지일 {d}", blocked)
        elif styp in ("PRICE_NOTICE", "STAY_RELEASE_PRICE"):
            if o["pb"] == amount:
                late = blocked or m != C
                return out("REALIZED_LATE" if late else "REALIZED", f"{m} 상한금액 = 예고 금액 {amount}", blocked)
            p0 = price_prev(m)
            if o["pb"] is not None and p0 is not None and o["pb"] != p0 and (not blocked or not active):
                return out("REALIZED_CHANGED", f"{m} 상한금액 {p0} → {o['pb']} (예고 {amount})", blocked)
        elif styp in ("LISTING_NOTICE", "LISTING_NOTE_EXISTING"):
            st = status(D, pc, m)
            if st == "reimbursed":
                return out("REALIZED" if m == C else "REALIZED_LATE", f"{m} 급여")
            if st == "suspended":
                return out("NOT_REALIZED:suspended", f"{m} 급여중지")
            continue
        elif styp == "DELISTING_NOTICE":
            return out("NOT_REALIZED:still_listed", f"{m} 아직 목록에 있음")
        if blocked and not active:
            return out("NOT_REALIZED:stay_ended_not_executed", f"{m} 집행정지 종료 뒤 미시행", True)
        if blocked:
            continue
        if active:
            blocked = True
            continue
        if styp == "PRICE_NOTICE" and any(t == "R2" and d == sdate for t, d, *_ in o["toks"]):
            continue
        return out("NOT_REALIZED:note_gone" if styp != "STAY_RELEASE_PRICE" else "NOT_REALIZED:stay_continues", f"{m} 변화 없이 비고 사라짐")
    return out("CENSORED:ONGOING_STAY" if blocked else "CENSORED:AWAITING", "분석 끝까지", blocked)


def fmt_signal(styp, r):
    o, date, why, blocked = r
    return styp + "|" + o + ("|blocked" if blocked else ""), date, why


# ---------------------------------------------------------------- 이벤트 정답
def event_truth(D, typ, pc, month):
    S = D.series(pc)
    i = D.idx[month]
    prev = D.months[i - 1] if i > 0 else None
    b = D.base(month)
    if typ == "ATTR_CHANGE":
        p, o = S[prev], S[month]
        f = [k for k, a in (("firm", "firm"), ("product_name", "name"), ("ingredient_code", "ing")) if p[a] != o[a]]
        return ("ATTR_CHANGE|" + "+".join(f) if f else "NONE"), b.isoformat(), f"{prev}→{month} 바뀐 칸 {f}"
    if typ == "DELISTING":
        lk = link_for_exit(D, pc, month)
        lo, hi = D.base(prev) + ONE, b
        if lk:
            return f"SUCCESSION_OUT|{lk[3]}", D.months[lk[5]] and D.base(D.months[lk[5]]).isoformat(), \
                f"이어받은 코드 {lk[2]} (순서 {lk[0]}, {lk[4]}, 공백 {max(0, lk[5] - i)}개월)"
        # 같은 코드가 다시 실리면서 집행정지 → 삭제 집행정지의 첫 삭제 (이 기록 자체는 급여삭제)
        kind = "delisting_spec" if item_listed(D, pc, month) else "delisting"
        r9 = sorted({d for m in D.months[: i] for t, d, *_ in S[m]["toks"] if t == "R9" and d and d > D.base(m)})
        for d in r9:                                 # 유효일 규칙 1: 실현된 삭제 예고 → 신호 날짜
            r = signal_truth(D, pc, "DELISTING_NOTICE", d)
            if r and r[0] == "REALIZED" and month in (r[2] or ""):
                return f"DELISTING|{kind}", d.isoformat(), f"삭제 예고 {d} 실현"
        return f"DELISTING|{kind}", f"{lo.isoformat()}~{hi.isoformat()}", "이어지는 코드 없음" + (", 같은 허가 다른 코드 남음" if kind == "delisting_spec" else "")
    if typ in ("LISTING", "TRANSFER"):
        first = next(m for m in D.months if S[m]["in_b"])
        if first != month:
            # 같은 코드가 다시 실림: 빠진 기간 안 날짜의 집행정지 비고가 있으면 삭제 집행정지
            j = i - 1
            while j >= 0 and not S[D.months[j]]["in_b"]:
                j -= 1
            lb = D.base(D.months[j])
            st = [d for t, d, *_ in S[month]["toks"] if t == "R4" and d and lb < d <= b]
            if st:
                return "DELISTING_STAY|delisting_stay", b.isoformat(), f"다시 실린 달 비고 집행정지({st[0]}), 빠진 기간 안"
            return "LISTING|relisting", b.isoformat(), "같은 코드 다시 실림"
        lk = link_for_entry(D, pc, month)
        if lk:
            return f"SUCCESSION|{lk[3]}", b.isoformat(), f"넘겨준 코드 {lk[2]} (순서 {lk[0]}, {lk[4]}, 공백 {max(0, i - lk[5])}개월)"
        kind = "added_listing" if item_listed(D, pc, month) else "new_listing"
        r1 = sorted(d for t, d, *_ in S[month]["toks"] if t == "R1" and d)
        eff = r1[0] if r1 else b
        return f"LISTING|{kind}", eff.isoformat(), "등재일 비고" if r1 else "관측월 1일"
    if typ == "PRICE_CHANGE":
        if status(D, pc, prev) != "reimbursed" or status(D, pc, month) != "reimbursed":
            return "NONE", "", "전후 두 달 모두 급여 상태가 아님"
        p0, p1 = S[prev]["pb"], S[month]["pb"]
        if p0 == p1 or p0 is None or p1 is None:
            return "NONE", "", "금액 변화 없음"
        # 이 달 실현으로 끝나는 약가 예고
        notes = {(t, d, a) for m in D.months[: i] for t, d, e, a, src in S[m]["toks"] if t in ("R2", "R5") and d and d > D.base(m)}
        for t5, d, a in sorted(notes):
            r = signal_truth(D, pc, "PRICE_NOTICE" if t5 == "R2" else "STAY_RELEASE_PRICE", d)
            if r and r[0].split(":")[0] in ("REALIZED", "REALIZED_CHANGED", "REALIZED_LATE") and \
                    re.search(re.escape(month), r[2] or "") and (a == p1 or r[0].startswith("REALIZED_CHANGED")):
                if r[0] == "REALIZED":
                    eff = d.isoformat()
                elif r[0] == "REALIZED_CHANGED":            # §6.4 ★: 실현 금액을 알린 비고(실현 달·직전 달)의 날짜, 없으면 실현 달 1일
                    nd = [dd for mm in (prev, month) for t2, dd, e2, a2, src in S[mm]["toks"]
                          if dd and D.base(prev) < dd and (dd.year, dd.month) <= (b.year, b.month) and (t2 == "R5" or (t2 == "R2" and a2 == p1))]
                    eff = (min(nd) if nd else b).isoformat()
                else:                                # 지연 실현: 실현 달 비고의 날짜 (직전 스냅샷 뒤 ~ 그 달 1일), 없으면 1일
                    nd = [dd for t2, dd, e2, a2, src in S[month]["toks"] if dd and D.base(prev) < dd <= b and (t2 == "R5" or (t2 == "R2" and a2 == p1))]
                    eff = (min(nd) if nd else b).isoformat()
                return "PRICE_CHANGE|signaled", eff, f"예고 {d} {a}원 → {r[0]}"
        # 대체조제 금액 신호 (§1): 앞선 달 대체조제 금액 ≠ 급여목록 금액 + 그 달 급여목록에 새 집행정지, 그 금액이 이 달 상한금액
        ST = stays(D, pc)
        for mm in D.months[: i]:
            x = S[mm]
            if x["in_b"] and x["in_s"] and x["ps"] is not None and x["pb"] is not None and x["ps"] < x["pb"] and x["ps"] == p1 \
                    and any(v[0] == mm and mm != D.months[0] for v in ST.values()):
                nd = [dd for t2, dd, e2, a2, src in S[month]["toks"] if dd and D.base(prev) < dd <= b and (t2 == "R5" or (t2 == "R2" and a2 == p1))]
                return "PRICE_CHANGE|signaled", (min(nd) if nd else b).isoformat(), f"{mm} 대체조제 금액 {x['ps']} = 막힌 인하 처분 (§1), {month} 실현"
        nd = sorted(d for t, d, e, a, src in S[month]["toks"] if t == "R2" and a == p1 and d and D.base(prev) < d <= b)
        return "PRICE_CHANGE|unobserved_notice", (nd[0] if nd else b).isoformat(), "연결된 예고 없음"
    if typ in ("RESUME", "SUSPEND"):
        # v4 관측월과 다를 수 있어 앞뒤 2개월에서 상태 전이를 찾는다
        for m in D.months[max(1, i - 2): i + 2]:
            a0, a1 = status(D, pc, D.months[D.idx[m] - 1]), status(D, pc, m)
            if typ == "SUSPEND" and a0 in ("reimbursed", "scheduled") and a1 == "suspended":
                r3, r3b = marks(D, S[m], m)
                d = min(r3 | {x for x, _ in r3b}) if (r3 or r3b) else D.base(m)
                return "SUSPEND|in_window", d.isoformat(), f"{m} 급여목록 표시 급여중지({d})"
            if typ == "RESUME" and a0 == "suspended" and a1 == "reimbursed":
                pm = D.months[D.idx[m] - 1]
                po = S[pm]
                r6 = [d for t, d, *_ in S[m]["toks"] + po["toks"] if t == "R6" and d]
                pr3, pr3b = marks(D, po, pm)
                if r6:
                    return "SUSPEND_LIFT|r6_note", min(r6).isoformat(), "급여정지 해제 비고"
                if pr3b and all(e < D.base(m) for _, e in pr3b) or any(t == "R3b" and e and e < D.base(m) for t, d, e, *_ in S[m]["toks"]):
                    e = max([e for _, e in pr3b] + [e for t, d, e, *_ in S[m]["toks"] if t == "R3b" and e])
                    return "SUSPEND_LIFT|period_end", (e + ONE).isoformat(), "기간 중지 종료일 다음 날"
                return "SUSPEND_LIFT|list_only", D.base(m).isoformat(), "급여목록에서 두 달 연속 표시 없음"
        return "NONE", "", "상태 전이 없음"
    if typ in ("STAY_START", "STAY_END"):
        ST = stays(D, pc)
        for d, (f, e) in sorted(ST.items()):
            if typ == "STAY_START" and f == month:
                if f == D.months[0] and d < D.base(f):
                    return "NONE", "", "창 시작 전 집행정지 (표시만)"
                fb = first_last(D, pc)[0]
                k = "window_first_month" if f == D.months[0] else ("at_entry" if f == fb else "in_window")
                return f"STAY_START|{k}", d.isoformat(), f"집행정지({d}) 처음 표시"
            if typ == "STAY_END" and e == month:
                r5f = sorted({dd for mm in D.months[: D.idx[month]] for t, dd, *_ in S[mm]["toks"] if t == "R5" and dd and dd > D.base(mm)})
                for dd in r5f:
                    r = signal_truth(D, pc, "STAY_RELEASE_PRICE", dd)
                    if r and r[0].startswith("REALIZED") and month in (r[2] or ""):
                        return "STAY_END|released", dd.isoformat(), f"집행정지 해제·인하 예고 {dd} 실현 (유효일 규칙 7)"
                r5 = [dd for t, dd, *_ in S[month]["toks"] if t == "R5" and dd and dd <= b]
                if r5:
                    return "STAY_END|released", min(r5).isoformat(), "집행정지 해제 비고"
                # 같은 파일에 처음 나타난 가격 예고가 실현되면 그 예고일 (NOTE_RULES §3 예외)
                ps = sorted(dd for t, dd, ee, a, src in S[month]["toks"] if t == "R2" and dd and dd > b)
                for dd in ps:
                    r = signal_truth(D, pc, "PRICE_NOTICE", dd)
                    if r and r[0] == "REALIZED":
                        return "STAY_END|note_gone", dd.isoformat(), f"같은 파일의 가격 예고 {dd} 실현"
                return "STAY_END|note_gone", b.isoformat(), "집행정지 표시 사라짐"
        return "NONE", "", "집행정지 시작·종료 없음"
    raise ValueError(typ)


SIG_MAP = {"PRICE_PENDING": "PRICE_NOTICE", "SUSPEND_SCHEDULED": "SUSPEND_NOTICE", "LISTING_SCHEDULED": "LISTING_NOTICE",
           "LISTING_NOTE_EXISTING": "LISTING_NOTE_EXISTING", "DELISTING_SCHEDULED": "DELISTING_NOTICE",
           "STAY_RELEASE_PRICE": "STAY_RELEASE_PRICE"}


def truth_for(D, row):
    typ, pc, key = row.record_id.split(":")[0], row.record_id.split(":")[1], row.record_id.split(":")[2]
    if row.record_type == "signal":
        if typ == "RESUME_SCHEDULED":
            return "NONE", "", "기간 중지 종료일은 별도 신호가 아님 (RULES §6.1)"
        styp = SIG_MAP[typ]
        r = signal_truth(D, pc, styp, dt.date.fromisoformat(key))
        if r is None:
            return "NONE", "", "원본에 그 날짜의 미래 비고 없음"
        if r[0].startswith("FOLLOW:"):
            new = r[0].split(":")[1]
            r2 = signal_truth_follow(D, styp, dt.date.fromisoformat(key), new, r)
            return fmt_signal(styp, r2)
        return fmt_signal(styp, r)
    return event_truth(D, typ, pc, key)


def signal_truth_follow(D, styp, sdate, new, r):
    """이어받은 코드에서 판정 (§6.2). 확인 달에 새 코드 상한금액이 예고 금액이면 실현"""
    S = D.series(new)
    o, date, why, blocked = r
    C = date.split(";")[1]
    if styp == "PRICE_NOTICE" and S[C]["in_b"]:
        amt = None
        return ("REALIZED" if S[C]["pb"] is not None else "EXCLUDED:target_gone"), date, why + f", 새 코드 {new} 상한금액 {S[C]['pb']}", blocked
    return o, date, why, blocked


def main(ids=None):
    rows = pd.read_csv(VAL / "sample_60.user_reviewed.csv", dtype=str)
    rows["sid"] = rows.sample_id.astype(int)
    D = Data()
    out = []
    for r in rows.itertuples():
        if r.sid in USER_ROWS or (ids and r.sid not in ids):
            continue
        t, d, why = truth_for(D, r)
        out.append(dict(sample_id=r.sid, record_id=r.record_id, truth_type=t, truth_date=d, truth_note=f"[rule_check] {why}"))
    df = pd.DataFrame(out)
    if not ids:
        df.to_csv(VAL / "rule_check.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_colwidth", 90, "display.max_rows", 60):
        print(df.to_string(index=False))


if __name__ == "__main__":
    main({int(x) for x in sys.argv[1:]} or None)
