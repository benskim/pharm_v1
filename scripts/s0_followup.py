"""S0 추가 확인 3건 (점검만). 결과 표는 outputs/s0/followup_*.csv.

1. 약효분류 후보(114, 141, 222, 229, 611~619)의 성분 구성, 2022 원외 청구수량, 계절성, 96개월 관측 성분 수
2. 종료일 없는 R3 급여정지: 복귀 뒤에도 비고가 남는 사례가 있는지
3. 대체조제 `생동대조약 여부`: 월별 분포, 오렌지북 표시와의 일치

사용: python scripts/s0_followup.py   (make s0-followup)
"""
from __future__ import annotations

import datetime as dt
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from s0_inspect import BENEFIT, SUBST, CODEMAP, ORANGE, USAGE, blank, dump, scan_note, write_csv  # noqa: E402

CANDIDATES = [114, 141, 222, 229] + list(range(611, 620))


# ---------------------------------------------------------------- 1. 약효분류 후보
def drug_class(con):
    # 성분4 → 분류: 그 성분의 제품(고유 제품코드)이 가장 많은 분류. 동점이면 작은 번호
    con.execute(f"""create table pc_cls as
        select distinct lpad(제품코드,9,'0') pc, substr(주성분코드,1,4) c4, 주성분코드 gc, 주성분명 gnm,
               cast(coalesce(분류, case when 기준일자 like '2022-11%' then 식약분류 end) as int) cls
        from {BENEFIT}""")
    con.execute("""create table c4_cls as
        with n as (select c4, cls, count(distinct pc) np from pc_cls where cls is not null group by 1,2)
        select c4, arg_max(cls, np*10000 - cls) cls, count(*) n_cls, sum(np) np_all,
               max(np) np_main from n group by 1""")
    # 성분4의 대표 이름: 제품이 가장 많은 9자리 주성분코드의 주성분명
    con.execute("""create table c4_name as
        with n as (select c4, gc, any_value(gnm) gnm, count(distinct pc) np from pc_cls group by 1,2)
        select c4, arg_max(gnm, np) name from n group by 1""")
    con.execute(f"""create table uo as
        select WK_COMPN_4 c4, STD_YYYYMM ym, sum(cast(PRSCRPTN_TOT_USE as double)) qty
        from {USAGE} where OUT_IN_HOS='OUT' group by 1,2""")
    con.execute("""create table c4_obs as select c4, count(*) n_months, sum(qty) filter (where ym like '2022%') qty2022 from uo group by 1""")
    cand = ",".join(map(str, CANDIDATES))
    dump(con, "followup_class_summary.csv", f"""
        with base as (select cc.cls, cc.c4, cc.n_cls, o.n_months, o.qty2022 from c4_cls cc left join c4_obs o using(c4)
                      where cc.cls in ({cand})),
        m as (select cc.cls, uo.ym, sum(uo.qty) qty from c4_cls cc join uo using(c4)
              where cc.cls in ({cand}) and uo.ym between '202201' and '202512' group by 1,2),
        s as (select cls, avg(qty) filter (where cast(right(ym,2) as int) in (11,12,1,2,3)) winter,
                          avg(qty) filter (where cast(right(ym,2) as int) in (6,7,8)) summer from m group by 1)
        select b.cls, count(*) n_c4_listed, count(n_months) n_c4_in_usage,
               count(*) filter (where n_months=96) n_c4_96m,
               count(*) filter (where n_cls>1) n_c4_multi_class,
               round(sum(qty2022)) out_qty_2022,
               round(sum(qty2022) filter (where n_months=96)) out_qty_2022_96m,
               round(s.winter/s.summer, 3) winter_summer_ratio
        from base b left join s using(cls) group by b.cls, s.winter, s.summer order by b.cls""", show=13)
    dump(con, "followup_class_top10.csv", f"""
        with r as (select cc.cls, cc.c4, nm.name, o.qty2022, o.n_months, cc.n_cls,
                          row_number() over (partition by cc.cls order by o.qty2022 desc nulls last, cc.c4) rk
                   from c4_cls cc left join c4_obs o using(c4) left join c4_name nm using(c4) where cc.cls in ({cand}))
        select cls, rk, c4, name, round(qty2022) out_qty_2022, n_months, n_cls from r where rk<=10 order by cls, rk""")
    # 성분별 계절성도 함께 (주력 묶음 고를 때 참고)
    dump(con, "followup_class_c4_season.csv", f"""
        with m as (select cc.cls, uo.c4, uo.ym, uo.qty from c4_cls cc join uo using(c4)
                   where cc.cls in ({cand}) and uo.ym between '202201' and '202512')
        select cls, c4, round(avg(qty) filter (where cast(right(ym,2) as int) in (11,12,1,2,3)) /
                          nullif(avg(qty) filter (where cast(right(ym,2) as int) in (6,7,8)),0), 3) winter_summer_ratio
        from m group by 1,2 order by 1,2""")


