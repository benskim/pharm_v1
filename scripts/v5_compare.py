"""v4(개정 전) → v5(RULES_2026-11-17) 비교표. 읽기 전용 (data/interim/pbd.duckdb). 출력: outputs/v5/compare_*.csv, rates_v5.csv
사용: PYTHONPATH=src python -I scripts/v5_compare.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb
import pandas as pd

from pbd.common import DB_PATH, ROOT
from pbd.ledger.v5_rules import omission_rate, rates

OUT = ROOT / "outputs/v5"


def main() -> None:
    c = duckdb.connect(str(DB_PATH), read_only=True)
    # v4(개정 전) 표는 보관본에서 읽는다 (RULES §11-4): 이름을 기본 DB → 보관본 순으로 찾는다
    leg = Path(__file__).resolve().parents[1] / "outputs/v4_legacy/v4_legacy.duckdb"
    c.execute(f"attach '{leg.as_posix()}' as v4 (read_only)")
    c.execute(f"set search_path = '{c.execute('select current_database()').fetchone()[0]}.main,v4.main'")
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 120)

    # 1. 신호 유형별 지표 (§7): A·B·정시·집행정지율, Wilson 95%
    rows = []
    for st, rule in c.execute("select distinct signal_type, source_rule from signal_v5 order by 1, 2").fetchall() + \
            [(t, "all") for (t,) in c.execute("select distinct signal_type from signal_v5 order by 1").fetchall()]:
        cond = "" if rule == "all" else f"and source_rule = '{rule}'"
        rs = [dict(outcome=o, reason=r, stay_blocked=b, current_status=s) for o, r, b, s in c.execute(
            f"select outcome, reason, stay_blocked, current_status from signal_v5 where signal_type = ? {cond}", [st]).fetchall()]
        x = rates(rs)
        row = dict(signal_type=st, source_rule=rule, n=x["n"], excluded=x["n_excluded"], censored_ongoing_stay=x["n_ongoing_stay"],
                   censored_awaiting=x["n_awaiting"])
        for k in ("A", "B", "on_time", "stay"):
            a, b, p, lo, hi = x[k]
            row.update({f"{k}_k": a, f"{k}_n": b, f"{k}_rate": None if p is None else round(p, 4),
                        f"{k}_lo": None if lo is None else round(lo, 4), f"{k}_hi": None if hi is None else round(hi, 4)})
        rows.append(row)
    r = pd.DataFrame(rows)
    r.to_csv(OUT / "rates_v5.csv", index=False)
    print(r[["signal_type", "source_rule", "n", "excluded", "censored_ongoing_stay", "censored_awaiting", "A_k", "A_n", "A_rate",
             "B_k", "B_n", "B_rate", "on_time_rate", "stay_k", "stay_n", "stay_rate"]].to_string(index=False))
    s, u = c.execute("""select count(*) filter (where kind = 'signaled'), count(*) filter (where kind = 'unobserved_notice')
                        from event_ledger_v5 where event_type = 'PRICE_CHANGE'""").fetchone()
    sb, ub = c.execute("""select count(*) filter (where kind = 'signaled' and not batch), count(*) filter (where kind = 'unobserved_notice' and not batch)
                          from event_ledger_v5 where event_type = 'PRICE_CHANGE'""").fetchone()
    om = pd.DataFrame([dict(scope="all", signaled=s, unobserved=u, omission_rate=round(omission_rate(s, u), 4)),
                       dict(scope="batch_false", signaled=sb, unobserved=ub, omission_rate=round(omission_rate(sb, ub), 4))])
    om.to_csv(OUT / "omission_rate.csv", index=False)
    print("\n", om.to_string(index=False))

    # 2. 결과값별 건수: v4 → v5
    v4 = c.execute("""select case signal_type when 'PRICE_PENDING' then 'PRICE_NOTICE' when 'SUSPEND_SCHEDULED' then 'SUSPEND_NOTICE'
                             when 'LISTING_SCHEDULED' then 'LISTING_NOTICE' when 'DELISTING_SCHEDULED' then 'DELISTING_NOTICE' else signal_type end signal_type,
                             'v4' ver, outcome, coalesce(reason, '-') reason, count(*) n from signal_outcome group by all""").df()
    v5 = c.execute("""select signal_type, 'v5' ver, outcome, coalesce(reason, current_status, '-') reason, count(*) n from signal_v5 group by all""").df()
    o = pd.concat([v4, v5]).sort_values(["signal_type", "ver", "outcome", "reason"])
    o.to_csv(OUT / "compare_outcomes.csv", index=False)
    print("\n", o.to_string(index=False))
    # 집행정지로 막힌 신호의 결말표 (§7)
    b = c.execute("""select signal_type, source_rule, case when outcome in ('REALIZED', 'REALIZED_LATE') then '1 예고대로 시행'
                       when outcome = 'REALIZED_CHANGED' then '2 다른 금액으로 시행' when reason = 'stay_ended_not_executed' then '3 종료 후 미시행'
                       when reason = 'stay_then_delisted' then '4 집행정지 중 삭제' when current_status = 'ONGOING_STAY' then '5 집행정지 중(미결)'
                       else outcome || ':' || coalesce(reason, '') end ending, count(*) n
                     from signal_v5 where stay_blocked group by all order by 1, 2, 3""").df()
    b.to_csv(OUT / "blocked_endings.csv", index=False)
    print("\n", b.to_string(index=False))
    # 약가 예고 삭제 종료: 집행정지로 막혔던 것과 아닌 것 (사용자 지시 2026-11-18)
    d = c.execute("""select 'v4' ver, l.source_rule, s.stay_blocked, count(*) n
                     from signal_outcome o join signal_ledger l using (signal_id)
                     left join signal_v5 s on s.signal_id = replace(o.signal_id, 'PRICE_PENDING', 'PRICE_NOTICE')
                     where o.signal_type = 'PRICE_PENDING' and o.reason = 'delisted' group by all
                     union all
                     select 'v5', source_rule, stay_blocked, count(*) from signal_v5
                     where signal_type = 'PRICE_NOTICE' and reason in ('ended_by_delisting', 'stay_then_delisted') group by all order by 1, 2, 3""").df()
    d.to_csv(OUT / "price_delisted_split.csv", index=False)
    print("\n", d.to_string(index=False))

    # 3. 이벤트 유형별 건수: v4 → v5 (v5는 철회되지 않은 행 = revised 보기, 철회 행은 따로)
    MAP = {("LISTING", "new_item"): "신규 등재", ("LISTING", "unlinked"): "신규 등재", ("LISTING", "new_pack_existing_item"): "추가 등재",
           ("LISTING", "reentry"): "재등재", ("TRANSFER", "same_item_recode"): "코드 변경(v4: 같은 품목, 업체 무관)",
           ("TRANSFER", "transfer_by_name"): "양도양수", ("TRANSFER", "same_firm_swap"): "교체 등재", ("TRANSFER", "list_recode"): "코드 변경",
           ("DELISTING", "exit"): "급여삭제", ("DELISTING", "item_remains"): "급여삭제(규격)", ("DELISTING", "recode_transfer"): "넘겨준 쪽(v4: 급여삭제로 셈)",
           ("PRICE_CHANGE", "signaled"): "약가 변경: 예고 실현", ("PRICE_CHANGE", "unsignaled"): "약가 변경: 예고 미관측",
           ("PRICE_CHANGE", "stay_released"): "약가 변경: 집행정지 해제(v4)",
           ("LISTING", "new_listing"): "신규 등재", ("LISTING", "added_listing"): "추가 등재", ("LISTING", "relisting"): "재등재",
           ("SUCCESSION", "transfer"): "양도양수", ("SUCCESSION", "recode"): "코드 변경", ("SUCCESSION", "swap"): "교체 등재",
           ("DELISTING", "delisting"): "급여삭제", ("DELISTING", "delisting_spec"): "급여삭제(규격)",
           ("DELISTING", "confirmed_after_stay"): "급여삭제 최종 확정(삭제 집행정지 뒤)", ("DELISTING_STAY", "delisting_stay"): "삭제 집행정지",
           ("PRICE_CHANGE", "unobserved_notice"): "약가 변경: 예고 미관측"}
    e4 = c.execute("select event_type, kind, count(*) n from event_ledger group by all").df().assign(ver="v4")
    e5 = c.execute("select event_type, kind, count(*) n from event_ledger_v5 where retracted_month is null group by all").df().assign(ver="v5")
    ex = c.execute("select event_type, kind || ' (as_known, 나중에 철회)' kind, count(*) n from event_ledger_v5 where retracted_month is not null group by all").df().assign(ver="v5")
    e = pd.concat([e4, e5, ex])
    e["name"] = [MAP.get((a, k), f"{a}:{k}") for a, k in zip(e.event_type, e.kind)]
    piv = e.pivot_table(index="name", columns="ver", values="n", aggfunc="sum", fill_value=0).reset_index()
    piv.to_csv(OUT / "compare_events.csv", index=False)
    print("\n", piv.to_string(index=False))
    bt = c.execute("""select observed_month, effective_date, count(*) n, count(distinct market_c4) n_c4 from event_ledger_v5
                      where event_type = 'PRICE_CHANGE' and batch group by 1, 2 order by 1""").df()
    bt.to_csv(OUT / "batch_months.csv", index=False)
    print("\n", bt.to_string(index=False))


if __name__ == "__main__":
    main()
