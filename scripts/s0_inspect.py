"""S0 자료 점검 (구현 아님, 점검만). 결과 표는 outputs/s0/에 쓴다.

사용: python scripts/s0_inspect.py   (make s0)

비고 분류는 점검용이다. NOTE_RULES.md의 R1~R7 문형 정규식으로 토큰을 찾고,
토큰과 구분자(공백, 쉼표, ' / ')를 지운 뒤 남는 글자가 있으면 그 칸 조각을 `unresolved`로 둔다.
규칙을 새로 만들지 않는다. unresolved 문형은 나열만 한다.
"""
from __future__ import annotations

import csv
import datetime as dt
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

RAW = Path("data/raw")
OUT = Path("outputs/s0")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pbd.common import list_files, sql_files, usage_files  # noqa: E402  기본 연도만 (2026-11-19 사용자 결정)
BENEFIT = f"read_parquet({sql_files(list_files('benefit'))}, union_by_name=true)"
SUBST = f"read_parquet({sql_files(list_files('substitute'))}, union_by_name=true)"
USAGE = f"read_csv({sql_files(usage_files())}, header=true, all_varchar=true, filename=true)"
CODEMAP = "read_parquet('data/raw/master/dim_drug_code_map_26.parquet')"
DRUGINFO = "read_parquet('data/raw/master/dim_drug_info_26.parquet')"
ORANGE = "read_parquet('data/raw/orangebook/dim_orange_book.parquet')"

# 결측 자리표시 값 (원본 parquet에 문자열로 들어 있다)
NULLS = ("", "nan", "None", "NaN")


def blank(col: str) -> str:
    return f"(trim(coalesce(cast({col} as varchar),'')) in {NULLS})"


# ---------------------------------------------------------------- 비고 토큰 (점검용)
DATE = r"'?(?P<y{i}>\d{{4}}|\d{{2}})\.(?P<m{i}>\d{{1,2}})\.(?P<d{i}>\d{{1,2}})\.?"
_TOKEN_DEFS = [  # 우선순위 순서. 해제 문형을 정지 문형보다 먼저 본다
    ("R5", r"집행정지 해제 및 상한금액 인하\({D}\s*고시\)"),
    ("R6", r"급여정지 해제\({D}\)"),
    ("R3", r"급여정지\({D}\)"),
    ("R4", r"집행정지\({D}\)"),
    ("R1", r"{D}\s*등재"),
    ("R2", r"{D},?\s*\d+원"),
    ("R7", r"산정불가"),
]
TOKEN_RE = re.compile(
    "|".join(f"(?P<{t}>{p.format(D=DATE.format(i=t))})" for t, p in _TOKEN_DEFS)
)
DATE_ANY = re.compile(r"'?(\d{4}|\d{2})\.(\d{1,2})\.(\d{1,2})\.?")
SEP = re.compile(r"[\s,/]+")


def _mkdate(y: str, m: str, d: str) -> dt.date | None:
    yy = int(y) + (2000 if len(y) == 2 else 0)
    try:
        return dt.date(yy, int(m), int(d))
    except ValueError:
        return None


def scan_part(part: str) -> dict:
    """비고 칸의 ' / ' 조각 하나를 토큰으로 나눈다.

    반환: tokens = [(type, date|None)], residue = 토큰·구분자를 지우고 남은 글자,
    dates_in_residue = 남은 글자 속 날짜들.
    """
    tokens, spans = [], []
    for m in TOKEN_RE.finditer(part):
        t = m.lastgroup
        g = m.groupdict()
        d = None
        if g.get(f"y{t}"):
            d = _mkdate(g[f"y{t}"], g[f"m{t}"], g[f"d{t}"])
        tokens.append((t, d))
        spans.append(m.span())
    rest, last = [], 0
    for a, b in spans:
        rest.append(part[last:a])
        last = b
    rest.append(part[last:])
    residue = SEP.sub("", "".join(rest))
    rdates = []
    if residue:
        raw_rest = "".join(rest)
        rdates = [_mkdate(*g) for g in DATE_ANY.findall(raw_rest)]
    return {"tokens": tokens, "residue": residue, "dates_in_residue": [d for d in rdates if d]}


