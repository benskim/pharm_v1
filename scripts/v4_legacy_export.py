"""v4 원장(개정 전)을 읽기 전용 보관본으로 내보낸다 (사용자 지시 2026-11-18, RULES §11-4).

- 출력: outputs/v4_legacy/v4_legacy.duckdb (v4 판정 표), outputs/v4_legacy/{s3,s4,s5}/ (v4 산출 CSV 복사), README.md
- v4 판정 표가 기본 DB(data/interim/pbd.duckdb)에 있으면 그대로 복사한다. 없으면 L1 표(stg_*, note_token, attr_2026_*)로
  S2·S3(v4)를 메모리에서 다시 만들어 쓴다 (깨끗한 클론 재현용).
- 파일은 쓴 뒤 읽기 전용(444)으로 둔다. 다시 만들려면 --force.
사용: PYTHONPATH=src python -I scripts/v4_legacy_export.py [--force]   (make p1-v4-legacy)
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb  # noqa: E402

from pbd.common import DB_PATH, connect  # noqa: E402

LEG = ROOT / "outputs/v4_legacy"
LEG_DB = LEG / "v4_legacy.duckdb"
V4_TABLES = ("product_state_month", "s2_state_override", "s2_suspend_signal_check", "s3_cal", "s3_entry_exit",
             "s3_entry_exit_sample", "signal_ledger", "signal_outcome", "event_ledger", "s3_compare_by_year")
README = """# v4 원장 보관본 (읽기 전용)

RULES_2026-11-17(v5) 개정 전 원장이다 (사용자 지시 2026-11-18, RULES §11-4). 파이프라인 기본 경로(`make p1-all`)는 이 파일을 만들거나 읽지 않는다.
읽는 곳: 개정 전후 비교(`scripts/v5_compare.py`), 라벨의 개정 전 주장(`scripts/v5_labels.py`), v4 회귀 시험(`tests/test_s2_integration.py`,
`tests/test_s3_integration.py`, `tests/test_s5.py`의 reference F8 분포).

- `v4_legacy.duckdb`: {tables}
- `s3/`, `s4/`, `s5/`: v4 원장으로 만든 산출 CSV (2026-11-18 이전 실행)
- 다시 만들기: `make p1-v4-legacy` (`--force`). 파일 권한은 읽기 전용(444)이다.
"""


def _writable(p: Path) -> None:
    if p.exists():
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)


def add_comments(con) -> None:
    """칸 설명은 표 복사로 따라오지 않으므로 S2의 product_state_month 칸 설명을 다시 붙인다"""
    from pbd.ledger.s2_state import comment_columns
    comment_columns(con)


def main(force: bool = False) -> None:
    LEG.mkdir(parents=True, exist_ok=True)
    if LEG_DB.exists() and not force:
        c = duckdb.connect(str(LEG_DB), read_only=True)
        n = c.execute("select count(*) from duckdb_columns() where table_name = 'product_state_month' and comment is not null").fetchone()[0]
        c.close()
        if n == 0:                                   # 이전 판(칸 설명 없음) 보완
            _writable(LEG_DB)
            c = duckdb.connect(str(LEG_DB))
            add_comments(c)
            c.execute("checkpoint")
            c.close()
            os.chmod(LEG_DB, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            print("칸 설명을 붙였다")
        print(f"{LEG_DB} 있음 (다시 만들려면 --force)")
        return
    _writable(LEG_DB)
    if LEG_DB.exists():
        LEG_DB.unlink()
    src = duckdb.connect(str(DB_PATH), read_only=True)
    have = {r[0] for r in src.execute("select table_name from information_schema.tables").fetchall()}
    src.close()
    out = duckdb.connect(str(LEG_DB))
    if set(V4_TABLES) <= have:
        out.execute(f"attach '{DB_PATH.as_posix()}' as src (read_only)")
        for t in V4_TABLES:
            out.execute(f"create table {t} as select * from src.{t}")
        how = "기본 DB에서 복사"
    else:
        from pbd.ledger import s2_state as S2, s3_ledger as S3
        c = connect(None)
        c.execute(f"attach '{DB_PATH.as_posix()}' as src (read_only)")
        for t in ("stg_benefit", "stg_substitute", "note_token", "attr_2026_product", "attr_2026_item"):
            c.execute(f"create table {t} as select * from src.{t}")
        S2.build_all(c)
        S3.build_all(c)
        out.execute("attach ':memory:' as x")
        for t in V4_TABLES:
            df = c.execute(f"select * from {t}").df()
            out.register("_d", df)
            out.execute(f"create table {t} as select * from _d")
            out.unregister("_d")
        c.close()
        how = "L1 표로 S2·S3(v4)를 다시 만듦"
    add_comments(out)
    for t in V4_TABLES:
        print(f"{t}: {out.execute(f'select count(*) from {t}').fetchone()[0]:,}행")
    out.execute("checkpoint")
    out.close()
    for d in ("s3", "s4", "s5"):
        s, dst = ROOT / "outputs" / d, LEG / d
        if s.exists() and (force or not dst.exists()):
            if dst.exists():
                for f in dst.rglob("*"):
                    _writable(f)
                shutil.rmtree(dst)
            shutil.copytree(s, dst)
    (LEG / "README.md").write_text(README.format(tables=", ".join(V4_TABLES)), encoding="utf-8")
    for f in [LEG_DB, *LEG.rglob("*.csv")]:
        os.chmod(f, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    print(f"v4 보관본: {LEG} ({how}), 읽기 전용")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
