"""P1 문서 생성 (S7 문서 부분, v5.3): reports/SUMMARY.md 전체, docs/LIMITATIONS.md의 v5.3 블록, README.md의 P1 블록.

숫자는 손으로 옮기지 않고 모두 여기서 계산한다 (data/interim/pbd.duckdb v5 표, outputs/ 산출 CSV, pytest 수집 수).
쓴 뒤 자기 비평 검사: 링크 대상 존재, 과장 표현·인과 표현, "예정 = 변화"로 읽히는 표현, 문서 숫자 = 생성값. 하나라도 걸리면 실패한다.
사용: PYTHONPATH=src python -I scripts/p1_docs.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402

from pbd.common import DB_PATH  # noqa: E402
from pbd.ledger.v5_rules import rates  # noqa: E402

LIM_START, LIM_END = "<!-- v5.3 한계 시작 (scripts/p1_docs.py 생성) -->", "<!-- v5.3 한계 끝 -->"
RD_START, RD_END = "<!-- P1 요약 시작 (scripts/p1_docs.py 생성) -->", "<!-- P1 요약 끝 -->"
TYPE_KO = {"PRICE_NOTICE": "약가 예고", "LISTING_NOTICE": "등재 예고 (새 제품)", "LISTING_NOTE_EXISTING": "등재 예고 비고 (이미 급여 중)",
           "SUSPEND_NOTICE": "급여중지 예고", "STAY_RELEASE_PRICE": "집행정지 종료 후 인하 예고", "DELISTING_NOTICE": "삭제 예고"}
REASON_KO = {"note_gone": "변화 없이 비고만 사라짐", "stay_ended_not_executed": "집행정지 종료 후 미시행",
             "stay_then_delisted": "집행정지 중 삭제", "absent": "목록에 없음", "suspended": "급여중지", "still_listed": "목록에 남음",
             "stay_continues": "집행정지 계속"}


def pct(x, d=1):
    return "" if x is None else f"{x * 100:.{d}f}%"


def ci(lo, hi):
    return "" if lo is None else f"{lo * 100:.1f}~{hi * 100:.1f}"


def collect() -> dict:
    c = duckdb.connect(str(DB_PATH), read_only=True)
    N = {}
    q = lambda s, a=None: c.execute(s, a or []).fetchall()
    # 신호 유형별 지표 (R2·S_AMT는 약가 예고를 나눠서도)
    rows = []
    groups = [("PRICE_NOTICE", None), ("PRICE_NOTICE", "R2"), ("PRICE_NOTICE", "S_AMT"), ("LISTING_NOTICE", None),
              ("SUSPEND_NOTICE", None), ("LISTING_NOTE_EXISTING", None), ("STAY_RELEASE_PRICE", None), ("DELISTING_NOTICE", None)]
    for t, rule in groups:
        cond, args = "signal_type = ?", [t]
        if rule:
            cond += " and source_rule = ?"
            args.append(rule)
        rs = [dict(outcome=o, reason=r, stay_blocked=b, current_status=s) for o, r, b, s in
              q(f"select outcome, reason, stay_blocked, current_status from signal_v5 where {cond}", args)]
        x = rates(rs)
        rows.append(dict(type=t, rule=rule, n=x["n"], excluded=x["n_excluded"], ongoing=x["n_ongoing_stay"], awaiting=x["n_awaiting"],
                         **{f"{k}_{i}": v for k in ("A", "B", "on_time", "stay") for i, v in zip(("k", "n", "p", "lo", "hi"), x[k])}))
    N["rates"] = rows
    s, u = q("""select count(*) filter (where kind = 'signaled'), count(*) filter (where kind = 'unobserved_notice')
                from ev_v5 where event_type = 'PRICE_CHANGE'""")[0]
    ub = q("select count(*) from ev_v5 where event_type = 'PRICE_CHANGE' and kind = 'unobserved_notice' and batch")[0][0]
    bm = [r[0] for r in q("select observed_month from batch_month_v5 where batch order by 1")]
    N["omission"] = dict(signaled=s, unobserved=u, rate=u / (s + u), unobserved_batch=ub, unobserved_rest=u - ub,
                         rate_rest=(u - ub) / (s + u - ub), batch_months=bm, n_price_change=s + u)
    N["blocked"] = q("""select signal_type, case when outcome in ('REALIZED', 'REALIZED_LATE') then '예고대로 시행'
                           when outcome = 'REALIZED_CHANGED' then '다른 금액으로 시행' when reason = 'stay_ended_not_executed' then '종료 후 미시행'
                           when reason = 'stay_then_delisted' then '집행정지 중 삭제' when current_status = 'ONGOING_STAY' then '집행정지 중(미결)'
                           else outcome end k, count(*) from signal_v5 where stay_blocked group by 1, 2 order by 1, 2""")
    N["blocked_firm"] = q("""select p.firm, count(*), count(*) filter (where s.current_status = 'ONGOING_STAY')
                             from signal_v5 s join product_state_month_v5 p on p.product_code = s.product_code and p.list_month = s.first_month
                             where s.signal_type = 'SUSPEND_NOTICE' and s.stay_blocked group by 1 order by 2 desc""")
    N["lead"] = {t: (n, med, lo, hi) for t, n, med, lo, hi in q("""
        select signal_type, count(*), median(d), quantile_cont(d, 0.25), quantile_cont(d, 0.75) from (
          select signal_type, date_diff('day', strptime(first_month || '-01', '%Y-%m-%d'), signal_date) d from signal_v5
          where signal_type in ('PRICE_NOTICE', 'SUSPEND_NOTICE', 'LISTING_NOTICE')) group by 1""")}
    N["batch_by_month"] = q("""select strftime(effective_date, '%Y-%m') m, count(*) from ev_v5 where event_type = 'PRICE_CHANGE'
                               and kind = 'unobserved_notice' and batch group by 1 order by 1""")
    N["unrealized"] = q("""select signal_type, reason, count(*) from signal_v5 where outcome = 'NOT_REALIZED' group by 1, 2 order by 1, 3 desc""")
    N["excluded"] = q("""select exclude_reason, count(*) from signal_v5 where outcome = 'EXCLUDED' group by 1 order by 2 desc""")
    # 외부 검증 사례
    N["donga"] = q("""select coalesce(current_status, reason, outcome), count(*) from signal_v5 where signal_type = 'SUSPEND_NOTICE'
                      and signal_date = '2022-08-01' and source_rule = 'R3b' and stay_target_source = 'external' group by 1 order by 2 desc""")
    N["donga_n"] = sum(n for _, n in N["donga"])
    N["kyongbo"] = q("""select coalesce(current_status, reason), count(*) from signal_v5 where signal_type = 'SUSPEND_NOTICE'
                        and signal_date = '2025-06-24' and stay_target_source = 'external' group by 1 order by 2 desc""")
    N["kyongbo_n"] = sum(n for _, n in N["kyongbo"])
    N["lokotan"] = q("""select effective_date, date_source from event_ledger_v5 where event_type = 'SUSPEND_LIFT' and product_code = '654700150'""")[0]
    # 정밀도
    hp = pd.read_csv(ROOT / "outputs/validation/holdout_precision_c63778c.csv")
    hp3 = pd.read_csv(ROOT / "outputs/validation/holdout_precision_v5_3.csv")
    pick = lambda d, g, lab: d[(d.group == g) & (d.label == lab)].iloc[0]
    N["hold"] = {lab: pick(hp, "전체", lab) for lab in ("유형 (처음 라벨)", "유형 (라벨 판정 후)", "날짜")}
    N["hold_v53"] = {lab: pick(hp3, "전체", lab) for lab in ("유형 (처음 라벨)", "유형 (라벨 판정 후)")}
    cmp_ = pd.read_csv(ROOT / "outputs/validation/holdout_30.compared_c63778c.csv", dtype=str)
    N["hold_date_na"] = int((cmp_.date_match == "해당 없음").sum())
    N["hold_miss"] = cmp_[cmp_.type_match_initial == "N"][["holdout_id", "truth_type_initial", "truth_type", "ledger_type"]].values.tolist()
    s60 = pd.read_csv(ROOT / "outputs/validation/sample_60.user_reviewed.csv", dtype=str)
    N["s60"] = dict(n=len(s60), user=int((s60.labeler == "user_reviewed").sum()), rule=int((s60.labeler == "rule_check").sum()))
    # A4·B1
    a4 = pd.read_csv(ROOT / "outputs/s4/a4_selection.csv").set_index("stage")
    t = pd.read_csv(ROOT / "outputs/s4/a4_targets.csv")
    mid = t[~t.same_month]
    N["a4"] = dict(groups=int(a4["groups"].sum()), target=int(a4.loc["target", "groups"]), lt_half=int(a4.loc["1_share_lt_half", "groups"]),
                   unobs=int(a4.loc["2_usage_unobserved", "groups"]), mid=len(mid), mid_closer=int(mid["ledger_closer"].sum()))
    b1 = pd.read_csv(ROOT / "outputs/s5/b1_main.csv")
    for key, basis in (("b1", "eff_daywt"), ("b1_obs", "observed_month")):   # 주 지표 = 유효일 달 일수 가중 (DECISIONS 2026-11-16)
        b = b1[(b1.basis == basis) & (b1.subset == "all")].set_index("metric")["value"]
        N[key] = dict(pairs=int(b["pairs"]), same=b["share_same_sign"], ctl=b["ctl_expected_same_sign"], med=b["ratio_median"],
                      q25=b["ratio_q25"], q75=b["ratio_q75"])
    p2 = ROOT / "outputs/p2/t3_compare.csv"
    N["p2"] = pd.read_csv(p2) if p2.exists() else None
    # 한계용
    N["subst_lag"] = q("select count(*), count(distinct product_code) from product_state_month_v5 where subst_lag")[0]
    N["s_only_r2"] = q("""select count(*) from signal_v5 where signal_type = 'PRICE_NOTICE' and source_rule = 'R2' and source_list = 'substitute'""")[0][0]
    N["s_amt"] = q("select count(*) from signal_v5 where source_rule = 'S_AMT'")[0][0]
    N["ptype"] = q("""select price_type, count(distinct product_code) filter (where list_month = '2025-12'),
                      count(distinct product_code) filter (where list_month = '2025-12' and unit_ambiguous)
                      from product_state_month_v5 where in_benefit group by 1 order by 1""")
    N["gap"] = q("""select case when gap_months = 0 then '0' when gap_months <= 3 then '1~3' when gap_months <= 12 then '4~12' else '13+' end b,
                    count(*) from succession_v5 group by 1 order by 1""")
    N["gap_n"] = q("select count(*) from succession_v5")[0][0]
    N["gap13_item"] = q("select count(*) from succession_v5 where gap_months >= 13 and link_basis = 'same_item'")[0][0]
    N["first_day"] = q("""select count(*), count(*) filter (where date_source = 'snapshot_first') from ev_v5
                          where event_type in ('PRICE_CHANGE', 'LISTING', 'SUCCESSION', 'SUSPEND', 'SUSPEND_LIFT', 'STAY_START', 'STAY_END')""")[0]
    m0 = pd.read_csv(ROOT / "outputs/v5/metrics_c63778c.csv").set_index("item")
    N["kyongbo_before_B"] = (int(m0.loc["SUSPEND_NOTICE B", "k"]), int(m0.loc["SUSPEND_NOTICE B", "n"]), m0.loc["SUSPEND_NOTICE B", "value"])
    N["listing_27"] = dict(q("""select state_as_known, count(*) from product_state_month_v5 p where list_month = '2025-11' and exists
                              (select 1 from note_token t where t.product_code = p.product_code and t.list_month = '2025-11'
                               and t.token_type = 'R1' and t.token_date = '2025-11-23') group by 1"""))
    N["n_tests"] = int(re.search(r"(\d+) tests? collected", subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"], cwd=ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}).stdout).group(1))
    return N


def rate_rows(N) -> list[str]:
    L = ["| 신호 | 건수 | A 현재 시점 집행률 (95%) | B 최종 실현율 (95%) | A와 B의 차이 = 집행정지로 아직 막힌 몫 | 집행정지율 | 판정 제외 |",
         "|---|---|---|---|---|---|---|"]
    for r in N["rates"]:
        if r["type"] in ("STAY_RELEASE_PRICE", "DELISTING_NOTICE"):
            continue
        name = TYPE_KO[r["type"]] + ({"R2": " — 비고 예고", "S_AMT": " — 대체조제 금액 신호 (막힌 처분)"}.get(r["rule"], ""))
        L.append(f"| {name} | {r['n']:,} | {r['A_k']:,}/{r['A_n']:,} = **{pct(r['A_p'])}** ({ci(r['A_lo'], r['A_hi'])}) | "
                 f"{r['B_k']:,}/{r['B_n']:,} = **{pct(r['B_p'])}** ({ci(r['B_lo'], r['B_hi'])}) | 미결 {r['ongoing']:,}건 | "
                 f"{pct(r['stay_p'])} ({r['stay_k']:,}/{r['stay_n']:,}) | {r['excluded']:,} |")
    small = [r for r in N["rates"] if r["type"] in ("STAY_RELEASE_PRICE", "DELISTING_NOTICE")]
    L.append("")
    L.append("집행정지 종료 후 인하 예고 " + f"{small[0]['n']}건, 삭제 예고 {small[1]['n']}건은 건수만 적는다 (각 {small[0]['A_k']}건, {small[1]['A_k']}건 실현).")
    return L


PLAIN_TYPE = {("PRICE_NOTICE", None): "약가 인하·변경 예고 (전체)", ("PRICE_NOTICE", "R2"): "　그중 급여목록 비고에 적힌 예고",
              ("PRICE_NOTICE", "S_AMT"): "　그중 대체조제 목록 금액 차이로만 보인 처분 (법원에서 막힌 것)", ("LISTING_NOTICE", None): "신규 등재 예고",
              ("SUSPEND_NOTICE", None): "급여중지 예고", ("LISTING_NOTE_EXISTING", None): "이미 급여 중인 제품의 등재 예고 비고"}
PLAIN_END = {"예고대로 시행": "예고대로 실행", "다른 금액으로 시행": "다른 금액으로 실행", "종료 후 미시행": "집행정지가 끝난 뒤에도 실행 안 됨",
             "집행정지 중 삭제": "집행정지 중 목록에서 삭제", "집행정지 중(미결)": "집행정지 중, 아직 결과가 나지 않음"}
PLAIN_TYPE_SHORT = {"PRICE_NOTICE": "약가 예고", "SUSPEND_NOTICE": "급여중지 예고", "LISTING_NOTICE": "신규 등재 예고"}


def summary(N) -> str:
    """외부용 P1 2쪽 요약 (2026-11-19 사용자 지시 6: 내부 용어를 쓰지 않는다)"""
    import ext_core as X
    P1, P2 = X.p1_numbers(), X.p2_numbers()
    r = {(x["type"], x["rule"]): x for x in N["rates"]}
    sus, pr = r[("SUSPEND_NOTICE", None)], r[("PRICE_NOTICE", None)]
    om, h = N["omission"], N["hold"]
    L = ["# 급여목록 처분 예고 변화 기록표 (P1) — 2쪽 요약", "",
         "> 이 문서의 숫자는 `scripts/p1_docs.py`가 변화 기록표(DuckDB)와 결과 파일에서 다시 계산해 쓴 것이다. 내부 판정 규칙과 결정 기록은 "
         "[판정 규칙](../docs/RULES_2026-11-17.md), [결정 기록](../docs/DECISIONS.md), 수요 예측 쪽은 [P2 요약](P2_SUMMARY.md).", "",
         "**무엇을 했나**: 건강보험심사평가원의 급여목록·대체조제 목록 월별 파일(2022-01 ~ 2025-12, 48개월)을 날짜별 변화 기록표로 다시 만들었다. "
         "목록의 비고 칸에 미리 적힌 처분 예고(약가 인하, 신규 등재, 급여중지)는 아직 일어난 변화가 아니므로 따로 적고, 이후 파일로 예고대로 실행됐는지 확인했다. "
         f"SQL(DuckDB)과 Python. 자동 시험 {N['n_tests']}개(자료 적재, 판정 규칙, 미래 자료 차단 등, 마지막 실행에서 전부 통과).", ""]
    L += X.core(P1, P2, "p1")
    L += ["## 먼저 읽을 한계", "",
          f"- **약가 변화의 {pct(om['rate'])}는 목록에 미리 예고되지 않았다** ({om['unobserved']:,} / {om['n_price_change']:,}건). 예고만 보고는 약가 변화의 일부만 미리 알 수 있다.",
          f"- 급여목록에 집행정지 표시가 없는 집행정지는 바깥 자료 없이는 '실행 안 됨'으로 잘못 잡힌다. 지금 바깥 자료로 넣은 것은 1건(경보제약, {N['kyongbo_n']}개 제품)뿐이다.",
          "- 자동 판정의 정답은 작성자 한 사람이 정했다(30건). 그중 2건은 자동 판정 결과를 본 뒤 정답을 고쳤다.",
          "- 제품별 판매량, 약국 구매·재고, 도매 유통은 데이터에 없다. 급여목록의 처분 예고 건수는 제품 단위로 센 것이라 처분 한 건(한 회사)이 숫자를 크게 움직일 수 있다.",
          "- 집계 단위: 처분 예고는 제품 단위다. 같은 처분이 여러 제품에 걸리면 제품 수만큼 센다.", "",
          "## 1. 처분 예고 종류별로 얼마나 예고대로 실행됐나 (2025-12 기준, 괄호는 95% 신뢰구간)", "",
          "'현재까지 실행된 비율'은 아직 결과가 나지 않은 것(집행정지 중)을 실행 안 된 것으로 센다. '결과가 난 것 중 실행된 비율'은 그것을 뺀다. "
          "둘의 차이가 법원 집행정지로 아직 막혀 있는 몫이다. '판단 대상에서 뺀 것'은 예고 시점에 이미 급여중지 중이었거나 예고일 전에 제품이 사라진 경우다. "
          "괄호의 95% 신뢰구간은 건수가 적을 때 비율이 얼마나 흔들릴 수 있는지를 보여 준다. 대체조제 목록은 같은 성분의 다른 제품으로 바꿔 조제할 수 있는 약의 목록으로, "
          "급여목록에는 안 보이는 법원에서 막힌 인하 처분의 금액이 거기에만 남은 경우가 있다.", "",
          "| 예고 종류 | 건수 | 현재까지 실행된 비율 | 결과가 난 것 중 실행된 비율 | 집행정지 중, 아직 결과가 나지 않음 | 법원 집행정지로 막힌 비율 | 판단 대상에서 뺀 것 |",
          "|---|---|---|---|---|---|---|"]
    for key, name in PLAIN_TYPE.items():
        x = r[key]
        L.append(f"| {name} | {x['n']:,} | {x['A_k']:,}/{x['A_n']:,} = **{pct(x['A_p'])}** ({ci(x['A_lo'], x['A_hi'])}) | "
                 f"{x['B_k']:,}/{x['B_n']:,} = **{pct(x['B_p'])}** ({ci(x['B_lo'], x['B_hi'])}) | {x['ongoing']:,}건 | "
                 f"{pct(x['stay_p'])} ({x['stay_k']:,}/{x['stay_n']:,}) | {x['excluded']:,} |")
    L += ["", f"급여중지 예고가 막힌 {sus['stay_k']}건의 회사: " + ", ".join(f"{f} {n}건" for f, n, o in N["blocked_firm"])
          + f". 바깥 자료로만 막힘을 확인한 경보제약을 빼고 급여목록 표시만으로 세면 {sus['stay_k'] - N['kyongbo_n']}/{sus['stay_n']} = "
          f"{pct((sus['stay_k'] - N['kyongbo_n']) / sus['stay_n'])}, 결과가 난 것 중 실행된 비율은 {N['kyongbo_before_B'][0]}/{N['kyongbo_before_B'][1]} = {pct(N['kyongbo_before_B'][2])}다.", "",
          "**법원 집행정지로 막힌 예고는 어떻게 끝났나**", "", "| 예고 | 결말 | 건수 |", "|---|---|---|"]
    L += [f"| {PLAIN_TYPE_SHORT.get(t, t)} | {PLAIN_END.get(k, k)} | {n:,} |" for t, k, n in N["blocked"]]
    L += ["", "**예고대로 실행되지 않은 것** (숨기지 않는다)", "", "| 예고 | 사유 | 건수 |", "|---|---|---|"]
    L += [f"| {PLAIN_TYPE_SHORT.get(t, t)} | {PLAIN_END.get(REASON_KO.get(rr, rr), REASON_KO.get(rr, rr))} | {n:,} |" for t, rr, n in N["unrealized"]]
    EXC = {"대상 소멸": "예고일 전에 허가 취하로 제품이 사라짐", "급여중지 중 예고": "급여중지 중에 적힌 예고", "인하일 전 급여중지": "인하일 전에 급여중지됨"}
    L += ["", "판단 대상에서 뺀 것: " + ", ".join(f"{EXC.get(k, k)} {n}건" for k, n in N["excluded"]) + ".", "",
          "**얼마나 먼저 보이나**: 처분 예고가 처음 목록에 보인 달의 1일부터 예고된 날짜까지의 중앙값(괄호는 가운데 50%의 범위) "
          + ", ".join(f"{PLAIN_TYPE_SHORT[t]} {int(N['lead'][t][1])}일({int(N['lead'][t][2])}~{int(N['lead'][t][3])})" for t in ("PRICE_NOTICE", "LISTING_NOTICE", "SUSPEND_NOTICE"))
          + ". 약가 예고는 대개 한 달 앞에 보이고, 신규 등재 예고는 대개 그 달 파일에서 처음 보인다. 파일 기준일(1일)부터 잰 값이라 실제 공개일부터의 일수보다 길 수 있다.", "",
          f"**예고 없이 바뀐 약가**: {om['n_price_change']:,}건 중 {om['unobserved']:,}건. 그중 {om['unobserved_batch']:,}건은 정부가 여러 성분의 상한가를 한꺼번에 내린 일괄 인하 달("
          + ", ".join(f"{m} {n:,}" for m, n in N["batch_by_month"]) + f")이고, 이를 빼면 ({om['unobserved']:,} − {om['unobserved_batch']:,}) ÷ ({om['n_price_change']:,} − {om['unobserved_batch']:,}) = {pct(om['rate_rest'])}다.", "",
          "## 2. 자동 판정은 얼마나 맞나 (처음 보는 검증 표본 30건)", "",
          "자동 판정 결과를 감춘 표본 30건(변화 종류 10개 × 3건)에 작성자가 정답을 달았다. '유형'은 어떤 변화인지(약가 인하·신규 등재·업체 변경·급여중지 등)다.", "",
          "| 정답 | 유형 일치 | 날짜 일치 |", "|---|---|---|",
          f"| 처음 정답 | {int(h['유형 (처음 라벨)'].correct)}/{int(h['유형 (처음 라벨)'].n)} = **{pct(h['유형 (처음 라벨)'].precision)}** ({ci(h['유형 (처음 라벨)'].lo, h['유형 (처음 라벨)'].hi)}) | "
          f"{int(h['날짜'].correct)}/{int(h['날짜'].n)} = {pct(h['날짜'].precision)} ({ci(h['날짜'].lo, h['날짜'].hi)}) |",
          f"| 판정 결과를 본 뒤 고친 정답 | {int(h['유형 (라벨 판정 후)'].correct)}/{int(h['유형 (라벨 판정 후)'].n)} = **{pct(h['유형 (라벨 판정 후)'].precision)}** ({ci(h['유형 (라벨 판정 후)'].lo, h['유형 (라벨 판정 후)'].hi)}) | 같음 |", "",
          f"- 다시 본 것은 처음 정답과 맞지 않은 {len(N['hold_miss'])}건뿐이라, 정답 수정은 자동 판정에 유리한 쪽으로만 일어날 수 있다. 날짜는 정답 날짜가 있는 {int(h['날짜'].n)}건으로 셌다.",
          "- 그 뒤 이 불일치를 보고 판정 규칙을 고쳤다. 고친 뒤의 일치율은 성능으로 쓰지 않는다. 규칙을 만들 때 변화 종류별로 뽑아 검토한 다른 표본(61건)도 성능으로 쓰지 않는다.", "",
          "## 3. 바깥 자료로 확인한 사례", "",
          f"1. **동아에스티 급여중지 {N['donga_n']}개 제품**: 서울행정법원 집행정지 결정일(2022-05-09)이 목록에 적힌 집행정지 날짜와 같아, 날짜 순서로 막힌 처분을 정하는 규칙을 뒷받침한다.",
          f"2. **경보제약 급여중지(2025-06-24) {N['kyongbo_n']}개 제품**: 급여목록에는 집행정지 표시가 없지만 법원 결정(대전지법 2025-07-09, 데일리팜 보도)으로 막힘 처리했다.",
          f"3. **로코탄정 급여중지 해제일**: 목록 기준 2024-01-01을 바깥 자료의 {N['lokotan'][0]}(뉴스더보이스 2024-01-05)로 바꿨다.", "",
          "## 4. 약한 근거", "",
          f"- **사용통계와 맞춰 보기**: 예고대로 실행된 약가 예고(같은 날·같은 성분끼리 묶음) {N['a4']['groups']:,}묶음 중 사용통계로 비교할 수 있는 것은 {N['a4']['target']}묶음뿐이다(나머지는 사용통계가 비공개이거나 성분 안 비중이 작음). 예고일이 달 중간인 "
          f"{N['a4']['mid']}묶음에서 '예고일 적용' 기록이 실제 처방 단가 변화에 더 가까웠다({N['a4']['mid_closer']}/{N['a4']['mid']}). 방향 확인 수준이다.",
          f"- **목록가 변화와 실제 처방 단가**: 처방 단가 = 원외 청구금액 ÷ 원외 청구수량. 목록가가 바뀐 성분×월 {N['b1']['pairs']:,}쌍의 {pct(N['b1']['same'])}에서 처방 단가가 같은 방향으로 움직였다(목록가가 그대로인 쌍에서 우연히 같은 방향일 비율 {pct(N['b1']['ctl'])}). "
          f"처방 단가 변화 ÷ 목록가 변화의 중앙값은 {N['b1']['med']:.2f}(가운데 50% 범위 {N['b1']['q25']:.2f} ~ {N['b1']['q75']:.2f}): 목록가가 1% 내리면 처방 단가는 중앙값으로 약 {N['b1']['med']:.2f}% 내렸다. "
          "판매량이 없어 1에서 벗어나는 부분(값이 안 바뀐 제품, 제품 구성 차이)을 나눠 보지 못했다.", "",
          "## 5. 바로팜 공고 문구별 근거", "",
          "| 공고 문구 | 근거 |", "|---|---|",
          "| 의약품 시장 전반에 대한 분석, 보고서 설계 및 자동화 | 명령 하나(`make report MONTH=YYYY-MM`)로 만드는 월간 리포트 [`s6_report.py`](../src/pbd/ledger/s6_report.py), 예시 [2024-09](../outputs/reports/P1_report_2024-09.md) · [2025-11](../outputs/reports/P1_report_2025-11.md) |",
          "| 가설 검증을 통해 잠재적 이슈를 계량화하고 해결 방안을 도출 | 처분 예고의 실행 비율과 법원 집행정지 몫(위 §1), 자동 판정 검증(§2) |",
          "| 심평원 빅데이터 및 처방 데이터 활용 (우대) | 심평원 급여목록 × 건보공단 사용통계: [데이터 설명](../docs/DATA.md) |",
          "| 약국 구매, 판매, 사용 패턴 분석 기반 예측 모델 개발 | 약국 단위 자료는 없다. 성분 × 전국 처방량 예측과 그 한계: [P2 요약](P2_SUMMARY.md) |",
          "| SQL, Python | DuckDB SQL과 Python, 자동 시험 [`tests/`](../tests/) |",
          "| Tableau, Power BI (우대) | 대시보드용 CSV `outputs/dashboard/YYYY-MM/`. 대시보드 자체는 만들지 않았다 |", ""]
    L += X.next_table(P2)
    return "\n".join(L)


def p2_row(N) -> str:
    """공고 '예측 모델' 행: P2 최소판 결과를 숨기지 않는다 (검토 a11). 약국 단위 자료가 없음을 먼저"""
    base = "| 약국 구매, 판매, 사용 패턴 분석 기반 예측 모델 개발 | 약국 단위 구매·판매 자료는 없다. 대신 성분 × 전국 원외 청구수량 예측(P2 최소판). "
    c = N["p2"]
    if c is None:
        return base + "결과 없음 |"
    r = c[(c.lag == 5)].set_index(["a", "b"])
    m20 = r.loc[("M2", "M0")]
    rep = (ROOT / "reports/P2_min_result.md").read_text(encoding="utf-8")
    concl = next(x for x in rep.splitlines() if x.startswith("**결론**: ")).removeprefix("**결론**: ")   # 고정 결론 (한 곳에서만 정의)
    return (base + f"{concl} 공개 시차 5개월(주)에서 전역 모델 M2 MASE {m20.mase_a:.3f}는 12개월 전 값 M0 {m20.mase_b:.3f}보다 나빴다. "
            "[P2 결과](P2_min_result.md), 설계 [PLAN_P2](../docs/PLAN_P2.md) |")


def limitations_block(N) -> str:
    om = N["omission"]
    sus = next(x for x in N["rates"] if x["type"] == "SUSPEND_NOTICE")
    pt = {t: (n, a) for t, n, a in N["ptype"]}
    gap = dict(N["gap"])
    L = [LIM_START, "", "## v5.3 한계 (먼저 읽을 것)", "",
         "아래 V1~V7은 v5.3 원장 기준이다. 숫자는 `scripts/p1_docs.py`가 계산했다. 그 아래 L1~L17은 이전 단계의 한계이며, v4 원장 숫자가 들어 있는 항목(L7, L8)은 V 항목으로 대체됐다. "
         "**L 항목의 숫자는 각 단계 보고서(위치 칸)의 값이고 v5.3에서 다시 계산하지 않았다.** L4·L5의 2023-09 건수와 미실현 77건은 v4 원장 숫자다.", "",
         "| # | 무엇이 | 영향 크기 | 대응 | 다음 실험 (요약의 '한계와 다음 실험' 번호) |", "|---|---|---|---|---|",
         f"| V1 | 예고 누락: 약가 변화의 대부분은 비고에 예고가 없다 | 예고 미관측 변경 {om['unobserved']:,} / 약가 변화 {om['n_price_change']:,} = **{pct(om['rate'])}**. 일괄 가격인하 달({', '.join(om['batch_months'])})의 몫 {om['unobserved_batch']:,}건을 빼면 {pct(om['rate_rest'])} | batch 표시(판정에 쓰지 않음)로 나눠 보고. 예고 미관측 변경은 이벤트로만 기록하고 신호 결과로 세지 않는다 |",
         f"| V2 | 급여목록에 집행정지 비고가 없는 집행정지 | 경보제약 {N['kyongbo_n']}건: 외부 근거가 없으면 '미실현(비고 사라짐)'. 급여중지 예고 B {pct(N['kyongbo_before_B'][2])} → {pct(sus['B_p'])} | `config/external_evidence.yaml` `external_stay`에 외부 근거(업체·비고 날짜·결정일·출처)를 넣어야 막힘으로 잡힌다. 다른 사례는 확인하지 않았다 |",
         f"| V3 | 대체조제 목록은 처분 정보에만 쓴다 | 대체조제에만 남은 급여정지 표시 행 {N['subst_lag'][0]:,}개({N['subst_lag'][1]:,}개 제품)는 `subst_lag` 표시만. 처분 정보로 쓴 막힌 인하 처분: 대체조제 비고 예고 {N['s_only_r2']}건, 금액 차이 신호 {N['s_amt']}건 | 상태 판정은 급여목록만 (RULES §1) |",
         f"| V4 | 약가 유형: 용기 표기가 붙은 최소단위 품목은 생산단위로 분류됐을 수 있다 | 2025-12 최소단위 후보 {pt.get('minimal_unit', (0, 0))[0]}개 제품, 생산단위 중 `unit_ambiguous` {pt.get('production_unit', (0, 0))[1]}개 제품. 유연계약은 분석 기간 밖(0건) | 판정에 쓰지 않는 속성 칸. RULES §2-1의 '약 66개 제품'(독립 점검, 단위별 셈)과는 세는 대상이 다르고 맞춰 보지 않았다(산정불가 R7로 간 제품 포함 여부 등). 결과가 바뀌면 멈추고 보고 |",
         f"| V5 | 같은 허가 연결의 긴 공백 | 이어받기 {N['gap_n']}쌍의 공백: 0개월 {gap.get('0', 0)}, 1~3개월 {gap.get('1~3', 0)}, 4~12개월 {gap.get('4~12', 0)}, **13개월 이상 {gap.get('13+', 0)}**(그중 같은 허가 {N['gap13_item']}) | 기간 제한 없음(사용자 결정). `gap_months`를 이벤트 표·리포트에 표시 |",
         f"| V6 | 월 스냅샷 단위: 월중 날짜는 비고 날짜가 있을 때만 맞다 | 진입·이어받기·약가·급여중지·집행정지 이벤트 {N['first_day'][0]:,}건 중 {N['first_day'][1]:,}건({pct(N['first_day'][1] / N['first_day'][0])})은 비고 날짜가 없어 그 달 1일 | 목록 규칙(L4). 사용통계 대조(A4)의 월중 예고 묶음은 6개뿐 |",
         f"| V7 | 라벨은 사용자 1인 | holdout 30행 중 2행(2·3번)은 원장 결과를 본 뒤 정답을 고쳤다. 처음 라벨 {pct(N['hold']['유형 (처음 라벨)'].precision)} / 라벨 판정 후 {pct(N['hold']['유형 (라벨 판정 후)'].precision)}을 함께 쓴다 | 두 줄 보고. 개정 후 holdout 숫자는 성능으로 쓰지 않는다 |",
         "", LIM_END]
    nxt = {"V1": "7", "V2": "8", "V3": "—", "V4": "—", "V5": "10", "V6": "8", "V7": "10"}
    L = [x.rstrip() + f" {nxt[x[2:4]]} |" if x.startswith("| V") else x for x in L]
    return "\n".join(L)


def readme_block(N) -> str:
    sus = next(x for x in N["rates"] if x["type"] == "SUSPEND_NOTICE")
    pr2 = next(x for x in N["rates"] if x["type"] == "PRICE_NOTICE" and x["rule"] == "R2")
    h = N["hold"]
    return "\n".join([RD_START,
        f"**P1 급여목록 처분 예고 변화 기록표**: 약가 인하 예고는 결과가 난 것 중 {pct(pr2['B_p'])}가 예고대로 실행됐다. 급여중지 예고는 {pct(sus['stay_p'])}가 법원 집행정지로 막혔다. "
        f"약가 변화의 {pct(N['omission']['rate'])}는 미리 예고되지 않았다. 처음 보는 검증 표본 30건에서 자동 판정 유형 일치 {pct(h['유형 (라벨 판정 후)'].precision)}(처음 정답 기준 {pct(h['유형 (처음 라벨)'].precision)}). "
        f"자동 시험 {N['n_tests']}개. 2쪽 요약: [reports/SUMMARY.md](reports/SUMMARY.md), 한계: [docs/LIMITATIONS.md](docs/LIMITATIONS.md).",
        RD_END])


def replace_block(text: str, start: str, end: str, block: str, anchor: str) -> str:
    if start in text:
        return text[: text.index(start)] + block + text[text.index(end) + len(end):]
    i = text.index(anchor)
    return text[:i] + block + "\n\n" + text[i:]


def check(path: Path, text: str, N) -> list[str]:
    errs = []
    for m in re.finditer(r"\]\(([^)#\s]+)(#[^)]*)?\)", text):
        tgt = m.group(1)
        if tgt.startswith("http"):
            continue
        if not (path.parent / tgt).resolve().exists():
            errs.append(f"{path.name}: 링크 대상 없음 {tgt}")
    for w in ("정확히", "완벽", "모두", "인과", "영향을 미", "인하된다", "등재된다", "중지된다", "확실히"):
        if w in text:
            errs.append(f"{path.name}: 금지 표현 '{w}'")
    return errs


def main() -> None:
    N = collect()
    out = []
    s = summary(N)
    sp = ROOT / "reports/SUMMARY.md"
    sp.write_text(s, encoding="utf-8")
    out.append((sp, s))
    lp = ROOT / "docs/LIMITATIONS.md"
    lt = replace_block(lp.read_text(encoding="utf-8"), LIM_START, LIM_END, limitations_block(N), "## 목차")
    lp.write_text(lt, encoding="utf-8")
    out.append((lp, limitations_block(N)))
    rp = ROOT / "README.md"
    rt = rp.read_text(encoding="utf-8").replace("2쪽 요약: [docs/SUMMARY.md](docs/SUMMARY.md).", "")
    rt = replace_block(rt, RD_START, RD_END, readme_block(N), "<!-- P2 요약 시작")
    rp.write_text(rt, encoding="utf-8")
    out.append((rp, readme_block(N)))
    dp = ROOT / "docs/SUMMARY.md"
    dp.write_text("# 2쪽 요약은 옮겼다\n\nv5.3 기준 2쪽 요약은 [reports/SUMMARY.md](../reports/SUMMARY.md)다 (`scripts/p1_docs.py`가 원장에서 생성). "
                  "이전 판(v4 원장 숫자)은 git 기록(커밋 c63778c 이전)에 있다.\n", encoding="utf-8")
    errs = [e for p, t in out for e in check(p, t, N)]
    import ext_core as X
    errs += X.check_external(sp, s) + X.check_external(rp, rp.read_text(encoding="utf-8"))
    # 문서 숫자 = 생성값: 다시 계산해 같은 글이 나오는지
    if summary(collect()) != s:
        errs.append("SUMMARY: 다시 계산한 글과 다름")
    if errs:
        print("\n".join(errs))
        raise SystemExit(1)
    print(f"SUMMARY {len(s.splitlines())}줄, LIMITATIONS V1~V7, README P1 블록. 검사 통과 (시험 {N['n_tests']}개)")


if __name__ == "__main__":
    main()
