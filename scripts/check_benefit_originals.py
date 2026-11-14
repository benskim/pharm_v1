"""급여목록 원본 xlsx(2022~2023)와 parquet 대조 (점검만. parquet은 고치지 않는다).

원본: data/raw/benefit/_original_check/*.xlsx (2022-01, 2022-03, 2022-09, 2023-11), 2025-11·12 CSV
결과: outputs/original_check/*.csv

원본 형식 (사용자 설명 2026-11-14): 주성분명 컬럼이 없고, 주성분마다 "묶음 머리 행"이 있다.
머리 행 판별은 구조로 한다: 업체명·상한금액이 비어 있고, 제품코드 칸이 9자리 숫자가 아니다.
음영(openpyxl fill)은 확인에만 쓴다. 녹색 = indexed 42 또는 rgb FFCCFFCC.
사용: python -I scripts/check_benefit_originals.py
"""
import glob
import re
from pathlib import Path

import duckdb
import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/raw/benefit/_original_check"
PQ = ROOT / "data/raw/benefit"
OUT = ROOT / "outputs/original_check"
GREEN = {"indexed:42", "rgb:FFCCFFCC"}


def fill(c):
    f = c.fill
    if f is None or f.fill_type is None:
        return None
    col = f.fgColor
    v = col.rgb if col.type == "rgb" else (col.theme if col.type == "theme" else col.indexed)
    return f"{col.type}:{v}"


def read_xlsx(path):
    m = re.search(r"\((\d{4})\.(\d{1,2})\.1\.\)", path.name)
    month = f"{m.group(1)}-{int(m.group(2)):02d}"
    wb = openpyxl.load_workbook(path, read_only=False)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows())
    hdr = [c.value for c in rows[0]]
    recs = []
    for i, r in enumerate(rows[1:], start=2):
        d = {h: (None if c.value is None else str(c.value).strip() or None) for h, c in zip(hdr, r)}
        d.update(xl_row=i, fill=fill(r[4]))
        recs.append(d)
    df = pd.DataFrame(recs)
    df["month"], df["sheet"], df["src"] = month, ws.title, path.name
    df["n_merged"] = len(ws.merged_cells.ranges)
    return df


