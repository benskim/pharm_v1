"""data/raw/ 원본으로 회귀 시험용 표본(data/sample/) 8개를 만든다.

- 스냅샷·마스터 5개: 원본을 설계 때 파일명으로 복사한다 (내용 변경 없음).
- fact_cmpn_stat_2023~2025: 사용통계 원본을 월 × 성분4 × 원내외로 합산한다.
  연령·성별·시도·요양기관그룹은 합쳐서 없앤다. 원본에 없는 조합은 만들지 않는다(비공개 = 미관측).
  PATIENT_CNT는 칸끼리 더한 값이라 중복이 들어 있다.

설계 때의 가공본은 남아 있지 않아 2026-11-14 이 스크립트로 다시 만들었다 (docs/DECISIONS.md).
사용: python scripts/build_sample.py
"""
import shutil
from pathlib import Path

import duckdb

RAW = Path("data/raw")
OUT = Path("data/sample")

COPIES = {
    "benefit_snapshot_table_2425.parquet": RAW / "benefit/benefit_snapshot_table_2425.parquet",
    "substitute_snapshot_table_2425.parquet": RAW / "substitute/substitute_snapshot_table_2425.parquet",
    "dim_drug_master_20260831.parquet": RAW / "master/dim_drug_code_map_26.parquet",
    "dim_drug_info.parquet": RAW / "master/dim_drug_info_26.parquet",
    "dim_generic_master.parquet": RAW / "orangebook/dim_orange_book.parquet",
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, src in COPIES.items():
        shutil.copyfile(src, OUT / name)
        print(f"복사 {src} -> {OUT / name}")

    con = duckdb.connect()
    for year in (2023, 2024, 2025):
        src = (RAW / "usage").as_posix() + f"/cmpn_stat_{year}*/MEDICINE_{year}*.csv"
        dst = (OUT / f"fact_cmpn_stat_{year}.parquet").as_posix()
        con.execute(f"""
            copy (
              select STD_YYYYMM, WK_COMPN_4, OUT_IN_HOS,
                     sum(cast(PRSCRPTN_TNDN_CNT as bigint)) PRSCRPTN_TNDN_CNT,
                     sum(cast(PRSCRPTN_AMT as bigint))      PRSCRPTN_AMT,
                     sum(cast(PATIENT_CNT as bigint))       PATIENT_CNT,
                     sum(cast(PRSCRPTN_TOT_USE as double))  PRSCRPTN_TOT_USE
              from read_csv('{src}', header=true, all_varchar=true)
              group by all
              order by STD_YYYYMM, WK_COMPN_4, OUT_IN_HOS
            ) to '{dst}' (format parquet)
        """)
        n, m = con.execute(f"select count(*), count(distinct STD_YYYYMM) from read_parquet('{dst}')").fetchone()
        print(f"집계 {dst}: {n}행, {m}개월")


if __name__ == "__main__":
    main()
