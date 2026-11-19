"""v5.3 새 검증 표본 30건 — 라벨 화면만 만든다 (2026-11-19 사용자 지시 C, 독립 검토 A·B 반영). 채점은 하지 않는다.

- v5.3 원장(signal_v5, ev_v5)에서 층마다 md5(seed || 기록 id) 순으로 뽑는다. 시드는 config/params.yaml `seed`.
- 기존 holdout_30·sample_60 제품(그리고 이어받기 상대 코드)은 뺀다. 한 제품은 한 번만.
- 층(합 30): 정시 실현 6, 변경·지연 실현 4, 급여중지 관련 4, 양도양수·코드 변경·교체 등재 6(종류별 2), 급여삭제 3, 중지해제 2,
  비고 미기재 약가 변경 3, 신규·추가 등재 2. (층 '급여중지 관련'은 key 값이라 그대로, 문서 표기는 급여정지·중지 관련)
- 월중 날짜 최소 8건: 기준월 전후 3개월 두 목록의 비고에 1일이 아닌 날짜가 있는 행. 먼저 층마다 md5 순으로 뽑고, 8건이 안 되면
  층 순서대로 각 층에서 md5 순 마지막의 월중 날짜가 아닌 행을, 그 층에서 아직 안 뽑힌 md5 순 첫 월중 날짜 행으로 바꾼다(결정론적).
- 라벨 화면(holdout_v53.blind.csv)에는 원장 판정값과 규칙 용어를 넣지 않는다. 넣는 것: 제품코드, 기준월, 볼 것(평이한 말),
  전후 3개월 두 목록 원문(업체, 제품명, 규격, 금액, 비고), 같은 허가 코드(기간 제한 없음), 같은 성분 후보(전후 3개월 안에 처음·마지막으로 실림).
- key(holdout_v53.key.csv)는 git에서 뺀다(.gitignore의 *.csv). 라벨을 마칠 때까지 열지 않는다.
- 라벨 파일이 이미 있고 정답 칸이 채워졌으면 덮어쓰지 않는다.
사용: python -I scripts/holdout_v53.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "outputs/validation"
BLIND, KEY = VAL / "holdout_v53.blind.csv", VAL / "holdout_v53.key.csv"
WIN = 3
MID_MIN = 30 - 22   # 최소 8건
TRUTH = ("truth_event", "truth_date", "truth_note")
EVENTS = ("예고대로 시행", "다른 금액·늦게 시행", "시행 안 됨", "아직 모름", "이어받기(다른 회사)", "이어받기(같은 회사)",
          "삭제", "해제", "비고 미기재 약가 변경", "신규 등재", "기타")
# (층 이름, 뽑을 수, [(기록 종류, 조건), ...]) — 조건이 여럿이면 한 목록으로 합쳐 md5 순
STRATA = [
    ("정시 실현", 6, [("signal", "outcome = 'REALIZED'")]),
    ("변경·지연 실현", 4, [("signal", "outcome in ('REALIZED_CHANGED', 'REALIZED_LATE')")]),
    ("급여중지 관련", 4, [("signal", "signal_type = 'SUSPEND_NOTICE'"), ("event", "event_type = 'SUSPEND'")]),
    ("양도양수", 2, [("event", "event_type = 'SUCCESSION' and kind = 'transfer'")]),
    ("코드 변경", 2, [("event", "event_type = 'SUCCESSION' and kind = 'recode'")]),
    ("교체 등재", 2, [("event", "event_type = 'SUCCESSION' and kind = 'swap'")]),
    ("급여삭제", 3, [("event", "event_type = 'DELISTING' and kind = 'delisting'")]),
    ("중지해제", 2, [("event", "event_type = 'SUSPEND_LIFT'")]),
    ("비고 미기재 약가 변경", 3, [("event", "event_type = 'PRICE_CHANGE' and kind = 'unobserved_notice'")]),
    ("신규·추가 등재", 2, [("event", "event_type = 'LISTING' and kind in ('new_listing', 'added_listing')")]),
]
assert sum(n for _, n, _ in STRATA) == 30


def months_around(con, ref: str) -> list[str]:
    return [r[0] for r in con.execute("""select list_month from cal_v5 where mi between (select mi from cal_v5 where list_month = ?) - ?
                                         and (select mi from cal_v5 where list_month = ?) + ? order by 1""", [ref, WIN, ref, WIN]).fetchall()]


def has_mid(con, pc: str, ref: str) -> bool:
    ms = months_around(con, ref)
    q = f"""select count(*) from note_token where product_code = ? and list_month in ({",".join("?" * len(ms))})
            and token_date is not null and extract(day from token_date) <> 1"""
    return con.execute(q, [pc, *ms]).fetchone()[0] > 0


def candidates(con, seed: int, cond: list[tuple[str, str]]) -> list[tuple]:
    parts = []
    for rtype, c in cond:
        if rtype == "event":
            parts.append(f"select 'event' rt, event_id rid, product_code, counterpart, observed_month ref from ev_v5 where {c}")
        else:
            parts.append(f"select 'signal' rt, signal_id rid, product_code, null counterpart, first_month ref from signal_v5 where {c}")
    return con.execute(f"select * from ({' union all '.join(parts)}) order by md5('{seed}' || rid)").fetchall()


def pick(con, seed: int, exclude: set[str]) -> pd.DataFrame:
    used = set(exclude)
    rows, pools = [], {}
    for name, n, cond in STRATA:
        pool = [r for r in candidates(con, seed, cond) if r[2] not in exclude and not (r[3] and r[3] in exclude)]
        pools[name] = pool
        k = 0
        for rt, rid, pc, cp, ref in pool:
            if pc in used or (cp and cp in used):
                continue
            rows.append(dict(stratum=name, record_type=rt, record_id=rid, product_code=pc, counterpart=cp, ref_month=ref, mid=has_mid(con, pc, ref)))
            used |= {pc} | ({cp} if cp else set())
            k += 1
            if k == n:
                break
        assert k == n, f"{name}: {k}/{n}"
    df = pd.DataFrame(rows)
    for name, _, _ in STRATA:                         # 월중 날짜 최소 MID_MIN건
        if df["mid"].sum() >= MID_MIN:
            break
        while df["mid"].sum() < MID_MIN:
            mine = df[df["stratum"] == name]
            non = mine[~mine["mid"]]
            if non.empty:
                break
            taken = set(df["product_code"]) | set(df["counterpart"].dropna())
            nxt = next(((rt, rid, pc, cp, ref) for rt, rid, pc, cp, ref in pools[name]
                        if pc not in taken and not (cp and cp in taken) and has_mid(con, pc, ref)), None)
            if nxt is None:
                break
            i = non.index[-1]
            rt, rid, pc, cp, ref = nxt
            df.loc[i, ["record_type", "record_id", "product_code", "counterpart", "ref_month", "mid"]] = [rt, rid, pc, cp, ref, True]
    assert df["mid"].sum() >= MID_MIN, f"월중 날짜 {df['mid'].sum()}건 < {MID_MIN}"
    df = df.sample(frac=1, random_state=seed).reset_index(drop=True)   # 층 순서가 드러나지 않게 섞는다
    df.insert(0, "holdout_id", range(1, len(df) + 1))
    return df


def window(con, pc: str, ref: str, table: str) -> str:
    out = []
    for m in months_around(con, ref):
        r = con.execute(f"""select firm, product_name, spec, unit, price, note from {table} where product_code = ? and list_month = ?
                            and row_kind = 'product_candidate' limit 1""", [pc, m]).fetchone()
        out.append(f"{m}: " + ("(없음)" if r is None else " / ".join("" if v is None else str(v) for v in
                                 (r[0], r[1], f"{r[2] or ''}{r[3] or ''}", r[4], r[5] or "-"))))
    return " | ".join(out)


def same_item(con, pc: str) -> str:
    rows = con.execute("""with it as (select item_code from bridge_product_item where product_code = ?),
             life as (select product_code, min(list_month) f, max(list_month) l, arg_max(firm, list_month) firm,
                             arg_max(product_name, list_month) nm, arg_max(price, list_month) price
                      from stg_benefit where row_kind = 'product_candidate' group by 1)
        select l.product_code, l.firm, l.nm, l.price, l.f, l.l from life l
        where l.product_code <> ? and l.product_code in (select product_code from bridge_product_item where item_code in (select item_code from it))
        order by l.product_code""", [pc, pc]).fetchall()
    return " ; ".join(f"{c}({f} / {n} / {p}, {a}~{b})" for c, f, n, p, a, b in rows) or "(없음)"


def same_ing(con, pc: str, ref: str) -> str:
    ms = months_around(con, ref)
    rows = con.execute(f"""with me as (select any_value(ingredient_code) ing from stg_benefit where product_code = ? and row_kind = 'product_candidate'),
             life as (select product_code, min(list_month) f, max(list_month) l, arg_max(firm, list_month) firm,
                             arg_max(product_name, list_month) nm, arg_max(price, list_month) price
                      from stg_benefit where row_kind = 'product_candidate' group by 1)
        select l.product_code, l.firm, l.nm, l.price, l.f, l.l from life l
        where l.product_code <> ? and l.product_code in (select product_code from stg_benefit where ingredient_code = (select ing from me))
          and (l.f in ({",".join("?" * len(ms))}) or l.l in ({",".join("?" * len(ms))}))
        order by l.product_code limit 12""", [pc, pc, *ms, *ms]).fetchall()
    return " ; ".join(f"{c}({f} / {n} / {p}, {a}~{b})" for c, f, n, p, a, b in rows) or "(없음)"


def look_at(con, r) -> str:
    """무엇을 볼지 (평이한 말, 판정값·규칙 용어 없음)"""
    if r.record_type == "signal":
        raw = con.execute("""select string_agg(distinct t.raw, ' ; ') from note_token t join signal_v5 s
                             on s.product_code = t.product_code and t.list_month = s.first_month
                             where s.signal_id = ? and t.token_date = s.signal_date""", [r.record_id]).fetchone()[0] or ""
        return f"기준월 목록 비고에 처음 적힌 예고 '{raw}'가 그 뒤 어떻게 됐나"
    return "기준월 전후 목록에서 이 제품에 무슨 변화가 있었나"


def build(con, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    s60 = pd.read_csv(VAL / "sample_60.user_reviewed.csv", dtype=str)
    old = pd.read_csv(VAL / "holdout_30.key.csv", dtype=str)
    exclude = set(s60["product_code"]) | set(old["product_code"])
    p = pick(con, seed, exclude)
    blind = [dict(holdout_id=r.holdout_id, product_code=r.product_code, ref_month=r.ref_month, look_at=look_at(con, r),
                  benefit_window=window(con, r.product_code, r.ref_month, "stg_benefit"),
                  substitute_window=window(con, r.product_code, r.ref_month, "stg_substitute"),
                  same_item_codes=same_item(con, r.product_code), same_ingredient_candidates=same_ing(con, r.product_code, r.ref_month),
                  truth_event="", truth_date="", truth_note="") for r in p.itertuples()]
    return pd.DataFrame(blind), p


def write_xlsx(blind: pd.DataFrame, path: Path) -> None:
    """라벨용 엑셀: 모든 칸을 문자열로(제품코드 앞 0 유지), truth_event는 범주 목록에서 고르게"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook(); ws = wb.active; ws.title = "label"
    ws.append(list(blind.columns))
    for r in blind.itertuples(index=False):
        ws.append(list(r))
    for row in ws.iter_rows():
        for c in row:
            c.number_format = "@"; c.alignment = Alignment(wrap_text=True, vertical="top")
    for c in ws[1]:
        c.font = Font(bold=True)
    width = dict(holdout_id=6, product_code=12, ref_month=9, look_at=40, benefit_window=80, substitute_window=60, same_item_codes=40,
                 same_ingredient_candidates=40, truth_event=22, truth_date=12, truth_note=40)
    for i, col in enumerate(blind.columns, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = width.get(col, 20)
    ws.freeze_panes = "D2"
    lst = wb.create_sheet("범주")
    for x in EVENTS:
        lst.append([x])
    col = ws.cell(1, list(blind.columns).index("truth_event") + 1).column_letter
    dv = DataValidation(type="list", formula1=f"=범주!$A$1:$A${len(EVENTS)}", allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{col}2:{col}{len(blind) + 1}")
    wb.save(path)


def main() -> None:
    seed = yaml.safe_load((ROOT / "config/params.yaml").read_text(encoding="utf-8"))["seed"]
    if BLIND.exists():
        old = pd.read_csv(BLIND, dtype=str).fillna("")
        if old[list(TRUTH)].ne("").any().any():
            raise SystemExit(f"{BLIND.name}에 정답이 있어 덮어쓰지 않는다")
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    blind, key = build(con, seed)
    blind.to_csv(BLIND, index=False, encoding="utf-8")
    key.to_csv(KEY, index=False, encoding="utf-8")
    write_xlsx(blind, VAL / "holdout_v53.label.xlsx")
    print(f"{BLIND.relative_to(ROOT)} {len(blind)}행 (월중 날짜 {int(key['mid'].sum())}행). 층별 수는 key에만 있다.")


if __name__ == "__main__":
    sys.exit(main())
