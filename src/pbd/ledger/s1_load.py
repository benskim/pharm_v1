"""P1 S1: L1 정규화 테이블 (docs/PLAN_P1.md §4).

snapshot_inventory, stg_benefit, stg_substitute, stg_usage(+ usage_c4_month 관측 격자),
note_token, attr_2026_product, attr_2026_item 을 data/interim/pbd.duckdb에 만든다.

- 컬럼 대응은 config/schema_map.yaml. 후보 열 목록을 앞에서부터 coalesce 한다.
- 결측 자리표시('', 'nan', 'None', 'NaN')는 NULL. 코드는 문자열.
- 제품코드(DATA.md §4, DECISIONS 2026-11-14): 숫자 8자리만 앞에 0을 채워 9자리로 만든다. 7자리 이하는 채우지 않는다.
  0 채움 후 숫자 9자리면 row_kind = 'product_candidate', 그 밖은 'non_product'.
  non_product 행은 stg에 그대로 두고(원본 행 수 = stg 행 수), note_token·이후 원장에서 뺀다 (outputs/audit/dropped_s1_non_product.csv).
- 원본 행은 버리지 않는다 (원본 행 수 = stg 행 수). 버린 행이 생기면 outputs/audit/dropped_s1_*.csv.
- 사용통계는 원본 단위 그대로. 원본에 행이 없는 칸은 0이 아니라 미관측 (usage_c4_month.observed_*).

사용: PYTHONPATH=src python -m pbd.ledger.s1_load   (make p1-s1)
"""
from __future__ import annotations

import csv
import glob
from pathlib import Path

import pandas as pd

from pbd.common import AUDIT, RAW, ROOT, connect, list_files, load_yaml, qident, sql_files, usage_files
from pbd.ledger.notes import parse_note

SCHEMA = load_yaml("schema_map.yaml")
PARAMS = load_yaml("params.yaml")
NULLS = tuple(SCHEMA.get("null_tokens", ["", "nan", "None", "NaN"]))

# 출력 컬럼과 종류. 후보 원본 열은 schema_map.yaml
SPEC = {
    "benefit": [
        ("row_no", "int"), ("product_code", "pcode"), ("ingredient_code", "text"), ("ingredient_name", "text"),
        ("product_name", "text"), ("firm", "text"), ("route", "text"), ("class_code", "int"),
        ("mfds_class", "int"), ("n_ingredients", "int"), ("spec", "text"), ("unit", "text"),
        ("price", "double"), ("rx_otc", "text"), ("note", "text"), ("prev_code", "code9"),
        ("base_date", "date"), ("source_file", "text"),
    ],
    "substitute": [
        ("row_no", "int"), ("product_code", "pcode"), ("ingredient_code", "text"), ("ingredient_name", "text"),
        ("product_name", "text"), ("firm", "text"), ("route", "text"), ("class_code", "int"),
        ("group_code", "text"), ("item_code", "code9"), ("rep_code", "code13"), ("prev_code", "code9"),
        ("spec", "text"), ("unit", "text"), ("price", "double"), ("rx_otc", "text"), ("sort_order", "int"),
        ("ref_drug", "flag"), ("bioeq", "flag"), ("bioeq_raw", "text"), ("bioeq_date", "text"),
        ("equiv", "text"), ("note", "text"), ("base_date", "date"), ("source_file", "text"),
    ],
}
# 같은 원본 열을 다른 이름으로도 내보내는 것
ALIAS_SOURCE = {"bioeq_raw": "bioeq"}
SQLTYPE = {"pcode": "VARCHAR", "text": "VARCHAR", "int": "BIGINT", "double": "DOUBLE", "code9": "VARCHAR", "code13": "VARCHAR",
           "date": "DATE", "flag": "BOOLEAN"}


def _text(col: str) -> str:
    c = f"trim(cast({qident(col)} as varchar))"
    return f"(case when {c} in {NULLS} then null else {c} end)"


def _pcode(expr: str) -> str:
    """제품코드: 문자열 그대로. 숫자 8자리만 앞에 0 하나를 채운다."""
    return f"(case when regexp_full_match({expr}, '[0-9]{{8}}') then '0' || {expr} else {expr} end)"


