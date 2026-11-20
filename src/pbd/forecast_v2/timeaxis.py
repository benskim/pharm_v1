"""달 계산과 읽기 잠금.

달은 'YYYY-MM' 문자열. 정수 번호 = 연 × 12 + (월 − 1).
`panel_upto`는 fc_panel을 기준월 이하로만 읽는다. 사전등록 태그 전에는 PRE_TAG_MAX(2022-12) 뒤를 읽지 않는다
(평가 기간 2023-01 ~ 의 정답을 태그 전에 보지 않음, PLAN §7-2).
"""
from __future__ import annotations

import pandas as pd

PRE_TAG_MAX = "2022-12"


def mnum(m: str) -> int:
    y, mm = m.split("-")
    return int(y) * 12 + int(mm) - 1


def mstr(n: int) -> str:
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


def madd(m: str, k: int) -> str:
    return mstr(mnum(m) + k)


def mrange(a: str, b: str) -> list[str]:
    return [mstr(n) for n in range(mnum(a), mnum(b) + 1)]


def panel_upto(con, max_month: str) -> pd.DataFrame:
    """fc_panel의 max_month 이하 행 (c4, month, observed, claim_qty, claim_amt). 태그 전 잠금: max_month ≤ PRE_TAG_MAX"""
    assert max_month <= PRE_TAG_MAX, f"사전등록 전에는 {PRE_TAG_MAX} 뒤 사용통계를 읽지 않는다: {max_month}"
    return con.execute("""select c4, month, observed, claim_qty, cast(claim_amt as double) claim_amt
                          from fc_panel where month <= ? order by c4, month""", [max_month]).df()


def panel_full(con, unlock, max_month: str = "2025-12") -> pd.DataFrame:
    """평가 기간을 포함한 fc_panel. gate.unlock()이 통과해 만든 Unlock 표가 있어야 한다(사전등록 태그 뒤에만)"""
    from pbd.forecast_v2.gate import Unlock
    assert isinstance(unlock, Unlock), "panel_full은 gate.unlock()의 표가 필요하다"
    return con.execute("""select c4, month, observed, claim_qty, cast(claim_amt as double) claim_amt,
                                 cast(claim_cnt as double) claim_cnt
                          from fc_panel where month <= ? order by c4, month""", [max_month]).df()
