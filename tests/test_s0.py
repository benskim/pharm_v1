"""S0 점검 도구 시험.

기대값 출처: docs/NOTE_RULES.md의 문형 예시, reference/precheck_v3_output.md.
구현 출력으로 기대값을 만들지 않는다.
"""
import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from s0_inspect import scan_note  # noqa: E402


def types(note):
    return [[t for t, _ in p["tokens"]] for p in scan_note(note)]


def residues(note):
    return [p["residue"] for p in scan_note(note)]


# NOTE_RULES.md §1 예시 → 유형
@pytest.mark.parametrize("note, expected_type, expected_date", [
    ("25.11.23 등재", "R1", dt.date(2025, 11, 23)),
    ("'25.11.23 등재", "R1", dt.date(2025, 11, 23)),          # P2 앞 따옴표
    ("24.9.2., 453원", "R2", dt.date(2024, 9, 2)),
    ("급여정지(22.10.20.)", "R3", dt.date(2022, 10, 20)),
    ("집행정지(24.9.30.)", "R4", dt.date(2024, 9, 30)),
    ("집행정지 해제 및 상한금액 인하(25.11.20. 고시)", "R5", dt.date(2025, 11, 20)),
    ("급여정지 해제(25.11.12)", "R6", dt.date(2025, 11, 12)),   # P2 끝 마침표 없음
    ("산정불가", "R7", None),
])
def test_note_rules_examples(note, expected_type, expected_date):
    parts = scan_note(note)
    assert len(parts) == 1
    assert parts[0]["tokens"] == [(expected_type, expected_date)]
    assert parts[0]["residue"] == ""


def test_p2_variants():
    # 4자리 연도, 쉼표 없는 가격
    assert scan_note("2024.3.1., 131원")[0]["tokens"] == [("R2", dt.date(2024, 3, 1))]
    assert scan_note("25.6.9. 402원")[0]["tokens"] == [("R2", dt.date(2025, 6, 9))]


def test_p1_compound():
    # ' / '와 ','로 이어진 복합 비고는 건별 토큰으로 나뉘고 남는 글자가 없다
    assert types("집행정지(22.5.3.) / 집행정지(22.5.9.)") == [["R4"], ["R4"]]
    assert types("집행정지(18.4.1.), 급여정지(19.9.26.)") == [["R4", "R3"]]
    assert residues("집행정지(18.4.1.), 급여정지(19.9.26.)") == [""]


def test_release_not_misread_as_suspension():
    # R6 해제를 R3 정지로, R5 해제를 R4 정지로 읽지 않는다
    assert types("급여정지 해제(25.11.12)") == [["R6"]]
    assert types("집행정지 해제 및 상한금액 인하(25.11.20. 고시)") == [["R5"]]


def test_unknown_text_is_unresolved():
    # R1~R7에 없는 글자가 남으면 unresolved (규칙을 추측하지 않는다)
    assert residues("급여정지(22.04.01.~22.12.31)") != [""]
    assert residues("허가변경 약제 재평가 집행정지(21.8.31.)") != [""]


def test_precheck_regression():
    """data/sample로 돌린 precheck 출력 = reference/precheck_v3_output.md"""
    if not (ROOT / "data/sample/benefit_snapshot_table_2425.parquet").exists():
        pytest.skip("data/sample 없음 (scripts/build_sample.py)")
    out = subprocess.run([sys.executable, "-I", "reference/precheck_v3.py", "data/sample"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out == (ROOT / "reference/precheck_v3_output.md").read_text(encoding="utf-8")
