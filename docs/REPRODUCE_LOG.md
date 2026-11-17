# 재현 로그 (P1 S7)

깨끗한 클론과 새 가상환경에서 `make all` → `make test` → `make report MONTH=2024-09`를 실행한 기록이다.

## 환경 (공통)
- 클론: 저장소를 임시 폴더에 `git clone`. 원본 데이터는 링크로 배치 (`data/raw` → 원본 폴더, `data/sample/*.parquet` 8개 → 원본 파일). 둘 다 저장소에 없다 (`.gitignore`).
- 가상환경: `python3.11 -m venv .venv` (Python 3.11.4, 작업 환경은 3.11.5), `pip install -r requirements.txt`. 주요 패키지 버전은 작업 환경과 같다: duckdb 2.0.0, pandas 2.3.3, pyarrow 26.0.1, pytest 9.2.0, PyYAML 6.0.3.
- 기계: macOS (Darwin 22.6.0).

## 2차 (2026-11-17, 커밋 `116ea57`) — 성공
1차 실패 뒤 반영한 것: 라벨 표본 `outputs/validation/sample_60.user_reviewed.csv`(최종)·`sample_60.claude_labeled.csv`(1차)를 저장소에 넣었고, `make p1-s4`가 이 파일을 덮어쓰지 않게 했다. `all = p1-all` (s0 → check-sample → p1-s1 ~ p1-s6 → test, P2 단계 제외).

| 순서 | 명령 | 걸린 시간 | 결과 |
|---|---|---|---|
| 1 | `python3.11 -m venv .venv` | 6초 | 성공 |
| 2 | `pip install -r requirements.txt` | 48초 | 성공 |
| 3 | `make all` | 613초 | **성공**. 끝의 `make test` 213개 통과 |
| 4 | `make test` | 196초 | **213개 통과** |
| 5 | `make report MONTH=2024-09` | 18초 | 성공 |

**출력 대조 (작업 환경과)**
| 파일 | 결과 |
|---|---|
| `outputs/reports/P1_report_2024-09.md` | 같음: `91538cc53b0ab21a80ae5b499c6460473663f19b1de70fe5cd799e86a9258568` |
| `outputs/reports/P1_report_2025-11.md` | 같음: `51b2890169ac1d68b7e11249486501026b46c33e3d23c383fd3e22621f19702c` |
| `outputs/s2/*.csv` 13개, `outputs/s3/*.csv` 5개, `outputs/s4/*.csv` 15개 | 모두 바이트 동일 |
| `outputs/s5/*.csv` 23개 | 22개 바이트 동일. **`b1_pairs_effective.csv` 1개 다름**: 3행(1765·1831·2261, 2024-11)의 `dlog_list_eff` 소수 17번째 자리 (…864 / …865). 이 칸으로 만든 요약 표(`b1_main`, `b1_slopes_effective`, `b1_selection_effective`)는 같다 |
| `outputs/validation/sample_60.user_reviewed.csv` | 바뀌지 않음. `make p1-s4`는 다시 채운 표본을 `sample_60.new.csv`에 썼다 |

**해결 (2026-11-17, 사용자 지시)**: 쌍별 `dlog_list`·`dlog_list_eff`·`dlog_up`을 소수 12자리로 반올림했다. `SET threads=1`로 다시 만든 S5 출력 23개가 병렬 실행과 바이트 동일함을 확인했다. 이 반올림으로 대조군 비율이 0.01~0.02%p 바뀌었다 (S5 보고 §18). 깨끗한 클론 재실행은 아직 하지 않았다.

## 1차 (2026-11-16, 커밋 `9724e25`) — 실패, 원인 기록
| 순서 | 명령 | 걸린 시간 | 결과 |
|---|---|---|---|
| 1 | venv / pip | 8초 / 75초 | 성공 |
| 2 | `make all` | 337초 | **실패 (`p1-s4`)**: `test_sample_60` (60 ≠ 61) |
| 3 | `make test` | 217초 | 2 실패 / 211 통과 (`test_sample_60`, `test_s5` FileNotFoundError) |
| 4 | `make report MONTH=2024-09` | 25초 | 성공, 해시 `91538cc5…` (작업 환경과 같음) |

- 원인: 라벨을 채운 검증 표본이 `.gitignore`의 `*.csv` 규칙 때문에 저장소에 없었다. 깨끗한 클론에서는 라벨 없는 60행 표본이 새로 만들어져 61행·라벨을 기대하는 시험이 실패했다. `test_s5`는 p1-s5가 돌지 않아 생긴 실패다.
- 또 `all`에 아직 구현하지 않은 P2 목표가 있어 그대로 두면 p1-s6 다음에 멈출 상태였다.
- 2차 전에 사용자 결정으로 고쳤다 (DECISIONS 2026-11-16 S7).