def scan_note(note: str) -> list[dict]:
    parts = [p.strip() for p in note.strip().split(" / ") if p.strip()]
    out = []
    for p in parts:
        r = scan_part(p)
        r["part"] = p
        out.append(r)
    return out


def pattern(s: str) -> str:
    return re.sub(r"\d", "9", s)


# ---------------------------------------------------------------- 출력 도우미
def write_csv(name: str, header: list[str], rows) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return p


def q(con, sql):
    con.execute(sql)
    return [d[0] for d in con.description], con.fetchall()


def dump(con, name, sql, show=0):
    h, rows = q(con, sql)
    write_csv(name, h, rows)
    print(f"\n### {name} ({len(rows)}행)")
    if show:
        print(" | ".join(h))
        for r in rows[:show]:
            print(" | ".join(str(x) for x in r))
    return h, rows


# ---------------------------------------------------------------- 1. 스냅샷 보유표
def snapshot_inventory(con):
    for lst, src, extra in [
        ("benefit", BENEFIT,
         f"""count(*) filter (where not {blank('목록정비전코드')}) prev_code_nonblank,
             count(*) filter (where 식약분류 is not null) mfds_class_nonnull,
             count(*) filter (where 주성분개수 is not null) n_ingr_nonnull,
             count(*) filter (where 분류 is null) class_null,
             count(*) filter (where not {blank('비고')}) note_nonblank"""),
        ("substitute", SUBST,
         f"""count(*) filter (where not {blank('목록정비전코드')}) prev_code_nonblank,
             count(*) filter (where "①주성분 및 투여제형코드" is not null) grp_code_spaced,
             count(*) filter (where "①주성분 및투여제형코드" is not null) grp_code_nospace,
             count(*) filter (where "생동대조약 여부" is not null) ref_spaced,
             count(*) filter (where "생동대조약여부" is not null) ref_nospace,
             count(*) filter (where not {blank('"비고(급여정지)"')}) note_nonblank"""),
    ]:
        dump(con, f"snapshot_inventory_{lst}.csv", f"""
            select substr(기준일자,1,7) ym, min(기준일자) base_date, count(distinct 기준일자) n_base_dates,
                   min(소스파일명) source_file, count(*) n_rows,
                   count(*) filter (where length(제품코드)=9) len9,
                   count(*) filter (where length(제품코드)=8) len8,
                   count(*) filter (where length(제품코드) not in (8,9)) len_other,
                   count(distinct lpad(제품코드,9,'0')) n_products,
                   count(*) - count(distinct lpad(제품코드,9,'0')) dup_product_rows,
                   count(*) filter (where 상한금액 is null) price_null,
                   {extra}
            from {src} group by 1 order by 1""")
        h, r = q(con, f"""with m as (select distinct substr(기준일자,1,7) ym from {src}),
            cal as (select strftime(d,'%Y-%m') ym from range(date '2022-01-01', date '2026-01-01', interval 1 month) t(d))
            select count(*) filter (where m.ym is null), string_agg(cal.ym, ',') filter (where m.ym is null)
            from cal left join m using(ym)""")
        print(f"{lst}: 2022-01~2025-12 중 없는 달 {r[0][0]}개 {r[0][1] or ''}")