def dump(con, name, sql):
    r = con.execute(sql)
    df = pd.DataFrame(r.fetchall(), columns=[d[0] for d in r.description])
    df.to_csv(OUT / f"{name}.csv", index=False)
    print(f"\n## {name}\n{df.head(20).to_string(index=False)}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    x = pd.concat([read_xlsx(p) for p in sorted(SRC.glob("*.xlsx"))], ignore_index=True)
    con = duckdb.connect()
    con.register("x0", x)
    con.execute("""create table x as select *,
        (업체명 is null and 상한금액 is null and not regexp_full_match(coalesce(제품코드, ''), '[0-9]{9}')) is_head,
        coalesce(fill in ('indexed:42', 'rgb:FFCCFFCC'), false) is_green
        from x0""")
    con.execute("""create table pq as select substr(기준일자, 1, 7) as month, *
        from read_parquet('%s/benefit_snapshot_table_2223.parquet', file_row_number = true)
        where substr(기준일자, 1, 7) in (select distinct month from x)""" % PQ.as_posix())

    # 1. 머리 행 판별: 구조 × 음영
    dump(con, "head_struct_vs_fill", """select month, is_head, is_green, coalesce(fill, '-') fill, count(*) n
        from x group by all order by 1, 2, 3, 4""")
    dump(con, "head_struct_fill_mismatch", """select month, xl_row, 연번, 주성분코드, 제품코드, 제품명, 업체명, 상한금액, fill
        from x where is_head <> is_green order by 1, 2""")
    # 2. 행 수와 파일명·시트명 건수
    dump(con, "row_counts", """select month, any_value(src) src, any_value(sheet) sheet, any_value(n_merged) n_merged,
        try_cast(replace(regexp_extract(any_value(src), '\\)\\(([0-9,]+)\\)', 1), ',', '') as int) n_filename,
        try_cast(replace(regexp_extract(any_value(sheet), '\\(([0-9,]+)\\)', 1), ',', '') as int) n_sheetname,
        count(*) filter (where not is_head) n_product, count(*) filter (where is_head) n_head,
        count(distinct 연번) filter (where not is_head) n_product_distinct_rowno,
        (select count(*) from pq where pq.month = x.month) n_parquet
        from x group by month order by 1""")
    dump(con, "rowno_duplicates", """select month, 연번, list(xl_row || ':' || coalesce(제품코드, '') || ':' || case when is_head then 'head' else 'product' end order by xl_row) rows_
        from x group by 1, 2 having count(*) > 1 order by 1, 2""")
    # 3. 제품 행 ↔ parquet (월, 제품코드). 연번은 고유 키가 아니다 (2023-11 원본에 같은 연번의 제품 행 3쌍)
    dump(con, "product_key_unique", """select month, count(*) n_product, count(distinct 제품코드) n_code from x where not is_head group by 1 order by 1""")
    con.execute("""create table runs as select *, sum(case when is_head and not coalesce(prev_head, false) then 1 else 0 end)
                       over (partition by month order by xl_row) run_id
                   from (select *, lag(is_head) over (partition by month order by xl_row) prev_head from x)""")
    con.execute("""create table rh as select month, run_id, count(*) n_heads, list(제품코드 order by xl_row) head_names,
                   list(distinct 주성분코드) head_ics from runs where is_head group by 1, 2""")
    con.execute("""create table xp as select r.*, rh.n_heads, rh.head_names, rh.head_ics from runs r join rh using (month, run_id)
                   where not r.is_head""")
    dump(con, "product_vs_parquet", """with j as (select x.*, p.연번 p_no, p.제품명 p_name, p.업체명 p_firm, p.상한금액 p_price, p.비고 p_note, p.주성분코드 p_ic
                   from xp x left join pq p on p.month = x.month and p.제품코드 = x.제품코드)
        select month, count(*) n_product, count(p_no) matched,
               count(*) filter (where p_no is not null and 주성분코드 is distinct from p_ic) diff_ic,
               count(*) filter (where p_no is not null and 제품명 is distinct from trim(p_name)) diff_name,
               count(*) filter (where p_no is not null and 업체명 is distinct from trim(p_firm)) diff_firm,
               count(*) filter (where p_no is not null and try_cast(상한금액 as double) is distinct from p_price) diff_price_num,
               count(*) filter (where p_no is not null and 상한금액 = '산정불가' and p_price is null and trim(p_note) = '산정불가' and 비고 is null) sanjung_moved,
               count(*) filter (where list_contains(head_ics, 주성분코드)) same_ic_as_head
        from j group by 1 order by 1""")
    dump(con, "parquet_only_rows", """select p.month, p.연번, p.주성분코드, p.제품코드, p.제품명, p.업체명, p.상한금액, p.주성분명 from pq p
        where not exists (select 1 from xp x where x.month = p.month and x.제품코드 = p.제품코드) order by 1, 2""")
    # 4. parquet 주성분명 ↔ 묶음 머리 행
    con.execute("""create table nm as select x.month, x.연번, x.주성분코드, x.제품코드, x.n_heads, x.head_names, trim(p.주성분명) pq_name,
                   list_position(x.head_names, trim(p.주성분명)) pos
                   from xp x join pq p on p.month = x.month and p.제품코드 = x.제품코드""")
    dump(con, "ingredient_name_vs_head", """select month, case when n_heads = 1 then 'single' else 'multi' end group_kind, count(*) n,
        count(*) filter (where pos = n_heads) eq_last_head_row, count(*) filter (where pos = 1 and n_heads > 1) eq_first_of_multi,
        count(*) filter (where pos > 1 and pos < n_heads) eq_middle, count(*) filter (where coalesce(pos, 0) = 0) not_in_group
        from nm group by 1, 2 order by 1, 2""")
    dump(con, "ingredient_name_exceptions", """select month, 연번, 주성분코드, 제품코드, pq_name, head_names,
        case when coalesce(pos, 0) = 0 then 'not_in_group' else 'first_of_multi' end kind
        from nm where coalesce(pos, 0) = 0 or (pos = 1 and n_heads > 1) order by 1, 2""")
    # 5. 2022-09 243001ATB 앞뒤
    dump(con, "anomaly_2022_09", """with a as (select xl_row r0 from x where month = '2022-09' and 주성분코드 = '243001ATB')
        select xl_row, 연번, 투여, 분류, 주성분코드, 제품코드, 제품명, 업체명, 규격, 단위, 상한금액, 전일, 비고,
               case when is_head then 'head' else 'product' end kind, coalesce(fill, '-') fill
        from x, a where month = '2022-09' and xl_row between r0 - 3 and r0 + 3 order by xl_row""")
    # 6. 형식이 바뀐 달 (parquet 48개월 연번 빈 번호로 추정 + 2025 CSV)
    dump(con, "format_by_month_parquet", """select substr(기준일자, 1, 7) as month, count(*) n, min(연번) min_no, max(연번) max_no,
        max(연번) - min(연번) + 1 - count(distinct 연번) rowno_gaps, count(주성분개수) has_n_ingredients
        from read_parquet('%s/benefit_snapshot_table_*.parquet', union_by_name = true) group by 1 order by 1""" % PQ.as_posix())
    csvs = sorted(glob.glob(str(SRC / "*_약제급여목록.csv")))
    dump(con, "format_2025_csv", " union all ".join(
        f"""select '{Path(f).name}' src, count(*) n, min(cast(연번 as int)) min_no, max(cast(연번 as int)) max_no,
                   count(*) filter (where nullif(trim(업체명), '') is null) empty_firm,
                   count(*) filter (where not regexp_full_match(trim(제품코드), '[0-9]{{9}}')) code_not9
            from read_csv('{f}', header = true, all_varchar = true)""" for f in csvs))


if __name__ == "__main__":
    main()
