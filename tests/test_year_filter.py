"""원본 연도 필터 시험 (2026-11-19 사용자 결정): 로더·시험은 기본 연도(사용통계 2018~2025, 목록 2022~2025) 파일만 고른다.
합성 폴더(빈 파일)로 시험한다. 파일 내용은 열지 않고 이름만 본다."""
from __future__ import annotations

import pytest

from pbd.common import list_files, load_yaml, sql_files, usage_files


@pytest.fixture()
def raw(tmp_path):
    u = tmp_path / "usage"
    for folder, names in (("cmpn_stat_2018(2024.12.30.)", ["MEDICINE_201801 (2024.12.30.).csv"]),
                          ("cmpn_stat_2025(2026.10.28.)", ["MEDICINE_202512 (2026.10.28.).csv"]),
                          ("cmpn_stat_2026 (2026.10.28.)", ["MEDICINE_202601 (2026.10.28.).csv", "MEDICINE_202606 (2026.10.28.).csv"])):
        (u / folder).mkdir(parents=True)
        for n in names:
            (u / folder / n).write_text("")
    for lst in ("benefit", "substitute"):
        (tmp_path / lst).mkdir()
        for n in (f"{lst}_snapshot_table_2223.parquet", f"{lst}_snapshot_table_2425.parquet", f"{lst}_snapshot_table_2626.parquet"):
            (tmp_path / lst / n).write_text("")
        (tmp_path / lst / f"{lst}_2026-05_1.csv").write_text("")
    return tmp_path


def test_default_years_in_params():
    y = load_yaml("params.yaml")["data_years"]
    assert y["usage"] == [2018, 2025] and y["lists"] == [2022, 2025]


def test_usage_default_excludes_2026(raw):
    names = [f.rsplit("/", 1)[-1] for f in usage_files(root=raw)]
    assert names == ["MEDICINE_201801 (2024.12.30.).csv", "MEDICINE_202512 (2026.10.28.).csv"]
    assert len(usage_files((2026, 2026), root=raw)) == 2            # Stage 4 명령만 연도를 넘긴다


@pytest.mark.parametrize("lst", ["benefit", "substitute"])
def test_lists_default_excludes_2026(raw, lst):
    assert [f.rsplit("/", 1)[-1] for f in list_files(lst, root=raw)] == [f"{lst}_snapshot_table_2223.parquet", f"{lst}_snapshot_table_2425.parquet"]


def test_sql_files_refuses_empty():
    with pytest.raises(AssertionError):
        sql_files([])
    assert sql_files(["a/b'c.csv"]) == "['a/b''c.csv']"