def _code(expr: str, width: int) -> str:
    """실수형으로 저장된 코드('645101520.0', 8806519003301.0): '.0'을 떼고, 한 자리 모자랄 때만 0을 채운다.
    숫자로 읽을 수 없으면 원문을 둔다."""
    num = f"cast(try_cast(try_cast({expr} as double) as bigint) as varchar)"
    return f"coalesce(case when length({num}) = {width - 1} then '0' || {num} else {num} end, {expr})"


def _typed(expr: str, kind: str) -> str:
    if kind == "text":
        return expr
    if kind == "int":
        return f"try_cast(try_cast({expr} as double) as bigint)"
    if kind == "double":
        return f"try_cast({expr} as double)"
    if kind == "pcode":
        return _pcode(expr)
    if kind == "code9":
        return _code(expr, 9)
    if kind == "code13":
        return _code(expr, 13)
    if kind == "date":
        return f"cast({expr} as date)"
    if kind == "flag":
        return f"coalesce({expr} like '○%', false)"
    raise ValueError(kind)


def _columns(con, path) -> list[str]:
    return [r[0] for r in con.execute(f"describe select * from read_parquet('{Path(path).as_posix()}')").fetchall()]


def _select_for_file(con, lst: str, path) -> str:
    present = set(_columns(con, path))
    m = SCHEMA[lst]
    parts = []
    for out, kind in SPEC[lst]:
        cands = [c for c in m.get(ALIAS_SOURCE.get(out, out), []) if c in present]
        text = "coalesce(" + ", ".join(_text(c) for c in cands) + ")" if cands else "cast(null as varchar)"
        parts.append(f"cast({_typed(text, kind)} as {SQLTYPE[kind]}) as {out}")
        if out == "product_code":
            parts.append(f"cast({text} as varchar) as product_code_raw")
    p = Path(path)
    return (f"select {', '.join(parts)}, '{p.name}' as src_file, file_row_number as src_row "
            f"from read_parquet('{p.as_posix()}', file_row_number=true)")


def build_stg_snapshot(con, lst: str, files) -> None:
    sql = " union all by name ".join(_select_for_file(con, lst, f) for f in files)
    con.execute(f"""create or replace table stg_{lst} as
        -- 적용 기준일 = 그 달 1일 (사용자 결정 2026-11-15). 원본 기준일자 칸은 base_date_raw로 보존하고 판정에 쓰지 않는다
        select strftime(base_date, '%Y-%m') as list_month, * replace (cast(date_trunc('month', base_date) as date) as base_date),
               base_date as base_date_raw, substr(ingredient_code, 1, 4) as c4,
               case when regexp_full_match(product_code, '[0-9]{{9}}') then 'product_candidate' else 'non_product' end as row_kind
        from ({sql}) order by list_month, product_code, src_file, src_row""")


def create_empty_stg(con, lst: str) -> None:
    cols = ", ".join(f"cast(null as {SQLTYPE[k]}) as {o}" for o, k in SPEC[lst])
    con.execute(f"""create or replace table stg_{lst} as select cast(null as varchar) list_month, {cols},
        cast(null as varchar) product_code_raw, cast(null as varchar) src_file, cast(null as bigint) src_row, cast(null as date) base_date_raw,
        cast(null as varchar) c4, cast(null as varchar) row_kind limit 0""")


