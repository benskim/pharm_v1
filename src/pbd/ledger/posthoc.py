"""뒤 스냅샷이 와야 정해지는 칸 (사후 칸). P2 특징으로 쓰지 않는다 (사용자 결정 2026-11-15, PLAN_P2 §2).

- 금지(POSTHOC_COLUMNS): 특징 생성 코드가 읽으면 안 되는 칸.
- 시점 조건(TIME_GATED_COLUMNS): 금지는 아니지만 origin보다 앞선 확인 달(check_month < origin)의 행에서만 쓴다
  (예: 신호 유형별 과거 실현율). 이 조건은 특징 생성 코드의 시험(T3 누수 시험)에서 확인한다.
"""
from __future__ import annotations

import re

POSTHOC_COLUMNS: dict[str, tuple[str, ...]] = {
    "signal_outcome": ("check_month", "check_month_by_date", "realized_month", "confirm_lag_days", "amount_gap",
                       "date_gap_days", "realized_after_followup", "event_effective_date", "event_date_source"),
    "signal_ledger": ("last_seen_month", "n_months_seen"),
    "event_ledger": ("exit_reversed", "late_realization_of"),
    "product_state_month": ("state_eom", "exposure_frac", "exposure_revised", "state_revised", "late_note_revised",
                            "resume_unconfirmed", "absent_next_month", "resume_date", "product_role_carried"),
}
TIME_GATED_COLUMNS: dict[str, tuple[str, ...]] = {
    "signal_outcome": ("outcome", "reason", "reason_source", "reason_external", "evidence"),
}


def find_posthoc(text: str) -> list[str]:
    """코드·SQL 문자열에서 사후 칸 이름(단어 단위)을 찾는다"""
    names = sorted({c for cols in POSTHOC_COLUMNS.values() for c in cols})
    return [c for c in names if re.search(rf"\b{re.escape(c)}\b", text)]