# ---------------------------------------------------------------- 2. 사용통계 원본
def usage_raw(con):
    files = [Path(f) for f in usage_files()]
    headers = Counter(f.open(encoding="utf-8").readline().strip() for f in files)
    print(f"\n사용통계 파일 {len(files)}개, 헤더 종류 {len(headers)}")
    for h, n in headers.items():
        print(f"  {n}개: {h}")
    con.execute(f"create table u as select * from {USAGE}")
    dump(con, "usage_month_rows.csv", """
        select STD_YYYYMM, count(*) n_rows, count(distinct WK_COMPN_4) n_c4,
               regexp_extract(filename, 'cmpn_stat_[0-9]{4}\\(([^)]*)\\)', 1) release
        from u group by 1, 4 order by 1""")
    h, r = q(con, """with cal as (select strftime(d,'%Y%m') m from range(date '2018-01-01', date '2026-01-01', interval 1 month) t(d))
        select count(*) filter (where m not in (select distinct STD_YYYYMM from u)) from cal""")
    print(f"2018-01~2025-12 중 없는 달: {r[0][0]}")
    rows = []
    for c in ["AGE_G", "SEX_TYPE", "SIDO", "YOYANG_CLSFC_CD_ADJ", "OUT_IN_HOS"]:
        for v, n, a, b in con.execute(
                f"select {c}, count(*), min(STD_YYYYMM), max(STD_YYYYMM) from u group by 1 order by 1").fetchall():
            rows.append((c, v, n, a, b))
    write_csv("usage_code_values.csv", ["column", "value", "n_rows", "first_month", "last_month"], rows)
    print("\n### usage_code_values.csv")
    for c in ["AGE_G", "SEX_TYPE", "SIDO", "YOYANG_CLSFC_CD_ADJ", "OUT_IN_HOS"]:
        print(f"  {c}: " + ", ".join(f"{v}({a}~{b})" if (a, b) != ("201801", "202512") else str(v)
                                     for cc, v, n, a, b in rows if cc == c))
    dump(con, "usage_checks.csv", """
        select count(*) n_rows,
          (select count(*) from (select 1 from u group by STD_YYYYMM,WK_COMPN_4,AGE_G,SEX_TYPE,SIDO,YOYANG_CLSFC_CD_ADJ,OUT_IN_HOS having count(*)>1)) dup_keys,
          count(*) filter (where length(WK_COMPN_4)<>4) c4_not4,
          count(*) filter (where position(STD_YYYYMM in filename)=0) month_ne_filename,
          count(*) filter (where try_cast(PRSCRPTN_TNDN_CNT as double) is null or try_cast(PRSCRPTN_AMT as double) is null
                            or try_cast(PATIENT_CNT as double) is null or try_cast(PRSCRPTN_TOT_USE as double) is null) nonnumeric,
          count(*) filter (where cast(PRSCRPTN_TNDN_CNT as double)<=0) cnt_le0,
          count(*) filter (where cast(PRSCRPTN_AMT as double)<=0) amt_le0,
          count(*) filter (where cast(PRSCRPTN_TOT_USE as double)<=0) qty_le0,
          min(cast(PATIENT_CNT as bigint)) min_patient,
          count(*) filter (where cast(PATIENT_CNT as bigint)<=2) patient_le2,
          min(cast(PRSCRPTN_TNDN_CNT as bigint)) min_cnt
        from u""", show=1)


