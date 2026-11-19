"""공통: 경로, 설정, DuckDB 연결."""
from __future__ import annotations

import re
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw"
INTERIM = ROOT / "data/interim"
AUDIT = ROOT / "outputs/audit"
DB_PATH = INTERIM / "pbd.duckdb"


def load_yaml(name: str) -> dict:
    with (ROOT / "config" / name).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def connect(path: Path | str | None = DB_PATH, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    if path is None:
        con = duckdb.connect()
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path), read_only=read_only)
    con.execute("SET enable_progress_bar=false")
    con.execute("SET preserve_insertion_order=true")
    return con


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _years(kind: str, years: tuple[int, int] | None) -> tuple[int, int]:
    lo, hi = years or load_yaml("params.yaml")["data_years"][kind]
    return int(lo), int(hi)


def usage_files(years: tuple[int, int] | None = None, root: Path = RAW) -> list[str]:
    """사용통계 원본 CSV(MEDICINE_YYYYMM*.csv) 중 처방 연도가 years 안인 것. 기본 = params data_years.usage (2018~2025).
    파일 이름만 보고 고른다(범위 밖 파일은 열지 않는다). 2026은 Stage 4 명령에서만 years를 넘긴다 (2026-11-19 사용자 결정)"""
    lo, hi = _years("usage", years)
    out = []
    for p in sorted((root / "usage").glob("cmpn_stat_*/MEDICINE_*.csv")):
        m = re.match(r"MEDICINE_(\d{4})(\d{2})", p.name)
        if m and lo <= int(m.group(1)) <= hi:
            out.append(p.as_posix())
    return out


def list_files(lst: str, years: tuple[int, int] | None = None, root: Path = RAW) -> list[str]:
    """급여목록·대체조제 목록 스냅샷 parquet(<lst>_snapshot_table_YYZZ.parquet) 중 두 연도가 모두 years 안인 것.
    기본 = params data_years.lists (2022~2025)"""
    lo, hi = _years("lists", years)
    out = []
    for p in sorted((root / lst).glob(f"{lst}_snapshot_table_*.parquet")):
        m = re.match(rf"{lst}_snapshot_table_(\d{{2}})(\d{{2}})\.parquet$", p.name)
        if m and lo <= 2000 + int(m.group(1)) and 2000 + int(m.group(2)) <= hi:
            out.append(p.as_posix())
    return out


def sql_files(files: list[str]) -> str:
    """DuckDB read_csv/read_parquet에 넘길 파일 목록 리터럴. 비어 있으면 멈춘다"""
    assert files, "읽을 원본 파일이 없다 (data_years 범위 확인)"
    return "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "]"
