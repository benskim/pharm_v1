"""검증 표본 라벨 개정 (RULES_2026-11-17 §9, 사용자 지시 2026-11-18).

라벨이 원장 버전에 묶이지 않도록 정답 칸(truth_type, truth_date, truth_note)을 두고, 원장 주장과 정답을 비교해 label_*을 다시 계산한다.
- 정답 출처 (labeler): user_reviewed 26행 (아래 USER_TRUTH, 사용자 판정) / rule_check 35행 (scripts/rule_check.py, 원본만 읽는 독립 점검).
  원장(v5)과 rule_check가 다른 행은 정답을 채우지 않고 outputs/validation/rule_check_disagree.csv에 남긴다.
- 원장 주장은 같은 표기로 바꿔 비교한다 (scripts/rule_check.py 머리말의 형식).
  v5 = event_ledger_v5·signal_v5·succession_v5 / v4 = event_ledger·signal_outcome (개정 전). v4 이름은 RULES §0 대응표로 바꾼다.
- 출력: outputs/validation/sample_60.user_reviewed.csv (칸 추가·label_* 재계산), outputs/validation/precision_v5.csv,
        outputs/validation/label_claims_v4_v5.csv
사용: PYTHONPATH=src python -I scripts/v5_labels.py
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "outputs/validation"
DB = ROOT / "data/interim/pbd.duckdb"

# ---------------------------------------------------------------- v4 → v5 이름 (RULES §0 "쓰지 않는 말" 대응)
V4_EVENT_KIND = {
    ("DELISTING", "exit"): "DELISTING|delisting", ("DELISTING", "item_remains"): "DELISTING|delisting_spec",
    ("DELISTING", "recode_transfer"): "SUCCESSION_OUT|*",
    ("LISTING", "new_item"): "LISTING|new_listing", ("LISTING", "unlinked"): "LISTING|new_listing",
    ("LISTING", "new_pack_existing_item"): "LISTING|added_listing", ("LISTING", "reentry"): "LISTING|relisting",
    ("TRANSFER", "same_item_recode"): "SUCCESSION|recode", ("TRANSFER", "list_recode"): "SUCCESSION|recode",
    ("TRANSFER", "transfer_by_name"): "SUCCESSION|transfer", ("TRANSFER", "same_firm_swap"): "SUCCESSION|swap",
    ("PRICE_CHANGE", "signaled"): "PRICE_CHANGE|signaled", ("PRICE_CHANGE", "unsignaled"): "PRICE_CHANGE|unobserved_notice",
    ("PRICE_CHANGE", "stay_released"): "PRICE_CHANGE|stay_released",
    ("RESUME", "two_month_gap"): "SUSPEND_LIFT|list_only", ("RESUME", "R3b_end"): "SUSPEND_LIFT|period_end",
    ("RESUME", "R6"): "SUSPEND_LIFT|r6_note",
}
V4_SIG_TYPE = {"PRICE_PENDING": "PRICE_NOTICE", "SUSPEND_SCHEDULED": "SUSPEND_NOTICE", "LISTING_SCHEDULED": "LISTING_NOTICE",
               "LISTING_NOTE_EXISTING": "LISTING_NOTE_EXISTING", "DELISTING_SCHEDULED": "DELISTING_NOTICE",
               "STAY_RELEASE_PRICE": "STAY_RELEASE_PRICE", "RESUME_SCHEDULED": "RESUME_SCHEDULED"}

# ---------------------------------------------------------------- 사용자 판정 26행 (정답)
# 기존 14행은 기존 label_note(사용자 검토 2026-11-15)의 판단을 v5 표기로 옮긴 것. 오늘 8행은 RULES §9 표, 대화 4행은 사용자 지시(2026-11-18).
USER_TRUTH = {
    2: ("ATTR_CHANGE|firm", "2023-06-01", "업체명만 다름: 회사 이름 개명 (사용자 검토 2026-11-15)", "raw"),
    7: ("DELISTING|delisting", "2024-12-02~2025-01-01", "목록에서 빠진 달의 1일이 시행일 = 구간 끝 (사용자 검토 2026-11-15)", "raw"),
    9: ("DELISTING|delisting", "2023-10-02~2023-11-01", "목록에서 빠진 달의 1일이 시행일 = 구간 끝 (사용자 검토 2026-11-15)", "raw"),
    16: ("LISTING|new_listing", "2022-11-01", "목록에 등록된 달의 1일이 시행일 (사용자 검토 2026-11-15)", "raw"),
    21: ("PRICE_CHANGE|signaled", "2022-07-15", "항소 포기로 집행정지 종료, 7/15 인하 (Dailypharm 2022-07-18). signaled와 stay_released가 겹치면 signaled (사용자 검토 2026-11-15)", "raw"),
    22: ("PRICE_CHANGE|signaled", "2024-07-01", "예고일이 있어 그날 시행 (사용자 검토 2026-11-15)", "raw"),
    25: ("PRICE_CHANGE|signaled", "2025-11-20", "보건복지부 고시 제2025-187호(2025.11.20.), 대한병원협회 보험 제2025-423호, 시행일 2025.11.20. (docs/evidence/보험_제2025-423_1_고시.pdf)", "external"),
    28: ("PRICE_CHANGE|unobserved_notice", "2024-11-01", "비고에 예고 날짜 없음 → 인하 금액이 기록된 달의 1일 (사용자 검토 2026-11-15, review10_evidence.md)", "raw"),
    29: ("PRICE_CHANGE|unobserved_notice", "2023-09-01", "비고에 예고 날짜 없음 → 인하 금액이 기록된 달의 1일 (사용자 검토 2026-11-15)", "raw"),
    31: ("PRICE_CHANGE|unobserved_notice", "2022-09-01", "비고에 예고 날짜 없음 → 인하 금액이 기록된 달의 1일 (사용자 검토 2026-11-15)", "raw"),
    35: ("PRICE_NOTICE|REALIZED", "2022-09-14;2022-10", "예고일이 있어 그날 시행 (사용자 검토 2026-11-15)", "raw"),
    44: ("STAY_END|released", "2025-11-20", "보건복지부 고시 제2025-187호, 시행일 2025.11.20. (docs/evidence/보험_제2025-423_1_고시.pdf)", "external"),
    # 2026-11-18 정정 (사용자 결정): 이전 정답 "SUSPEND_NOTICE|NOT_REALIZED:note_gone" (raw, 2026-11-15) → 외부 집행정지 경로 반영
    51: ("SUSPEND_NOTICE|CENSORED:ONGOING_STAY|blocked", "2025-06-24;2025-07", "경보제약 급여정지(25.6.24.), 대전지법 2025-07-09 본 집행정지, https://m.dailypharm.com/user/news/4216 (2026-11-18 정정, 이전 정답: 미실현·비고 사라짐)", "external"),
    59: ("SUCCESSION|transfer", "2024-11-01", "같은 품목, 함량 표기 방식만 바뀜 (사용자 검토 2026-11-15). 한국산도스 → 삼일제약 같은 허가 = 양도양수 (RULES §3-1 예)", "raw"),
    11: ("DELISTING|delisting_spec", "2022-10-02~2022-11-01", "RULES §9: 디어큐어점안액 15mg/0.5mL, 같은 허가 안 특정 규격의 급여삭제", "raw"),
    12: ("SUCCESSION_OUT|transfer", "2024-08-01", "RULES §9: 휴듀오서방정 → 대웅제약 포시로멧서방정(같은 허가 202300356 승계) = 양도양수 (넘겨준 쪽)", "raw"),
    17: ("SUCCESSION|transfer", "2022-08-01", "RULES §9: 한국신텍스 → 씨엠지, 같은 허가 201905521 승계 = 양도양수(공백 3개월)", "raw"),
    18: ("DELISTING_STAY|delisting_stay", "2025-02-01", "RULES §9: 2025-02는 삭제 집행정지에 따른 일시 급여 유지, 급여 유지 유효일 2025-02-01. 2025-01-31은 집행정지 공고일", "raw"),
    37: ("NONE", "", "RULES §9: 기간 중지 종료일을 별도 신호로 판정하지 않음 → RESUME_SCHEDULED 신호 자체가 없어야 함", "raw"),
    42: ("SUSPEND_LIFT|list_only", "2024-01-04", "RULES §9: 중지해제 맞음. 급여목록 기준 2024-01-01, 외부 확인 실제 해제일 2024-01-04 (뉴스더보이스 2024-01-05, https://www.newsthevoice.com/news/articleView.html?idxno=36093)", "external"),
    58: ("SUCCESSION|swap", "2022-02-01", "RULES §9: 휴메리나 → 휴다트, 같은 업체·성분·함량·금액, 다른 허가, 다음 달 이어짐 → 교체 등재", "raw"),
    60: ("SUCCESSION|transfer", "2023-07-01", "RULES §9: 스트렌식주 한독 → 아스트라제네카, 주성분코드·함량·금액 같고 기간 빈틈없이 이어짐 → 양도양수", "raw"),
    32: ("PRICE_NOTICE|EXCLUDED:listed_during_suspension", "2023-01-09;2023-02", "사용자 판정 2026-11-18: 급여정지(22.12.28.) 중 기재된 인하 예고 → 판정 제외(중지 중 기재)", "raw"),
    33: ("PRICE_NOTICE|*|blocked", "2022-05-04;2022-06", "사용자 판정 2026-11-18: 대체조제 목록의 22.5.4. 550원을 하루 전 집행정지(22.5.3.)가 막음. 이후 결과는 규칙대로 추적", "raw"),
    50: ("SUSPEND_NOTICE|REALIZED_BY_DELISTING", "2022-01-11;2022-02", "사용자 판정 2026-11-18: 급여정지(22.1.11) 예고 뒤 2022-02 급여삭제 → 삭제로 실현", "raw"),
    61: ("SUSPEND_NOTICE|*|blocked", "2022-08-01;2022-08", "사용자 판정 2026-11-18: 집행정지(22.5.9.)가 8월 급여중지 처분을 막음 (외부 확인: 고시 제2022-117호, 서울행정법원 2022아11352)", "external"),
}


def iso(x):
    if x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NaT:
        return ""
    return x.isoformat() if hasattr(x, "isoformat") else str(x)


def add_months(m: str, k: int) -> str:
    y, mo = int(m[:4]), int(m[5:]) + k
    while mo > 12:
        y, mo = y + 1, mo - 12
    while mo < 1:
        y, mo = y - 1, mo + 12
    return f"{y:04d}-{mo:02d}"


# ---------------------------------------------------------------- 원장 주장
def claim_v4(c, rid: str, rtype: str):
    if rtype == "signal":
        r = c.execute("select signal_type, outcome, reason, date_gap_days, signal_date, check_month from signal_outcome where signal_id = ?", [rid]).fetchone()
        if not r:
            return "NONE", ""
        st, o, reason, gap, sd, cm = r
        t = V4_SIG_TYPE[st]
        if o == "realized":
            oo = "REALIZED"
        elif o == "realized_partial":
            oo = "REALIZED_LATE" if (st == "SUSPEND_SCHEDULED" and (gap or 0) > 0) else "REALIZED_CHANGED"
        elif o == "not_realized":
            oo = f"NOT_REALIZED:{reason}"
        elif o in ("censored", "unverifiable"):
            oo = "CENSORED:AWAITING"
        else:
            oo = o.upper()
        return f"{t}|{oo}", f"{iso(sd)};{cm or '-'}"
    r = c.execute("""select event_type, kind, effective_date, effective_date_lo, effective_date_hi, date_source from event_ledger where event_id = ?""", [rid]).fetchone()
    if not r:
        return "NONE", ""
    et, k, e, lo, hi, ds = r
    t = V4_EVENT_KIND.get((et, k), f"{et}|{k}")
    if (et, k) == ("PRICE_CHANGE", "stay_released"):     # RULES §3: 예고와 연결되면 그 신호의 실현, 아니면 비고 미기재 약가 변경
        sid = c.execute("select signal_id from event_ledger where event_id = ?", [rid]).fetchone()[0]
        t = "PRICE_CHANGE|signaled" if sid else "PRICE_CHANGE|unobserved_notice"
    d = f"{iso(lo)}~{iso(hi)}" if ds == "interval" else iso(e)
    return t, d


def claim_v5(c, rid: str, rtype: str):
    typ, pc, key = rid.split(":")[0], rid.split(":")[1], rid.split(":")[2]
    if rtype == "signal":
        if typ == "RESUME_SCHEDULED":
            return "NONE", ""
        sid = f"{V4_SIG_TYPE[typ]}:{pc}:{key}"
        r = c.execute("select signal_type, outcome, reason, current_status, stay_blocked, signal_date, check_month from signal_v5 where signal_id = ?", [sid]).fetchone()
        if not r:
            return "NONE", ""
        st, o, reason, cs, bl, sd, cm = r
        oo = o + (f":{reason}" if o in ("NOT_REALIZED", "EXCLUDED") and reason else "") + (f":{cs}" if o == "CENSORED" and cs else "")
        return f"{st}|{oo}" + ("|blocked" if bl else ""), f"{iso(sd)};{cm or '-'}"
    m = key
    if typ == "ATTR_CHANGE":
        r = c.execute("select kind, effective_date from event_ledger_v5 where event_id = ?", [rid]).fetchone()
        return (f"ATTR_CHANGE|{r[0]}", iso(r[1])) if r else ("NONE", "")
    if typ == "DELISTING":
        r = c.execute("""select kind, effective_lo, effective_hi from event_ledger_v5 where event_type = 'DELISTING' and product_code = ?
                         and observed_month = ? and retracted_month is null""", [pc, m]).fetchone()
        if r:
            return f"DELISTING|{r[0]}", f"{iso(r[1])}~{iso(r[2])}"
        r = c.execute("select kind, entry_month from succession_v5 where old = ? and gone_month = ?", [pc, m]).fetchone()
        return (f"SUCCESSION_OUT|{r[0]}", f"{r[1]}-01") if r else ("NONE", "")
    if typ in ("LISTING", "TRANSFER"):
        r = c.execute("""select event_type, kind, effective_date from event_ledger_v5 where product_code = ? and observed_month = ?
                         and event_type in ('LISTING', 'SUCCESSION', 'DELISTING_STAY') order by event_type limit 1""", [pc, m]).fetchone()
        return (f"{r[0]}|{r[1]}", iso(r[2])) if r else ("NONE", "")
    if typ == "PRICE_CHANGE":
        r = c.execute("select kind, effective_date from event_ledger_v5 where event_type = 'PRICE_CHANGE' and product_code = ? and observed_month = ?", [pc, m]).fetchone()
        return (f"PRICE_CHANGE|{r[0]}", iso(r[1])) if r else ("NONE", "")
    if typ in ("RESUME", "SUSPEND"):
        et = "SUSPEND_LIFT" if typ == "RESUME" else "SUSPEND"
        r = c.execute(f"""select kind, effective_date, observed_month from event_ledger_v5 where event_type = '{et}' and product_code = ?
                          and observed_month between ? and ? order by abs(datediff('month', strptime(observed_month, '%Y-%m'), strptime(?, '%Y-%m'))), observed_month limit 1""",
                      [pc, add_months(m, -2), add_months(m, 1), m]).fetchone()
        return (f"{et}|{r[0]}", iso(r[1])) if r else ("NONE", "")
    if typ in ("STAY_START", "STAY_END"):
        v4d = c.execute("select effective_date from event_ledger where event_id = ?", [rid]).fetchone()
        rows = c.execute(f"""select kind, effective_date, event_id from event_ledger_v5 where event_type = '{typ}' and product_code = ? and observed_month = ?
                             order by event_id""", [pc, m]).fetchall()
        if not rows:
            return "NONE", ""
        pick = next((x for x in rows if v4d and x[1] == v4d[0]), rows[0])
        return f"{typ}|{pick[0]}", iso(pick[1])
    raise ValueError(rid)


def same_type(claim: str, truth: str) -> bool:
    if truth == "" or claim == "":
        return False
    cp, tp = claim.split("|"), truth.split("|")
    if len(cp) < 2 or len(tp) < 2:
        return claim == truth
    if cp[0] != tp[0]:
        return False
    # 와일드카드: 정답 '*' = 결과값은 규칙대로(사용자는 막힘만 판정), 주장 '*' = v4가 종류를 나누지 않음 (recode_transfer)
    if tp[1] != "*" and cp[1] != "*" and cp[1] != tp[1]:
        return False
    return ("blocked" in tp[2:]) == ("blocked" in cp[2:]) if (len(tp) > 2 or len(cp) > 2) else True


def same_date(claim: str, truth: str) -> str:
    if not truth:
        return ""
    if not claim:
        return "N"
    if "~" in claim and "~" not in truth:          # 구간 주장: 정답 날짜가 구간 안이면 Y
        lo, hi = claim.split("~")
        return "Y" if lo <= truth <= hi else "N"
    if "~" in claim and "~" in truth:
        return "Y" if claim == truth else "N"
    return "Y" if claim == truth else "N"


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    cc = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, cc - h), 4), round(min(1, cc + h), 4))


def main(dry: bool = False) -> None:
    c = duckdb.connect(str(DB), read_only=True)
    # v4(개정 전) 표는 보관본에서 읽는다 (RULES §11-4): 이름을 기본 DB → 보관본 순으로 찾는다
    leg = Path(__file__).resolve().parents[1] / "outputs/v4_legacy/v4_legacy.duckdb"
    c.execute(f"attach '{leg.as_posix()}' as v4 (read_only)")
    c.execute(f"set search_path = '{c.execute('select current_database()').fetchone()[0]}.main,v4.main'")
    s = pd.read_csv(VAL / "sample_60.user_reviewed.csv", dtype=str)
    if "prev_label_type_correct" not in s.columns:          # 개정 전 라벨(v4 원장에 대한 기존 판단)을 보존
        s["prev_labeler"] = s["labeler"]
        s["prev_label_type_correct"] = s["label_type_correct"]
        s["prev_label_date_correct"] = s["label_date_correct"]
    rc = pd.read_csv(VAL / "rule_check.csv", dtype=str).fillna("").set_index("sample_id")
    out, disagree = [], []
    for r in s.itertuples():
        sid = int(r.sample_id)
        v4t, v4d = claim_v4(c, r.record_id, r.record_type)
        v5t, v5d = claim_v5(c, r.record_id, r.record_type)
        if sid in USER_TRUTH:
            tt, td, tn, src = USER_TRUTH[sid]
            lab = "user_reviewed"
        else:
            x = rc.loc[str(sid)]
            tt, td, tn, src, lab = x.truth_type, x.truth_date, x.truth_note, "raw", "rule_check"
            if not (same_type(v5t, tt) and v5t.split("|")[1:2] == tt.split("|")[1:2]) or same_date(v5d, td) == "N":
                disagree.append(dict(sample_id=sid, record_id=r.record_id, ledger_v5_type=v5t, ledger_v5_date=v5d,
                                     rule_check_type=tt, rule_check_date=td, rule_check_note=tn))
                tt, td, tn, lab = "", "", "", "claude"
        lt5 = ("Y" if same_type(v5t, tt) else "N") if tt else ""
        lt4 = ("Y" if same_type(v4t, tt) else "N") if tt else ""
        out.append(dict(sample_id=r.sample_id, labeler=lab, label_source=src if tt else r.label_source,
                        truth_type=tt, truth_date=td, truth_note=tn,
                        ledger_v5_type=v5t, ledger_v5_date=v5d, ledger_v4_type=v4t, ledger_v4_date=v4d,
                        label_type_correct=lt5, label_date_correct=same_date(v5d, td) if tt else "",
                        label_type_correct_v4=lt4, label_date_correct_v4=same_date(v4d, td) if tt else ""))
    o = pd.DataFrame(out)
    pd.DataFrame(disagree).to_csv(VAL / "rule_check_disagree.csv", index=False)
    o.to_csv(VAL / "label_claims_v4_v5.csv", index=False)
    with pd.option_context("display.width", 260, "display.max_colwidth", 52, "display.max_rows", 80):
        print(o[["sample_id", "labeler", "truth_type", "truth_date", "ledger_v5_type", "ledger_v5_date", "ledger_v4_type",
                 "label_type_correct", "label_date_correct", "label_type_correct_v4", "label_date_correct_v4"]].to_string(index=False))
        print("\n원장(v5)과 rule_check가 다른 행:", len(disagree))
        if disagree:
            print(pd.DataFrame(disagree).to_string(index=False))
    if dry:
        return
    s = s.drop(columns=[x for x in o.columns if x in s.columns and x != "sample_id"])
    s = s.merge(o, on="sample_id", how="left")
    s.to_csv(VAL / "sample_60.user_reviewed.csv", index=False)
    # 정밀도 세 묶음 × 개정 전·후
    rows = []
    for ver, tcol, dcol in (("v4", "label_type_correct_v4", "label_date_correct_v4"), ("v5", "label_type_correct", "label_date_correct")):
        for grp, sub in (("user_reviewed", o[o.labeler == "user_reviewed"]), ("rule_check", o[o.labeler == "rule_check"]), ("all", o[o.truth_type != ""])):
            for lab, col in (("type", tcol), ("date", dcol)):
                v = sub[col][sub[col] != ""]
                k, n = int((v == "Y").sum()), int(len(v))
                lo, hi = wilson(k, n)
                rows.append(dict(ledger=ver, group=grp, label=lab, n=n, correct=k, precision=round(k / n, 4) if n else None, lo=lo, hi=hi))
    p = pd.DataFrame(rows)
    p.to_csv(VAL / "precision_v5.csv", index=False)
    print(p.to_string(index=False))


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
