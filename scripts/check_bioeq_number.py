"""대체조제 목록 `생동인정여부(공고번호)` 괄호 숫자 점검 (2026-11-16, PLAN_P1 §8 4번).

사용자 확인: 괄호 숫자 = 식약처 생물학적동등성 인정품목 목록의 순번 (목록 버전마다 바뀐다).
이 해석과 원장 값이 맞는지 센다. 판정에는 쓰지 않는다 (○ 여부만 `sub_bioeq`).
사용: .venv/bin/python -I scripts/check_bioeq_number.py  → outputs/s2/bioeq_number_*.csv
"""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/s2"


def main() -> None:
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    con.execute(r"""create temp table b as
        select product_code, list_month, coalesce(sub_item_code, item_code) item, sub_bioeq_raw raw,
               cast(regexp_extract(sub_bioeq_raw, '\((\d+)\)', 1) as int) num
        from product_state_month where sub_bioeq_raw is not null""")
    summary = con.execute(r"""
        with p as (select product_code, count(distinct num) nd, count(distinct list_month) nm from b group by 1),
             dup as (select list_month, num, count(*) n, count(distinct item) ni from b group by 1, 2 having count(*) > 1),
             im as (select list_month, item, count(distinct num) nn from b where item is not null group by 1, 2)
        select (select count(*) from b) rows_with_value,
               (select count(*) from b where not regexp_matches(raw, '^○\(\d+\)$')) rows_other_format,
               (select count(*) from p) products,
               (select count(*) from p where nm > 1) products_multi_month,
               (select count(*) from p where nd > 1) products_number_changed,
               (select count(*) from p where nm > 1 and nd = 1) products_multi_month_unchanged,
               (select count(*) from p where nm = 1) products_single_month,
               (select count(*) from dup) same_month_duplicate_numbers,
               (select count(*) from dup where ni = 1) duplicates_same_item,
               (select count(*) from im) item_months,
               (select count(*) from im where nn > 1) item_months_multi_number""").df()
    by_month = con.execute("""with c as (select list_month, num, lag(num) over (partition by product_code order by list_month) pn from b)
        select list_month, count(*) filter (where pn is not null) continuing, count(*) filter (where pn <> num) changed,
               round(count(*) filter (where pn <> num) / nullif(count(*) filter (where pn is not null), 0), 4) share_changed
        from c group by 1 order by 1""").df()
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "bioeq_number_summary.csv", index=False, lineterminator="\n")
    by_month.to_csv(OUT / "bioeq_number_by_month.csv", index=False, lineterminator="\n")
    print(summary.T.to_string(header=False))
    con.close()


if __name__ == "__main__":
    main()
