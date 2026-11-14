"""비고 파서 (docs/NOTE_RULES.md).

비고 한 칸을 ' / '로 나눈 조각(P1)마다 문형을 찾는다. 한 조각 안에서 쉼표로 이어진 여러 건도
각각 토큰이 된다. 토큰과 구분자(공백, 쉼표)를 지우고 글자가 남는 조각은 규칙을 추측하지 않고
조각 전체를 `unresolved` 토큰 하나로 둔다.

토큰: {seq, part, type, variant, date, date_end, amount, raw}
type: R1 등재 / R2 가격 예고 / R3 급여정지 / R3b 기간 명시 급여정지 / R4 집행정지 /
      R5 집행정지 해제·가격 / R6 급여정지 해제 / R7 산정불가 / R9 급여삭제 / unresolved
"""
from __future__ import annotations

import datetime as dt
import re

_D = r"'?(\d{{4}}|\d{{2}})\.(\d{{1,2}})\.(\d{{1,2}})\.?"   # P2: yy/yyyy, 끝 마침표 유무, 앞 따옴표


def _d(n: int) -> str:
    """번호를 붙인 날짜 그룹 (y{n}, m{n}, d{n})."""
    return _D.replace("(\\d{{4}}|\\d{{2}})", f"(?P<y{n}>\\d{{4}}|\\d{{2}})") \
             .replace("(\\d{{1,2}})\\.(\\d{{1,2}})", f"(?P<m{n}>\\d{{1,2}})\\.(?P<d{n}>\\d{{1,2}})") \
             .replace("{{", "{").replace("}}", "}")


D1, D2 = _d(1), _d(2)
AMT = r"(?P<amt>\d+)원"

# (type, variant, 정규식). 우선순위 순서. 앞 규칙이 차지한 글자는 뒤 규칙이 보지 않는다.
PATTERNS: list[tuple[str, str | None, re.Pattern]] = [
    # U6: 집행정지(21.9.1.)->집행정지해제(22.1.18., 176원) → R4 + R5
    ("U6", "arrow", re.compile(rf"집행정지\({D1}\)\s*->\s*집행정지\s*해제\({D2},?\s*{AMT}\)")),
    ("R5", None, re.compile(rf"집행정지 해제 및 상한금액 인하\({D1}\s*고시\)")),
    ("R6", None, re.compile(rf"급여정지 해제\({D1}\)")),
    ("R3b", None, re.compile(rf"급여정지\({D1}\s*~\s*{D2}\)")),                 # U2·U3·U8
    ("R3", None, re.compile(rf"급여정지\({D1}\)")),
    ("R4", "reeval", re.compile(rf"허가변경 약제 재평가 집행정지\({D1}\)")),     # U1
    ("R4", None, re.compile(rf"집행정지\({D1}\)")),
    ("R9", None, re.compile(rf"급여삭제\({D1}\)")),                             # U7
    ("R1", "shinseol", re.compile(rf"{D1}\s*신설")),                           # U9
    ("R1", None, re.compile(rf"{D1}\s*등재")),
    ("R2", "paren", re.compile(rf"\(\s*{D1},?\s*{AMT}\s*\)")),                  # U4·U5
    ("R2", None, re.compile(rf"{D1},?\s*{AMT}")),
    ("R7", None, re.compile(r"산정불가")),
]
_SEP = re.compile(r"[\s,/]+")


def _date(m: re.Match, n: int) -> dt.date | None:
    y = m.group(f"y{n}")
    yy = int(y) + (2000 if len(y) == 2 else 0)
    try:
        return dt.date(yy, int(m.group(f"m{n}")), int(m.group(f"d{n}")))
    except ValueError:
        return None


class _Bad(Exception):
    """날짜를 읽을 수 없는 토큰 → 조각 전체 unresolved"""


def _tokens_from(typ: str, variant: str | None, m: re.Match) -> list[dict]:
    g = m.groupdict()
    amount = int(g["amt"]) if g.get("amt") else None
    d1 = _date(m, 1) if g.get("y1") else None
    d2 = _date(m, 2) if g.get("y2") else None
    if (g.get("y1") and d1 is None) or (g.get("y2") and d2 is None):
        raise _Bad
    if typ == "U6":
        raw = m.group(0)
        return [dict(type="R4", variant=variant, date=d1, date_end=None, amount=None, raw=raw),
                dict(type="R5", variant=variant, date=d2, date_end=None, amount=amount, raw=raw)]
    return [dict(type=typ, variant=variant, date=d1, date_end=d2 if typ == "R3b" else None,
                 amount=amount, raw=m.group(0))]


def _parse_part(part: str) -> list[dict]:
    masked = part
    found = []
    try:
        for typ, variant, pat in PATTERNS:
            for m in pat.finditer(masked):
                found.append((m.start(), _tokens_from(typ, variant, m)))
            masked = pat.sub(lambda m: " " * len(m.group(0)), masked)
    except _Bad:
        return [dict(type="unresolved", variant=None, date=None, date_end=None, amount=None, raw=part)]
    if _SEP.sub("", masked) or not found:
        return [dict(type="unresolved", variant=None, date=None, date_end=None, amount=None, raw=part)]
    out = []
    for _, toks in sorted(found, key=lambda x: x[0]):
        out.extend(toks)
    return out


def parse_note(note: str | None) -> list[dict]:
    """비고 한 칸 → 토큰 목록. 빈 비고는 빈 목록."""
    if note is None or not note.strip():
        return []
    tokens = []
    for pi, part in enumerate(p.strip() for p in note.strip().split(" / ")):
        if not part:
            continue
        for t in _parse_part(part):
            t["part"] = pi + 1
            tokens.append(t)
    for i, t in enumerate(tokens, 1):
        t["seq"] = i
    return tokens


def is_future(date: dt.date | None, base_date: dt.date) -> bool | None:
    """NOTE_RULES §2: date > 스냅샷의 실제 기준일."""
    if date is None:
        return None
    return date > base_date
