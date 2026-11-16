# 공개 의약품 데이터로 본 시장의 선행 신호와 수요

심평원 급여목록의 비고에 적힌 예고(가격 인하·등재·급여정지)를 유효일 기준 원장으로 정리한다. 그 예고가 실현된 비율과 영향 규모를, 사용통계가 공개되기 전(5개월 뒤)에 매달 1일 리포트로 낸다. 2쪽 요약: [docs/SUMMARY.md](docs/SUMMARY.md).

| | P1 선행 신호 원장 (완료) | P2 수요 예측 (설계만) |
|---|---|---|
| 질문 | 비고의 예고는 실제 변화로 얼마나, 얼마 만에 이어지나 | 사용통계 공백 5개월을 목록 신호로 얼마나 메울 수 있나 |
| 데이터 | 급여목록·대체조제 목록 2022-01 ~ 2025-12, 건보공단 사용통계 | 사용통계 2018-01 ~ 2025-12, P1 신호 |
| 산출물 | 원장, 분석 A·B·C, `make report MONTH=YYYY-MM` 월간 리포트 | [docs/PLAN_P2.md](docs/PLAN_P2.md) |

## 폴더
```
config/        파라미터(params.yaml), 리포트 설정(report.yaml), 컬럼 대응표, 외부 근거
data/raw/      원본 데이터 (저장소에 없음, 읽기 전용) → docs/DATA.md §1
data/sample/   회귀 시험용 표본 (README만 저장소에 있음, parquet은 따로 받는다)
data/interim/  적재·원장 DB (pbd.duckdb, 생성물)
docs/          설계(PLAN_P1·P2), 자료(DATA), 결정(DECISIONS), 한계(LIMITATIONS), 요약(SUMMARY), 재현 로그
reference/     표본 점검 스크립트와 기대 출력 (make check-sample의 기준)
reports/       단계별 결과 보고서 (S0, P1 S1 ~ S6)
src/pbd/       코드 (ledger/ = P1, forecast/ = P2)
tests/         시험
outputs/       생성물 (리포트 Markdown은 저장소에 있음, CSV는 생성)
```

## 필요한 데이터
원본은 저장소에 없다. [docs/DATA.md](docs/DATA.md) §1의 표대로 다음을 배치한다.
- `data/raw/`: `benefit/`, `substitute/`, `usage/`(사용통계 원본 96개 CSV), `master/`, `orangebook/`
- `data/sample/`: 표본 parquet 8개 ([data/sample/README.md](data/sample/README.md))

## 설치와 실행
Python 3.11, 디스크 약 5GB (원본 2.3GB, DB 약 1.7GB).
```
make venv                      # .venv 생성, requirements.txt 설치 (약 1~2분)
make check-sample              # 표본 회귀: reference/precheck_v3_output.md와 같아야 한다
make all                       # = p1-all: S0 → check-sample → P1 S1 ~ S6 → 전체 시험 (약 10분)
make report MONTH=2024-09      # 한 달 리포트 → outputs/reports/P1_report_2024-09.md, outputs/dashboard/2024-09/
make test                      # 전체 시험 (213개, 약 3분)
```
깨끗한 클론에서 `make all`(613초), `make test`(213개 통과), `make report MONTH=2024-09`가 모두 성공했다. 리포트 2개는 커밋된 파일과 해시가 같다 ([docs/REPRODUCE_LOG.md](docs/REPRODUCE_LOG.md)). P2 단계는 구현한 뒤 `all`에 넣는다. 라벨을 채운 검증 표본(`outputs/validation/sample_60.user_reviewed.csv`)은 저장소에 있고, `make p1-s4`는 이 파일을 덮어쓰지 않는다.

## 예시 리포트
- [2024-09](outputs/reports/P1_report_2024-09.md): 시타글립틴 인하 예고, 아픽사반 등재 예고
- [2025-11](outputs/reports/P1_report_2025-11.md): 토파시티닙 등재 예고, 11월 정기 정비 삭제 611건

리포트는 발간월 M 기준으로 목록 ≤ M, 사용통계 ≤ M−5만 쓴다. 같은 입력이면 같은 파일 해시가 나온다.

## 문서 지도
| 문서 | 내용 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | 작업 규칙 (사실 규칙, 데이터 규칙, 단계 운영) |
| [docs/PLAN_P1.md](docs/PLAN_P1.md), [docs/PLAN_P2.md](docs/PLAN_P2.md) | 설계, 단계별 완료 기준 |
| [docs/DATA.md](docs/DATA.md) | 입력 데이터, 알려진 위험, 사용통계 공개 시차 |
| [docs/NOTE_RULES.md](docs/NOTE_RULES.md) | 비고 유형별 규칙 |
| [docs/DECISIONS.md](docs/DECISIONS.md) | 정한 것과 근거 (날짜별) |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | 한계 (자료 / 규칙·판정 / 검증 / 리포트 / 사용통계 시차) |
| [docs/BACKLOG.md](docs/BACKLOG.md) | 2차 항목 (구현하지 않음) |
| [reports/](reports/) | 단계별 결과: [S0](reports/S0_result.md), [S1](reports/P1_S1_result.md), [S2](reports/P1_S2_result.md), [S3](reports/P1_S3_result.md), [S4](reports/P1_S4_result.md), [S5](reports/P1_S5_result.md), [S6](reports/P1_S6_result.md) |
