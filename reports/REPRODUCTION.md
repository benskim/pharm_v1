# 재현 방법과 기록

## 방법

1. 저장소를 새 폴더에 내려받는다: `git clone <저장소> pharm_v1 && cd pharm_v1 && git checkout v1.0`
2. 새 가상환경: `make venv` (Python 3.11, `requirements.txt` 설치)
3. 데이터 배치: 원본은 저장소에 없다. [docs/DATA.md](../docs/DATA.md) "원본 파일 목록"의 출처에서 받아 `data/raw/`에 같은 이름으로 둔다. 표의 크기·SHA-256으로 대조한다: `python -I scripts/data_manifest.py --check`
4. 전체 실행: `make all` (적재 → 표본 회귀 → 변화 기록표 → 분석 → 리포트 → 처방 수요 예측과 진단 → 문서 → 전체 시험)
5. 시험만 다시: `make test`
6. 숫자 대조: `python -I scripts/reproduce_check.py <원래 폴더> <재현 폴더>` — 주요 숫자(처분 예고 비율, 검증 표본 정밀도, 예측 주 결과·추가 확인)가 같은지 본다

## 기록

<!-- 재현 기록 시작 (재현 실행 뒤 채움) -->
## 2026-11-19 깨끗한 재현 (release, 커밋 e933957 → 버그 수정 뒤 재시험)

| 단계 | 명령 | 시간 | 결과 |
|---|---|---|---|
| 1 내려받기 | `git clone` → `git checkout release` (e933957) | 수 초 | |
| 2 가상환경 | `make venv` (Python 3.11, pip 올린 뒤 `requirements.txt`) | 39초 | 고정 판 그대로 설치 |
| 3 데이터 배치 | 원본 127개 파일을 `data/raw/`에 복사 → `python -I scripts/data_manifest.py --check` | 0초 | 127/127 크기·SHA-256 일치 |
| 4 전체 실행 | `make all` | 3시간 23분 (12,181초) | 적재~문서까지 통과, 마지막 `make test`에서 8개 실패 (356개 통과) |
| 5 수정 | `scripts/v4_legacy_export.py` 버그 수정 (아래) → `python -I scripts/v4_legacy_export.py --force` | 17초 | v4 보관본 10개 표가 원래 보관본과 칸 형식·행 모두 같음 |
| 6 시험 | `make test` | 22분 23초 | **364개 모두 통과**, 경고 0 |
| 7 숫자 대조 | `python -I scripts/reproduce_check.py <원래 폴더> <재현 폴더>` | 수 초 | 대조 37개 중 다른 것 0개 |

숫자 대조 범위: 처분 예고 비율(P1 지표), 검증 표본 정밀도(`holdout_precision_c63778c.csv`), 처방 수요 예측 주 결과·진단·추가 확인 CSV 전부, README·SUMMARY·P2_SUMMARY·OVERVIEW·P2_min_result·LIMITATIONS 문서(시험 시간 표기만 가림).

발견한 버그 (재현에서만 드러남): 원래 폴더에서는 v4 판정 표가 기본 DB에 있어 보관본을 그대로 복사했지만, 깨끗한 클론에서는 기본 DB에 없어 메모리에서 다시 만든다. 이때 pandas를 거쳐 저장하면서 DATE 칸이 TIMESTAMP가 됐고, v4 회귀 시험 8개(날짜 비교)가 실패했다. DuckDB끼리 바로 옮기도록 고쳤다. 판정 규칙·시험 기대값·결과 숫자는 바뀌지 않았다 (원래 폴더 보관본과 행 단위로 같음).

재현 중 함께 고친 것 (커밋 6080f13, e933957): 쓰지 않는 statsforecast·holidays 제거와 판 고정(새 환경에서 설치 실패), `make venv`가 pip을 먼저 올림(옛 pip이 pyarrow 휠을 못 골라 원본 빌드 시도).

경고: 실행 기록과 시험 출력에 경고 없음.
<!-- 재현 기록 끝 -->
