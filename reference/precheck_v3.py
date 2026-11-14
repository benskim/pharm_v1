"""PRECHECK v3 — 원본 스냅샷(급여목록·대체조제 목록) 점검. DESIGN_v3.md의 모든 숫자를 재현한다.
사용: python precheck_v3.py <parquet 폴더>   (파일명에 아래 키워드가 포함되면 된다)
필요: duckdb"""
import duckdb, glob, sys, os
D = sys.argv[1] if len(sys.argv) > 1 else "data/raw"
def f(key):
    m = [p for p in glob.glob(os.path.join(D, "*.parquet")) if key in os.path.basename(p)]
    assert m, f"{key} 파일 없음"; return m[0]
con = duckdb.connect()
def q(title, sql):
    print(f"\n## {title}"); con.execute(sql); cols = [d[0] for d in con.description]
    print(" | ".join(cols))
    for r in con.fetchall(): print(" | ".join(str(x)[:60] for x in r))
SUSP = "(note like '%급여정지%' and note not like '%해제%')"
STAY = "(note like '%집행정지%' and note not like '%해제%')"
con.execute(f"""create table b as select lpad(제품코드,9,'0') pc, 제품코드 pc_raw, substr(기준일자,1,7) ym, cast(기준일자 as date) sd, 상한금액 price,
 case when 비고 is null or trim(비고) in ('','nan','None') then null else trim(비고) end note,
 주성분코드 gc, substr(주성분코드,1,4) c4, 제품명 nm, 업체명 firm, 규격 spec, 전일 rx, 주성분명 gnm from read_parquet('{f("benefit_snapshot")}')""")
con.execute("create table mon as select ym, row_number() over(order by ym) mi from (select distinct ym from b)")
con.execute("create table bb as select b.*, mi from b join mon using(ym)")
con.execute("create table life as select pc, min(mi) f, max(mi) l from bb group by 1")
con.execute(f"""create table ss as select lpad(제품코드,9,'0') pc, substr(기준일자,1,7) ym, mi, 상한금액 sprice, nullif(trim("비고(급여정지)"),'') snote,
 cast("②품목기준코드" as bigint) item, "①주성분 및 투여제형코드" grp, 주성분코드 gc, 업체명 firm, "생동대조약 여부" ref
 from read_parquet('{f("substitute_snapshot")}') s join mon on mon.ym=substr(s.기준일자,1,7)""")
con.execute(f"""create table u as select substr(cast(STD_YYYYMM as varchar),1,4)||'-'||substr(cast(STD_YYYYMM as varchar),5,2) ym, lpad(cast(WK_COMPN_4 as varchar),4,'0') c4,
 OUT_IN_HOS io, PRSCRPTN_AMT amt, PRSCRPTN_TOT_USE use from read_parquet('{os.path.join(D,"*fact_cmpn_stat_*.parquet")}')""")
con.execute("create table pm as select c4, ym, sum(amt)/sum(use) up from u where io='OUT' group by 1,2")

q("F1 코드 폭: 원본 고유 제품 수 vs 0 채움 후", "select count(distinct pc_raw) raw, count(distinct pc) zfill, sum((length(pc_raw)=8)::int) rows8 from b")
q("F1 (제품,월) 중복", "select count(*) dup from (select pc, ym from b group by 1,2 having count(*)>1)")
q("F2 비고 유형별 행·제품 수", f"""select case when note='산정불가' then '산정불가' when note like '%해제%' then '해제' when {STAY} and note like '%원%' then '집행정지+가격예고'
 when {STAY} then '집행정지' when {SUSP} then '급여정지' when note like '%등재%' then '등재일' when note like '%원%' then '가격예고' else '기타' end k, count(*) n_rows, count(distinct pc) np from bb where note is not null group by 1 order by 2 desc""")
