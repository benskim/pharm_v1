"""P2 T1 예측 마트 (PLAN_P2 §2·§7, 최소판: 주력 묶음 성분 × 전국).

- fc_panel: 성분4 × 월(2018-01 ~ 2025-12) 전국 원외 격자. 원본에 행이 없는 칸(비공개)은 0이 아니라 미관측:
  claim_qty = NULL, observed = false (CLAUDE.md 데이터 규칙, DECISIONS 2026-11-14 usage_c4_month).
- usable: 학습·평가에 쓸 수 있는 칸. 미관측이 아니고, 첫 관측월이 아니어야 한다 (DATA.md: 업체 3곳을 넘는 달에 성분이
  "나타나는" 것은 수요 급증이 아니다). 단 첫 관측월이 자료 시작월(2018-01)이면 등장이 아니라 자료의 시작이므로 쓴다.
- fc_target: 대상 성분. 탈락 단계표 fc_target_steps.
- 대상 선정 (PLAN_P2 §2): ① 사용통계에 있음 → ② 원외 관측 있음 → ③ 주력 묶음 분류(141·222·229·618) → ④ 96개월 원외 모두 관측
  → ⑤ 2022 원외 청구수량 상위 p2.top_n (96개월 관측 성분 전체에서 순위).
  성분4 → 분류: 그 성분의 제품(고유 제품코드)이 가장 많은 분류, 같으면 작은 번호 (S0 추가 확인 10.1과 같은 규칙).
사용: python -m pbd.forecast.t1_panel  (make p2-t1)
"""
from __future__ import annotations

import csv

from pbd.common import DB_PATH, RAW, ROOT, connect, load_yaml, sql_files, usage_files

P = load_yaml("params.yaml")["p2"]
MAIN_CLASSES = (141, 222, 229, 618)
OUT = ROOT / "outputs/p2"
RAW_GLOB = None   # None = 기본 연도(2018~2025) 원본 파일 목록 (pbd.common.usage_files, 2026-11-19 사용자 결정)


def usable_flag(month: str, observed: bool, first_month: str | None, start: str) -> bool:
    """학습·평가에 쓸 수 있는 칸인가. 미관측 칸과 (자료 시작월이 아닌) 첫 관측월은 뺀다"""
    if not observed:
        return False
    if first_month is not None and month == first_month and first_month > start:
        return False
    return True


def build_panel(con, start: str = P["start_month"], end: str = P["end_month"]) -> None:
    """fc_panel (전 성분4, 전국 원외). stg_usage(원본 행만)와 usage_c4_month(관측 격자)에서 만든다"""
    con.execute("""create or replace table fc_panel as
        with a as (select c4, month, sum(claim_qty) claim_qty, sum(claim_cnt) claim_cnt, sum(claim_amt) claim_amt
                   from stg_usage where in_out = 'OUT' and month between ? and ? group by 1, 2)
        select z.c4, z.month, z.observed_out observed, z.first_observed_month_out first_month,
               case when z.observed_out then a.claim_qty end claim_qty,
               case when z.observed_out then a.claim_cnt end claim_cnt,
               case when z.observed_out then a.claim_amt end claim_amt,
               z.observed_out and not (z.month = z.first_observed_month_out and z.first_observed_month_out > ?) usable
        from usage_c4_month z left join a using (c4, month)
        where z.month between ? and ?
        order by z.c4, z.month""", [start, end, start, start, end])
    con.execute("comment on table fc_panel is 'P2 T1: 성분4 × 월 전국 원외 (미관측 = NULL, usable = 미관측·첫 관측월 제외)'")


def build_class(con) -> None:
    """성분4 → 약효분류 (제품 수 최다, 동점은 작은 번호). 급여목록 전 스냅샷의 제품 행"""
    con.execute("""create or replace table fc_c4_class as
        with n as (select c4, cast(class_code as int) cls, count(distinct product_code) np
                   from stg_benefit where row_kind = 'product_candidate' and class_code is not null and c4 is not null
                   group by 1, 2)
        select c4, arg_max(cls, np * 10000 - cls) cls, count(*) n_classes from n group by 1""")


