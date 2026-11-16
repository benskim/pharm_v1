"""주성분코드별 주성분명 수 점검 (2026-11-16, S6 성분명 표시).

같은 주성분코드에 이름이 2개 이상 나온 코드 수와, 그중 복합제(주성분개수 2 이상, 이 칸은 2025-11·12 급여목록에만 있다) 비율.
공백만 다른 이름은 같은 이름으로 본다. 사용: .venv/bin/python -I scripts/check_ingredient_names.py → outputs/s2/ingredient_name_*.csv
"""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/s2"


def main() -> None:
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    con.execute(r"""create temp table nm as
        select ingredient_code, list_month, regexp_replace(trim(ingredient_name), '\s+', ' ', 'g') nm from stg_benefit
        where ingredient_name is not null and row_kind is distinct from 'non_product'
        union all select ingredient_code, list_month, regexp_replace(trim(ingredient_name), '\s+', ' ', 'g') from stg_substitute
        where ingredient_name is not null""")
    con.execute("""create temp table cx as select ingredient_code, max(try_cast(n_ingredients as int)) ni from stg_benefit
                   where list_month in ('2025-11', '2025-12') group by 1""")
    summary = con.execute("""with m as (select ingredient_code, count(distinct nm) k from nm group by 1)
        select case when cx.ni is null then 'unknown' when cx.ni >= 2 then 'complex' else 'single' end kind,
               count(*) codes, count(*) filter (where k > 1) codes_multi_name
        from m left join cx using (ingredient_code) group by 1 order by 1""").df()
    detail = con.execute("""with m as (select ingredient_code, list(distinct nm order by nm) names, count(distinct nm) k from nm group by 1)
        select m.ingredient_code, cx.ni n_ingredients, m.k n_names, array_to_string(m.names, ' | ') names
        from m left join cx using (ingredient_code) where m.k > 1 order by 1""").df()
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "ingredient_name_summary.csv", index=False, lineterminator="\n")
    detail.to_csv(OUT / "ingredient_name_multi.csv", index=False, lineterminator="\n")
    tot, multi = int(summary["codes"].sum()), int(summary["codes_multi_name"].sum())
    cxm = int(summary.loc[summary["kind"] == "complex", "codes_multi_name"].sum())
    print(summary.to_string(index=False))
    print(f"이름 2개 이상: {multi} / {tot} 코드, 그중 복합제 {cxm} ({cxm / multi:.1%})")
    con.close()


if __name__ == "__main__":
    main()
