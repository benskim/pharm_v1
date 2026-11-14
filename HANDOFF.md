# HANDOFF — Claude Code 인계 패키지 (2026-11-14)

이 폴더를 저장소 루트로 쓴다. 시작은 `prompts/STAGES.md`의 **S0**다. 이 패키지는 2026-11-13 `handoff_final` 패키지를 대체한다. 내용이 다르면 이 패키지가 우선이다.

## 들어 있는 것
```
HANDOFF.md              이 문서
CLAUDE.md               작업 규칙 (Claude Code가 자동으로 읽는다)
README.md               포트폴리오 첫 화면 (S7·T5에서 완성)
requirements.txt
docs/
  DATA.md               입력 데이터, 폴더, 위험, S0 확인 항목
  PLAN_P1.md            P1 선행 신호 원장: 모델, 분석, 단계별 완료 기준
  PLAN_P2.md            P2 수요 예측: 대상, 특징, 모델, 평가, 발주 시뮬레이션
  NOTE_RULES.md         비고 유형별 규칙 (R1~R7, P1~P3) + 신호 판정
  DECISIONS.md          이미 정한 것, 정해야 할 것
  BACKLOG.md            2차 (구현하지 않음)
prompts/STAGES.md       단계별 지시문 (복사해 붙여 넣기)
config/
  params.yaml           기간, 추적 기간, 예측 설정, 시드
  schema_map.yaml       컬럼 대응표 초안 (S0에서 채움)
reference/
  precheck_v3.py        표본 점검 스크립트 (회귀 시험 기준)
  precheck_v3_output.md 기대 출력
  secondary/README.md   2차 근거 스크립트 위치
data/raw/...            원본 자리 (아래)
data/sample/            표본 자리
src/pbd/ledger/         P1 코드 (S1부터)
src/pbd/forecast/       P2 코드 (T1부터)
tests/ reports/ outputs/
```

## 시작 전에 할 일
1. 원본 배치. 받은 형식 그대로 넣는다.
   ```
   data/raw/benefit/      급여목록 스냅샷 2022-01~2025-12
   data/raw/substitute/   대체조제 목록 스냅샷 2022-01~2025-12
   data/raw/usage/        건보공단 사용통계 원본 집계파일 2018-01~2025-12
   data/raw/master/       dim_drug_master_20260831, dim_drug_info, dim_generic_master
   data/raw/price_2026/   2026 약가정보 (심평원 버전, 의약품안전나라 버전)
   data/raw/orangebook/   오렌지북 2026
   data/sample/           표본 parquet 8개 (파일명 그대로)
   ```
2. `pip install -r requirements.txt`
3. 표본 재현 확인:
   ```
   python -I reference/precheck_v3.py data/sample > /tmp/out.md
   diff /tmp/out.md reference/precheck_v3_output.md
   ```
   차이가 없어야 한다. (2026-11-14에 표본으로 실행해 확인했다.)
4. S0 지시문을 Claude Code에 붙여 넣는다.

## 진행 방식
- 한 단계씩 지시하고, `reports/<단계>_result.md`의 "확인 필요"에 답한 뒤 다음으로 간다.
- 이 패키지의 숫자는 전부 2024~2025 표본에서 나왔다. 2022~2023 스냅샷과 사용통계 원본에는 표본에 없던 문형·컬럼이 나올 수 있다. S0가 찾아 보고하고, 규칙은 사용자가 정한다.

## 일정 (마감 2026-11-30)
| 기간 | 작업 |
|---|---|
| 11/14~11/15 | S0. 확인 필요 답변 |
| 11/16~11/18 | P1 S1, S2 |
| 11/19~11/20 | P1 S3. 병행: P2 T1, T2 |
| 11/21~11/22 | P1 S4 (60건 라벨링 포함) |
| 11/23~11/25 | P2 T3, T4 |
| 11/26~11/27 | P1 S6 리포트·대시보드, S5 (B1 중심) |
| 11/28~11/29 | S7, T5 문서·2쪽 요약, 재현 로그 |
| 11/30 | 제출 |

**밀릴 때 줄이는 순서**: P1 S5 → P2 계층 정합 → P2 발주 시뮬레이션을 비용 비율 1개로 → P2를 최소판으로 (`PLAN_P2.md` §7). P1 S0~S4와 S6은 줄이지 않는다.

## 사용자가 할 일
| 시점 | 할 일 |
|---|---|
| S0 전 | 원본 배치. 사용통계 원본과 가공본 컬럼 대응, 가공 시 뺀 행의 기준 메모 (`DECISIONS.md`) |
| S0 후 | 새 비고 문형 규칙, P2 주력 묶음 약효분류번호 |
| T2 | 사용통계 공개 시차 현실값 |
| S4 | 60건 라벨링 |