# ---------------------------------------------------------------- note_token
def build_note_token(con) -> None:
    notes = [r[0] for r in con.execute(
        "select distinct note from stg_benefit where note is not null union select distinct note from stg_substitute where note is not null"
    ).fetchall()]
    rows = []
    for n in sorted(notes):
        for t in parse_note(n):
            rows.append((n, t["seq"], t["part"], t["type"], t["variant"],
                         t["date"].isoformat() if t["date"] else None,
                         t["date_end"].isoformat() if t["date_end"] else None, t["amount"], t["raw"]))
    df = pd.DataFrame(rows, columns=["note", "seq", "part", "token_type", "variant", "d", "d_end", "amount", "raw"])
    df["amount"] = df["amount"].astype("Int64")
    con.register("note_parse_df", df)
    con.execute("""create or replace table note_parse as select note, cast(seq as int) seq, cast(part as int) part,
        token_type, variant, cast(d as date) token_date, cast(d_end as date) token_date_end, cast(amount as bigint) amount, raw
        from note_parse_df""")
    con.unregister("note_parse_df")
    con.execute("""create or replace table note_token as
        with s as (select 'benefit' source_list, list_month, base_date, product_code, src_file, src_row, note from stg_benefit
                   where note is not null and row_kind = 'product_candidate'
                   union all
                   select 'substitute', list_month, base_date, product_code, src_file, src_row, note from stg_substitute
                   where note is not null and row_kind = 'product_candidate')
        select s.source_list, s.list_month, s.base_date, s.product_code, p.seq, p.part, p.token_type, p.variant,
               p.token_date, p.token_date_end, p.amount, p.raw, s.note as note_raw,
               p.token_date > s.base_date as is_future, p.token_date_end > s.base_date as is_future_end,
               -- 날짜가 그 달 2일~말일 (날짜 조건일 뿐 수정의 증거가 아니다. 판정에 쓰지 않음, P2 민감도용, 사용자 결정 2026-11-15)
               coalesce((strftime(p.token_date, '%Y-%m') = s.list_month and day(p.token_date) >= 2)
                        or (strftime(p.token_date_end, '%Y-%m') = s.list_month and day(p.token_date_end) >= 2), false) as in_month_dated,
               s.src_file, s.src_row
        from s join note_parse p using (note)
        order by s.source_list, s.list_month, s.product_code, s.src_file, s.src_row, p.seq""")


# ---------------------------------------------------------------- 사용통계
def build_stg_usage(con, csv_glob: str | list[str], sido_merge: dict) -> None:
    m = {k: v[0] for k, v in SCHEMA["usage_raw"].items()}
    case = " ".join(f"when '{k}' then '{v}'" for k, v in sido_merge.items())
    q = qident
    con.execute(f"""create or replace table stg_usage as
        select substr({q(m['month'])},1,4) || '-' || substr({q(m['month'])},5,2) as month,
               lpad({q(m['ingredient_c4'])}, 4, '0') as c4,
               cast({q(m['age_band'])} as int) as age_band,
               cast({q(m['sex'])} as int) as sex,
               {q(m['sido'])} as sido_raw,
               case {q(m['sido'])} {case} else {q(m['sido'])} end as sido,
               cast({q(m['inst_type'])} as int) as inst_type,
               {q(m['in_out'])} as in_out,
               cast({q(m['claim_cnt'])} as bigint) as claim_cnt,
               cast({q(m['claim_amt'])} as bigint) as claim_amt,
               cast({q(m['patient_cnt'])} as bigint) as patient_cnt,
               cast({q(m['claim_qty'])} as double) as claim_qty,
               true as observed,
               regexp_extract(filename, 'cmpn_stat_[0-9]{{4}}\\(([^)]*)\\)', 1) as release,
               regexp_extract(filename, '[^/]+$') as src_file
        from read_csv({sql_files(csv_glob) if isinstance(csv_glob, list) else repr(csv_glob)}, header=true, all_varchar=true, filename=true)
        order by month, c4, age_band, sex, sido_raw, inst_type, in_out""")


def build_usage_obs(con, start: str, end: str) -> None:
    """성분4 × 월 관측 격자. 원본에 행이 없는 달은 observed=false (0이 아니라 미관측)."""
    con.execute(f"""create or replace table usage_c4_month as
        with cal as (select strftime(d, '%Y-%m') as month from range(cast('{start}-01' as date), cast('{end}-01' as date) + interval 1 month, interval 1 month) t(d)),
        c as (select distinct c4 from stg_usage),
        o as (select c4, month, true any_, bool_or(in_out = 'OUT') out_ from stg_usage group by 1, 2),
        f as (select c4, min(month) first_any, min(month) filter (where in_out = 'OUT') first_out from stg_usage group by 1)
        select c.c4, cal.month, coalesce(o.any_, false) observed_any, coalesce(o.out_, false) observed_out,
               f.first_any first_observed_month, f.first_out first_observed_month_out
        from c cross join cal left join o using (c4, month) left join f using (c4)
        order by c.c4, cal.month""")