def build_target(con, top_n: int = P["top_n"], n_months: int = 96) -> list[dict]:
    """fc_target과 탈락 단계표"""
    cls = ",".join(map(str, MAIN_CLASSES))
    con.execute(f"""create or replace temp table _cand as
        with u as (select c4, count(*) filter (where observed) n_obs, sum(claim_qty) filter (where month like '2022-%') qty2022
                   from fc_panel group by 1),
        allc as (select distinct c4 from stg_usage)
        select allc.c4, coalesce(u.n_obs, 0) n_obs, u.qty2022, k.cls, k.n_classes,
               coalesce(k.cls in ({cls}), false) main_bundle
        from allc left join u using (c4) left join fc_c4_class k using (c4)""")
    con.execute("""create or replace temp table _rank as
        select c4, row_number() over (order by qty2022 desc, c4) qty_rank from _cand where n_obs = ?""", [n_months])
    con.execute("""create or replace table fc_target as
        select c.c4, c.cls, c.n_classes, c.qty2022, r.qty_rank from _cand c join _rank r using (c4)
        where c.main_bundle and r.qty_rank <= ? order by c.cls, c.c4""", [top_n])
    steps = [("① 사용통계에 있는 성분4", "select count(*) from _cand"),
             ("② 원외 관측 1개월 이상", "select count(*) from _cand where n_obs > 0"),
             ("③ 주력 묶음 분류 (141·222·229·618)", "select count(*) from _cand where n_obs > 0 and main_bundle"),
             (f"④ {n_months}개월 원외 모두 관측", f"select count(*) from _cand where main_bundle and n_obs = {n_months}"),
             (f"⑤ 2022 원외 청구수량 상위 {top_n}", "select count(*) from fc_target")]
    out, prev = [], None
    for name, q in steps:
        n = con.execute(q).fetchone()[0]
        out.append(dict(step=name, n_c4=n, dropped=None if prev is None else prev - n))
        prev = n
    return out


def raw_reaggregate(con, glob: str = RAW_GLOB):
    """원본 CSV를 다시 읽어 성분4 × 월 원외 합을 만든다 (stg_usage를 거치지 않는 독립 경로)"""
    return con.execute(f"""
        select lpad(WK_COMPN_4, 4, '0') c4, substr(STD_YYYYMM, 1, 4) || '-' || substr(STD_YYYYMM, 5, 2) as month,
               sum(cast(PRSCRPTN_TOT_USE as double)) qty, sum(cast(PRSCRPTN_TNDN_CNT as double)) cnt, count(*) n_rows
        from read_csv({sql_files(usage_files()) if glob is None else repr(glob)}, header = true, all_varchar = true)
        where OUT_IN_HOS = 'OUT' group by 1, 2""").df()


def reagg_check(con, raw) -> dict:
    """원본 재집계 합 = 마트 합 (칸마다, 전체 합)"""
    con.register("_raw", raw)
    r = con.execute("""
        with m as (select c4, month, claim_qty, claim_cnt from fc_panel where observed)
        select count(*) filter (where m.c4 is null) only_raw, count(*) filter (where r.c4 is null) only_mart,
               count(*) filter (where abs(m.claim_qty - r.qty) > 1e-6 * greatest(1, abs(r.qty))
                                or abs(m.claim_cnt - r.cnt) > 1e-6 * greatest(1, abs(r.cnt))) diff_cells,
               count(*) filter (where m.c4 is not null and r.c4 is not null) both_cells,
               sum(r.qty) raw_qty, sum(m.claim_qty) mart_qty
        from _raw r full join m using (c4, month)""").fetchall()[0]
    con.unregister("_raw")
    return dict(zip(("only_raw", "only_mart", "diff_cells", "both_cells", "raw_qty", "mart_qty"), r))


def coverage(con) -> list[dict]:
    """커버리지: 대상 성분 수, 2022 전체 원외 청구금액 중 비중"""
    r = con.execute("""
        with t as (select c4 from fc_target),
        a as (select sum(claim_amt) amt, sum(claim_amt) filter (where c4 in (select c4 from t)) amt_t,
                     sum(claim_qty) qty, sum(claim_qty) filter (where c4 in (select c4 from t)) qty_t
              from fc_panel where month like '2022-%' and observed)
        select (select count(*) from t), amt_t, amt, amt_t / amt, qty_t / qty from a""").fetchone()
    return [dict(n_target=r[0], amt2022_target=r[1], amt2022_all=r[2], amt_share=r[3], qty_share=r[4])]


def write_csv(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    con = connect(DB_PATH)
    build_panel(con)
    build_class(con)
    steps = build_target(con)
    write_csv(OUT / "t1_target_steps.csv", steps)
    raw = raw_reaggregate(con)
    chk = reagg_check(con, raw)
    write_csv(OUT / "t1_reagg_check.csv", [chk])
    cov = coverage(con)
    write_csv(OUT / "t1_coverage.csv", cov)
    tgt = con.execute("select * from fc_target").df()
    tgt.to_csv(OUT / "t1_target.csv", index=False)
    nu = con.execute("""select count(*), count(*) filter (where not observed), count(*) filter (where observed and not usable)
                        from fc_panel""").fetchone()
    print("탈락 단계:", *[f"{s['step']} {s['n_c4']}" for s in steps], sep="\n  ")
    print("재집계:", chk)
    print("커버리지:", cov[0])
    print(f"fc_panel {nu[0]}칸, 미관측 {nu[1]}, 첫 관측월 제외 {nu[2]}")
    assert chk["only_raw"] == 0 and chk["only_mart"] == 0 and chk["diff_cells"] == 0, chk


if __name__ == "__main__":
    main()
