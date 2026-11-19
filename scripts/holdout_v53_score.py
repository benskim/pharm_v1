"""v5.3 새 검증 표본 채점 (2026-11-19 사용자 지시 C). **라벨을 마치기 전에는 실행하지 않는다.**

정답 범주(truth_event, 규칙 용어 없음) → 원장 결과값 대응표 MAP. 원장 결과값은 key의 기록 id로 DB에서 읽는다:
신호는 outcome, 이벤트는 event_type:kind.
일치 = 원장 결과값이 정답 범주의 대응 집합에 들어 있음. '기타'는 어떤 결과값과도 맞지 않는다(불일치로 셈).
날짜 일치: 정답 날짜가 있으면 원장 유효일(구간이면 구간 안)과 같은지.
사용: python -I scripts/holdout_v53_score.py --run  (라벨이 다 채워졌을 때만. 결과 outputs/validation/holdout_v53_precision.csv)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
VAL = ROOT / "outputs/validation"

MAP = {
    "예고대로 시행": {"REALIZED"},
    "다른 금액·늦게 시행": {"REALIZED_CHANGED", "REALIZED_LATE"},
    "시행 안 됨": {"NOT_REALIZED"},
    "아직 모름": {"CENSORED"},
    "이어받기(다른 회사)": {"SUCCESSION:transfer"},
    "이어받기(같은 회사)": {"SUCCESSION:recode", "SUCCESSION:swap"},
    "삭제": {"DELISTING:delisting", "DELISTING:delisting_spec", "REALIZED_BY_DELISTING"},
    "해제": {"SUSPEND_LIFT:list_only", "SUSPEND_LIFT:period_end", "SUSPEND_LIFT:r6_note"},
    "예고 없는 변화": {"PRICE_CHANGE:unobserved_notice"},
    "신규 등재": {"LISTING:new_listing", "LISTING:added_listing"},
    "기타": set(),
}
# 확인 필요(사용자): 예고 없이 시작된 급여중지(SUSPEND:in_window)는 정답 범주에 맞는 칸이 없다. 정해질 때까지 어느 범주와도 맞지 않는다.
UNMAPPED = {"SUSPEND:in_window"}


def match(truth_event: str, ledger_value: str) -> bool:
    if truth_event not in MAP:
        raise ValueError(f"알 수 없는 정답 범주: {truth_event}")
    return ledger_value in MAP[truth_event]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (c - h, c + h)


def score(df: pd.DataFrame) -> pd.DataFrame:
    """df: stratum, truth_event, ledger_value. 전체와 층별 유형 일치율"""
    d = df.assign(ok=[match(t, v) for t, v in zip(df["truth_event"], df["ledger_value"])])
    rows = []
    for g, x in [("전체", d), *d.groupby("stratum", sort=False)]:
        k, n = int(x["ok"].sum()), len(x)
        lo, hi = wilson(k, n)
        rows.append(dict(group=g, n=n, correct=k, precision=k / n if n else float("nan"), lo=lo, hi=hi))
    return pd.DataFrame(rows)


def ledger_values(key: pd.DataFrame) -> pd.Series:
    import duckdb
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    out = []
    for r in key.itertuples():
        if r.record_type == "signal":
            v = con.execute("select outcome from signal_v5 where signal_id = ?", [r.record_id]).fetchone()[0]
        else:
            t, k = con.execute("select event_type, kind from ev_v5 where event_id = ?", [r.record_id]).fetchone()
            v = f"{t}:{k}"
        out.append(v)
    return pd.Series(out, index=key.index)


def main() -> None:
    if "--run" not in sys.argv:
        raise SystemExit("라벨을 마친 뒤 --run으로 실행한다 (2026-11-19 사용자 지시: 지금은 실행하지 않음)")
    blind = pd.read_csv(VAL / "holdout_v53.blind.csv", dtype=str).fillna("")
    if (blind["truth_event"] == "").any():
        raise SystemExit("truth_event가 빈 행이 있다")
    key = pd.read_csv(VAL / "holdout_v53.key.csv", dtype=str)
    d = key.merge(blind[["holdout_id", "truth_event", "truth_date"]], on="holdout_id")
    d["ledger_value"] = ledger_values(d)
    score(d).to_csv(VAL / "holdout_v53_precision.csv", index=False)


if __name__ == "__main__":
    main()