# ---------------------------------------------------------------- 보유표
# 원본 대조로 설명된 파일명 건수 차이 (사용자 결정 2026-11-14). 다른 달로 일반화하지 않는다.
#   ('benefit', '2023-11'): 파일명 건수 = 원본 제품 행의 고유 연번 수 (같은 연번의 제품 행 3쌍) → 중복 연번 수만큼 설명
EXPLAINED_ROWNO_DUP = {("benefit", "2023-11")}


S1_COMMENTS = {
    ("snapshot_inventory", "base_date"): "적용 기준일: 항상 그 달 1일 (사용자 결정 2026-11-15). 파일의 공개·수정 시점은 알 수 없다",
    ("snapshot_inventory", "base_date_raw"): "원본 기준일자 칸의 값 (참고). 판정에 쓰지 않는다. 1일이 아닌 달: 2023-04-08, 2023-09-05, 2025-03-04",
    ("snapshot_inventory", "filename_revision_date"): "원본 파일명의 수정일 ('_1.21.수정'). 참고만: 파일명 날짜는 수정이 있었다는 근거로도 없었다는 근거로도 쓰지 않는다",
    ("note_token", "base_date"): "적용 기준일: 그 달 1일 (snapshot_inventory.base_date와 같음)",
    ("note_token", "is_future"): "token_date > 그 달 1일 (적용 기준일 기준 판정)",
    ("note_token", "is_future_end"): "token_date_end > 그 달 1일 (R3b 종료일)",
    ("note_token", "in_month_dated"): "토큰 날짜(시작일 또는 종료일)가 그 달 2일~말일. 날짜 조건일 뿐 수정의 증거가 아니다. 판정에 쓰지 않고 P2 민감도(이 토큰을 뺀 결과)에만 쓴다",
}


def comment_s1(con) -> None:
    for (t, col), text in S1_COMMENTS.items():
        esc = text.replace("'", "''")
        con.execute(f"comment on column {t}.{col} is '{esc}'")


