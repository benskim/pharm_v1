# 재현 로그 (P1 S7, 2026-11-16)

깨끗한 클론과 새 가상환경에서 `make all` → `make test` → `make report MONTH=2024-09`를 실행한 기록이다. **실패한 단계가 있어 고치지 않고 원인을 먼저 적는다** (지시).

## 환경
- 클론: 저장소 커밋 `9724e25`을 임시 폴더에 `git clone`. 원본 데이터는 링크로 배치 (`data/raw` → 원본 폴더, `data/sample/*.parquet` 8개 → 원본 파일). 둘 다 저장소에 없다 (`.gitignore`).
- 가상환경: `python3.11 -m venv .venv` (Python 3.11.4, 작업 환경은 3.11.5), `pip install -r requirements.txt`. 설치된 주요 버전은 작업 환경과 같다: duckdb 2.0.0, pandas 2.3.3, pyarrow 26.0.1, pytest 9.2.0, PyYAML 6.0.3.
- 기계: macOS (Darwin 22.6.0).

## 결과

| 순서 | 명령 | 걸린 시간 | 결과 |
|---|---|---|---|
| 1 | `python3.11 -m venv .venv` | 8초 | 성공 |
| 2 | `pip install -r requirements.txt` | 75초 | 성공 |
| 3 | `make all` | 337초 | **실패 (`p1-s4`에서 멈춤)**. s0 → check-sample("precheck 차이 없음") → test(139 통과, 74 건너뜀: DB가 아직 없음) → p1-s1(시험 49 통과) → p1-s2(59) → p1-s3(45) → p1-s4 시험 **1 실패** / 12 통과 |
| 4 | `make test` | 217초 | **2 실패** / 211 통과 |
| 5 | `make report MONTH=2024-09` | 25초 | 성공 |

**출력 해시**
| 파일 | 깨끗한 클론 | 작업 환경 (커밋된 파일) |
|---|---|---|
| `outputs/reports/P1_report_2024-09.md` | `91538cc53b0ab21a80ae5b499c6460473663f19b1de70fe5cd799e86a9258568` | 같음 |
| `outputs/precheck_sample.md` | 같음 (cmp) | |
| `outputs/s2/*.csv` 13개, `outputs/s3/*.csv` 5개, `outputs/s4/*.csv` 15개 | 33개 모두 바이트 동일 (cmp) | |

## 실패와 원인 (고치지 않음)
1. **`tests/test_s4.py::test_sample_60` — `assert (60 == 61)`** (`make all`의 p1-s4, `make test` 둘 다)
   - 원인: 라벨을 채운 검증 표본 `outputs/validation/sample_60.csv`가 저장소에 없다. `.gitignore`의 `*.csv` 규칙에 걸린다 (`git check-ignore` 확인). 이 파일에는 고정 60행 + 추가 1행(654700150, DECISIONS 2026-11-15)과 사용자·Claude 라벨이 있다.
   - 깨끗한 클론에서는 `p1-s4`가 라벨 없는 60행 표본을 새로 만든다. 그래서 61행·라벨을 기대하는 시험이 실패한다. `make p1-s4-precision`(정밀도)도 같은 이유로 재현할 수 없다.
   - 저장소에 있는 것은 `outputs/validation/LABELING.md`와 `review10_evidence.md`뿐이다.
2. **`tests/test_s5.py::test_b1_outputs_consistent` — FileNotFoundError** (`make test`)
   - 원인: `make all`이 p1-s4에서 멈춰 `p1-s5`가 돌지 않았고 `outputs/s5/`가 없다. 1의 결과다.
3. **(아직 닿지 않은 지점)** `Makefile`의 `all`에는 `p2-t1 p2-t2 p2-t3 p2-t4`가 들어 있고, 이 목표는 "아직 구현하지 않은 단계"를 출력하고 실패하도록 되어 있다. 1을 해결해도 `make all`은 p1-s6 다음에 멈춘다 (Makefile에서 확인, 실행하지는 않았다).

## 고칠 방법 후보 (사용자 결정 필요)
- 1: 라벨 표본 CSV를 저장소에 넣는다 (`.gitignore`에 `!outputs/validation/sample_60.csv` 예외). 또는 `data/raw`처럼 따로 받는 입력으로 README·DATA.md에 적는다.
- 3: `all`에서 P2 목표를 빼거나 P1 전용 목표(`p1-all`)를 둔다.
