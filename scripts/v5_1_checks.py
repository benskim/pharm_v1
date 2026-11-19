"""v5.1 점검표 (사용자 지시 2026-11-18, RULES §11-2·3·7). 읽기 전용. 출력 outputs/v5/check_*.csv
1. batch 경계 사례: 2022-09·2024-05·2024-08·2024-11의 n_products·n_ingredients (비고 미기재 약가 변경만, batch_month_v5)
2. 업체명이 원문 그대로 같은데 양도양수로 잡힌 쌍 (옛 코드 마지막 달 업체명 = 새 코드 첫 달 업체명, stg_benefit 원문)
3. v4의 코드 변경(TRANSFER same_item_recode) 338건이 v5에서 무엇이 됐나 (v4 보관본)
4. 집행정지로 막힌 약가 예고가 집행정지 중 삭제된 건(stay_then_delisted) 목록: 제품코드, 제품명, 최초 기재월, 집행정지 날짜, 마지막 등재월,
   같은 허가·같은 성분(9자리) 후속 코드 유무. 판정은 바꾸지 않는다
사용: PYTHONPATH=src python -I scripts/v5_1_checks.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402

from pbd.common import DB_PATH  # noqa: E402

OUT = ROOT / "outputs/v5"
LEG = ROOT / "outputs/v4_legacy/v4_legacy.duckdb"


def main() -> None:
    c = duckdb.connect(str(DB_PATH), read_only=True)
    c.execute(f"attach '{LEG.as_posix()}' as v4 (read_only)")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 70)
    pd.set_option("display.max_rows", 80)

    b = c.execute("""select * from batch_month_v5 where observed_month in ('2022-09', '2024-05', '2024-08', '2024-11')
                     union all select * from batch_month_v5 where batch order by 1""").df().drop_duplicates()
    allpc = c.execute("""select observed_month, count(*) n_all, count(distinct market_c4) n_c4_all from ev_v5
                         where event_type = 'PRICE_CHANGE' group by 1""").df()
    b = b.merge(allpc, on="observed_month", how="left")
    b.to_csv(OUT / "check_batch_months.csv", index=False)
    print("== batch 경계 (비고 미기재 약가 변경만 / 참고: 모든 약가 변경)\n", b.to_string(index=False))

    firm = c.execute("""with f as (select product_code, arg_min(firm, list_month) firm_first, arg_max(firm, list_month) firm_last
                                   from stg_benefit where row_kind = 'product_candidate' group by 1)
        select s.old, s.new, s.kind, s.link_basis, s."order", fo.firm_last old_firm, fn.firm_first new_firm, s.gone_month, s.entry_month
        from succession_v5 s join f fo on fo.product_code = s.old join f fn on fn.product_code = s.new
        where s.kind = 'transfer' and fo.firm_last = fn.firm_first order by s.gone_month, s.old""").df()
    firm.to_csv(OUT / "check_same_firm_transfer.csv", index=False)
    print(f"\n== 업체명 원문이 같은데 양도양수: {len(firm)}쌍\n", firm.head(20).to_string(index=False))

    r = c.execute("""select e.product_code, e.observed_month, coalesce(n.event_type || '|' || n.kind, '(없음)') v5,
                            n.link_basis, n.counterpart, s."order"
        from v4.event_ledger e
        left join ev_v5 n on n.product_code = e.product_code and n.observed_month = e.observed_month
             and n.event_type in ('LISTING', 'SUCCESSION', 'DELISTING_STAY')
        left join succession_v5 s on s.new = e.product_code and s.entry_month = e.observed_month
        where e.event_type = 'TRANSFER' and e.kind = 'same_item_recode' order by 2, 1""").df()
    r.to_csv(OUT / "check_v4_recode_338.csv", index=False)
    t = r.groupby(["v5", "link_basis"], dropna=False).size().rename("n").reset_index().sort_values("n", ascending=False)
    t.to_csv(OUT / "check_v4_recode_338_summary.csv", index=False)
    print(f"\n== v4 코드 변경 {len(r)}건 → v5\n", t.to_string(index=False))

    d = c.execute("""with life as (select product_code, min(list_month) f, max(list_month) l, arg_max(product_name, list_month) nm,
                                          arg_max(firm, list_month) firm, arg_max(ingredient_code, list_month) ing
                                   from stg_benefit where row_kind = 'product_candidate' group by 1),
             it as (select product_code, min(item_code) item from bridge_product_item group by 1 having count(distinct item_code) = 1)
        select s.signal_id, s.source_rule, s.product_code, l.nm product_name, l.firm, s.first_month, s.blocking_stays stay_dates,
               l.l last_listed, s.resolved_month,
               (select string_agg(x.product_code || '(' || x.f || '~' || x.l || ')', ', ') from life x join it ix using (product_code)
                where ix.item = (select item from it where product_code = s.product_code) and x.product_code <> s.product_code
                  and x.f > s.first_month) same_item_successor,
               (select string_agg(x.product_code || '(' || x.firm || ', ' || x.f || '~' || x.l || ')', ', ') from life x
                where x.ing = l.ing and x.product_code <> s.product_code and x.f between strftime(strptime(l.l, '%Y-%m') - interval 1 month, '%Y-%m')
                      and strftime(strptime(l.l, '%Y-%m') + interval 3 month, '%Y-%m')) same_ingredient_successor,
               (select count(*) from succession_v5 p where p.old = s.product_code) linked_pairs
        from signal_v5 s join life l using (product_code)
        where s.signal_type = 'PRICE_NOTICE' and s.reason = 'stay_then_delisted' order by s.source_rule, s.first_month, s.product_code""").df()
    d.to_csv(OUT / "check_stay_then_delisted_price.csv", index=False)

    f = same_form(c)
    f.to_csv(OUT / "check_suspend_then_price_same_note.csv", index=False)
    print(f"\n== 한 비고에 급여중지 + 그 뒤 날짜 인하: {len(f)}건 (다음 달 삭제 {int((~f['listed_next']).sum())})\n",
          f.drop(columns=["product_name"]).to_string(index=False))
    print(f"\n== 약가 예고 집행정지 중 삭제 {len(d)}건\n", d.drop(columns=["signal_id"]).to_string(index=False))


def same_form(c) -> pd.DataFrame:
    """한 달 비고(두 목록)에 급여중지(R3·R3b)와 그보다 뒤 날짜의 인하 예고(R2)가 함께 있고, 다음 달 급여목록에서 사라진 제품 (전수)"""
    return c.execute("""with t as (select distinct product_code, list_month, token_type, token_date, amount, raw from note_token),
             nx as (select list_month, lead(list_month) over (order by list_month) nm from (select distinct list_month from stg_benefit))
        select s.product_code, s.list_month, s.raw suspend_raw, s.token_date suspend_date, p.raw price_raw, p.token_date price_date,
               nx.nm next_month,
               exists (select 1 from stg_benefit b where b.product_code = s.product_code and b.list_month = nx.nm) listed_next,
               g.outcome price_outcome, g.reason price_reason, g.exclude_reason, (select any_value(product_name) from stg_benefit b
                   where b.product_code = s.product_code) product_name
        from t s join t p on p.product_code = s.product_code and p.list_month = s.list_month and p.token_type = 'R2' and p.token_date > s.token_date
        join nx on nx.list_month = s.list_month
        left join signal_v5 g on g.signal_id = 'PRICE_NOTICE:' || p.product_code || ':' || strftime(p.token_date, '%Y-%m-%d')
        where s.token_type in ('R3', 'R3b') order by s.list_month, s.product_code""").df()


if __name__ == "__main__":
    main()