def build_snapshot_inventory(con, files: dict, start: str, end: str) -> None:
    pieces = []
    for lst, paths in files.items():
        for p in paths:
            cols = _columns(con, p)
            nonblank = ", ".join(f"case when count(*) filter (where not ({_text(c)} is null)) > 0 then '{c}' end" for c in cols)
            pieces.append(f"""select '{lst}' source_list, substr(cast(기준일자 as varchar),1,7) list_month,
                list_filter([{nonblank}], lambda x: x is not null) cols from read_parquet('{Path(p).as_posix()}') group by 2""")
    con.execute("create or replace temp table inv_cols as " + " union all ".join(pieces))
    agg = """count(*) n_rows, count(distinct product_code) n_products,
                 count(*) filter (where regexp_full_match(product_code_raw, '[0-9]{8}')) n_code_padded,
                 count(*) filter (where row_kind = 'non_product') n_non_product,
                 count(note) n_note_rows, count(*) filter (where price is null) n_price_null,
                 count(*) - count(distinct row_no) n_rowno_dup"""
    ex = ", ".join(f"('{a}', '{b}')" for a, b in sorted(EXPLAINED_ROWNO_DUP)) or "(null, null)"
    con.execute(f"""create or replace table snapshot_inventory as
        with cal as (select strftime(d, '%Y-%m') list_month from range(cast('{start}-01' as date), cast('{end}-01' as date) + interval 1 month, interval 1 month) t(d)),
        lists as (select unnest(['benefit', 'substitute']) source_list),
        s as (select 'benefit' source_list, list_month, min(base_date) base_date, min(base_date_raw) base_date_raw, count(distinct base_date_raw) n_base_dates,
                     min(source_file) source_file, {agg} from stg_benefit group by 2
              union all
              select 'substitute', list_month, min(base_date), min(base_date_raw), count(distinct base_date_raw), min(source_file), {agg}
              from stg_substitute group by 2),
        -- 원본 파일명에 적힌 건수와의 차이. 설명: 미래 날짜 등재 제품(BACKLOG X2) + non_product 행
        fr as (select source_list, list_month, count(distinct product_code) n_future_listing
               from note_token where token_type = 'R1' and is_future group by 1, 2)
        select lists.source_list, cal.list_month, s.list_month is null as missing, s.base_date, s.base_date_raw, s.n_base_dates, s.source_file,
               coalesce(s.n_rows, 0) n_rows, s.n_products, s.n_code_padded, s.n_non_product, s.n_note_rows, s.n_price_null, s.n_rowno_dup,
               try_cast(replace(regexp_extract(s.source_file, '\\(([0-9,]+)\\)', 1), ',', '') as bigint) n_rows_in_filename,
               s.n_rows - n_rows_in_filename as diff_vs_filename,
               coalesce(fr.n_future_listing, 0) n_future_listing,
               case when (lists.source_list, cal.list_month) in ({ex}) then s.n_rowno_dup else 0 end n_explained_other,
               diff_vs_filename - coalesce(fr.n_future_listing, 0) - s.n_non_product - n_explained_other as diff_unexplained,
               -- 파일명의 수정일 '_1.21.수정' (2022-01~2023-11 급여목록 일부). 참고만: 수정 여부·시점의 근거로 쓰지 않는다 (사용자 결정 2026-11-15)
               case when regexp_matches(s.source_file, '_[0-9]{{1,2}}\\.[0-9]{{1,2}}\\.\\s*수정') then
                    make_date(cast(year(s.base_date) as bigint)
                                - case when cast(regexp_extract(s.source_file, '_([0-9]{{1,2}})\\.([0-9]{{1,2}})\\.\\s*수정', 1) as int) > month(s.base_date) + 6 then 1 else 0 end,
                              cast(regexp_extract(s.source_file, '_([0-9]{{1,2}})\\.([0-9]{{1,2}})\\.\\s*수정', 1) as bigint),
                              cast(regexp_extract(s.source_file, '_([0-9]{{1,2}})\\.([0-9]{{1,2}})\\.\\s*수정', 2) as bigint)) end filename_revision_date,
               case when diff_vs_filename > 0 then 'rows_gt_filename' when diff_vs_filename < 0 then 'filename_gt_rows'
                    when diff_vs_filename = 0 then 'equal' end as diff_direction,
               (select list_sort(flatten(list(c.cols))) from inv_cols c where c.source_list = lists.source_list and c.list_month = cal.list_month) columns_nonblank
        from lists cross join cal left join s using (source_list, list_month) left join fr using (source_list, list_month)
        order by 1, 2""")


# ---------------------------------------------------------------- 2026 속성표 (시계열로 쓰지 않음)
def build_attr_2026(con, code_map, drug_info, orange) -> None:
    q = qident
    con.execute(f"""create or replace table attr_2026_product as
        select lpad(cast(cast({q('제품코드(개정후)')} as bigint) as varchar), 9, '0') product_code,
               lpad(cast(품목기준코드 as varchar), 9, '0') item_code,
               min(cast(대표코드 as varchar)) rep_code, count(distinct 표준코드) n_std_codes,
               min({q('일반명코드(성분명코드)')}) ingredient_code, min({q('국제표준코드(ATC코드)')}) atc,
               min(한글상품명) product_name, min(업체명) firm, min(전문일반구분) rx_otc,
               max(취소일자) cancel_date, max({q('양도양수적용(공고)일자')}) transfer_date, max(양도양수종료일자) transfer_end_date
        from read_parquet('{Path(code_map).as_posix()}') where {q('제품코드(개정후)')} is not null
        group by 1, 2 order by 1, 2""")
    con.execute(f"""create or replace table attr_2026_item as
        with di as (select lpad(cast(품목기준코드 as varchar), 9, '0') item_code, 제품명 di_product_name, 업체명 di_firm,
                           허가일 permit_date, {q('취소/취하')} cancel_status, {q('취소/취하일자')} cancel_date,
                           ATC코드 atc, 전문의약품 rx_class, 신약구분 new_drug
                    from read_parquet('{Path(drug_info).as_posix()}')),
             ob as (select lpad(cast(기준코드 as varchar), 9, '0') item_code, count(*) ob_rows,
                           coalesce(bool_or(공고대조약 = 'O'), false) ob_ref, coalesce(bool_or(제네릭의약품 = 'O'), false) ob_generic
                    from read_parquet('{Path(orange).as_posix()}') group by 1)
        select coalesce(di.item_code, ob.item_code) item_code, di.* exclude (item_code),
               di.item_code is not null in_drug_info, ob.item_code is not null in_orangebook,
               ob.ob_rows, ob.ob_ref, ob.ob_generic
        from di full join ob using (item_code) order by 1""")


