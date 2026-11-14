"""급여목록 2025-11·12 제품코드 복원 (사용자 지시 2026-11-14).

benefit_snapshot_table_2425.parquet의 2025-11·12 제품코드 2,357행은 앞자리 0이 빠진 8자리였다.
사용자가 준 원본 CSV(data/raw/benefit/_original_check/1_2025-12-01_약제급여목록.csv, 2_2025-11-01_약제급여목록.csv)의
제품코드로 바꾼다. 다른 칸과 행 순서는 바꾸지 않는다.

- 교체 전 파일: data/raw/benefit/_superseded/benefit_snapshot_table_2425.parquet (회귀 표본은 이 파일을 쓴다)
- 대응: (기준일자의 달, 제품코드). 연번은 고유 키가 아니다 (DECISIONS 2026-11-14). parquet 제품코드를 0 채움(숫자 8자리만)한 값이
  같은 달 CSV 제품코드에 정확히 한 번 있어야 한다. 두 달의 제품코드 집합이 같지 않으면 멈춘다.
사용: python -I scripts/fix_benefit_2511_2512_codes.py
"""
import hashlib
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
B = ROOT / "data/raw/benefit"
SRC = B / "_superseded/benefit_snapshot_table_2425.parquet"
DST = B / "benefit_snapshot_table_2425.parquet"
CSV = {"2025-11": B / "_original_check/2_2025-11-01_약제급여목록.csv", "2025-12": B / "_original_check/1_2025-12-01_약제급여목록.csv"}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    t = pq.read_table(SRC)
    df = t.to_pandas()
    month = df["기준일자"].str[:7]
    code = df["제품코드"].copy()
    n_changed = 0
    for m, f in CSV.items():
        c = pd.read_csv(f, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        c.columns = [x.replace("\n", "") for x in c.columns]
        csv_codes = c["제품코드"].str.strip()
        assert csv_codes.is_unique, m
        idx = df.index[month == m]
        pad = {i: (code[i].zfill(9) if code[i].isdigit() and len(code[i]) == 8 else code[i]) for i in idx}
        assert sorted(pad.values()) == sorted(csv_codes), m          # (달, 제품코드) 집합이 같다
        for i, nv in pad.items():
            if nv != code[i]:
                code[i] = nv
                n_changed += 1
    assert n_changed == 2357, n_changed
    col = t.schema.get_field_index("제품코드")
    t2 = t.set_column(col, t.schema.field(col), pa.array(code.tolist(), type=pa.string()))
    tmp = DST.with_suffix(".tmp")
    pq.write_table(t2, tmp, compression="snappy", row_group_size=t.num_rows)
    os.replace(tmp, DST)
    print(f"바꾼 행 {n_changed}")
    print(f"교체 전 sha256 {sha(SRC)}")
    print(f"교체 후 sha256 {sha(DST)}")


if __name__ == "__main__":
    main()
