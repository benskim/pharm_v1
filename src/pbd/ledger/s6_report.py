"""P1 S6: 월간 선행 신호 리포트 (docs/PLAN_P1.md §1·§6 S6).

목적: 매달 1일, 그 달과 다음 달 초에 적용될 급여목록 변화를 정리하고, 각 변화의 과거 실현율과 시장 영향 규모를 함께
보여 줘서 약국·유통이 대응 우선순위를 정하게 한다.
  Q1 이번 달 무엇이 바뀌나 → 새 예고 + 이번 달 관측된 변화 (목록 ≤ MONTH)
  Q2 믿어도 되나          → 같은 유형 예고의 과거 실현율 (MONTH 시점 원장)
  Q3 얼마나 큰 일인가      → 영향 규모 (사용통계 ≤ MONTH − usage_lag_months)

make report MONTH=YYYY-MM (MONTH = 발간월) → outputs/reports/P1_report_YYYY-MM.md, outputs/dashboard/YYYY-MM/*.csv

시점 고정
- 목록: 원본 L1 표(stg_benefit, stg_substitute, note_token)를 MONTH 이하로 잘라 메모리 DB에 S2·S3 원장을 다시 만든다 (as-of 원장).
- 사용통계: MONTH − usage_lag_months(5) 이하만 `_u` 표 하나로 읽고, 리포트의 사용통계 계산은 모두 `_u`만 쓴다.
- 뒤 스냅샷이 와야 정해지는 칸(src/pbd/ledger/posthoc.py 금지 칸)은 이 파일에서 읽지 않는다 (tests/test_s6.py).
  "이번 달 확인된 결과" = MONTH as-of 원장에서 판정이 났고, 직전 스냅샷 달 as-of 원장에서는 censored였던 신호.
- 과거 실현율 = MONTH as-of 원장에서 결과가 난 신호만 (censored·unverifiable·moot는 분모에서 빼고 건수를 적는다).
- 같은 입력이면 같은 파일: 생성 시각을 넣지 않고, 모든 표를 정렬 키로 고정한다.

문장 원칙: 신호는 "예고"로 쓰고, 예고를 변화로 읽히게 쓰지 않는다. 같은 유형의 과거 실현율을 옆에 적는다.
사용: PYTHONPATH=src python -m pbd.ledger.s6_report 2025-11   |   python -m pbd.ledger.s6_report --groups 2025-12
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

from pbd.common import DB_PATH, ROOT, connect, load_yaml
from pbd.ledger import s2_state as S2

OUT_MD = ROOT / "outputs/reports"
OUT_CSV = ROOT / "outputs/dashboard"
OUT_GROUPS = ROOT / "outputs/s6"
CFG = load_yaml("report.yaml")
USAGE_LAG = load_yaml("params.yaml")["usage_lag_months"]       # 5 (사용자 확인 2026-11-16)
USAGE_WINDOW = 12            # C 순위표: 12개월 원외 금액 (S5 §10과 같은 기준: 12개월 모두 관측된 성분4)
BASE_MONTHS = CFG["impact"]["base_months"]
PASS_THROUGH = CFG["impact"]["pass_through_ratio"]
SUBST_MIN_RATIO = CFG["subst_min_ratio"]
SALT = {w.lower() for w in CFG["salt_words"]}
UNITS = {str(w).lower() for w in CFG["unit_words"]}
GROUP_OVERRIDES = {str(k): str(v) for k, v in (CFG.get("group_overrides") or {}).items()}
# 2026 약가정보(의약품안전나라판) 주성분: 복합제 성분 수('/'로 나뉨)를 정할 때만 쓰는 속성표 (DATA.md: 속성표로만)
DRUG_INFO = ROOT / "data/raw/master/dim_drug_info_26.parquet"
TOP_C4 = 20
LIST_ROWS = 20               # Markdown 목록 행 수 (전체는 CSV)

SIGNAL_LABEL = {                                    # v5 신호 유형 (RULES_2026-11-17 §6)
    "PRICE_NOTICE": "약가 예고",
    "LISTING_NOTICE": "등재 예고 (새 제품)",
    "LISTING_NOTE_EXISTING": "등재 예고 비고 (이미 급여 중인 제품)",
    "SUSPEND_NOTICE": "급여중지 예고",
    "STAY_RELEASE_PRICE": "집행정지 종료 후 인하 예고",
    "DELISTING_NOTICE": "삭제 예고",
}
SIGNAL_ORDER = list(SIGNAL_LABEL)
OUTCOME_LABEL = {"REALIZED": "실현", "REALIZED_CHANGED": "변경 실현", "REALIZED_LATE": "지연 실현", "REALIZED_BY_DELISTING": "삭제로 실현",
                 "NOT_REALIZED": "미실현", "EXCLUDED": "판정 제외", "CENSORED": "미결"}
REASON_LABEL = {
    "note_gone": "변화 없이 비고만 사라짐", "stay_ended_not_executed": "집행정지 종료 후 미시행", "stay_then_delisted": "집행정지 중 삭제",
    "listed_during_suspension": "급여중지 중 예고", "suspended_before_price_date": "인하일 전 급여중지", "target_gone": "대상 소멸",
    "still_listed": "목록에 남음", "stay_continues": "집행정지 계속", "suspended": "급여중지", "absent": "목록에 없음",
    "ONGOING_STAY": "집행정지 중", "AWAITING": "관측 기간 부족",
}
EVENT_LABEL = {"SUSPEND": "급여중지", "SUSPEND_LIFT": "중지해제", "DELISTING": "급여삭제", "DELISTING_STAY": "삭제 집행정지",
               "SUCCESSION": "코드 이어받기 (양도양수·코드 변경·교체 등재)"}
DELIST_KIND = {"delisting": "급여삭제", "delisting_spec": "급여삭제 (규격, 같은 허가 다른 규격은 남음)",
               "confirmed_after_stay": "급여삭제 최종 확정 (삭제 집행정지 뒤)", "transfer": "양도양수", "recode": "코드 변경", "swap": "교체 등재",
               "list_only": "급여목록 표시 사라짐", "list_only_unconfirmed": "급여목록 표시 사라짐 (분석 끝, 미확정)",
               "period_end": "기간 중지 종료", "r6_note": "급여정지 해제 비고", "delisting_stay": "삭제 집행정지"}
DATE_SOURCE = {"signal_confirmed": "예고일 (실현 확인)", "note_benefit": "급여목록 비고 날짜", "note_substitute": "대체조제 목록 비고 날짜",
               "snapshot_first": "그 달 1일 (비고 없음)", "interval": "구간", "gap": "구간", "external": "외부 확인 날짜"}
NOVEMBER_NOTE = ("11월은 미생산·미청구 약제 정기 정비 달(최근 3년 생산·수입 실적이 없고 유효기한이 지난 약제를 매년 1회 삭제). "
                 "근거: 데일리팜 2024-10-21 https://dailypharm.com/user/news/12292 (2024-11-01 1,056개 조정). 이번 해 고시 번호는 미확인.")


# ---------------------------------------------------------------- 시점
def _m(month: str, k: int) -> str:
    return str(pd.Period(month, "M") + k)


def usage_window(month: str, lag: int | None = USAGE_LAG, n: int = USAGE_WINDOW) -> list[str]:
    """MONTH − 공개 시차에서 끝나는 n개월 (C 순위표 12개월 = MONTH−16 ~ MONTH−5)"""
    end = pd.Period(month, "M") - (lag or 0)
    return [str(end - i) for i in range(n - 1, -1, -1)]


def base_window(month: str, lag: int | None = USAGE_LAG) -> list[str]:
    """F1 기준 월 U = MONTH−7 ~ MONTH−5 (발간 시점에 공개된 최근 3개월)"""
    return usage_window(month, lag, BASE_MONTHS)


def refs(month: str) -> dict[str, str]:
    """CSV 공통 칸. usage_ref_* = 그 표가 쓰는 사용통계 기간 (C 순위표는 12개월 창, 나머지는 기준 월 U)"""
    b = base_window(month)
    return {"report_month": month, "list_ref_month": month, "usage_ref_from": b[0], "usage_ref_to": b[-1]}


# ---------------------------------------------------------------- as-of 원장
def snapshot_months(src: Path | str = DB_PATH) -> list[str]:
    con = duckdb.connect(str(src), read_only=True)
    try:
        return [r[0] for r in con.execute("select distinct list_month from stg_benefit order by 1").fetchall()]
    finally:
        con.close()


def build_asof(month: str, src: Path | str = DB_PATH) -> duckdb.DuckDBPyConnection:
    """MONTH 이하 스냅샷만으로 S2 속성과 v5 원장(RULES_2026-11-17)을 다시 만든 메모리 DB. 원본 DB는 src로 붙인다 (읽기 전용).
    v4 원장(S3)은 만들지 않는다 (v4 리포트는 outputs/v4_legacy/reports/에 고정, 2026-11-18)"""
    c = connect(None)
    c.execute(f"attach '{Path(src).as_posix()}' as src (read_only)")
    for t in ("stg_benefit", "stg_substitute", "note_token"):
        c.execute(f"create table {t} as select * from src.{t} where list_month <= ?", [month])
    for t in ("attr_2026_product", "attr_2026_item"):      # 2026 속성표 (속성으로만 쓴다, DATA.md)
        c.execute(f"create table {t} as select * from src.{t}")
    S2.build_all(c)
    S2.build_attr_month(c)
    from pbd.ledger import v5_build
    v5_build.build(c)
    for t in S2.V4_JUDGMENT_TABLES:            # v4 판정 표는 리포트에서 쓰지 않는다 (RULES §11-4)
        c.execute(f"drop table if exists {t}")
    return c


def load_usage(con, month: str) -> None:
    """사용통계 읽기는 여기 한 곳: MONTH − usage_lag_months 이하, 리포트가 쓰는 가장 이른 달(12개월 창 시작) 이상.
    _u(c4, month, amt, observed): 원외 청구금액 합과 원외 관측 여부"""
    win = usage_window(month)
    lo, hi = win[0], win[-1]
    con.execute("""create or replace temp table _u as
        with a as (select c4, month, sum(claim_amt) amt from src.stg_usage
                   where in_out = 'OUT' and month between ? and ? group by 1, 2)
        select z.c4, z.month, a.amt, z.observed_out observed from src.usage_c4_month z left join a using (c4, month)
        where z.month between ? and ?""", [lo, hi, lo, hi])
    b = base_window(month)
    con.execute("""create or replace temp table _base as
        select c4, avg(amt) filter (where observed) base_amt, count(*) filter (where observed) base_months_observed
        from _u where month between ? and ? group by 1""", [b[0], b[-1]])


# ---------------------------------------------------------------- 이름·묶음 (F3)
def _names(con, month: str, drug_info: Path | None = DRUG_INFO) -> None:
    """표시 이름을 주성분코드마다 하나로 고정한다 (사용자 결정 2026-11-16).
    - 주성분코드 → 성분명 마스터가 없다 (code_map은 일반명코드만, 오렌지북·약가정보는 품목별 주성분). 그래서
      MONTH 이하 가장 최근 스냅샷의 이름 (급여목록 먼저, 같은 달 안에서는 연번이 가장 작은 행).
    - 복합제는 "외 N종": 성분 수 = MONTH 이하 가장 최근 스냅샷의 주성분개수 칸(2025-11부터 있음),
      없으면 그 달 그 코드 제품의 2026 약가정보 주성분 '/' 개수 중 가장 많은 값 (같으면 큰 값).
    - 원본 값(그 달 행의 이름)은 CSV의 *_raw 칸에 그대로 둔다.
    _ingname(ingredient_code, display_name, base_name, n_components, n_source),
    _c4name(c4, ingredient_name, ingredient_name_raw, rep_code, base_name, n_components)"""
    con.execute("""create or replace temp table _nm as
        select ingredient_code, list_month, 0 src, row_no, regexp_replace(trim(ingredient_name), '\\s+', ' ', 'g') nm,
               try_cast(n_ingredients as int) ni
        from stg_benefit where ingredient_name is not null and list_month <= ?
        union all select ingredient_code, list_month, 1, row_no, regexp_replace(trim(ingredient_name), '\\s+', ' ', 'g'), null
        from stg_substitute where ingredient_name is not null and list_month <= ?""", [month, month])
    if drug_info is not None and Path(drug_info).exists():
        con.execute(f"""create or replace temp table _mi as
            with x as (select p.ingredient_code, len(string_split(d."주성분", '/')) k
                       from state_month_v5 p join s2_product_link k on k.product_code = p.product_code
                       join read_parquet('{Path(drug_info).as_posix()}') d on d."품목기준코드" = try_cast(k.item_code as bigint)
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
        with k as (select c4, ingredient_code, count(*) n from state_month_v5 where list_month = ? group by 1, 2),
             top as (select c4, arg_min(ingredient_code, (-n, ingredient_code)) ingredient_code from k group by 1),
             raw as (select c4, min(ingredient_name) ingredient_name_raw from (
                        select c4, ingredient_name from stg_benefit where list_month = ? and ingredient_name is not null
                        union all select c4, ingredient_name from stg_substitute where list_month = ? and ingredient_name is not null) group by 1)
        select coalesce(top.c4, raw.c4) c4, i.display_name ingredient_name, raw.ingredient_name_raw, top.ingredient_code rep_code,
               i.base_name, i.n_components
        from top full join raw on raw.c4 = top.c4 left join _ingname i on i.ingredient_code = top.ingredient_code""",
                [month, month, month])
    _groups(con)


def short_name(name: str | None, n_components=None) -> str:
    """F3: 괄호 내용, 숫자로 시작하는 함량 토큰, 단위 토큰, 염·수화물 단어(config/report.yaml)를 뺀 이름. 첫 단어는 빼지 않는다.
    복합제는 "외 N종"을 붙인다 (묶지는 않는다)"""
    if name is None or (isinstance(name, float) and math.isnan(name)):
        return ""
    s = str(name)
    while True:
        t = re.sub(r"\([^()]*\)", " ", s)
        if t == s:
            break
        s = t
    s = s.replace("(", " ").replace(")", " ").replace(",", " ")
    keep: list[str] = []
    for tok in s.split():
        tl = tok.lower()
        if re.match(r"^[\d.]", tl) or tl in UNITS:
            continue
        if keep and tl in SALT:
            continue
        keep.append(tl)
    out = " ".join(keep) or " ".join(str(name).split()).lower()
    try:
        k = int(n_components) if n_components is not None and not pd.isna(n_components) else 1
    except (TypeError, ValueError):
        k = 1
    return out + (f" 외 {k - 1}종" if k >= 2 else "")


def _groups(con) -> None:
    """_c4grp(c4, short_name, group_key, is_complex): 단일 성분은 short_name으로 묶고, 복합제는 성분4 그대로 (c4:XXXX).
    config group_overrides(c4 → group_key)가 먼저다"""
    c = con.execute("select c4, base_name, n_components from _c4name order by c4").df()
    rows = []
    for r in c.itertuples(index=False):
        sn = short_name(r.base_name, r.n_components)
        cx = r.n_components is not None and not pd.isna(r.n_components) and int(r.n_components) >= 2
        key = GROUP_OVERRIDES.get(str(r.c4)) or (f"c4:{r.c4}" if cx or not sn else short_name(r.base_name))
        rows.append((r.c4, sn, key, bool(cx)))
    df = pd.DataFrame(rows, columns=["c4", "short_name", "group_key", "is_complex"])
    con.register("_c4grp_df", df)
    con.execute("create or replace temp table _c4grp as select * from _c4grp_df")
    con.unregister("_c4grp_df")


def group_label(con) -> dict[str, str]:
    """group_key → 표시 이름 (복합제·덮어쓰기는 그 성분4의 short_name)"""
    g = con.execute("select group_key, min(short_name) sn from _c4grp group by 1").df()
    return {r.group_key: (r.group_key if not r.group_key.startswith("c4:") else r.sn) for r in g.itertuples()}


def ingredient_groups(month: str = "2025-12", src: Path | str = DB_PATH) -> pd.DataFrame:
    """성분4가 2개 이상 묶인 group_key 전체 목록 (검토용, outputs/s6/ingredient_groups.csv)"""
    con = build_asof(month, src)
    try:
        _names(con, month)
        df = con.execute("""select g.group_key, count(*) n_c4, string_agg(g.c4, ' | ' order by g.c4) c4s,
                   string_agg(n.ingredient_name, ' | ' order by g.c4) display_names
            from _c4grp g join _c4name n using (c4) group by 1 having count(*) >= 2 order by n_c4 desc, group_key""").df()
    finally:
        con.close()
    df.insert(0, "list_ref_month", month)
    return df


# ---------------------------------------------------------------- 표
def new_signals(con, month: str) -> pd.DataFrame:
    """이번 달 스냅샷에서 처음 나온 예고 (issued_month = MONTH)"""
    return con.execute("""select l.signal_type, l.signal_id, l.product_code, coalesce(p.product_name, s.product_name) product_name,
               coalesce(p.firm, s.firm) firm, l.market_c4 c4, n.ingredient_name, g.short_name, g.group_key,
               n.ingredient_name_raw, l.signal_date, l.signal_date period_start, l.period_end, l.amount, p.price_listed price_now,
               case when l.signal_type = 'PRICE_NOTICE' and p.price_listed > 0 and l.amount is not null
                    then round((l.amount / p.price_listed - 1) * 100, 1) end change_pct,
               l.source_list, (select string_agg(distinct t.raw, ' ; ') from note_token t where t.product_code = l.product_code
                                and t.list_month = l.first_month and t.token_date = l.signal_date) raw
        from signal_v5 l left join state_month_v5 p on p.product_code = l.product_code and p.list_month = l.first_month
        left join (select product_code, min(product_name) product_name, min(firm) firm from stg_substitute where list_month = ?
                   group by 1) s on s.product_code = l.product_code
        left join _c4name n on n.c4 = l.market_c4 left join _c4grp g on g.c4 = l.market_c4
        where l.first_month = ? and l.source_rule <> 'S_AMT' order by l.signal_type, l.market_c4, l.product_code, l.signal_date""", [month, month]).df()


def impact_table(con, month: str) -> pd.DataFrame:
    """F1 영향 규모: 이번 달 새 가격 변경 예고, 성분4 × 예고일.
    impact_listed = base_amt × (1/N) × Σ r_i (r_i = 예고 금액 / 현재 상한금액 − 1, 비예고 제품 r = 0, N = 성분4 reimbursed 제품 수).
    impact_pt = impact_listed × 전가율(참고). 이번 달 적용 비율 = (말일 − 예고일 + 1) / 일수. 금액은 억 원/월 (음수 = 감소)"""
    last = pd.Period(month, "M").end_time.normalize()
    df = con.execute("""with s as (select l.market_c4 c4, l.signal_date, l.amount / p.price_listed - 1 r
                                   from signal_v5 l join state_month_v5 p
                                        on p.product_code = l.product_code and p.list_month = l.first_month and p.price_listed > 0
                                   -- 분자도 분모(N)와 같이 급여 상태 제품만 (RULES_2026-11-17 §7)
                                   where l.first_month = ? and l.signal_type = 'PRICE_NOTICE' and l.source_rule <> 'S_AMT' and p.state = 'reimbursed'),
             n as (select c4, count(*) n from state_month_v5 where list_month = ? and state = 'reimbursed' group by 1)
        select s.c4, s.signal_date notice_date, any_value(n.n) n_products_c4, count(*) n_noticed, sum(s.r) sum_r,
               avg(s.r) mean_r, median(s.r) median_r, min(s.r) min_r, max(s.r) max_r,
               any_value(b.base_amt) base_amt_won, coalesce(any_value(b.base_months_observed), 0) base_months_observed
        from s left join n using (c4) left join _base b using (c4) group by 1, 2 order by 1, 2""", [month, month]).df()
    days = last.day
    df["impact_listed_won"] = df["base_amt_won"] * df["sum_r"] / df["n_products_c4"]
    df["exposure_this_month"] = [round(min(1.0, max(0.0, ((last - pd.Timestamp(d)).days + 1) / days)), 4) for d in df["notice_date"]]
    out = pd.DataFrame({
        **refs(month), "c4": df["c4"], "notice_date": pd.to_datetime(df["notice_date"]).dt.strftime("%Y-%m-%d"),
        "n_products_c4": df["n_products_c4"], "n_noticed": df["n_noticed"],
        "mean_cut_noticed": (df["mean_r"] * 100).round(2), "median_cut_noticed": (df["median_r"] * 100).round(2),
        "min_cut_noticed": (df["min_r"] * 100).round(2), "max_cut_noticed": (df["max_r"] * 100).round(2),
        "base_amt": (df["base_amt_won"] / 1e8).round(2), "base_months_observed": df["base_months_observed"].astype(int),
        "impact_listed": (df["impact_listed_won"] / 1e8).round(2),
        "impact_pt": (df["impact_listed_won"] * PASS_THROUGH / 1e8).round(2),
        "exposure_this_month": df["exposure_this_month"],
        "base_amt_won": df["base_amt_won"], "sum_r": df["sum_r"]})
    return out


def stay_pending_table(con, month: str) -> pd.DataFrame:
    """집행정지 중인 처분(미결, RULES_2026-11-17 §6.3·§7): MONTH까지의 스냅샷으로 만든 v5 원장에서 final_outcome = CENSORED,
    current_status = ONGOING_STAY인 신호. 성분4 × 신호 유형. 잠재 영향(약가 예고만) = 기준 원외 금액 × (1/N) × Σ r_i,
    r_i = 막힌 처분 금액 / MONTH 상한금액 − 1, N = 성분4 급여 상태 제품 수. 시행 시점을 모르므로 월 단위 잠재 규모로만 쓴다"""
    df = con.execute("""with s as (select g.signal_type, g.market_c4 c4, g.product_code, g.amount, g.first_month, g.signal_date,
                                          case when g.signal_type = 'PRICE_NOTICE' and p.price_listed > 0 then g.amount / p.price_listed - 1 end r
                                   from signal_v5 g left join product_state_month_v5 p on p.product_code = g.product_code and p.list_month = ?
                                   where g.outcome = 'CENSORED' and g.current_status = 'ONGOING_STAY'),
             n as (select c4, count(*) n from state_month_v5 where list_month = ? and state = 'reimbursed' group by 1)
        select s.signal_type, s.c4, count(*) n_signals, count(distinct s.product_code) n_products_pending, min(s.first_month) first_listed_min,
               max(s.first_month) first_listed_max, any_value(n.n) n_products_c4, sum(s.r) sum_r, median(s.r) median_r,
               any_value(b.base_amt) base_amt_won, coalesce(any_value(b.base_months_observed), 0) base_months_observed,
               any_value(g.short_name) short_name
        from s left join n using (c4) left join _base b using (c4) left join _c4grp g using (c4) group by 1, 2 order by 1, 2""", [month, month]).df()
    df["potential_impact"] = (df["base_amt_won"] * df["sum_r"] / df["n_products_c4"] / 1e8).round(2)
    df["median_cut"] = df["median_r"].round(4)                   # 비율 (표시는 _pct)
    df["base_amt"] = (df["base_amt_won"] / 1e8).round(2)
    return pd.DataFrame({**refs(month), **{k: df[k] for k in ("signal_type", "c4", "short_name", "n_signals", "n_products_pending", "first_listed_min",
                                                              "first_listed_max", "n_products_c4", "median_cut", "base_amt",
                                                              "base_months_observed", "potential_impact")}})


def listing_table(con, month: str) -> pd.DataFrame:
    """등재 예고(새 제품) 성분4 × 예고일: 기준 원외 금액, 업체 수 a(MONTH reimbursed) → b(a + 새 업체). 금액 영향은 계산하지 않는다"""
    df = con.execute("""with s as (select l.market_c4 c4, l.signal_date, l.product_code, coalesce(p.firm, x.firm) firm
                                   from signal_v5 l left join state_month_v5 p on p.product_code = l.product_code and p.list_month = l.first_month
                                   left join (select product_code, min(firm) firm from stg_substitute where list_month = ? group by 1) x
                                        on x.product_code = l.product_code
                                   where l.first_month = ? and l.signal_type = 'LISTING_NOTICE'),
             f as (select distinct c4, firm from state_month_v5 where list_month = ? and state = 'reimbursed')
        select s.c4, s.signal_date notice_date, count(*) n_products, count(distinct s.firm) n_firms_noticed,
               (select count(*) from f where f.c4 = s.c4) firms_now,
               count(distinct s.firm) filter (where not exists (select 1 from f where f.c4 = s.c4 and f.firm = s.firm)) new_firms,
               any_value(b.base_amt) base_amt_won, coalesce(any_value(b.base_months_observed), 0) base_months_observed
        from s left join _base b using (c4) group by 1, 2 order by 1, 2""", [month, month, month]).df()
    df["firms_after"] = df["firms_now"] + df["new_firms"]
    return pd.DataFrame({**refs(month), "c4": df["c4"], "notice_date": pd.to_datetime(df["notice_date"]).dt.strftime("%Y-%m-%d"),
                         "n_products": df["n_products"], "n_firms_noticed": df["n_firms_noticed"], "firms_now": df["firms_now"],
                         "new_firms": df["new_firms"], "firms_after": df["firms_after"],
                         "base_amt": (df["base_amt_won"] / 1e8).round(2), "base_months_observed": df["base_months_observed"].astype(int)})


def listing_groups(con, month: str) -> pd.DataFrame:
    """F2 2)용: 등재 예고(새 제품)를 성분 묶음(group_key)으로 모은 값. 업체 수는 묶음 안에서 중복 없이 센다"""
    return con.execute("""with s as (select g.group_key gk, l.signal_date, coalesce(p.firm, x.firm) firm
                                   from signal_v5 l join _c4grp g on g.c4 = l.market_c4
                                   left join state_month_v5 p on p.product_code = l.product_code and p.list_month = l.first_month
                                   left join (select product_code, min(firm) firm from stg_substitute where list_month = ? group by 1) x
                                        on x.product_code = l.product_code
                                   where l.first_month = ? and l.signal_type = 'LISTING_NOTICE'),
             f as (select distinct g.group_key gk, p.firm from state_month_v5 p join _c4grp g using (c4)
                   where p.list_month = ? and p.state = 'reimbursed'),
             bs as (select g.group_key gk, sum(b.base_amt) base_amt from _c4grp g join _base b using (c4)
                    where g.c4 in (select market_c4 from signal_v5 where first_month = ? and signal_type = 'LISTING_NOTICE') group by 1)
        select s.gk, min(s.signal_date) notice_date, count(distinct s.firm) firms_noticed,
               (select count(*) from f where f.gk = s.gk) firms_now,
               count(distinct s.firm) filter (where not exists (select 1 from f where f.gk = s.gk and f.firm = s.firm)) new_firms,
               any_value(bs.base_amt) / 1e8 base_amt
        from s left join bs using (gk) group by 1 order by 1""", [month, month, month, month]).df()


def realization_rates(con, month: str) -> pd.DataFrame:
    """MONTH as-of 원장의 신호 유형별 지표 (RULES §7): B 최종 실현율(주) = 실현 계열 ÷ (전체 − 판정 제외 − 미결),
    A 현재 시점 집행률 = 실현 계열 ÷ (전체 − 판정 제외 − 미결 AWAITING), Wilson 95%"""
    from pbd.ledger.v5_rules import rates as v5_rates
    df = con.execute("""select signal_type, outcome, reason, stay_blocked, current_status from signal_v5 where first_month <= ?""", [month]).df()
    rows = []
    for t in SIGNAL_ORDER:
        g = df[df["signal_type"] == t]
        x = v5_rates(g.to_dict("records"))
        bk, bn, bp, blo, bhi = x["B"]
        ak, an, ap, alo, ahi = x["A"]
        r4 = lambda v: None if v is None else round(v, 4)
        rows.append({"signal_type": t, "label": SIGNAL_LABEL[t], "decided": bn, "realized": bk, "rate": r4(bp), "rate_lo": r4(blo),
                     "rate_hi": r4(bhi), "rate_a": r4(ap), "rate_a_lo": r4(alo), "rate_a_hi": r4(ahi), "a_denominator": an,
                     "censored": x["n_censored"], "ongoing_stay": x["n_ongoing_stay"], "excluded": x["n_excluded"]})
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
    return con.execute("select signal_id, outcome from signal_v5").df()


def outcomes_this_month(con, prev: pd.DataFrame | None, month: str) -> pd.DataFrame:
    """이번 달 스냅샷으로 결과가 난 신호: MONTH as-of에서 censored가 아니고, 직전 스냅샷 달 as-of에서는 censored(또는 없음)"""
    cur = con.execute("""select o.signal_type, o.signal_id, o.product_code, p.product_name, p.firm, o.market_c4 c4, n.ingredient_name,
               o.first_month issued_month, o.signal_date, o.amount, p.price_listed price_now, o.outcome, coalesce(o.reason, o.current_status) reason,
               o.reason_external, o.stay_blocked,
               concat_ws(' · ', o.exclude_reason, case when o.effective_date is not null then '유효일 ' || cast(o.effective_date as varchar) end,
                         case when o.stay_blocked then '집행정지로 막혔던 처분' end) evidence
        from signal_v5 o
        left join state_month_v5 p on p.product_code = o.product_code and p.list_month = ?
        left join _c4name n on n.c4 = o.market_c4
        where o.outcome <> 'CENSORED' order by o.signal_type, o.market_c4, o.product_code, o.signal_date""", [month]).df()
    if prev is None:
        return cur.iloc[0:0]
    before = prev.set_index("signal_id")["outcome"]
    was = cur["signal_id"].map(before)
    return cur[was.isna() | (was == "CENSORED")].reset_index(drop=True)


def remaining_counts(con, month: str) -> None:
    """F4: 이번 달 이벤트 제품마다 MONTH 스냅샷의 같은 주성분코드(9자리)·같은 성분4 reimbursed 제품 수 (자기 제외).
    MONTH 행만 쓴다. 제품의 주성분코드는 MONTH 행, 없으면(사라진 제품) 직전 스냅샷 행. _rem(product_code, ingredient_code, ...)"""
    con.execute("""create or replace temp table _rem as
        with ev as (select e.product_code, min(e.market_c4) c4 from ev_v5 e where e.observed_month = ? group by 1),
             k as (select prev_month from cal_v5 where list_month = ?),
             code as (select ev.product_code, ev.c4, coalesce(p1.ingredient_code, p0.ingredient_code) ingredient_code
                      from ev left join state_month_v5 p1 on p1.product_code = ev.product_code and p1.list_month = ?
                      left join state_month_v5 p0 on p0.product_code = ev.product_code and p0.list_month = (select prev_month from k)),
             cur as (select product_code, ingredient_code, c4 from state_month_v5 where list_month = ? and state = 'reimbursed')
        select code.product_code, code.ingredient_code,
               (select count(*) from cur where cur.ingredient_code = code.ingredient_code and cur.product_code <> code.product_code) same_code_remaining,
               (select count(*) from cur where cur.c4 = code.c4 and cur.product_code <> code.product_code) same_c4_remaining
        from code""", [month, month, month, month])


def events_this_month(con, month: str) -> pd.DataFrame:
    remaining_counts(con, month)
    # 제품명·업체: 이번 달 행, 없으면(사라진 제품) 직전 스냅샷 행
    df = con.execute("""select e.event_type, e.kind, e.counterpart, e.gap_months, e.product_code, coalesce(p1.product_name, p0.product_name) product_name,
               coalesce(p1.firm, p0.firm) firm, e.market_c4 c4, r.ingredient_code, n.ingredient_name, g.short_name, g.group_key,
               e.effective_date, e.effective_lo effective_date_lo, e.effective_hi effective_date_hi, e.date_source, e.evidence,
               r.same_code_remaining, r.same_c4_remaining, round(b.base_amt / 1e8, 2) base_amt, nc.n n_products_c4,
               -- 제품 1개 몫 (근사) = 성분4 원외 금액 / 성분4 reimbursed 제품 수 (F1과 같은 제품 수 비중 근사)
               round(b.base_amt / nullif(nc.n, 0) / 1e8, 3) product_share,
               -- 이름 보조: 제품 자신의 주성분코드 표시 이름 (성분4 제품이 이번 달에 하나도 남지 않은 경우)
               ig.base_name ing_base_name, ig.n_components ing_n_components
        from ev_v5 e join cal_v5 k on k.list_month = e.observed_month
        left join (select c4, count(*) n from state_month_v5 where list_month = ? and state = 'reimbursed' group by 1) nc
               on nc.c4 = e.market_c4
        left join state_month_v5 p1 on p1.product_code = e.product_code and p1.list_month = e.observed_month
        left join state_month_v5 p0 on p0.product_code = e.product_code and p0.list_month = k.prev_month
        left join _c4name n on n.c4 = e.market_c4 left join _c4grp g on g.c4 = e.market_c4
        left join _rem r on r.product_code = e.product_code left join _base b on b.c4 = e.market_c4
        left join _ingname ig on ig.ingredient_code = r.ingredient_code
        where e.observed_month = ? order by e.event_type, e.kind, e.market_c4, e.product_code""", [month, month]).df()
    return df


def price_changes(con, month: str) -> pd.DataFrame:
    """이번 달 관측된 상한금액 변경 (직전 스냅샷 → 이번 스냅샷), 성분4별"""
    return con.execute("""with e as (select e.product_code, e.market_c4 c4, e.kind, e.effective_date, e.date_source
                                  from ev_v5 e where e.event_type = 'PRICE_CHANGE' and e.observed_month = ?),
             pp as (select e.*, a.price_listed p0, b.price_listed p1 from e
                    join cal_v5 k on k.list_month = ?
                    join state_month_v5 b on b.product_code = e.product_code and b.list_month = ?
                    join state_month_v5 a on a.product_code = e.product_code and a.list_month = k.prev_month)
        select pp.c4, n.ingredient_name, g.short_name, g.group_key, n.ingredient_name_raw, count(*) n_products,
               count(*) filter (where p1 < p0) n_cut, count(*) filter (where p1 > p0) n_raise,
               round(median(case when p1 < p0 then (p1 / p0 - 1) * 100 end), 1) median_cut_pct,
               count(*) filter (where kind = 'signaled') n_signaled, min(effective_date) eff_min, max(effective_date) eff_max
        from pp left join _c4name n on n.c4 = pp.c4 left join _c4grp g on g.c4 = pp.c4 group by 1, 2, 3, 4, 5
        order by n_cut desc, n_products desc, pp.c4""", [month, month, month]).df()


def subst_rank(con, month: str) -> pd.DataFrame:
    """대체조제 그룹 순위 (성분4 단위, S5 §10과 같은 기준): 그 달 대체조제 목록·reimbursed·상한금액 있음, 제품 2개 이상 그룹,
    성분4 원외가 12개월 창(MONTH−16 ~ MONTH−5) 모두 관측. 순서: 12개월 원외 금액. 가장 가격 차가 큰 그룹 = 최고/최저 비율 최대 (같으면 제품 수)"""
    win = usage_window(month)
    return con.execute("""with p as (select sub_group_code grp, ingredient_code, c4, product_code, price_listed price
                                    from state_month_v5 where list_month = ? and in_substitute_list and state = 'reimbursed'
                                    and price_listed > 0),
             g as (select grp, ingredient_code, min(c4) c4, count(*) n_products, min(price) min_price, max(price) max_price,
                          round(max(price) / min(price), 3) ratio from p group by 1, 2 having count(*) >= 2),
             ua as (select c4, sum(amt) amt, count(*) filter (where observed) n_obs from _u where month between ? and ? group by 1),
             gg as (select g.*, row_number() over (partition by g.c4 order by g.ratio desc, g.n_products desc, g.grp, g.ingredient_code) rk
                    from g join ua using (c4) where ua.n_obs = ?),
             c as (select c4, count(*) n_groups, sum(n_products) n_products from gg group by 1)
        select row_number() over (order by ua.amt desc, c.c4) rank, c.c4, i.display_name ingredient_name, gr.short_name,
               s.ingredient_name ingredient_name_raw, round(ua.amt / 1e8, 1) out_amt_12m_eok,
               c.n_groups, c.n_products, w.grp widest_group, w.ingredient_code widest_ingredient_code, w.n_products widest_n_products,
               w.min_price, w.max_price, w.ratio widest_max_min_ratio
        from c join ua using (c4) join gg w on w.c4 = c.c4 and w.rk = 1
        left join (select ingredient_code, min(ingredient_name) ingredient_name from stg_substitute where list_month = ?
                   group by 1) s on s.ingredient_code = w.ingredient_code
        left join _ingname i on i.ingredient_code = w.ingredient_code left join _c4grp gr on gr.c4 = c.c4
        order by rank""", [month, win[0], win[-1], USAGE_WINDOW, month]).df()


def delisting_history(con, month: str) -> pd.DataFrame:
    """MONTH 전 달들의 급여삭제(DELISTING delisting) 건수 (as-of 원장, 이번 달 비교용)"""
    return con.execute("""select k.list_month as month, count(e.event_id) n_exit
        from cal_v5 k left join ev_v5 e on e.observed_month = k.list_month and e.event_type = 'DELISTING' and e.kind = 'delisting'
        where k.list_month < ? and k.prev_month is not null group by 1 order by 1""", [month]).df()


# ---------------------------------------------------------------- F2 주목할 것
def attention(sig: pd.DataFrame, imp: pd.DataFrame, lstg: pd.DataFrame, ev: pd.DataFrame, rates: pd.DataFrame,
              grp: pd.DataFrame, labels: dict[str, str]) -> list[str]:
    """분류별 최대 1줄, 합계 최대 3줄. 동점 처리는 각 규칙의 마지막 키(group_key 오름차순)로 고정한다.
    grp: c4 → group_key 표"""
    g = dict(zip(grp["c4"], grp["group_key"]))
    lines: list[str] = []
    rate_pp = rates.set_index("signal_type").loc["PRICE_NOTICE", "rate"]
    # 1) 가격 인하 예고: 점수 = 과거 실현율 × |Σ impact_listed| (묶음 합산)
    p = sig[sig["signal_type"] == "PRICE_NOTICE"].assign(gk=lambda d: d["c4"].map(g))
    if len(p) and len(imp):
        im = imp.assign(gk=imp["c4"].map(g)).groupby("gk").agg(impact=("impact_listed", lambda s: s.sum(min_count=1))).reset_index()
        a = p.groupby("gk").agg(n=("product_code", "size"), date=("signal_date", "min"), med=("change_pct", "median")).reset_index()
        a = a.merge(im, on="gk", how="left")
        a = a[a["med"] < 0]
        a["score"] = (rate_pp or 0) * a["impact"].abs()
        a = a[a["score"].notna()].sort_values(["score", "n", "gk"], ascending=[False, False, True])
        if len(a):
            r = a.iloc[0]
            lines.append(f"**가격 인하 예고** — {labels.get(r.gk, r.gk)} {int(r.n):,}개 제품 {_d(r.date)}부터 상한금액 중앙 {r.med:.1f}% 인하 예고 "
                         f"(과거 최종 실현율 {_pct(rate_pp)}, 월 영향 " + ("0.1억 원 미만" if abs(r.impact) < 0.05 else f"약 {r.impact:,.1f}억 원") + ").")
    # 2) 등재 예고: 새 업체 수가 가장 많은 묶음 (동점이면 base_amt 큰 쪽, 그다음 group_key)
    if len(lstg):
        a = lstg.sort_values(["new_firms", "base_amt", "gk"], ascending=[False, False, True], na_position="last")
        if a.iloc[0]["new_firms"] > 0:
            r = a.iloc[0]
            lines.append(f"**등재 예고** — {labels.get(r.gk, r.gk)} {int(r.firms_noticed):,}개 업체 {_d(r.notice_date)} 등재 예고 "
                         f"(업체 수 {int(r.firms_now):,} → {int(r.firms_now + r.new_firms):,}).")
    # 3) 삭제·정지: 같은 주성분코드에 남은 reimbursed 제품이 0인 삭제(exit)·정지의 건수가 가장 많은 묶음.
    #    같으면 그 제품들의 제품 몫(근사) 합이 큰 쪽, 그다음 묶음 이름 순. 그런 제품이 없으면 줄을 쓰지 않는다
    d = ev[((ev["event_type"] == "DELISTING") & (ev["kind"] == "delisting")) | (ev["event_type"] == "SUSPEND")]
    if len(d):
        d = d.assign(gk=d["c4"].map(g).fillna("c4:" + d["c4"].fillna("")), none=d["same_code_remaining"] == 0)
        nd = d[d["none"]]
        if len(nd):
            a = nd.groupby("gk").agg(k=("product_code", "size"), share=("product_share", lambda x: x.sum(min_count=1))).reset_index()
            a["n"] = a["gk"].map(d.groupby("gk").size())
            a = a.assign(_s=a["share"].fillna(-1)).sort_values(["k", "_s", "gk"], ascending=[False, False, True])
            r = a.iloc[0]
            kinds = set(d.loc[d["gk"] == r.gk, "event_type"])
            what = "삭제/급여중지" if kinds == {"DELISTING", "SUSPEND"} else ("급여중지" if kinds == {"SUSPEND"} else "삭제")
            share = "자료 없음" if pd.isna(r.share) else ("0.1억 원/월 미만" if abs(r.share) < 0.05 else f"약 {r.share:,.1f}억 원/월")
            lines.append(f"**삭제·정지** — {labels.get(r.gk, r.gk)} {int(r.n):,}개 제품 {what}, 그중 같은 함량·제형 대체 제품이 없는 것 "
                         f"{int(r.k):,}개 (제품 몫 합 {share}).")
    return lines[:3]


ATTENTION_RULES = [
    "가격 인하 예고: 점수 = 약가 예고의 과거 최종 실현율 × |상한금액 기준 월 영향|. 같은 성분 묶음(염·수화물만 다른 성분4)으로 합산해 가장 큰 묶음 1개. "
    "같으면 예고 제품 수가 많은 쪽, 그다음 묶음 이름 순. 월 영향을 계산할 수 없는(사용통계 미관측) 묶음은 뽑지 않는다.",
    "등재 예고: 새 업체 수(이번 달 reimbursed 업체에 없던 업체)가 가장 많은 묶음 1개. 같으면 기준 원외 금액이 큰 쪽, 그다음 묶음 이름 순.",
    "삭제·정지: 이번 달 급여삭제·급여중지 중 같은 주성분코드(성분·함량·제형)에 남은 reimbursed 제품이 0인 것의 건수가 가장 많은 묶음 1개. "
    "같으면 그 제품들의 제품 몫 합(근사: 성분4 원외 금액 ÷ 성분4 reimbursed 제품 수)이 큰 쪽, 그다음 묶음 이름 순. 그런 제품이 없으면 쓰지 않는다. "
    "삭제·정지 줄은 건수 기준이라 금액이 작은 묶음이 뽑힐 수 있다.",
]


# ---------------------------------------------------------------- Markdown
def _f(v, nd: int = 0) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)) or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if nd == 0:
        return f"{v:,.0f}"
    return f"{v:,.{nd}f}"


def _small(v, nd: int = 1) -> str:
    """영향 규모·제품 몫 (억 원): 절댓값 0.05억 미만은 "0.1억 미만", 미관측은 자료 없음 (CSV는 원값, 사용자 결정 2026-11-16)"""
    if v is None or pd.isna(v):
        return "자료 없음"
    return "0.1억 미만" if abs(v) < 0.05 else f"{v:,.{nd}f}"


def _amt(v) -> str:
    """억 원 소수 첫째 자리, 미관측은 자료 없음"""
    return "자료 없음" if v is None or pd.isna(v) else f"{v:,.1f}"


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
    a = f", 현재 시점 집행률 {_pct(r['rate_a'])}" if r["rate_a"] is not None and r["rate_a"] != r["rate"] else ""
    return f"최종 실현율 {_pct(r['rate'])} ({int(r['realized']):,}/{int(r['decided']):,}){a}"


def _ym_kor(month: str) -> str:
    y, m = month.split("-")
    return f"{y}년 {int(m)}월"


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
        load_usage(con, month)
        _names(con, month)
        sig = new_signals(con, month)
        rates = realization_rates(con, month)
        outc = outcomes_this_month(con, prev, month)
        ev = events_this_month(con, month)
        pc = price_changes(con, month)
        cr = subst_rank(con, month)
        imp = impact_table(con, month)
        lst = listing_table(con, month)
        lstg = listing_groups(con, month)
        grp = con.execute("select c4, short_name, group_key, is_complex from _c4grp order by c4").df()
        labels = group_label(con)
        n_products = con.execute("select count(*) from state_month_v5 where list_month = ?", [month]).fetchone()[0]
        dl = delisting_history(con, month)
        stp = stay_pending_table(con, month)
        # 이번 달 새로 기록된 막힌 인하 처분 (§1 대체조제 금액 신호): §2에 한 줄, 내용은 §3a
        samt = con.execute("""select count(*), count(distinct market_c4) from signal_v5
                              where first_month = ? and source_rule = 'S_AMT'""", [month]).fetchone()
    finally:
        con.close()
    att = attention(sig, imp, lstg, ev, rates, grp, labels)
    md = _render_md(month, prev_month, sig, rates, outc, ev, pc, cr, n_products, dl, imp, lst, att, grp, labels, stp, samt)
    r = refs(month)
    win = usage_window(month)
    no_usage = {**r, "usage_ref_from": None, "usage_ref_to": None}
    tabs = {"new_signals": sig.assign(**no_usage), "signal_rates": rates.assign(**no_usage),
            "signal_results": outc.assign(**no_usage), "events": ev.assign(**r),
            "price_changes": pc.assign(**no_usage), "subst_rank_c4": cr.assign(**{**r, "usage_ref_from": win[0], "usage_ref_to": win[-1]}),
            "impact": imp.drop(columns=["base_amt_won", "sum_r"]), "listing": lst, "stay_pending": stp,
            "attention": pd.DataFrame({**r, "line": list(range(1, len(att) + 1)), "text": att})}
    return md, tabs


def render_no_data(month: str, months: list[str]) -> str:
    rng = f"{months[0]} ~ {months[-1]}" if months else "없음"
    return "\n".join([f"# 선행 신호 월간 리포트 {month}", "", "**자료 없음**: 이 달의 급여목록 스냅샷이 없다.", "",
                      f"- 스냅샷이 있는 달: {rng} ({len(months)}개)", ""])


def _render_md(month, prev_month, sig, rates, outc, ev, pc, cr, n_products, dl, imp, lst, att, grp, labels, stp=None, samt=(0, 0)) -> str:
    b = base_window(month)
    win = usage_window(month)
    ub = f"(사용통계 기준 {b[0]} ~ {b[-1]})"
    L: list[str] = [f"# 선행 신호 월간 리포트 {month}", "",
                    f"- 기준: 발간 {month}-01 · 목록: {month} 스냅샷까지 · 사용통계: ~{b[-1]} (발간 시점 공개분, 수정본). 이 달 급여목록 제품 {n_products:,}개.",
                    "- 목적: 그 달과 다음 달 초에 적용될 급여목록 변화를 정리하고, 각 변화의 과거 실현율과 시장 영향 규모를 함께 보여 "
                    "대응 우선순위를 정하게 한다 (Q1 무엇이 바뀌나 · Q2 믿어도 되나 · Q3 얼마나 큰 일인가).",
                    "- 예고(선행 신호)는 시장 변화가 아니다. 연기·정지·철회될 수 있어, 같은 유형의 과거 실현율을 옆에 적는다.", ""]
    g = dict(zip(grp["c4"], grp["group_key"]))
    sn = dict(zip(grp["c4"], grp["short_name"]))
    cnt = sig["signal_type"].value_counts()
    ev_cnt = ev["event_type"].value_counts()
    n_del = int(((ev["event_type"] == "DELISTING") & (ev["kind"] == "delisting")).sum())

    # 주목할 것
    L += ["## 이번 달 주목할 것", ""]
    L += [f"{i}. {t}" for i, t in enumerate(att, 1)] if att else ["이번 달은 규칙에 해당하는 항목이 없다."]
    L += ["", "고른 규칙은 맨 아래 \"이 리포트를 읽는 법\"에 있다.", ""]

    # 1. 요약 (성분 묶음 단위, short_name)
    L += ["## 1. 이번 달 요약", ""]
    parts = [f"{SIGNAL_LABEL[t]} {int(cnt[t]):,}건" for t in SIGNAL_ORDER if t in cnt]
    L.append(f"- 새 예고 {len(sig):,}건" + (f": {', '.join(parts)}." if parts else "."))
    if len(pc):
        cut = pc[pc["n_cut"] > 0].assign(gk=lambda d: d["c4"].map(g))
        top = (cut.groupby("gk").agg(n=("n_cut", "sum"), med=("median_cut_pct", "median")).reset_index()
               .sort_values(["n", "gk"], ascending=[False, True]).head(3))
        tops = ", ".join(f"{labels.get(r.gk, r.gk)} {int(r.n)}개 제품 중앙 {r.med:.1f}%" for r in top.itertuples())
        L.append(f"- 상한금액이 바뀐 제품 {int(pc['n_products'].sum()):,}개 (인하 {int(pc['n_cut'].sum()):,}, 인상 {int(pc['n_raise'].sum()):,})"
                 + (f". 인하 제품이 많은 성분: {tops}." if tops else "."))
    else:
        L.append("- 상한금액이 바뀐 제품 없음.")
    L.append(f"- 급여중지 {int(ev_cnt.get('SUSPEND', 0)):,}건, 중지해제 {int(ev_cnt.get('SUSPEND_LIFT', 0)):,}건, 급여삭제 {n_del:,}건, "
             f"코드 이어받기 {int(ev_cnt.get('SUCCESSION', 0)):,}건.")
    if prev_month:
        oc = outc["outcome"].value_counts()
        L.append(f"- 지난 예고 중 이번 스냅샷으로 결과가 난 것 {len(outc):,}건: "
                 + ", ".join(f"{OUTCOME_LABEL[k]} {int(oc.get(k, 0)):,}" for k in OUTCOME_LABEL if k != "CENSORED") + ".")
    L.append("")

    # 2. 새 예고
    L += [f"## 2. 이번 달 새 예고 {ub}", "",
          f"과거 실현율 (RULES §7, {month} 스냅샷까지): 최종 실현율 B = 실현 계열(실현·변경 실현·지연 실현·삭제로 실현) ÷ (전체 − 판정 제외 − 미결). "
          "현재 시점 집행률 A = 실현 계열 ÷ (전체 − 판정 제외 − 관측 기간 부족) (집행정지 중인 처분은 미실현으로 셈). A와 B의 차이는 집행정지로 아직 막힌 몫이다.", ""]
    rows = []
    for t in SIGNAL_ORDER:
        r = rates.set_index("signal_type").loc[t]
        rows.append([SIGNAL_LABEL[t], _f(int(cnt.get(t, 0))), _pct(r["rate"]) if r["decided"] else "",
                     f"{_pct(r['rate_lo'])} ~ {_pct(r['rate_hi'])}" if r["decided"] else "", _f(int(r["decided"])),
                     _pct(r["rate_a"]) if r["a_denominator"] else "",
                     f"{_pct(r['rate_a_lo'])} ~ {_pct(r['rate_a_hi'])}" if r["a_denominator"] else "",
                     _f(int(r["ongoing_stay"])), _f(int(r["censored"] - r["ongoing_stay"])), _f(int(r["excluded"]))])
    L += _table(["유형", "이번 달 새 예고", "최종 실현율 B", "B 95% 구간", "B 분모", "현재 시점 집행률 A", "A 95% 구간",
                 "미결 (집행정지 중)", "미결 (관측 기간 부족)", "판정 제외"], rows) + [""]
    L += [f"과거 실현율은 {month} 스냅샷까지 결과가 난 예고만 센다. 미결이던 예고의 결과가 새로 나면(시행·미실현·집행정지 중 삭제 등) "
          "분모와 분자가 바뀌어, 같은 유형이라도 리포트 달마다 B와 A가 달라진다.", ""]
    if samt and samt[0]:
        L += [f"- 이번 달 새로 기록된 막힌 인하 처분 (대체조제 목록 금액, 급여목록에 새 집행정지) {int(samt[0]):,}건 · 성분4 {int(samt[1]):,}개: "
              "최초 기재 때부터 집행정지로 시행되지 않아 새 예고 표·영향 규모에서는 빼고 §3a에 적는다.", ""]
    for t in SIGNAL_ORDER:
        gs = sig[sig["signal_type"] == t]
        if gs.empty:
            continue
        rt = _rate_text(rates, t)
        L += [f"### {SIGNAL_LABEL[t]} {len(gs):,}건 (" + (f"과거 {rt})" if rt != "과거 결과 없음" else "같은 유형의 과거 결과 없음)"), ""]
        if t == "PRICE_NOTICE":
            a = gs.groupby(["c4", "signal_date"], dropna=False).agg(
                name=("ingredient_name", "first"), n=("product_code", "size"),
                med=("change_pct", "median"), lo=("change_pct", "min"), hi=("change_pct", "max")).reset_index()
            a["notice_date"] = pd.to_datetime(a["signal_date"]).dt.strftime("%Y-%m-%d")
            a = a.merge(imp[["c4", "notice_date", "n_products_c4", "base_amt", "base_months_observed", "impact_listed", "impact_pt",
                             "exposure_this_month"]], on=["c4", "notice_date"], how="left")
            a = a.sort_values(["n", "c4", "signal_date"], ascending=[False, True, True])
            L += [f"월 영향 = 기준 원외 금액({b[0]} ~ {b[-1]} 관측 달 평균) × (1 / 성분4 reimbursed 제품 수) × Σ(예고 금액 / 지금 금액 − 1). "
                  f"전가율 적용 값은 × {PASS_THROUGH} (S5 B1 쌍별 비율 중앙값, 참고). 단위 억 원/월, 음수 = 감소.", ""]
            L += _table(["성분4", "성분", "묶음", "예고 날짜", "제품 (예고 / 성분4)", "예고 금액 / 지금 금액 − 1 (중앙, 범위)",
                         "기준 원외 금액 (억/월)", "상한금액 기준 월 영향", "전가율 적용 월 영향 (참고)", "이번 달 적용 비율"],
                        [[r.c4, _name(r.name), sn.get(r.c4, ""), r.notice_date, f"{int(r.n):,} / {_f(r.n_products_c4)}",
                          f"{r.med:+.1f}% ({r.lo:+.1f} ~ {r.hi:+.1f})" if not pd.isna(r.med) else "",
                          _amt(r.base_amt) + (f" ({int(r.base_months_observed)}개월)" if 0 < r.base_months_observed < BASE_MONTHS else ""),
                          _small(r.impact_listed), _small(r.impact_pt), f"{r.exposure_this_month:.2f}"]
                         for r in a.head(LIST_ROWS).itertuples()])
            if len(a) > LIST_ROWS:
                L.append(f"\n외 성분4 {len(a) - LIST_ROWS:,}개 (`impact.csv`, `new_signals.csv`).")
        elif t == "LISTING_NOTICE":
            s2 = lst.sort_values(["new_firms", "n_products", "c4"], ascending=[False, False, True])
            L += _table(["성분4", "성분", "묶음", "예고 날짜", "제품", "새 업체", "업체 수 (지금 → 등재 후)", "기준 원외 금액 (억/월)"],
                        [[r.c4, _name(gs.loc[gs["c4"] == r.c4, "ingredient_name"].iloc[0]), sn.get(r.c4, ""), r.notice_date,
                          _f(r.n_products), _f(r.new_firms), f"{int(r.firms_now)} → {int(r.firms_after)}", _amt(r.base_amt)]
                         for r in s2.head(LIST_ROWS).itertuples()]) + [""]
            L += _table(["제품코드", "제품명", "업체", "성분4", "예고 날짜", "비고 원문"],
                        [[r.product_code, _name(r.product_name), _name(r.firm), r.c4 or "", _d(r.signal_date), _name(r.raw)]
                         for r in gs.head(LIST_ROWS).itertuples()])
            if len(gs) > LIST_ROWS:
                L.append(f"\n외 {len(gs) - LIST_ROWS:,}건 (`new_signals.csv`).")
        else:
            if t == "LISTING_NOTE_EXISTING":
                L += ["이미 급여 중인 제품. 같은 날 같은 고시에 함께 실렸고 상태는 바뀌지 않는다.", ""]
            L += _table(["제품코드", "제품명", "업체", "성분4", "예고 날짜", "비고 원문"],
                        [[r.product_code, _name(r.product_name), _name(r.firm), r.c4 or "", _d(r.signal_date), _name(r.raw)]
                         for r in gs.head(LIST_ROWS).itertuples()])
            if len(gs) > LIST_ROWS:
                L.append(f"\n외 {len(gs) - LIST_ROWS:,}건 (`new_signals.csv`).")
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
            rows.append([SIGNAL_LABEL[t]] + [_f(int(r.get(k, 0))) for k in OUTCOME_LABEL if k != "CENSORED"] + [_f(int(r.sum()))])
        L += _table(["유형"] + [OUTCOME_LABEL[k] for k in OUTCOME_LABEL if k != "CENSORED"] + ["합"], rows) + [""]
        nr = outc[outc["outcome"] != "REALIZED"]
        if len(nr):
            L += ["**실현되지 않았거나 다르게 실현된 예고**", ""]
            L += _table(["유형", "결과", "사유", "제품코드", "제품명", "성분4", "예고 날짜", "근거"],
                        [[SIGNAL_LABEL[r.signal_type], OUTCOME_LABEL.get(r.outcome, r.outcome),
                          REASON_LABEL.get(r.reason, r.reason or "") + (" (외부 자료)" if isinstance(r.reason_external, str) and r.reason_external else ""),
                          r.product_code, _name(r.product_name), r.c4 or "", _d(r.signal_date), _name(r.evidence)]
                         for r in nr.head(LIST_ROWS).itertuples()])
            if len(nr) > LIST_ROWS:
                L.append(f"\n외 {len(nr) - LIST_ROWS:,}건 (`signal_results.csv`).")
            L.append("")

    # 3a. 집행정지 중인 처분 (미결)과 잠재 영향 규모 (RULES_2026-11-17 §7)
    L += [f"## 3a. 집행정지 중인 처분 (미결)과 잠재 영향 규모 {ub}", ""]
    if stp is None or stp.empty:
        L += ["이번 스냅샷 기준 집행정지로 시행되지 않고 있는 처분은 없다.", ""]
    else:
        tot = stp.groupby("signal_type")["n_signals"].sum()
        L += ["예정일이 지났지만 법원 집행정지로 아직 시행되지 않은 처분이다 (v5 원장 `CENSORED` · `ONGOING_STAY`). "
              "언제 시행될지는 알 수 없어, 잠재 영향은 지금 시행된다고 할 때의 월 규모(억 원, 음수 = 감소)로만 적는다. "
              "급여중지 처분은 금액 영향을 계산하지 않는다.", "",
              "- " + ", ".join(f"{SIGNAL_LABEL.get(t, t)} {_f(int(v))}건" for t, v in tot.items()), ""]
        top = stp.assign(a=stp["potential_impact"].abs()).sort_values(["a", "n_signals"], ascending=[False, False], na_position="last")
        L += _table(["유형", "성분4", "이름", "처분 수", "최초 기재", "중앙 인하율", "기준 원외 금액", "잠재 영향"],
                    [[SIGNAL_LABEL.get(r.signal_type, r.signal_type), r.c4 or "", _name(r.short_name), _f(r.n_signals),
                      f"{r.first_listed_min}~{r.first_listed_max}" if r.first_listed_min != r.first_listed_max else r.first_listed_min,
                      _pct(r.median_cut), _amt(r.base_amt), _amt(r.potential_impact)] for r in top.head(LIST_ROWS).itertuples()])
        if len(stp) > LIST_ROWS:
            L.append(f"\n외 {len(stp) - LIST_ROWS:,}행 (`stay_pending.csv`).")
        L.append("")

    # 4. 정지·복귀, 삭제, 양도양수 (F4)
    L += [f"## 4. 급여중지·중지해제, 급여삭제, 코드 이어받기 {ub}", ""]
    sub = ev[ev["event_type"].isin(list(EVENT_LABEL))]
    if sub.empty:
        L += ["이번 스냅샷에서 관측된 것 없음.", ""]
    for t in EVENT_LABEL:
        gt = sub[sub["event_type"] == t]
        if gt.empty:
            continue
        if t == "DELISTING":
            kinds = ", ".join(f"{DELIST_KIND.get(k, k)} {n:,}" for k, n in gt["kind"].value_counts().sort_index().items())
            L += [f"### {EVENT_LABEL[t]} {len(gt):,}건 ({kinds})", ""]
            ex = gt[gt["kind"] == "delisting"]
            k_none = int((ex["same_code_remaining"] == 0).sum())
            L.append(f"**같은 함량·제형 대체 제품 없는 삭제 {k_none:,}건** (같은 주성분코드 = 성분·함량·제형이 같은 reimbursed 제품이 {month} 목록에 남지 않음).")
            if len(ex):
                def _tops(x):
                    tf = x.groupby("firm").size().reset_index(name="n").sort_values(["n", "firm"], ascending=[False, True]).head(5)
                    nm = [sn.get(c) or short_name(b, k) for c, b, k in zip(x["c4"], x["ing_base_name"], x["ing_n_components"])]
                    ti = x.assign(nm=nm).groupby(["c4", "nm"]).size().reset_index(name="n") \
                        .sort_values(["n", "c4"], ascending=[False, True]).head(5)
                    return (", ".join(f"{_name(r.firm)} {r.n}" for r in tf.itertuples()),
                            ", ".join(f"{r.nm or r.c4} ({r.c4}) {r.n}" for r in ti.itertuples()))
                fa, ia = _tops(ex)
                L.append(f"- 업체별 상위 5 (전체 삭제 {len(ex):,}건 기준): {fa}.")
                L.append(f"- 성분별 상위 5 (전체 삭제 {len(ex):,}건 기준): {ia}.")
                nx = ex[ex["same_code_remaining"] == 0]
                if len(nx):
                    fn, inn = _tops(nx)
                    L.append(f"- 업체별 상위 5 (같은 함량·제형 대체 제품 없는 삭제 {len(nx):,}건 기준): {fn}.")
                    L.append(f"- 성분별 상위 5 (같은 함량·제형 대체 제품 없는 삭제 {len(nx):,}건 기준): {inn}.")
            if month[5:] == "11":
                L.append(f"- {NOVEMBER_NOTE}")
            if len(dl):
                same = dl[dl["month"].str[5:] == month[5:]]
                same_txt = ", ".join(f"{r.month} {int(r.n_exit):,}건" for r in same.itertuples())
                L.append(f"- 삭제 건수 비교 ({month} 전 스냅샷 기준): 이번 달 {n_del:,}건, 이전 {len(dl)}개월 월평균 {dl['n_exit'].mean():,.1f}건 "
                         f"(중앙 {dl['n_exit'].median():,.0f}건)" + (f", 지난해까지 같은 달 {same_txt}" if same_txt else "") + ".")
            L.append("")
        else:
            L += [f"### {EVENT_LABEL[t]} {len(gt):,}건", ""]
        gt = gt.assign(_ps=gt["product_share"].fillna(-1)).sort_values(
            ["same_code_remaining", "_ps", "c4", "product_code"], ascending=[True, False, True, True], na_position="last")
        L += _table(["제품코드", "제품명", "업체", "성분4", "구분", "유효일", "유효일 근거", "같은 주성분코드 남은 제품", "같은 성분4 남은 제품",
                     "성분4 전체 원외 금액 (억/월)", "제품 1개 몫 (근사, 억/월)"],
                    [[r.product_code, _name(r.product_name), _name(r.firm), r.c4 or "",
                      DELIST_KIND.get(r.kind, r.kind or "") + (f" ← {r.counterpart}, 공백 {int(r.gap_months)}개월"
                                                                 if t == "SUCCESSION" and isinstance(r.counterpart, str) else ""),
                      _d(r.effective_date) or f"{_d(r.effective_date_lo)} ~ {_d(r.effective_date_hi)}",
                      DATE_SOURCE.get(r.date_source, r.date_source or ""), _f(r.same_code_remaining), _f(r.same_c4_remaining),
                      _amt(r.base_amt), _small(r.product_share, 2)]
                     for r in gt.head(LIST_ROWS).itertuples()])
        if len(gt) > LIST_ROWS:
            L.append(f"\n외 {len(gt) - LIST_ROWS:,}건 (`events.csv`).")
        L.append("")

    # 5. 대체조제 그룹 순위 (F6)
    shown = cr[cr["widest_max_min_ratio"] >= SUBST_MIN_RATIO]
    dropped = int((cr.head(TOP_C4)["widest_max_min_ratio"] < SUBST_MIN_RATIO).sum())
    body = shown[shown["rank"] <= TOP_C4]
    L += [f"## 5. 대체조제 그룹 순위 (성분4 단위) (사용통계 기준 {win[0]} ~ {win[-1]})", "",
          f"{month} 대체조제 목록의 reimbursed 제품, 제품 2개 이상 그룹, 성분4 원외 사용통계가 위 {USAGE_WINDOW}개월 모두 관측된 "
          f"성분4 {len(cr):,}개 중 원외 금액 상위 {TOP_C4}개. 가격은 그 성분4에서 최고/최저 비율이 가장 큰 그룹의 {month} 목록 상한금액이고, "
          f"가격 차는 그 달 목록만으로 계산했다 (실제 청구가 차이가 아니다). 최고/최저 {SUBST_MIN_RATIO} 미만 {dropped}개는 본문에서 뺐다 "
          "(순위는 빼기 전 순위). 그룹 코드·그룹 수는 `subst_rank_c4.csv`.", ""]
    L += _table(["순위", "성분", f"원외 금액 (참고, 12개월 {win[0]} ~ {win[-1]}, 억 원)", "제품 수", "최저가", "최고가", "최고/최저"],
                [[str(int(r.rank)), _name(r.short_name), _f(r.out_amt_12m_eok), _f(int(r.widest_n_products)),
                  _f(r.min_price), _f(r.max_price), f"{r.widest_max_min_ratio:.2f}"] for r in body.itertuples()]) + [""]

    # 6. 한계
    L += ["## 6. 자료 한계", "",
          "- 비고 없이 바뀐 변화는 그 스냅샷 달 1일을 유효일로 보고, 삭제·양도양수 구분은 다음 달 스냅샷으로 바뀔 수 있다.",
          f"- 사용통계는 발간 시점 공개분(~{b[-1]})만 쓴다. 영향 규모는 제품 수 비중으로 근사한 전국 성분 단위 추정이며, 기준 기간 이후 시장 변화는 반영되지 않는다.",
          "- 실제 월간 보고서는 사용통계 초본을 쓰지만 이 리포트는 2026-11 수정본을 썼다. 초본과 수정본의 차이는 측정하지 못했다.",
          "- 제품별 판매량·약국 재고·도매 유통은 데이터에 없다.", ""]

    # 읽는 법
    L += ["## 이 리포트를 읽는 법", "",
          "- Q1 이번 달 무엇이 바뀌나: §2 새 예고, §4 이번 스냅샷에서 관측된 급여중지·중지해제·급여삭제·코드 이어받기 (목록 ≤ 발간월).",
          "- Q2 믿어도 되나: 같은 유형 예고의 과거 최종 실현율 B와 현재 시점 집행률 A (발간월 시점 원장, RULES §7).",
          f"- Q3 얼마나 큰 일인가: 영향 규모 (사용통계 ≤ 발간월 − {USAGE_LAG}개월).",
          "- \"이번 달 주목할 것\"을 고른 규칙 (분류별 최대 1줄, 합계 최대 3줄, 해당 없으면 쓰지 않는다):"]
    L += [f"  {i}. {t}" for i, t in enumerate(ATTENTION_RULES, 1)]
    L += ["- 성분 묶음: 표시 이름에서 괄호 내용·함량·염과 수화물 단어를 뺀 이름이 같으면 한 성분으로 본다 (복합제는 묶지 않음, "
          "`config/report.yaml`). 요약과 주목할 것에서만 묶는다.", ""]
    L += [f"<sub>발간일 {month}-01 · 목록 기준 {month} · 사용통계 기준 ~{b[-1]} ({_ym_kor(b[-1])} 유통분석에 해당)</sub>", ""]
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


def write_groups(month: str = "2025-12", out: Path = OUT_GROUPS) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / "ingredient_groups.csv"
    ingredient_groups(month).to_csv(p, index=False, lineterminator="\n")
    return p


def main() -> None:
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == "--groups":
        print(write_groups(a[1]))
        return
    if len(a) != 1 or len(a[0]) != 7:
        raise SystemExit("사용: python -m pbd.ledger.s6_report YYYY-MM | --groups YYYY-MM")
    print(write(a[0]))


if __name__ == "__main__":
    main()