# ---------------------------------------------------------------- 실행
def raw_files(lst: str) -> list[str]:
    """기본 연도(params data_years.lists, 2022~2025)의 스냅샷 파일만 (2026-11-19 사용자 결정)"""
    return list_files(lst)


def write_csv(path: Path, header, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main() -> None:
    con = connect()
    con.execute("SET threads=8")
    p1, p2 = PARAMS["p1"], PARAMS["p2"]
    files = {lst: raw_files(lst) for lst in ("benefit", "substitute")}
    for lst, fs in files.items():
        build_stg_snapshot(con, lst, fs)
        print(f"stg_{lst}: {con.execute(f'select count(*) from stg_{lst}').fetchone()[0]:,}행")
    build_note_token(con)
    print(f"note_token: {con.execute('select count(*) from note_token').fetchone()[0]:,}개")
    build_snapshot_inventory(con, files, p1["start_month"], p1["end_month"])
    comment_s1(con)
    build_stg_usage(con, usage_files(), SCHEMA["sido_merge"])   # 기본 연도 2018~2025만 (2026-11-19 사용자 결정)
    print(f"stg_usage: {con.execute('select count(*) from stg_usage').fetchone()[0]:,}행")
    build_usage_obs(con, p2["start_month"], p2["end_month"])
    build_attr_2026(con, RAW / "master/dim_drug_code_map_26.parquet", RAW / "master/dim_drug_info_26.parquet",
                    RAW / "orangebook/dim_orange_book.parquet")
    # 버린 행: stg에서는 버리지 않는다. non_product 행은 note_token과 이후 원장에서 뺀다.
    rows, n_tok = [], 0
    for lst in ("benefit", "substitute"):
        for r in con.execute(f"""select list_month, src_file, src_row, product_code_raw, product_name, firm, note
                                 from stg_{lst} where row_kind = 'non_product' order by 1, 2, 3""").fetchall():
            k = len(parse_note(r[6]))
            n_tok += k
            rows.append((lst, *r[:6], k, "non_product: 0 채움 후 숫자 9자리가 아닌 제품코드. note_token·원장 제외"))
    write_csv(AUDIT / "dropped_s1_non_product.csv",
              ["source_list", "list_month", "src_file", "src_row", "product_code_raw", "product_name", "firm",
               "n_tokens_excluded", "reason"], rows)
    print(f"non_product 행 {len(rows)}개, note_token에서 뺀 토큰 {n_tok}개")
    for t in ("stg_benefit", "stg_substitute", "stg_usage"):
        write_csv(AUDIT / f"dropped_s1_{t}.csv", ["source", "reason", "n_rows"], [])
    # 숫자로 읽지 못해 원문을 그대로 둔 코드 (조용히 바꾸지 않는다)
    rows = []
    for t, cols in (("stg_benefit", ["product_code", "prev_code"]), ("stg_substitute", ["product_code", "item_code", "rep_code", "prev_code"])):
        for c in cols:
            n = con.execute(f"select count(*) from {t} where {c} is not null and not regexp_full_match({c}, '[0-9]+')").fetchone()[0]
            rows.append((t, c, n))
    write_csv(AUDIT / "s1_code_non_numeric.csv", ["table", "column", "n_rows"], rows)
    con.execute("checkpoint")
    con.close()
    print(f"DB: {(ROOT / 'data/interim/pbd.duckdb').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
