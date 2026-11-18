import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LEGACY_DB = ROOT / "outputs/v4_legacy/v4_legacy.duckdb"   # v4 원장 보관본 (읽기 전용, scripts/v4_legacy_export.py)


def v4_connect():
    """v4 회귀 시험용 연결: 기본 DB(L1·S2 속성) + v4 보관본(판정 표)을 붙이고 이름을 둘 다에서 찾는다"""
    import duckdb
    from pbd.common import DB_PATH
    c = duckdb.connect()
    c.execute(f"attach '{DB_PATH.as_posix()}' as m (read_only)")
    c.execute(f"attach '{LEGACY_DB.as_posix()}' as v4 (read_only)")
    c.execute("set search_path = 'v4.main,m.main'")
    return c