q("F3 등재일 비고: 진입 첫 달 여부", "select (mi=f) first_month, (f=1) left_trunc, count(*) n from bb join life using(pc) where note like '%등재%' group by 1,2 order by 1,2")
q("F3 진입 1,697건의 첫 달 비고", "select case when note like '%등재%' then '등재일 있음' when note is null then '없음' else note end k, count(*) n from bb join life using(pc) where f>1 and mi=f group by 1 order by 1")
q("F3 대체조제 목록의 등재일 비고(급여목록 비고와 대조)", "select ss.ym, b.note 급여목록비고, ss.snote 대체목록비고, count(*) n from ss join bb b on b.pc=ss.pc and b.mi=ss.mi where ss.snote like '%등재%' group by 1,2,3 order by 1,2,3")
con.execute(r"""create table pn as select *, try_cast(replace(regexp_extract(note,'(\d[\d,]*)원',1),',','') as double) amt2, regexp_extract(note,'(\d{2,4}\.\d{1,2}\.\d{1,2})\.?,? *\d[\d,]*원',1) d from bb where note like '%원%'""")
q("F4 가격 예고: 비고 날짜와 익월 가격 일치", """select p.ym 비고월, min(d) 적용일, count(*) n, sum((n.price=p.amt2)::int) 익월일치, sum((n.pc is null)::int) 익월부재, sum((p.price=p.amt2)::int) 당월일치 from pn p left join bb n on n.pc=p.pc and n.mi=p.mi+1 group by 1 order by 1""")
q("F4 검증: 예고 비중 50% 이상 성분의 원외 단가(비고월-2 ~ +2)", """with cs as (select c4, mi, ym, sum((note like '%원%' and note not like '%정지%')::int)*1.0/count(*) sh, count(*) np, min(case when note like '%원%' and note not like '%정지%' then regexp_extract(note,'(\\d{2,4}\\.\\d{1,2}\\.\\d{1,2})',1) end) d from bb where ym in ('2024-04','2024-09','2025-06') group by 1,2,3 having sh>=0.3)
 select cs.c4, cs.ym 비고월, d 적용일, np, round(sh,2) sh, list(round(up,1) order by m.mi) 단가 from cs join mon m on m.mi between cs.mi-2 and cs.mi+2 join pm on pm.c4=cs.c4 and pm.ym=m.ym group by 1,2,3,4,5 order by 2,1""")
q("F5 급여정지 구간", f"""with s as (select pc, min(mi) a, max(mi) z from bb where {SUSP} group by pc) select case when a=1 then '창 시작 전부터' else '창 안에서 시작' end st,
 case when z=24 then '창 끝까지' when l=z then '정지 중 목록 삭제' else '해제' end en, count(*) np from s join life using(pc) group by 1,2 order by 1,2""")
q("F5 집행정지 구간", f"""with s as (select pc, min(mi) a, max(mi) z from bb where {STAY} group by pc) select case when a=1 then '창 시작 전부터' else '창 안에서 시작' end st,
 case when z=24 then '창 끝까지' when l=z then '정지 중 목록 삭제' else '해제' end en, count(*) np from s join life using(pc) group by 1,2 order by 1,2""")
q("F5 집행정지 해제 직후 가격", f"""with s as (select pc, max(mi) z from bb where {STAY} group by pc) select m.ym 해제관측월, count(*) np, sum((n.price<c.price)::int) 인하, round(median(n.price/c.price-1)*100,1) med_pct
 from s join bb c on c.pc=s.pc and c.mi=s.z join bb n on n.pc=s.pc and n.mi=s.z+1 join mon m on m.mi=s.z+1 group by 1 order by 1""")
q("F5 두 목록의 가격 불일치(급여목록 비고별)", "select ss.ym, b.note, count(*) n, round(median(ss.sprice/b.price-1)*100,1) med_gap_pct from ss join bb b on b.pc=ss.pc and b.mi=ss.mi where b.price<>ss.sprice group by 1,2 order by 1,2")
q("F6 시점에 따라 바뀌는 속성(제품 수)", "select sum((nf>1)::int) 업체명, sum((nn>1)::int) 제품명, sum((ng>1)::int) 주성분코드 from (select pc, count(distinct firm) nf, count(distinct nm) nn, count(distinct gc) ng from bb group by 1)")
con.execute(f"""create table lk1 as select pc, min(item) item from (select lpad(cast(cast("제품코드(개정후)" as bigint) as varchar),9,'0') pc, 품목기준코드 item from read_parquet('{f("dim_drug_master")}') where "제품코드(개정후)" is not null
 union select pc, item from ss) group by pc having count(distinct item)=1""")
