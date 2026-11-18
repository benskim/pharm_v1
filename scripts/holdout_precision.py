"""holdout 30행 정밀도 (RULES §11-8). 사용자 정답(outputs/validation/holdout_30.labeled.csv)과 원장(v5) 판정을 비교한다. 읽기 전용.

- 원장 판정: holdout_30.key.csv의 record_id로 event_ledger_v5 / signal_v5에서 붙인다. 원장 값은 RULES §0·§6의 한글 이름으로 바꿔 비교한다.
- 유형 일치: 정답 truth_type = 원장 이름. 날짜 일치: 정답 truth_date가 있으면 원장 유효일과 같은지(급여삭제처럼 구간이면 구간 안인지), 없으면 해당 없음.
- 출력: outputs/validation/holdout_30.compared.csv, outputs/validation/holdout_precision.csv
- 라벨 두 가지: truth_type_initial(처음 라벨) / truth_type(라벨 판정 후 정정). 정정 행은 truth_note에 근거
사용: python -I scripts/holdout_precision.py [원장 이름표, 예: c63778c]
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "outputs/validation"
DB = ROOT / "data/interim/pbd.duckdb"

EVENT_NAME = {("SUCCESSION", "transfer"): "양도양수", ("SUCCESSION", "recode"): "코드 변경", ("SUCCESSION", "swap"): "교체 등재",
              ("LISTING", "new_listing"): "신규 등재", ("LISTING", "added_listing"): "추가 등재", ("LISTING", "relisting"): "재등재",
              ("DELISTING", "delisting"): "급여삭제", ("DELISTING", "delisting_spec"): "급여삭제(규격)",
              ("SUSPEND_LIFT", None): "중지해제", ("SUSPEND", None): "급여중지", ("DELISTING_STAY", None): "삭제 집행정지",
              ("PRICE_CHANGE", "signaled"): "예고 실현", ("PRICE_CHANGE", "unobserved_notice"): "예고 미관측 변경"}
OUTCOME_NAME = {"REALIZED": "실현", "REALIZED_CHANGED": "변경 실현", "REALIZED_LATE": "지연 실현", "REALIZED_BY_DELISTING": "삭제로 실현",
                "NOT_REALIZED": "미실현", "EXCLUDED": "판정 제외"}
CENSORED_NAME = {"ONGOING_STAY": "미결(집행정지 중)", "AWAITING": "미결(관측 기간 부족)"}


def wilson(k: int, n: int, z: float = 1.959963984540054):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h), 4), round(min(1, c + h), 4))


def ledger(c, rtype: str, rid: str) -> dict:
    if rtype == "event":
        r = c.execute("""select event_type, kind, effective_date, effective_lo, effective_hi, counterpart, link_basis
                         from event_ledger_v5 where event_id = ?""", [rid]).fetchone()
        et, k, e, lo, hi, cp, lb = r
        name = EVENT_NAME.get((et, k)) or EVENT_NAME.get((et, None)) or f"{et}:{k}"
        return dict(ledger_type=name, ledger_kind=f"{et}|{k}", ledger_date=e.isoformat() if e else None,
                    ledger_lo=lo.isoformat() if lo else None, ledger_hi=hi.isoformat() if hi else None,
                    ledger_detail=f"상대 {cp}, 근거 {lb}" if cp else (lb or ""))
    r = c.execute("""select outcome, reason, current_status, effective_date, stay_blocked, blocking_stays, realized_month
                     from signal_v5 where signal_id = ?""", [rid]).fetchone()
    o, reason, cs, e, bl, bs, rm = r
    name = CENSORED_NAME.get(cs) if o == "CENSORED" else OUTCOME_NAME[o]
    return dict(ledger_type=name, ledger_kind=f"{o}|{reason or cs or ''}", ledger_date=e.isoformat() if e else None,
                ledger_lo=None, ledger_hi=None,
                ledger_detail=f"사유 {reason or cs or '-'}, 막힘 {bool(bl)} {bs or ''}, 실현 달 {rm or '-'}")


def date_match(truth: str | None, r: dict) -> str:
    if not isinstance(truth, str) or not truth.strip():
        return "해당 없음"
    t = truth.strip()
    if r["ledger_lo"] and r["ledger_hi"] and not r["ledger_date"]:
        return "Y" if r["ledger_lo"] <= t <= r["ledger_hi"] else "N"
    return "Y" if r["ledger_date"] == t else "N"


def main(tag: str = "current") -> None:
    lab = pd.read_csv(VAL / "holdout_30.labeled.csv", dtype=str)
    key = pd.read_csv(VAL / "holdout_30.key.csv", dtype=str)
    m = lab.merge(key[["holdout_id", "stratum", "record_type", "record_id"]], on="holdout_id", how="left")
    assert len(m) == 30 and m["record_id"].notna().all()
    c = duckdb.connect(str(DB), read_only=True)
    rows = []
    for r in m.itertuples():
        x = ledger(c, r.record_type, r.record_id)
        tt = (r.truth_type or "").strip()
        ti = (getattr(r, "truth_type_initial", None) or tt).strip()
        rows.append(dict(holdout_id=int(r.holdout_id), record_kind=r.record_kind, product_code=r.product_code, ref_month=r.ref_month,
                         sample_stratum=r.stratum, record_id=r.record_id, truth_type=tt, truth_type_initial=ti, truth_date=r.truth_date,
                         **x, type_match="Y" if tt == x["ledger_type"] else "N",
                         type_match_initial="Y" if ti == x["ledger_type"] else "N", date_match=date_match(r.truth_date, x),
                         label_source=r.label_source, truth_note=r.truth_note))
    d = pd.DataFrame(rows)
    d.to_csv(VAL / f"holdout_30.compared_{tag}.csv", index=False)
    out = []
    for grp, sub in (("전체", d), ("목록 변화", d[d.record_kind == "목록 변화"]), ("비고 예고", d[d.record_kind == "비고 예고"])):
        for lab_, col in (("유형 (처음 라벨)", "type_match_initial"), ("유형 (라벨 판정 후)", "type_match"), ("날짜", "date_match")):
            v = sub[col][sub[col] != "해당 없음"]
            k, n = int((v == "Y").sum()), len(v)
            lo, hi = wilson(k, n)
            out.append(dict(ledger=tag, group=grp, label=lab_, n=n, correct=k, precision=round(k / n, 4) if n else None, lo=lo, hi=hi,
                            not_applicable=int((sub[col] == "해당 없음").sum())))
    p = pd.DataFrame(out)
    p.to_csv(VAL / f"holdout_precision_{tag}.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_colwidth", 60, "display.max_rows", 40):
        print(p.to_string(index=False))
        print(d[["holdout_id", "record_kind", "sample_stratum", "truth_type", "ledger_type", "type_match", "truth_date", "ledger_date",
                 "ledger_lo", "ledger_hi", "date_match", "ledger_detail"]].to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "current")
