"""사용자 직접 검토용 근거 문서 outputs/validation/review10_evidence.md (사용자 지시 2026-11-15).

판정·의견·label_note 없이 data/raw 원본 값만 적는다. 행마다 ledger_claim 한 줄, 기준 달의 앞뒤 1개월을 포함한 3개월의
급여목록·대체조제 목록 원본 행 (파일 경로, 원본 파일명, 제품코드, 업체명, 제품명, 주성분코드, 품목기준코드, 상한금액, 비고 원문).
7·9·16·59행은 같은 품목기준코드의 다른 제품코드 행과, 같은 주성분코드 + 같은 업체의 다른 제품코드 행도 넣는다.
품목기준코드: 대체조제 목록 ②품목기준코드, 없으면 의약품 마스터(data/raw/master/dim_drug_code_map_26.parquet, 2026 속성표).
사용: python -I scripts/build_review10.py
"""
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
ROWS = [28, 29, 31, 7, 9, 16, 22, 35, 2, 59]
RELATED = {7, 9, 16, 59}
OUT = ROOT / "outputs/validation/review10_evidence.md"


def main():
    s = pd.read_csv(ROOT / "outputs/validation/sample_60.csv", dtype=str, keep_default_na=False).set_index("sample_id")
    c = duckdb.connect()
    files = {"B": sorted((RAW / "benefit").glob("benefit_snapshot_table_*.parquet")),
             "S": sorted((RAW / "substitute").glob("substitute_snapshot_table_*.parquet"))}
    parts = []
    for f in files["B"]:
        parts.append(f"""select '급여목록' list, '{f.relative_to(ROOT).as_posix()}' path, 소스파일명 src, substr(기준일자, 1, 7) ym,
            lpad(cast(제품코드 as varchar), 9, '0') pc, 업체명 firm, 제품명 nm, 주성분코드 gc, cast(null as varchar) item_sub,
            상한금액 price, 비고 note from read_parquet('{f.as_posix()}')""")
    for f in files["S"]:
        parts.append(f"""select '대체조제' list, '{f.relative_to(ROOT).as_posix()}' path, 소스파일명 src, substr(기준일자, 1, 7) ym,
            lpad(cast(제품코드 as varchar), 9, '0') pc, 업체명 firm, 제품명 nm, 주성분코드 gc,
            cast(cast("②품목기준코드" as bigint) as varchar) item_sub, 상한금액 price, "비고(급여정지)" note
            from read_parquet('{f.as_posix()}')""")
    c.execute("create table r as " + " union all by name ".join(parts))
    c.execute(f"""create table cm as select distinct lpad(cast(cast("제품코드(개정후)" as bigint) as varchar), 9, '0') pc, cast(품목기준코드 as varchar) item
        from read_parquet('{(RAW / "master/dim_drug_code_map_26.parquet").as_posix()}') where "제품코드(개정후)" is not null""")
    c.execute("""create table it as select pc, min(item) item from (select pc, item_sub item from r where item_sub is not null
                 union select pc, item from cm) group by pc""")
    months = [m for (m,) in c.execute("select distinct ym from r order by 1").fetchall()]

    def window(m):
        i = months.index(m)
        return months[max(0, i - 1): i + 2]

    def table(where, args, ms):
        q = f"""select r.list, r.ym, r.pc, r.firm, r.nm, r.gc, coalesce(r.item_sub, it.item) item, r.price, coalesce(r.note, '') note,
                       r.path, r.src from r left join it on it.pc = r.pc
                where ({where}) and r.ym in ({", ".join("?" for _ in ms)}) order by r.pc, r.ym, r.list"""
        df = c.execute(q, list(args) + list(ms)).df()
        if df.empty:
            return "_(해당 행 없음)_\n"
        lines = ["| 목록 | 달 | 제품코드 | 업체명 | 제품명 | 주성분코드 | 품목기준코드 | 상한금액 | 비고 원문 | 파일 경로 | 원본 파일명 |",
                 "|---|---|---|---|---|---|---|---|---|---|---|"]
        for x in df.itertuples(index=False):
            cells = [x.list, x.ym, x.pc, x.firm, x.nm, x.gc, x.item or "", "" if pd.isna(x.price) else f"{x.price:g}",
                     str(x.note).replace("|", "\\|"), x.path, x.src]
            lines.append("| " + " | ".join(str(v).replace("\n", " ") for v in cells) + " |")
        return "\n".join(lines) + "\n"

    out = ["# 직접 검토용 원본 값 (10행)", "",
           "판정·의견 없이 `data/raw` 원본 값만 적었다. 행마다 원장 주장(ledger_claim)과, 기준 달(`month`)의 앞뒤 1개월을 포함한 3개월의 원본 행.",
           "- 품목기준코드: 대체조제 목록 `②품목기준코드`, 급여목록 행은 같은 제품코드의 대체조제 목록 값이나 의약품 마스터(2026 속성표) 값.",
           "- 원본에 없는 달은 행이 없다. 비고 원문의 `|`는 `\\|`로 적었다.",
           f"- 만든 스크립트: `scripts/build_review10.py` (같은 입력이면 같은 문서).", ""]
    for sid in ROWS:
        r = s.loc[str(sid)]
        ms = window(r["month"])
        out += [f"## {sid}행 `{r['record_id']}`", "", f"- ledger_claim: `{r['ledger_claim']}`",
                f"- 기준 달 {r['month']} (원장 확인 달 {r['look_month']}), 표시한 달: {', '.join(ms)}", ""]
        if r["record_type"] == "signal" and r["look_month"] not in ms:
            ms = ms + [r["look_month"]]
        out += ["### 이 제품", "", table("r.pc = ?", [r["product_code"]], ms)]
        for lst in ("급여목록", "대체조제"):
            have = {m for (m,) in c.execute("select distinct ym from r where pc = ? and list = ?", [r["product_code"], lst]).fetchall()}
            miss = [m for m in ms if m not in have]
            out.append(f"- {lst}에 이 제품코드의 행이 없는 달: {', '.join(miss) if miss else '없음'}")
        out.append("")
        if sid in RELATED:
            item = c.execute("select item from it where pc = ?", [r["product_code"]]).fetchone()
            item = item[0] if item else None
            out += [f"### 같은 품목기준코드({item or '없음'})의 다른 제품코드", ""]
            out += [table("r.pc <> ? and coalesce(r.item_sub, it.item) = ?", [r["product_code"], item], ms) if item else "_(품목기준코드 없음)_\n"]
            gcf = c.execute("select gc, firm from r where pc = ? and ym in (" + ", ".join("?" for _ in ms) + ") limit 1",
                            [r["product_code"], *ms]).fetchone()
            out += [f"### 같은 주성분코드({gcf[0]}) + 같은 업체({gcf[1]})의 다른 제품코드", "",
                    table("r.pc <> ? and r.gc = ? and r.firm = ?", [r["product_code"], gcf[0], gcf[1]], ms)]
        out.append("")
    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"→ {OUT} ({len(ROWS)}행)")


if __name__ == "__main__":
    main()