con.execute("create table ent as select bb.*, item from bb join life using(pc) left join lk1 using(pc) where f>1 and mi=f")
con.execute("create table ext as select bb.*, item, l+1 gone from bb join life using(pc) left join lk1 using(pc) where l<24 and mi=l")
con.execute("""create table entc as select e.*, case
 when exists(select 1 from ext x where x.item=e.item and abs(x.gone-e.mi)<=1) then '1 동일 품목 코드 교체'
 when exists(select 1 from ext x where x.gc=e.gc and abs(x.gone-e.mi)<=1 and x.firm<>e.firm and split_part(x.nm,'_',1)=split_part(e.nm,'_',1)) then '2 양도양수 추정'
 when exists(select 1 from ext x where x.gc=e.gc and abs(x.gone-e.mi)<=1 and x.firm=e.firm) then '3 동일 업체 교체'
 when exists(select 1 from bb o join lk1 k on k.pc=o.pc where k.item=e.item and o.pc<>e.pc and o.mi<e.mi) then '4 기등재 품목의 새 코드'
 when e.item is null then '5 품목 연결 불가' else '6 실질 신규 품목' end kind from ent e""")
q("F7 진입 재분류", "select kind, count(*) n, round(100.0*count(*)/sum(count(*)) over(),1) pct from entc group by 1 order by 1")
q("F7 퇴출 재분류", """select case when exists(select 1 from ent e where (e.item=x.item or (e.gc=x.gc and (e.firm=x.firm or split_part(e.nm,'_',1)=split_part(x.nm,'_',1)))) and abs(x.gone-e.mi)<=1) then '코드 교체·양도양수'
 when exists(select 1 from bb o join lk1 k on k.pc=o.pc where k.item=x.item and o.pc<>x.pc and o.mi=x.gone) then '품목 잔존' else '실질 퇴출' end k, (x.gone in (11,23)) 정비월, count(*) n from ext x group by 1,2 order by 1,2""")
q("F8 실질 신규의 성분×월 군집", """with g as (select c4, mi, count(distinct firm) nf from entc where kind like '6%' group by 1,2)
 select case when nf>=10 then '10+' when nf>=5 then '5~9' when nf>=2 then '2~4' else '1' end 신규업체수, count(*) 이벤트, sum((c4 in (select c4 from u))::int) 사용정보관측, sum((c4 in (select c4 from u) and mi<=18)::int) 사후6개월 from g group by 1 order by 1""")
q("F8 사례: empagliflozin(6282) 목록 vs 사용정보", """select b.ym, count(*) 제품, count(distinct firm) 업체, round(min(price)) 최저가, round(max(price)) 최고가, round(max(pm.up),1) 원외단가 from bb b left join pm on pm.c4=b.c4 and pm.ym=b.ym where b.c4='6282' and b.mi>=20 group by 1 order by 1""")
q("F9 비고 없는 가격 변화: 단가가 전월/관측월 중 언제 움직였나", """with ch as (select c.c4, c.mi, a.price p0, c.price p1, (a.note like '%원%') noted, c.pc from bb a join bb c on c.pc=a.pc and c.mi=a.mi+1 where a.price<>c.price),
 cm as (select b.c4, b.mi, count(*) np, count(ch.pc) nch, avg(ch.p1/ch.p0-1) chg, bool_or(coalesce(ch.noted,false)) noted from bb b left join ch on ch.pc=b.pc and ch.mi=b.mi group by 1,2),
 pmi as (select pm.*, mi from pm join mon using(ym))
 select sum((abs(p1.up/p0.up-1)>abs(p2.up/p1.up-1))::int) 전월, sum((abs(p1.up/p0.up-1)<=abs(p2.up/p1.up-1))::int) 관측월, count(*) n from cm join pmi p0 on p0.c4=cm.c4 and p0.mi=cm.mi-2 join pmi p1 on p1.c4=cm.c4 and p1.mi=cm.mi-1 join pmi p2 on p2.c4=cm.c4 and p2.mi=cm.mi
 where not noted and nch*1.0/np>=0.5 and abs(chg)>=0.03 and np>=3""")
q("F10 대체조제 그룹(①코드×주성분코드) 가격 분산, 2025-12", """with g as (select grp, gc, count(*) n, min(sprice) mn, max(sprice) mx from ss where mi=24 group by 1,2)
 select count(*) 그룹, sum((n>=2)::int) 제품2개이상, round(median(case when n>=2 then mx/mn end),2) 최고_최저_중앙값, round(quantile_cont(case when n>=2 then mx/mn end,0.9),2) p90 from g""")
q("F10 대체조제 목록 편입 시차(개월)", """with sl as (select pc, min(mi) sf from ss group by pc) select case when l.f=1 and sf=1 then '창 시작' when sf=l.f then '동시' else '급여 후 편입' end k, count(*) np, round(avg(sf-l.f),1) avg_lag from sl join life l using(pc) group by 1 order by 1""")