# ---------------------------------------------------------------- 2. 종료일 없는 R3
def r3_persistence(con):
    """종료일 없는 R3 토큰(제품, 정지일)이 언제까지 비고에 남는지, 복귀를 시사하는 증거와 겹치는지 센다.

    복귀 증거로 쓸 수 있는 것 (데이터 안에서):
      a. 같은 제품에 R6 '급여정지 해제(날짜)' 비고가 나온 뒤에도 같은 R3 토큰이 남음
      b. 같은 제품·같은 달에 다른 목록에는 같은 R3 토큰이 없음 (두 목록 불일치)
      c. R3 토큰이 사라졌다가 같은 날짜로 다시 나타남
    """
    rows = con.execute(f"""
        select 'benefit' lst, 기준일자, lpad(제품코드,9,'0') pc, trim(비고) note from {BENEFIT} where not {blank('비고')}
        union all
        select 'substitute', 기준일자, lpad(제품코드,9,'0'), trim("비고(급여정지)") from {SUBST} where not {blank('"비고(급여정지)"')}""").fetchall()
    months = sorted({r[0][:7] for r in con.execute(f"select distinct 기준일자 from {BENEFIT}").fetchall()})
    midx = {m: i for i, m in enumerate(months)}
    present = defaultdict(set)   # (lst, pc) -> 목록에 있던 월 인덱스
    for lst, src in (("benefit", BENEFIT), ("substitute", SUBST)):
        for pc, ym in con.execute(f"select distinct lpad(제품코드,9,'0'), substr(기준일자,1,7) from {src}").fetchall():
            present[(lst, pc)].add(midx[ym])
    r3 = defaultdict(set)        # (lst, pc, date) -> 월 인덱스
    r6 = defaultdict(list)       # (lst, pc) -> [(월, 해제일)]
    for lst, base, pc, note in rows:
        mi = midx[base[:7]]
        for p in scan_note(note):
            for t, d in p["tokens"]:
                if t == "R3" and d:
                    r3[(lst, pc, d)].add(mi)
                if t == "R6" and d:
                    r6[(lst, pc)].append((mi, d))
    out, a_cases, b_rows, c_cases = [], [], [], []
    for (lst, pc, d), ms in r3.items():
        ms = sorted(ms)
        span = ms[-1] - ms[0] + 1
        gaps = span - len(ms)
        on_list = present[(lst, pc)]
        gap_on_list = sum(1 for m in range(ms[0], ms[-1] + 1) if m not in ms and m in on_list)
        after = "목록 끝까지" if ms[-1] == len(months) - 1 else ("토큰 사라짐·제품 잔존" if (ms[-1] + 1) in on_list else "제품 삭제")
        out.append((lst, pc, d.isoformat(), months[ms[0]], months[ms[-1]], len(ms), gap_on_list, after))
        if gap_on_list:
            c_cases.append((lst, pc, d.isoformat(), months[ms[0]], months[ms[-1]], len(ms), gap_on_list))
        for rm, rd in r6.get((lst, pc), []):
            if rd >= d and any(m > rm for m in ms):
                a_cases.append((lst, pc, d.isoformat(), rd.isoformat(), months[rm], months[ms[-1]]))
        other = "substitute" if lst == "benefit" else "benefit"
        for m in ms:
            if m in present[(other, pc)] and m not in r3.get((other, pc, d), set()):
                b_rows.append((lst, pc, d.isoformat(), months[m]))
    write_csv("followup_r3_tokens.csv", ["list", "product_code", "suspend_date", "first_month", "last_month",
                                         "n_months", "months_missing_while_listed", "after_last"], sorted(out))
    write_csv("followup_r3_after_r6.csv", ["list", "product_code", "suspend_date", "release_date", "release_month", "r3_last_month"], a_cases)
    write_csv("followup_r3_flipflop.csv", ["list", "product_code", "suspend_date", "first_month", "last_month", "n_months", "months_missing_while_listed"], c_cases)
    write_csv("followup_r3_cross_list_mismatch.csv", ["list_with_token", "product_code", "suspend_date", "month_other_list_without"], b_rows)
    from collections import Counter
    print(f"\nR3 (제품, 정지일) 토큰: {len(out)}")
    print("  마지막 이후:", dict(Counter((o[0], o[7]) for o in out)))
    print(f"  a. R6 해제 뒤에도 R3 토큰 잔존: {len(a_cases)}건")
    print(f"  b. 같은 달 다른 목록에는 없음: {len(b_rows)}행 / 토큰 {len({r[:3] for r in b_rows})}개")
    print(f"  c. 사라졌다 다시 나타남: {len(c_cases)}건")
    dur = Counter(min(o[5] // 12, 4) for o in out if o[0] == 'benefit')
    print("  급여목록 토큰 지속 연수(0=12개월 미만, 4=48개월):", dict(sorted(dur.items())))
    return out


# ---------------------------------------------------------------- 3. 생동대조약 여부
def ref_drug(con):
    dump(con, "followup_ref_drug_by_month.csv", f"""
        select substr(기준일자,1,7) ym, count(*) n_rows,
               count(*) filter (where coalesce("생동대조약 여부","생동대조약여부")='○') ref_o,
               count(*) filter (where coalesce("생동대조약 여부","생동대조약여부") is null) ref_null,
               count(*) filter (where coalesce("생동대조약 여부","생동대조약여부") not in ('○')) ref_other,
               count(*) filter (where 의약동등='1') eq1, count(*) filter (where 의약동등='4') eq4, count(*) filter (where 의약동등='6') eq6,
               count(*) filter (where coalesce("생동대조약 여부","생동대조약여부")='○' and 의약동등 not in ('4','6')) ref_o_eq_other
        from {SUBST} group by 1 order by 1""", show=0)
    con.execute(f"""create table ob as select 기준코드 item, bool_or(공고대조약='O') ob_ref, bool_or(제네릭의약품='O') ob_generic
                    from {ORANGE} group by 1""")
    con.execute(f"""create table cmi as select distinct lpad(cast(cast("제품코드(개정후)" as bigint) as varchar),9,'0') pc, 품목기준코드 item
                    from {CODEMAP} where "제품코드(개정후)" is not null""")
    # 제품별 마지막 관측월의 값. 품목 연결은 (1) 대체조제 ②품목기준코드 (2) code_map
    con.execute(f"""create table sref as
        select lpad(제품코드,9,'0') pc, arg_max(coalesce(coalesce("생동대조약 여부","생동대조약여부")='○', false), 기준일자) is_ref,
               arg_max(cast("②품목기준코드" as bigint), 기준일자) item_sub, max(substr(기준일자,1,7)) last_ym
        from {SUBST} group by 1""")
    dump(con, "followup_ref_drug_vs_orange.csv", """
        with t as (select s.pc, s.is_ref, coalesce(s.item_sub, (select min(item) from cmi where cmi.pc=s.pc)) item,
                          case when s.item_sub is not null then 'subst_item' when exists(select 1 from cmi where cmi.pc=s.pc) then 'code_map' else 'none' end path
                   from sref s)
        select path, is_ref, ob.item is not null linked,
               case when ob.item is null then '-' when ob.ob_ref then '공고대조약 O' else '공고대조약 X' end ob_ref,
               case when ob.item is null then '-' when ob.ob_generic then '제네릭 O' else '제네릭 X' end ob_generic,
               count(*) n_products
        from t left join ob using(item) group by all order by 1,2,3,4,5""", show=20)
    # 대조약 표시가 바뀐 제품
    dump(con, "followup_ref_drug_changes.csv", f"""
        with m as (select lpad(제품코드,9,'0') pc, substr(기준일자,1,7) ym, coalesce(coalesce("생동대조약 여부","생동대조약여부")='○', false) r from {SUBST}),
        w as (select pc, ym, r, lag(r) over (partition by pc order by ym) pr from m)
        select count(distinct pc) filter (where pr is not null and r<>pr) products_changed,
               count(*) filter (where pr=false and r=true) to_ref, count(*) filter (where pr=true and r=false) from_ref from w""", show=1)


# ---------------------------------------------------------------- 4. 2차 답변: 두 목록 합친 R3 공백, 날짜 불일치, 역할 근거 충돌
def r3_combined(con):
    """사용자 규칙(2차 답변 2): 두 목록 어디에도 정지 토큰이 없는 달이 2개월 연속이면 복귀.

    (제품, 정지일) 토큰을 두 목록 합쳐 추적한다. 제품이 어느 목록에든 있는 달만 센다.
    """
    rows = con.execute(f"""
        select 'benefit' lst, 기준일자, lpad(제품코드,9,'0') pc, trim(비고) note from {BENEFIT} where not {blank('비고')}
        union all
        select 'substitute', 기준일자, lpad(제품코드,9,'0'), trim("비고(급여정지)") from {SUBST} where not {blank('"비고(급여정지)"')}""").fetchall()
    months = sorted({r[0][:7] for r in con.execute(f"select distinct 기준일자 from {BENEFIT}").fetchall()})
    midx = {m: i for i, m in enumerate(months)}
    last = len(months) - 1
    listed = defaultdict(set)
    for src in (BENEFIT, SUBST):
        for pc, ym in con.execute(f"select distinct lpad(제품코드,9,'0'), substr(기준일자,1,7) from {src}").fetchall():
            listed[pc].add(midx[ym])
    tok = defaultdict(set)            # (pc, date) -> 월 (어느 목록이든)
    by_list_month = defaultdict(set)  # (lst, pc, 월) -> 그 달 그 목록의 R3 정지일들
    r6 = defaultdict(list)
    for lst, base, pc, note in rows:
        mi = midx[base[:7]]
        for p in scan_note(note):
            for t, d in p["tokens"]:
                if t == "R3" and d:
                    tok[(pc, d)].add(mi)
                    by_list_month[(lst, pc, mi)].add(d)
                if t == "R6" and d:
                    r6[pc].append((mi, d))
    gaps, tails = [], Counter()
    for (pc, d), ms in tok.items():
        ms = sorted(ms)
        # 중간 공백: 제품이 목록에 있는데 토큰이 없는 연속 구간
        run = []
        for m in range(ms[0], ms[-1] + 1):
            if m in ms:
                if run:
                    gaps.append((pc, d.isoformat(), months[run[0]], months[run[-1]], len(run)))
                    run = []
            elif m in listed[pc]:
                run.append(m)
        # 마지막 등장 이후
        after = [m for m in range(ms[-1] + 1, last + 1) if m in listed[pc]]
        if ms[-1] == last:
            tails["토큰이 2025-12까지 남음"] += 1
        elif not after:
            tails["토큰 다음 달 제품 삭제"] += 1
        elif len(after) >= 2 and after[0] == ms[-1] + 1 and after[1] == ms[-1] + 2:
            tails["2개월 연속 없음 → 복귀(b)"] += 1
        elif after == [last] and after[0] == ms[-1] + 1:
            tails["분석 끝 1개월만 없음 → 복귀 미확정"] += 1
        else:
            tails["1개월 없음 뒤 제품 삭제 또는 공백"] += 1
    write_csv("followup2_r3_gaps.csv", ["product_code", "suspend_date", "gap_first", "gap_last", "gap_months"], sorted(gaps))
    write_csv("followup2_r3_tails.csv", ["category", "n_tokens"], sorted(tails.items()))
    g = Counter(min(x[4], 3) for x in gaps)
    print(f"\n두 목록 합친 (제품, 정지일) 토큰 {len(tok)}개")
    print(f"  중간 공백 (다시 나타남): 1개월 {g.get(1, 0)}건, 2개월 {g.get(2, 0)}건, 3개월 이상 {g.get(3, 0)}건")
    for x in [x for x in gaps if x[4] >= 2][:10]:
        print("   ", x)
    print("  마지막 등장 이후:", dict(tails))
    print(f"  R6 해제 비고가 있는 제품: {len(r6)}")
    # 날짜 불일치: 같은 제품·같은 달에 두 목록 모두 R3가 있는데 정지일 집합이 다름
    diff, both = [], 0
    for (lst, pc, m), ds in by_list_month.items():
        if lst != "benefit":
            continue
        other = by_list_month.get(("substitute", pc, m))
        if other is None:
            continue
        both += 1
        if ds != other:
            diff.append((pc, months[m], ";".join(sorted(x.isoformat() for x in ds)), ";".join(sorted(x.isoformat() for x in other))))
    write_csv("followup2_r3_date_mismatch.csv", ["product_code", "month", "benefit_dates", "substitute_dates"], sorted(diff))
    print(f"  두 목록 모두 R3가 있는 (제품, 월) {both}개 중 정지일이 다른 경우 {len(diff)}개")
    for x in sorted(diff)[:5]:
        print("   ", x)


def product_role(con):
    """사용자 정의(2차 답변 3): reference / generic / NULL. 근거 충돌만 센다 (컬럼은 만들지 않음)."""
    con.execute(f"""create or replace table rs as select lpad(제품코드,9,'0') pc, cast("②품목기준코드" as bigint) item,
        coalesce(bool_or(coalesce("생동대조약 여부","생동대조약여부")='○'), false) s_ref,
        coalesce(bool_or("생동인정여부(공고번호)" like '○%'), false) s_gen,
        string_agg(distinct 의약동등, ',' order by 의약동등) eq from {SUBST} group by 1,2""")
    con.execute(f"""create or replace table ro as select 기준코드 item, coalesce(bool_or(공고대조약='O'), false) o_ref,
        coalesce(bool_or(제네릭의약품='O'), false) o_gen from {ORANGE} group by 1""")
    dump(con, "followup2_role_conflict.csv", """
        select case when s_ref and s_gen then '대체조제 안에서 대조약○·생동인정○'
                    when s_ref and o_gen then '대체조제 대조약○ vs 오렌지북 제네릭'
                    when s_gen and o_ref then '대체조제 생동인정○ vs 오렌지북 공고대조약'
                    when o_ref and o_gen then '오렌지북 안에서 공고대조약·제네릭' end kind,
               count(*) n_products, string_agg(pc || '(' || eq || ')', ' ' order by pc) filter (where true) examples
        from rs left join ro using(item)
        where (s_ref or coalesce(o_ref,false)) and (s_gen or coalesce(o_gen,false)) group by 1 order by 2 desc""", show=4)


def main():
    import duckdb
    con = duckdb.connect()
    con.execute("SET threads=8"); con.execute("SET enable_progress_bar=false")
    print("## 1. 약효분류 후보"); drug_class(con)
    print("\n## 2. 종료일 없는 R3"); r3_persistence(con)
    print("\n## 3. 생동대조약 여부"); ref_drug(con)
    print("\n## 4. 2차: 두 목록 합친 R3"); r3_combined(con)
    print("\n## 5. 2차: product_role 근거 충돌"); product_role(con)


if __name__ == "__main__":
    main()
