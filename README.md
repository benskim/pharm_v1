# 공개 의약품 데이터로 본 시장의 선행 신호와 수요

심평원 급여목록의 비고에 적힌 예고(가격 인하·등재·급여정지)를 유효일 기준 원장으로 정리한다. 그 예고가 실현된 비율과 영향 규모를, 사용통계가 공개되기 전(5개월 뒤)에 매달 1일 리포트로 낸다. 

<!-- P1 요약 시작 (scripts/p1_docs.py 생성) -->
**P1 (v5.3)**: 약가 예고 최종 실현율 B 99.7%·현재 시점 집행률 A 99.7%, 급여중지 예고 B 89.3%·A 53.7% (집행정지율 47.3%), 약가 변화의 66.7%는 예고 없음. holdout 정밀도 처음 라벨 83.3%, 라벨 판정 후 90.0%. 시험 362개. 2쪽 요약: [reports/SUMMARY.md](reports/SUMMARY.md), 한계: [docs/LIMITATIONS.md](docs/LIMITATIONS.md) V1~V7, 규칙: [docs/RULES_2026-11-17.md](docs/RULES_2026-11-17.md).
<!-- P1 요약 끝 -->

<!-- P2 요약 시작 (scripts/p2_docs.py 생성) -->
**P2 (최소판)**: 성분 32개 × 전국 원외 청구수량의 다음 달 예측(약국 단위 아님, 사용통계 공개 시차 5개월). MASE: 12개월 전 값 M0 0.923 / 전역 모델 M2 1.202 / M2 + P1 신호 M3 1.350 — 전역 모델도 단순 예측에 졌다. 결론(고정 문장): 이 설정(성분 32개, 신호가 0이 아닌 칸 2~11%, 전국 단위)에서는 선행 신호 특징이 예측을 개선하지 못했고, 주입 시험에서 20% 효과도 일부만 검출되어 신호 효과를 검출할 힘이 부족했다. 실제 사건을 미리 넣은 오라클과 시점 이동(−3~+3개월)으로도 개선되지 않았다. 금액 목표에서도 같았는데, 가격 몫이 금액 변화 분산의 약 3%로 작아 완벽한 가격 정보로도 개선 상한이 2% 안팎이고, 이 모델이 그 효과를 배울 학습 예가 적었던 것으로 본다. 2쪽 요약: [reports/P2_SUMMARY.md](reports/P2_SUMMARY.md), 결과: [reports/P2_min_result.md](reports/P2_min_result.md), 한계(P2 W1~W9: 생존 편향, 단순 예측에 짐, 빈칸 시기 표시, 수정본, 검출력 등): [docs/LIMITATIONS.md](docs/LIMITATIONS.md). 실행: `make p2-min`. P2 실험은 동결(이후 버그 수정만).
<!-- P2 요약 끝 -->


| | P1 선행 신호 원장 (완료) | P2 수요 예측 (최소판 완료, 실험 동결) |
|---|---|---|
| 질문 | 비고의 예고는 실제 변화로 얼마나, 얼마 만에 이어지나 | 사용통계가 5개월 늦게 공개될 때 다음 달 성분 수요를 맞추나, 목록 신호를 넣으면 나아지나 |
| 데이터 | 급여목록·대체조제 목록 2022-01 ~ 2025-12, 건보공단 사용통계 | 사용통계 2018-01 ~ 2025-12, P1 신호(2022-01 ~) |
| 산출물 | 원장, 분석 A·B·C, `make report MONTH=YYYY-MM` 월간 리포트 | `make p2-min`, [2쪽 요약](reports/P2_SUMMARY.md), [결과](reports/P2_min_result.md), 설계 [PLAN_P2](docs/PLAN_P2.md) |

## 폴더
```
config/        파라미터(params.yaml), 리포트 설정(report.yaml), 컬럼 대응표, 외부 근거
data/raw/      원본 데이터 (저장소에 없음, 읽기 전용) → docs/DATA.md §1
data/sample/   회귀 시험용 표본 (README만 저장소에 있음, parquet은 따로 받는다)
data/interim/  적재·원장 DB (pbd.duckdb, 생성물)
docs/          설계(PLAN_P1·P2), 자료(DATA), 결정(DECISIONS), 한계(LIMITATIONS), 요약(SUMMARY), 재현 로그
reference/     표본 점검 스크립트와 기대 출력 (make check-sample의 기준)
reports/       단계별 결과 보고서 (S0, P1 S1 ~ S6, v5 원장 v5~v5.3, holdout 정밀도), 2쪽 요약 SUMMARY.md
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
make all                       # = p1-all: S0 → check-sample → P1 S1 → S2 → v5 원장 → S4 → S5 → S6 → 전체 시험
make report MONTH=2024-09      # 한 달 리포트 → outputs/reports/P1_report_2024-09.md, outputs/dashboard/2024-09/
make test                      # 전체 시험 (시험 수는 위 P1 요약 블록)
```
깨끗한 클론 재현 기록은 v4 원장 시점(2026-11-16)의 것이다 ([docs/REPRODUCE_LOG.md](docs/REPRODUCE_LOG.md)). v5.3에서는 깨끗한 클론 재현을 다시 하지 않았다. v4 원장은 `outputs/v4_legacy/`에 읽기 전용으로 있고 기본 경로(`make all`)에서 쓰지 않는다 (`make p1-v4-legacy`). P2 최소판은 `make p2-min`(T1 → T2 → T3)으로 따로 돌린다(`all`에 넣지 않았다). 라벨을 채운 검증 표본(`outputs/validation/sample_60.user_reviewed.csv`)은 저장소에 있고, `make p1-s4`는 이 파일을 덮어쓰지 않는다.

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
| [docs/RULES_2026-11-17.md](docs/RULES_2026-11-17.md) | v5 판정 규칙 (용어·상태·이벤트·신호·지표, §11 결정) |
| [reports/SUMMARY.md](reports/SUMMARY.md) | P1 2쪽 요약 (v5.3, `scripts/p1_docs.py` 생성) |
| [reports/P2_SUMMARY.md](reports/P2_SUMMARY.md) | P2 2쪽 요약 (최소판, `scripts/p2_docs.py` 생성) |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | 한계 (v5.3 V1~V7 먼저, 그다음 자료 / 규칙·판정 / 검증 / 리포트 / 사용통계 시차) |
| [docs/BACKLOG.md](docs/BACKLOG.md) | 2차 항목 (구현하지 않음) |
| [reports/](reports/) | 단계별 결과: [S0](reports/S0_result.md), [S1](reports/P1_S1_result.md), [S2](reports/P1_S2_result.md), [S3](reports/P1_S3_result.md), [S4](reports/P1_S4_result.md), [S5](reports/P1_S5_result.md), [S6](reports/P1_S6_result.md) |
