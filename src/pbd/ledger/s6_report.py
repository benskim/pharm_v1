"""P1 S6: 월간 선행 신호 리포트 (docs/PLAN_P1.md §6 S6).

make report MONTH=YYYY-MM → outputs/reports/P1_report_YYYY-MM.md, outputs/dashboard/YYYY-MM/*.csv

시점 고정
- MONTH 리포트는 MONTH까지의 스냅샷만 쓴다. 원본 L1 표(stg_benefit, stg_substitute, note_token)를 MONTH 이하로 잘라
  메모리 DB에 S2·S3 원장을 다시 만든다 (as-of 원장, S3 증분 시험과 같은 방식). 사용통계도 MONTH 이하 달만 읽는다.
- 뒤 스냅샷이 와야 정해지는 칸(src/pbd/ledger/posthoc.py 금지 칸)은 이 파일에서 읽지 않는다 (tests/test_s6.py).
  "이번 달 확인된 결과" = MONTH as-of 원장에서 판정이 났고, 직전 스냅샷 달 as-of 원장에서는 censored였던 신호.
- 과거 실현율 = MONTH as-of 원장에서 결과가 난 신호만 (censored·unverifiable·moot는 분모에서 빼고 건수를 적는다).
- 같은 입력이면 같은 파일: 생성 시각을 넣지 않고, 모든 표를 정렬 키로 고정한다.

문장 원칙: 신호는 "예고"로 쓰고, 예고를 변화로 읽히게 쓰지 않는다. 같은 유형의 과거 실현율을 옆에 적는다.
사용: PYTHONPATH=src python -m pbd.ledger.s6_report 2025-11
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import duckdb
import pandas as pd

from pbd.common import DB_PATH, ROOT, connect, load_yaml
from pbd.ledger import s2_state as S2
from pbd.ledger import s3_ledger as S3

OUT_MD = ROOT / "outputs/reports"
OUT_CSV = ROOT / "outputs/dashboard"
USAGE_WINDOW = 12            # C 순위표: 12개월 원외 금액 (S5 §10과 같은 기준: 12개월 모두 관측된 성분4)
USAGE_LAG = load_yaml("params.yaml").get("usage_lag_months")   # 공개 시차(개월). None = 미정 → 0으로 계산하고 표시
# 2026 약가정보(의약품안전나라판) 주성분: 복합제 성분 수('/'로 나뉨)를 정할 때만 쓰는 속성표 (DATA.md: 속성표로만)
DRUG_INFO = ROOT / "data/raw/master/dim_drug_info_26.parquet"
TOP_C4 = 20
LIST_ROWS = 20               # Markdown 목록 행 수 (전체는 CSV)

SIGNAL_LABEL = {
    "PRICE_PENDING": "가격 변경 예고",
    "LISTING_SCHEDULED": "등재 예고 (새 제품)",
    "LISTING_NOTE_EXISTING": "등재 예고 비고 (이미 급여 중인 제품)",
    "SUSPEND_SCHEDULED": "급여정지 예고",
    "RESUME_SCHEDULED": "급여정지 종료 예고",
    "STAY_RELEASE_PRICE": "집행정지 해제·인하 예고",
    "DELISTING_SCHEDULED": "삭제 예고",
}
SIGNAL_ORDER = list(SIGNAL_LABEL)
OUTCOME_LABEL = {"realized": "실현", "realized_partial": "부분 실현 (금액·날짜 다름)", "not_realized": "미실현",
                 "unverifiable": "판정 불가", "moot": "전제 신호 미실현"}
REASON_LABEL = {
    "delisted": "확인 스냅샷에 제품 없음", "suspended": "급여정지", "price_kept_stay_note": "가격 그대로 (집행정지 비고 있음)",
    "unpriced": "산정불가", "price_kept_note_remains": "가격 그대로 (예고 비고 남음)", "note_gone_price_kept": "가격 그대로 (비고 사라짐)",
    "still_scheduled": "아직 시행 전", "stay_continues": "집행정지 계속", "new_suspension": "새 급여정지", "still_suspended": "정지 계속",
    "still_listed": "목록에 남음", "recode_transfer": "코드 교체·양도양수",
}
EVENT_LABEL = {"SUSPEND": "급여정지", "RESUME": "급여정지 복귀", "DELISTING": "목록에서 사라짐", "TRANSFER": "양도양수·코드 교체"}
DELIST_KIND = {"exit": "삭제", "recode_transfer": "코드 교체·양도양수로 사라짐", "item_remains": "같은 품목의 다른 제품은 남음"}
DATE_SOURCE = {"signal_confirmed": "예고일 (실현 확인)", "note_benefit": "급여목록 비고 날짜", "note_substitute": "대체조제 목록 비고 날짜",
               "snapshot_first": "그 달 1일 (비고 없음)", "interval": "구간", "gap": "구간"}


# ---------------------------------------------------------------- as-of 원장
def snapshot_months(src: Path | str = DB_PATH) -> list[str]:
    con = duckdb.connect(str(src), read_only=True)
    try:
        return [r[0] for r in con.execute("select distinct list_month from stg_benefit order by 1").fetchall()]
    finally:
        con.close()


def build_asof(month: str, src: Path | str = DB_PATH) -> duckdb.DuckDBPyConnection:
    """MONTH 이하 스냅샷만으로 S2·S3 원장을 다시 만든 메모리 DB. 원본 DB는 src로 붙인다 (읽기 전용)"""
    c = connect(None)
    c.execute(f"attach '{Path(src).as_posix()}' as src (read_only)")
    for t in ("stg_benefit", "stg_substitute", "note_token"):
        c.execute(f"create table {t} as select * from src.{t} where list_month <= ?", [month])
    for t in ("attr_2026_product", "attr_2026_item"):      # 2026 속성표 (속성으로만 쓴다, DATA.md)
        c.execute(f"create table {t} as select * from src.{t}")
    S2.build_all(c)
    S3.build_all(c)
    return c


# ---------------------------------------------------------------- 표
def usage_window(month: str, lag: int | None = USAGE_LAG) -> list[str]:
    """C 순위표의 사용통계 12개월: MONTH − 공개 시차에서 끝난다"""
    end = pd.Period(month, "M") - (lag or 0)
    return [str(end - i) for i in range(USAGE_WINDOW - 1, -1, -1)]


def _names(con, month: str, drug_info: Path | None = DRUG_INFO) -> None:
    """표시 이름을 주성분코드마다 하나로 고정한다 (사용자 결정 2026-11-16).
    - 주성분코드 → 성분명 마스터가 없다 (code_map은 일반명코드만, 오렌지북·약가정보는 품목별 주성분). 그래서
      MONTH 이하 가장 최근 스냅샷의 이름 (급여목록 먼저, 같은 달 안에서는 연번이 가장 작은 행).
    - 복합제는 "외 N종": 성분 수 = MONTH 이하 가장 최근 스냅샷의 주성분개수 칸(2025-11부터 있음),
      없으면 그 달 그 코드 제품의 2026 약가정보 주성분 '/' 개수 중 가장 많은 값 (같으면 큰 값).
    - 원본 값(그 달 행의 이름)은 CSV의 *_raw 칸에 그대로 둔다.
    _ingname(ingredient_code, display_name, base_name, n_components, n_source), _c4name(c4, ingredient_name, ingredient_name_raw)"""
    con.execute("""create or replace temp table _nm as
        select ingredient_code, list_month, 0 src, row_no, regexp_replace(trim(ingredient_name), '\\s+', ' ', 'g') nm,
               try_cast(n_ingredients as int) ni
        from stg_benefit where ingredient_name is not null and list_month <= ?
        union all select ingredient_code, list_month, 1, row_no, regexp_replace(trim(ingredient_name), '\\s+', ' ', 'g'), null
        from stg_substitute where ingredient_name is not null and list_month <= ?""", [month, month])
    if drug_info is not None and Path(drug_info).exists():
        con.execute(f"""create or replace temp table _mi as
            with x as (select p.ingredient_code, len(string_split(d."주성분", '/')) k
                       from product_state_month p join read_parquet('{Path(drug_info).as_posix()}') d
                            on d."품목기준코드" = try_cast(p.item_code as bigint)
                       where p.list_month = ? and d."주성분" is not null)
            select ingredient_code, arg_max(k, (n, k)) k from (select ingredient_code, k, count(*) n from x group by 1, 2) group by 1""",
                    [month])
    else:
        con.execute("create or replace temp table _mi (ingredient_code varchar, k bigint)")
    con.execute("""create or replace temp table _ingname as
        with b as (select ingredient_code, nm base_name from (select *, row_number() over (partition by ingredient_code
                        order by list_month desc, src, row_no, nm) r from _nm) where r = 1),
             s as (select ingredient_code, ni from (select *, row_number() over (partition by ingredient_code
                        order by list_month desc, src, row_no, ni) r from _nm where ni is not null) where r = 1),
             n as (select b.ingredient_code, b.base_name, coalesce(s.ni, _mi.k) n_components,
                          case when s.ni is not null then 'snapshot_n_ingredients' when _mi.k is not null then 'drug_info_2026' end n_source
                   from b left join s using (ingredient_code) left join _mi using (ingredient_code))
        select ingredient_code, base_name || case when n_components >= 2 then ' 외 ' || (n_components - 1) || '종' else '' end display_name,
               base_name, n_components, n_source from n""")
    # 성분4 표시 이름: 그 달 제품이 가장 많은 주성분코드(같으면 작은 코드)의 표시 이름. 원본 값 = 그 달 행 이름의 최솟값 (이전 방식)
    con.execute("""create or replace temp table _c4name as
        with k as (select c4, ingredient_code, count(*) n from product_state_month where list_month = ? group by 1, 2),
             top as (select c4, arg_min(ingredient_code, (-n, ingredient_code)) ingredient_code from k group by 1),
             raw as (select c4, min(ingredient_name) ingredient_name_raw from (
                        select c4, ingredient_name from stg_benefit where list_month = ? and ingredient_name is not null
                        union all select c4, ingredient_name from stg_substitute where list_month = ? and ingredient_name is not null) group by 1)
        select coalesce(top.c4, raw.c4) c4, i.display_name ingredient_name, raw.ingredient_name_raw
        from top full join raw on raw.c4 = top.c4 left join _ingname i on i.ingredient_code = top.ingredient_code""",
                [month, month, month])


def new_signals(con, month: str) -> pd.DataFrame:
    """이번 달 스냅샷에서 처음 나온 예고 (issued_month = MONTH)"""
    return con.execute("""select l.signal_type, l.signal_id, l.product_code, p.product_name, p.firm, l.market_c4 c4, n.ingredient_name,
               n.ingredient_name_raw, l.signal_date, l.period_start, l.period_end, l.amount, p.price_listed price_now,
               case when l.signal_type = 'PRICE_PENDING' and p.price_listed > 0 and l.amount is not null
                    then round((l.amount / p.price_listed - 1) * 100, 1) end change_pct,
               array_to_string(l.source_list, '+') source_list, l.raw
        from signal_ledger l left join product_state_month p on p.product_code = l.product_code and p.list_month = l.issued_month
        left join _c4name n on n.c4 = l.market_c4
        where l.issued_month = ? order by l.signal_type, l.market_c4, l.product_code, l.signal_date""", [month]).df()


def realization_rates(con, month: str) -> pd.DataFrame:
    """MONTH 시점까지 결과가 난 신호의 유형별 실현율 (실현 = realized + realized_partial, A1과 같은 정의, Wilson 95%)"""
    df = con.execute("""select signal_type, outcome, count(*) n from signal_outcome where issued_month <= ? group by 1, 2""",
                     [month]).df()
    rows = []
    for t in SIGNAL_ORDER:
        g = df[df["signal_type"] == t].set_index("outcome")["n"]
        r, p, nr = (int(g.get(k, 0)) for k in ("realized", "realized_partial", "not_realized"))
        d = r + p + nr
        lo, hi = wilson(r + p, d)
        rows.append({"signal_type": t, "label": SIGNAL_LABEL[t], "decided": d, "realized": r, "realized_partial": p,
                     "not_realized": nr, "rate": round((r + p) / d, 4) if d else None, "rate_lo": lo, "rate_hi": hi,
                     "censored": int(g.get("censored", 0)), "unverifiable": int(g.get("unverifiable", 0)), "moot": int(g.get("moot", 0))})
    return pd.DataFrame(rows)


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    ph = k / n
    den = 1 + z * z / n
    mid = (ph + z * z / (2 * n)) / den
    half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return round(mid - half, 4), round(mid + half, 4)


def _outcome_state(con) -> pd.DataFrame:
    return con.execute("select signal_id, outcome from signal_outcome").df()


def outcomes_this_month(con, prev: pd.DataFrame | None, month: str) -> pd.DataFrame:
    """이번 달 스냅샷으로 결과가 난 신호: MONTH as-of에서 censored가 아니고, 직전 스냅샷 달 as-of에서는 censored(또는 없음)"""
    cur = con.execute("""select o.signal_type, o.signal_id, o.product_code, p.product_name, p.firm, l.market_c4 c4, n.ingredient_name,
               o.issued_month, o.signal_date, l.amount, p.price_listed price_now, o.outcome, o.reason, o.reason_source, o.evidence
        from signal_outcome o join signal_ledger l using (signal_id)
        left join product_state_month p on p.product_code = o.product_code and p.list_month = ?
        left join _c4name n on n.c4 = l.market_c4
        where o.outcome <> 'censored' order by o.signal_type, l.market_c4, o.product_code, o.signal_date""", [month]).df()
    if prev is None:
        return cur.iloc[0:0]
    before = prev.set_index("signal_id")["outcome"]
    was = cur["signal_id"].map(before)
    return cur[was.isna() | (was == "censored")].reset_index(drop=True)


def events_this_month(con, month: str) -> pd.DataFrame:
    # 제품명·업체: 이번 달 행, 없으면(사라진 제품) 직전 스냅샷 행
    return con.execute("""select e.event_type, e.kind, e.product_code, coalesce(p1.product_name, p0.product_name) product_name,
               coalesce(p1.firm, p0.firm) firm, e.market_c4 c4, n.ingredient_name,
               e.effective_date, e.effective_date_lo, e.effective_date_hi, e.date_source, e.evidence
        from event_ledger e join s3_cal k on k.list_month = e.observed_month
        left join product_state_month p1 on p1.product_code = e.product_code and p1.list_month = e.observed_month
        left join product_state_month p0 on p0.product_code = e.product_code and p0.list_month = k.prev_month
        left join _c4name n on n.c4 = e.market_c4
        where e.observed_month = ? order by e.event_type, e.kind, e.market_c4, e.product_code""", [month]).df()


def price_changes(con, month: str) -> pd.DataFrame:
    """이번 달 관측된 상한금액 변경 (직전 스냅샷 → 이번 스냅샷), 성분4별"""
    return con.execute("""with e as (select e.product_code, e.market_c4 c4, e.kind, e.effective_date, e.date_source
                                  from event_ledger e where e.event_type = 'PRICE_CHANGE' and e.observed_month = ?),
             pp as (select e.*, a.price_listed p0, b.price_listed p1 from e
                    join s3_cal k on k.list_month = ?
                    join product_state_month b on b.product_code = e.product_code and b.list_month = ?
                    join product_state_month a on a.product_code = e.product_code and a.list_month = k.prev_month)
        select pp.c4, n.ingredient_name, n.ingredient_name_raw, count(*) n_products, count(*) filter (where p1 < p0) n_cut, count(*) filter (where p1 > p0) n_raise,
               round(median(case when p1 < p0 then (p1 / p0 - 1) * 100 end), 1) median_cut_pct,
               count(*) filter (where kind = 'signaled') n_signaled, min(effective_date) eff_min, max(effective_date) eff_max
        from pp left join _c4name n on n.c4 = pp.c4 group by 1, 2, 3
        order by n_cut desc, n_products desc, pp.c4""", [month, month, month]).df()


def subst_rank(con, month: str) -> pd.DataFrame:
    """대체조제 그룹 순위 (성분4 단위, S5 §10과 같은 기준): 그 달 대체조제 목록·reimbursed·상한금액 있음, 제품 2개 이상 그룹,
    성분4 원외가 MONTH까지 12개월 모두 관측. 순서: 12개월 원외 금액. 가장 가격 차가 큰 그룹 = 최고/최저 비율 최대 (같으면 제품 수)"""
    window = usage_window(month)
    con.execute('create or replace temp table _win as select unnest(?::varchar[]) as "month"', [window])
    return con.execute("""with p as (select sub_group_code grp, ingredient_code, c4, product_code, price_listed price
                                    from product_state_month where list_month = ? and in_substitute_list and state_asof = 'reimbursed'
                                    and price_listed > 0),
             g as (select grp, ingredient_code, min(c4) c4, count(*) n_products, min(price) min_price, max(price) max_price,
                          round(max(price) / min(price), 3) ratio from p group by 1, 2 having count(*) >= 2),
             ua as (select u.c4, sum(u.claim_amt) amt from src.stg_usage u join _win w using (month)
                    where u.in_out = 'OUT' group by 1),
             obs as (select z.c4, count(*) filter (where z.observed_out) n_obs from src.usage_c4_month z join _win w using (month) group by 1),
             gg as (select g.*, row_number() over (partition by g.c4 order by g.ratio desc, g.n_products desc, g.grp, g.ingredient_code) rk
                    from g join obs using (c4) where obs.n_obs = ?),
             c as (select c4, count(*) n_groups, sum(n_products) n_products from gg group by 1)
        select row_number() over (order by ua.amt desc, c.c4) rank, c.c4, i.display_name ingredient_name, s.ingredient_name ingredient_name_raw,
               round(ua.amt / 1e8, 1) out_amt_12m_eok,
               c.n_groups, c.n_products, w.grp widest_group, w.ingredient_code widest_ingredient_code, w.n_products widest_n_products,
               w.min_price, w.max_price, w.ratio widest_max_min_ratio
        from c join ua using (c4) join gg w on w.c4 = c.c4 and w.rk = 1
        left join (select ingredient_code, min(ingredient_name) ingredient_name from stg_substitute where list_month = ?
                   group by 1) s on s.ingredient_code = w.ingredient_code
        left join _ingname i on i.ingredient_code = w.ingredient_code
        order by rank""", [month, USAGE_WINDOW, month]).df()


# ---------------------------------------------------------------- Markdown
def _f(v, nd: int = 0) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, (int,)) or (isinstance(v, float) and nd == 0):
        return f"{v:,.0f}"
    return f"{v:,.{nd}f}"


def _pct(v) -> str:
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:.1f}%"


def _d(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v):
        return ""
    return pd.Timestamp(v).strftime("%Y-%m-%d")


def _name(s) -> str:
    return "" if s is None or (isinstance(s, float) and math.isnan(s)) else " ".join(str(s).split())


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(r) + " |" for r in rows]


def _rate_text(rates: pd.DataFrame, t: str) -> str:
    r = rates.set_index("signal_type").loc[t]
    if not r["decided"]:
        return "과거 결과 없음"
    return f"{_pct(r['rate'])} ({int(r['realized'] + r['realized_partial']):,}/{int(r['decided']):,})"


def render(month: str, src: Path | str = DB_PATH) -> tuple[str, dict[str, pd.DataFrame]]:
    """(Markdown 본문, 대시보드 CSV 표들)"""
    months = snapshot_months(src)
    if month not in months:
        return render_no_data(month, months), {}
    prev_month = max([m for m in months if m < month], default=None)
    prev = None
    if prev_month:
        cp = build_asof(prev_month, src)
        prev = _outcome_state(cp)
        cp.close()
    con = build_asof(month, src)
    try:
        _names(con, month)
        sig = new_signals(con, month)
        rates = realization_rates(con, month)
        outc = outcomes_this_month(con, prev, month)
        ev = events_this_month(con, month)
        pc = price_changes(con, month)
        cr = subst_rank(con, month)
        n_products = con.execute("select count(*) from product_state_month where list_month = ?", [month]).fetchone()[0]
        dl = delisting_history(con, month)
    finally:
        con.close()
    md = _render_md(month, prev_month, sig, rates, outc, ev, pc, cr, n_products, dl)
    tabs = {"new_signals": sig, "signal_rates": rates, "signal_results": outc, "events": ev,
            "price_changes": pc, "subst_rank_c4": cr}
    return md, tabs


def delisting_history(con, month: str) -> pd.DataFrame:
    """MONTH 전 달들의 삭제(DELISTING exit) 건수 (as-of 원장, 이번 달 비교용)"""
    return con.execute("""select k.list_month as month, count(e.event_id) n_exit
        from s3_cal k left join event_ledger e on e.observed_month = k.list_month and e.event_type = 'DELISTING' and e.kind = 'exit'
        where k.list_month < ? and k.prev_month is not null group by 1 order by 1""", [month]).df()


def render_no_data(month: str, months: list[str]) -> str:
    rng = f"{months[0]} ~ {months[-1]}" if months else "없음"
    return "\n".join([f"# 선행 신호 월간 리포트 {month}", "", "**자료 없음**: 이 달의 급여목록 스냅샷이 없다.", "",
                      f"- 스냅샷이 있는 달: {rng} ({len(months)}개)", ""])


def _render_md(month, prev_month, sig, rates, outc, ev, pc, cr, n_products, dl) -> str:
    L: list[str] = [f"# 선행 신호 월간 리포트 {month}", "",
                    f"- 기준: {month} 급여목록·대체조제 목록 스냅샷까지의 자료 (그 뒤 스냅샷은 쓰지 않는다). 이 달 급여목록 제품 {n_products:,}개.",
                    "- 예고(선행 신호)는 시장 변화가 아니다. 연기·정지·철회될 수 있어, 같은 유형의 과거 실현율을 옆에 적는다.", ""]
    cnt = sig["signal_type"].value_counts()
    ev_cnt = ev["event_type"].value_counts()
    n_del = int(((ev["event_type"] == "DELISTING") & (ev["kind"] == "exit")).sum())
    # 1. 요약
    L += ["## 1. 이번 달 요약", ""]
    parts = [f"{SIGNAL_LABEL[t]} {int(cnt[t]):,}건" for t in SIGNAL_ORDER if t in cnt]
    L.append(f"- 새 예고 {len(sig):,}건" + (f": {', '.join(parts)}." if parts else "."))
    if len(pc):
        top = pc[pc["n_cut"] > 0].head(3)
        tops = ", ".join(f"{_name(r.ingredient_name) or r.c4} ({r.c4}) {int(r.n_cut)}개 제품 중앙 {r.median_cut_pct:.1f}%"
                         for r in top.itertuples())
        L.append(f"- 상한금액이 바뀐 제품 {int(pc['n_products'].sum()):,}개 (인하 {int(pc['n_cut'].sum()):,}, 인상 {int(pc['n_raise'].sum()):,})"
                 + (f". 인하 제품이 많은 성분: {tops}." if tops else "."))
    else:
        L.append("- 상한금액이 바뀐 제품 없음.")
    L.append(f"- 급여정지 {int(ev_cnt.get('SUSPEND', 0)):,}건, 정지 복귀 {int(ev_cnt.get('RESUME', 0)):,}건, 삭제 {n_del:,}건, "
             f"양도양수·코드 교체 {int(ev_cnt.get('TRANSFER', 0)):,}건.")
    if prev_month:
        oc = outc["outcome"].value_counts()
        L.append(f"- 지난 예고 중 이번 스냅샷으로 결과가 난 것 {len(outc):,}건: 실현 {int(oc.get('realized', 0)):,}, "
                 f"부분 실현 {int(oc.get('realized_partial', 0)):,}, 미실현 {int(oc.get('not_realized', 0)):,}.")
    L.append("")

    # 2. 새 예고
    L += ["## 2. 이번 달 새 예고", "",
          f"과거 실현율 = {month} 스냅샷까지 결과가 난 같은 유형 예고의 (실현 + 부분 실현) / (실현 + 부분 실현 + 미실현). "
          "아직 확인 스냅샷이 오지 않은 예고, 판정 불가, 전제 신호 미실현은 분모에서 뺀다.", ""]
    rows = []
    for t in SIGNAL_ORDER:
        r = rates.set_index("signal_type").loc[t]
        rows.append([SIGNAL_LABEL[t], _f(int(cnt.get(t, 0))), _pct(r["rate"]) if r["decided"] else "",
                     f"{_pct(r['rate_lo'])} ~ {_pct(r['rate_hi'])}" if r["decided"] else "", _f(int(r["decided"])),
                     _f(int(r["censored"]))])
    L += _table(["유형", "이번 달 새 예고", "과거 실현율", "95% 구간", "분모 (결과 난 예고)", "결과 대기"], rows) + [""]
    for t in SIGNAL_ORDER:
        g = sig[sig["signal_type"] == t]
        if g.empty:
            continue
        rt = _rate_text(rates, t)
        L += [f"### {SIGNAL_LABEL[t]} {len(g):,}건 (" + (f"과거 실현율 {rt})" if rt != "과거 결과 없음" else "같은 유형의 과거 결과 없음)"), ""]
        if t == "PRICE_PENDING":
            a = g.groupby(["c4", "signal_date"], dropna=False).agg(
                name=("ingredient_name", "first"), n=("product_code", "size"),
                med=("change_pct", "median"), lo=("change_pct", "min"), hi=("change_pct", "max")).reset_index()
            a = a.sort_values(["n", "c4", "signal_date"], ascending=[False, True, True])
            L += _table(["성분4", "성분", "예고 날짜", "제품", "예고 금액 / 지금 금액 − 1 (중앙, 범위)"],
                        [[r.c4, _name(r.name), _d(r.signal_date), _f(int(r.n)),
                          f"{r.med:+.1f}% ({r.lo:+.1f} ~ {r.hi:+.1f})" if not pd.isna(r.med) else ""]
                         for r in a.head(LIST_ROWS).itertuples()])
            if len(a) > LIST_ROWS:
                L.append(f"\n외 성분4 {len(a) - LIST_ROWS:,}개 (`new_signals.csv`).")
        else:
            L += _table(["제품코드", "제품명", "업체", "성분4", "예고 날짜", "비고 원문"],
                        [[r.product_code, _name(r.product_name), _name(r.firm), r.c4 or "", _d(r.signal_date), _name(r.raw)]
                         for r in g.head(LIST_ROWS).itertuples()])
            if len(g) > LIST_ROWS:
                L.append(f"\n외 {len(g) - LIST_ROWS:,}건 (`new_signals.csv`).")
        L.append("")

    # 3. 지난 예고의 결과
    L += ["## 3. 지난 예고의 결과 (이번 스냅샷에서 확인)", ""]
    if prev_month is None:
        L += ["분석 첫 달이라 직전 스냅샷이 없다.", ""]
    elif outc.empty:
        L += [f"{prev_month} 스냅샷까지 결과를 기다리던 예고 중 이번 스냅샷으로 결과가 난 것은 없다.", ""]
    else:
        tab = outc.groupby(["signal_type", "outcome"]).size().unstack(fill_value=0)
        rows = []
        for t in SIGNAL_ORDER:
            if t not in tab.index:
                continue
            r = tab.loc[t]
            rows.append([SIGNAL_LABEL[t]] + [_f(int(r.get(k, 0))) for k in ("realized", "realized_partial", "not_realized")]
                        + [_f(int(r.get("unverifiable", 0)) + int(r.get("moot", 0))), _f(int(r.sum()))])
        L += _table(["유형", "실현", "부분 실현", "미실현", "판정 불가·전제 미실현", "합"], rows) + [""]
        nr = outc[outc["outcome"] != "realized"]
        if len(nr):
            L += ["**실현되지 않았거나 다르게 실현된 예고**", ""]
            L += _table(["유형", "결과", "사유", "제품코드", "제품명", "성분4", "예고 날짜", "근거"],
                        [[SIGNAL_LABEL[r.signal_type], OUTCOME_LABEL.get(r.outcome, r.outcome),
                          REASON_LABEL.get(r.reason, r.reason or "") + (" (외부 자료)" if r.reason_source in ("external", "data+external") else ""),
                          r.product_code, _name(r.product_name), r.c4 or "", _d(r.signal_date), _name(r.evidence)]
                         for r in nr.head(LIST_ROWS).itertuples()])
            if len(nr) > LIST_ROWS:
                L.append(f"\n외 {len(nr) - LIST_ROWS:,}건 (`signal_results.csv`).")
            L.append("")

    # 4. 정지·복귀, 삭제, 양도양수
    L += ["## 4. 급여정지·복귀, 삭제, 양도양수·코드 교체", ""]
    sub = ev[ev["event_type"].isin(list(EVENT_LABEL))]
    if sub.empty:
        L += ["이번 스냅샷에서 관측된 것 없음.", ""]
    for t in EVENT_LABEL:
        g = sub[sub["event_type"] == t]
        if g.empty:
            continue
        if t == "DELISTING":
            kinds = ", ".join(f"{DELIST_KIND.get(k, k)} {n:,}" for k, n in g["kind"].value_counts().sort_index().items())
            L += [f"### {EVENT_LABEL[t]} {len(g):,}건 ({kinds})", ""]
            if len(dl):
                same = dl[dl["month"].str[5:] == month[5:]]
                same_txt = ", ".join(f"{r.month} {int(r.n_exit):,}건" for r in same.itertuples())
                L += [f"삭제 건수 비교 ({month} 전 스냅샷 기준): 이번 달 {n_del:,}건, 이전 {len(dl)}개월 월평균 {dl['n_exit'].mean():,.1f}건 "
                      f"(중앙 {dl['n_exit'].median():,.0f}건)" + (f", 지난해까지 같은 달 {same_txt}" if same_txt else "") + ".", ""]
        else:
            L += [f"### {EVENT_LABEL[t]} {len(g):,}건", ""]
        L += _table(["제품코드", "제품명", "업체", "성분4", "구분", "유효일", "유효일 근거"],
                    [[r.product_code, _name(r.product_name), _name(r.firm), r.c4 or "",
                      DELIST_KIND.get(r.kind, r.kind) if t == "DELISTING" else (r.kind or ""),
                      _d(r.effective_date) or f"{_d(r.effective_date_lo)} ~ {_d(r.effective_date_hi)}",
                      DATE_SOURCE.get(r.date_source, r.date_source or "")]
                     for r in g.head(LIST_ROWS).itertuples()])
        if len(g) > LIST_ROWS:
            L.append(f"\n외 {len(g) - LIST_ROWS:,}건 (`events.csv`).")
        L.append("")

    # 5. 대체조제 그룹 순위
    win = usage_window(month)
    lag_txt = f"공개 시차 {USAGE_LAG}개월" if USAGE_LAG is not None else "공개 시차 미정, 0개월로 계산"
    L += [f"## 5. 대체조제 그룹 순위 (성분4 단위) — 사용통계 기간: {win[0]} ~ {win[-1]} ({lag_txt})", "",
          f"{month} 대체조제 목록의 reimbursed 제품, 제품 2개 이상 그룹, 성분4 원외 사용통계가 위 {USAGE_WINDOW}개월 모두 관측된 "
          f"성분4 {len(cr):,}개. 순서는 {USAGE_WINDOW}개월 원외 금액. 성분명은 주성분코드마다 하나로 고정한 표시 이름 (복합제는 \"외 N종\", "
          "원본 값은 CSV `ingredient_name_raw`). 최고/최저는 상한금액 비율이고 실제 청구가 차이가 아니다.", ""]
    L += _table(["순위", "성분4", "성분 (가격 차가 가장 큰 그룹)", "원외 금액 12개월 (억 원)", "그룹", "제품", "가격 차가 가장 큰 그룹",
                 "최저", "최고", "최고/최저"],
                [[str(int(r.rank)), r.c4, _name(r.ingredient_name), _f(r.out_amt_12m_eok), _f(int(r.n_groups)), _f(int(r.n_products)),
                  f"{r.widest_group} {r.widest_ingredient_code} ({int(r.widest_n_products)}개)", _f(r.min_price), _f(r.max_price),
                  f"{r.widest_max_min_ratio:.2f}"] for r in cr.head(TOP_C4).itertuples()]) + [""]

    # 6. 한계
    lag_note = (f"공개 시차는 {USAGE_LAG}개월로 두었다" if USAGE_LAG is not None
                else "공개 시차는 아직 정하지 않아 0개월로 계산했다 (실제 공개는 더 늦다)")
    L += ["## 6. 자료 한계", "",
          "비고 없이 바뀐 변화는 그 스냅샷 달 1일을 유효일로 보고, 삭제·양도양수 구분은 다음 달 스냅샷으로 바뀔 수 있으며, "
          f"사용통계는 {win[0]} ~ {win[-1]}을 썼고 {lag_note}. 제품별 판매량·약국 재고·도매 유통은 데이터에 없다.", ""]
    return "\n".join(L)


# ---------------------------------------------------------------- 쓰기
def write(month: str, src: Path | str = DB_PATH, md_dir: Path = OUT_MD, csv_dir: Path = OUT_CSV) -> Path:
    md, tabs = render(month, src)
    md_dir.mkdir(parents=True, exist_ok=True)
    path = md_dir / f"P1_report_{month}.md"
    path.write_text(md, encoding="utf-8")
    d = csv_dir / month
    d.mkdir(parents=True, exist_ok=True)
    for k, v in tabs.items():
        v.to_csv(d / f"{k}.csv", index=False, lineterminator="\n")
    return path


def main() -> None:
    if len(sys.argv) != 2 or len(sys.argv[1]) != 7:
        raise SystemExit("사용: python -m pbd.ledger.s6_report YYYY-MM")
    print(write(sys.argv[1]))


if __name__ == "__main__":
    main()
