"""공통: 경로, 설정, DuckDB 연결."""
from __future__ import annotations

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