# ---------------------------------------------------------------- 3. 원본 → 가공본 대조
def usage_vs_fact(con):
    con.execute("""create table fact as select * from read_parquet('data/sample/fact_cmpn_stat_*.parquet')""")
    dump(con, "usage_vs_fact_totals.csv", """
        with r as (select left(STD_YYYYMM,4) y,
                    sum(cast(PRSCRPTN_TNDN_CNT as bigint)) cnt, sum(cast(PRSCRPTN_AMT as bigint)) amt,
                    sum(cast(PRSCRPTN_TOT_USE as decimal(20,2))) qty, sum(cast(PATIENT_CNT as bigint)) pat,
                    count(distinct (STD_YYYYMM, WK_COMPN_4, OUT_IN_HOS)) keys
                   from u where STD_YYYYMM between '202301' and '202512' group by 1),
             f as (select left(STD_YYYYMM,4) y, sum(PRSCRPTN_TNDN_CNT) cnt, sum(PRSCRPTN_AMT) amt,
                    sum(cast(PRSCRPTN_TOT_USE as decimal(20,2))) qty, sum(PATIENT_CNT) pat, count(*) keys from fact group by 1)
        select y, r.keys raw_keys, f.keys fact_rows, r.cnt raw_cnt, f.cnt fact_cnt, r.amt raw_amt, f.amt fact_amt,
               r.qty raw_qty, f.qty fact_qty, r.pat raw_patient_sum, f.pat fact_patient_sum
        from r join f using(y) order by y""", show=3)
    dump(con, "usage_vs_fact_keydiff.csv", """
        with r as (select STD_YYYYMM, WK_COMPN_4, OUT_IN_HOS, sum(cast(PRSCRPTN_AMT as bigint)) amt,
                          sum(cast(PRSCRPTN_TOT_USE as decimal(20,2))) qty
                   from u where STD_YYYYMM between '202301' and '202512' group by all)
        select count(*) filter (where f.STD_YYYYMM is null) only_raw, count(*) filter (where r.STD_YYYYMM is null) only_fact,
               count(*) filter (where r.amt <> f.PRSCRPTN_AMT or r.qty <> cast(f.PRSCRPTN_TOT_USE as decimal(20,2))) value_diff
        from r full join fact f using (STD_YYYYMM, WK_COMPN_4, OUT_IN_HOS)""", show=1)


# ---------------------------------------------------------------- 4. 성분별 관측 연속성
def continuity(con):
    for scope, cond in [("all", "true"), ("out", "OUT_IN_HOS='OUT'")]:
        con.execute(f"""create or replace table cont_{scope} as
            with m as (select WK_COMPN_4 c4, STD_YYYYMM ym from u where {cond} group by 1,2),
            idx as (select c4, ym, (cast(left(ym,4) as int)-2018)*12 + cast(right(ym,2) as int) mi from m)
            select c4, count(*) n_months, min(ym) first_month, max(ym) last_month,
                   max(mi)-min(mi)+1-count(*) gap_months from idx group by 1""")
        dump(con, f"usage_continuity_summary_{scope}.csv", f"""
            select case when n_months=96 then '1 96개월 전부'
                        when first_month>'201801' and last_month='202512' and gap_months=0 then '2 중간에 나타남(이후 연속)'
                        when first_month='201801' and last_month<'202512' and gap_months=0 then '3 중간에 사라짐(이전 연속)'
                        when gap_months>0 then '4 중간에 빈 달 있음'
                        else '5 나타났다 사라짐(연속)' end category,
                   count(*) n_c4,
                   count(*) filter (where first_month>'201801') appeared_after_201801,
                   count(*) filter (where last_month<'202512') last_before_202512
            from cont_{scope} group by 1 order by 1""", show=6)
        dump(con, f"usage_continuity_{scope}.csv", f"select * from cont_{scope} order by c4")


# ---------------------------------------------------------------- 5. 2026 속성표 연결률
def attr_link(con):
    dump(con, "attr_tables.csv", f"""
        select 'dim_drug_code_map_26' tbl, count(*) n_rows, count(distinct "제품코드(개정후)") n_key,
               'distinct 제품코드(개정후)' key, count(*) filter (where "제품코드(개정후)" is null) key_null from {CODEMAP}
        union all select 'dim_drug_info_26', count(*), count(distinct 품목기준코드), 'distinct 품목기준코드',
               count(*) filter (where 품목기준코드 is null) from {DRUGINFO}
        union all select 'dim_orange_book', count(*), count(distinct 기준코드), 'distinct 기준코드',
               count(*) filter (where 기준코드 is null) from {ORANGE}""", show=3)
    con.execute(f"""create table cm as select distinct lpad(cast(cast("제품코드(개정후)" as bigint) as varchar),9,'0') pc,
                    품목기준코드 item from {CODEMAP} where "제품코드(개정후)" is not null""")
    h, r = q(con, "select count(*) - count(distinct pc) from cm")
    print(f"code_map: 제품코드 하나에 품목기준코드가 2개 이상인 초과 행 {r[0][0]}")
    dump(con, "attr_link_by_year.csv", f"""
        with bp as (select distinct left(기준일자,4) y, lpad(제품코드,9,'0') pc from {BENEFIT}),
        lk as (select bp.y, bp.pc, min(cm.item) item from bp left join cm using(pc) group by 1,2)
        select y, count(*) n_products,
               count(item) link_code_map, round(100.0*count(item)/count(*),1) pct_code_map,
               count(*) filter (where item in (select 품목기준코드 from {DRUGINFO})) link_drug_info,
               round(100.0*count(*) filter (where item in (select 품목기준코드 from {DRUGINFO}))/count(*),1) pct_drug_info,
               count(*) filter (where item in (select 기준코드 from {ORANGE})) link_orange,
               round(100.0*count(*) filter (where item in (select 기준코드 from {ORANGE}))/count(*),1) pct_orange
        from lk group by 1 order by 1""", show=4)
    dump(con, "orangebook_values.csv", f"""
        select 'generic' col, coalesce(제네릭의약품,'NULL') v, count(*) n from {ORANGE} group by 2
        union all select 'ref_drug', coalesce(공고대조약,'NULL'), count(*) from {ORANGE} group by 2 order by 1,3 desc""", show=8)


# ---------------------------------------------------------------- 6. 비고 문형
def notes(con):
    rows = con.execute(f"""
        select 'benefit' lst, 기준일자, lpad(제품코드,9,'0') pc, trim(비고) note from {BENEFIT} where not {blank('비고')}
        union all
        select 'substitute', 기준일자, lpad(제품코드,9,'0'), trim("비고(급여정지)") from {SUBST} where not {blank('"비고(급여정지)"')}
    """).fetchall()
    note_pat = Counter()            # (lst, year, pattern, explained)
    note_ex = {}
    unres = defaultdict(list)       # (lst, part pattern) -> examples
    unres_n = Counter()
    unres_years = defaultdict(set)
    future = Counter()              # (lst, year, type)
    future_ex = defaultdict(list)
    tok_n = Counter()
    bad_dates = []
    for lst, base, pc, note in rows:
        y = base[:4]
        bd = dt.date.fromisoformat(base)
        parts = scan_note(note)
        explained = all(not p["residue"] for p in parts)
        k = (lst, y, pattern(note), "설명됨" if explained else "unresolved")
        note_pat[k] += 1
        note_ex.setdefault(k, note)
        for p in parts:
            for t, d in p["tokens"]:
                tok_n[(lst, t)] += 1
                if d is None and t != "R7":
                    bad_dates.append((lst, base, pc, p["part"]))
                if d and d > bd:
                    future[(lst, y, t)] += 1
                    if len(future_ex[(lst, t)]) < 5:
                        future_ex[(lst, t)].append(f"{base} {pc} {p['part']}")
            if p["residue"]:
                kk = (lst, pattern(p["part"]))
                unres_n[kk] += 1
                unres_years[kk].add(y)
                if len(unres[kk]) < 5:
                    unres[kk].append(f"{base} | {pc} | {p['part']}")
                for d in p["dates_in_residue"]:
                    if d > bd:
                        future[(lst, y, "unresolved")] += 1
                        if len(future_ex[(lst, "unresolved")]) < 5:
                            future_ex[(lst, "unresolved")].append(f"{base} {pc} {p['part']}")

    write_csv("note_pattern_by_year.csv", ["list", "year", "pattern", "status", "n_rows", "example"],
              sorted(((*k, n, note_ex[k]) for k, n in note_pat.items()), key=lambda r: (r[0], r[1], -r[4])))
    write_csv("note_unresolved_patterns.csv", ["list", "part_pattern", "n_parts", "years", "ex1", "ex2", "ex3", "ex4", "ex5"],
              sorted(((lst, pat, unres_n[(lst, pat)], ",".join(sorted(unres_years[(lst, pat)])),
                       *(unres[(lst, pat)] + [""] * 5)[:5]) for lst, pat in unres), key=lambda r: (r[0], -r[2])))
    write_csv("note_future_by_type.csv", ["list", "year", "type", "n_tokens"],
              sorted((*k, n) for k, n in future.items()))
    write_csv("note_future_examples.csv", ["list", "type", "examples"],
              sorted((lst, t, " || ".join(ex)) for (lst, t), ex in future_ex.items()))
    write_csv("note_token_counts.csv", ["list", "type", "n_tokens"], sorted((*k, n) for k, n in tok_n.items()))
    write_csv("note_bad_dates.csv", ["list", "base_date", "product_code", "part"], bad_dates)

    tot = Counter()
    for (lst, y, pat, st), n in note_pat.items():
        tot[(lst, st)] += n
    print("\n### 비고 있는 행 (설명됨 / unresolved)")
    for k in sorted(tot):
        print(f"  {k[0]} {k[1]}: {tot[k]}")
    print(f"\n### unresolved 조각 문형 {len(unres)}종 (note_unresolved_patterns.csv)")
    for (lst, pat), n in sorted(unres_n.items(), key=lambda x: (x[0][0], -x[1]))[:20]:
        print(f"  {lst} | {n} | {pat}")
    print("\n### 미래 날짜 토큰 (note_future_by_type.csv)")
    agg = Counter()
    for (lst, y, t), n in future.items():
        agg[(lst, t)] += n
    for k in sorted(agg):
        print(f"  {k[0]} {k[1]}: {agg[k]}")
    print(f"\n날짜를 읽지 못한 토큰: {len(bad_dates)}")


# ---------------------------------------------------------------- 7. 약효분류
def drug_class(con):
    dump(con, "class_code_check.csv", f"""
        with a as (select lpad(제품코드,9,'0') pc, 분류 c from {BENEFIT} where 기준일자 like '2022-10%'),
             b as (select lpad(제품코드,9,'0') pc, 식약분류 c from {BENEFIT} where 기준일자 like '2022-11%'),
             o as (select 분류, 식약분류 from {BENEFIT} where 기준일자 like '2025-10%')
        select '2022-10 분류 vs 2022-11 식약분류 (같은 제품)' cmp, count(*) n, count(*) filter (where a.c=b.c) same from a join b using(pc)
        union all select '2025-10 같은 행 분류 vs 식약분류', count(*), count(*) filter (where 분류=식약분류) from o""", show=2)
    dump(con, "class_code_top50.csv", f"""
        with t as (select lpad(제품코드,9,'0') pc, substr(기준일자,1,7) ym,
                          cast(cast(coalesce(분류, case when 기준일자 like '2022-11%' then 식약분류 end) as int) as varchar) cls
                   from {BENEFIT})
        select cls, count(distinct pc) n_products, count(*) n_product_months,
               count(distinct pc) filter (where ym='2025-12') n_products_2025_12
        from t where cls is not null group by 1 order by 2 desc limit 50""", show=20)


def main() -> None:
    import duckdb
    con = duckdb.connect()
    con.execute("SET threads=8")
    print("## 1. 스냅샷 보유표"); snapshot_inventory(con)
    print("\n## 2. 사용통계 원본"); usage_raw(con)
    print("\n## 3. 원본 → 가공본 대조 (2023~2025)"); usage_vs_fact(con)
    print("\n## 4. 성분별 관측 연속성"); continuity(con)
    print("\n## 5. 2026 속성표 연결률"); attr_link(con)
    print("\n## 6. 비고 문형"); notes(con)
    print("\n## 7. 약효분류"); drug_class(con)


if __name__ == "__main__":
    main()
